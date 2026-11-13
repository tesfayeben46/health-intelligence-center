"""Column masking: the enforcement half of the OPA masking obligation.

OPA decides which classified columns the caller may not see in clear
(`decision.masked_columns`, from the per-role reveal sets). This module decides
how each is transformed, by its mask_class in ops_perm.column_classification
(mart_attrs.columns_for_mart):

    drop        column removed from every row and from the column list
    hash        keyed HMAC-SHA256, 16 hex chars; irreversible and unlinkable
                across responses (a fresh nonce is mixed in per call)
    partial     tail reveal, •••<last 4 chars> (phone numbers, ids)
    tokenize    deterministic HMAC pseudonym, stable across calls, so joins on
                the column still work without exposing the raw value
    generalize  precision reduction: numbers rounded to 1 decimal (about 11 km
                for GPS), dates truncated to month; anything else is dropped
    none        left as is (classified but explicitly unmasked)

Every edge fails closed: an unknown mask_class drops; hash and tokenize drop
when no key is configured (never fall back to cleartext); a masked column with
no classification row drops. Callers pass rows as list[dict] (the trino_client
shape) and get back (rows, dropped), where `dropped` lists the columns removed
entirely so the caller can adjust its column list.
"""
from __future__ import annotations

import datetime as _dt
import decimal as _decimal
import hashlib
import hmac
import logging
import os
import secrets as _secrets
from typing import Any

from .mart_attrs import ColumnClass

log = logging.getLogger("gateway.masking")

# Deployment secret for hash and tokenize (secret MASKING_HMAC_KEY). When it is
# absent those classes degrade to drop, never to cleartext.
_HMAC_KEY = os.environ.get("MASKING_HMAC_KEY", "")


def _hmac_hex(key: bytes, value: Any, n: int = 16) -> str:
    raw = str(value).encode("utf-8", "replace")
    return hmac.new(key, raw, hashlib.sha256).hexdigest()[:n]


def _generalize(value: Any):
    """Reduce precision. None means the value could not be generalised and the
    column is dropped."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    # Decimal must be listed: Trino returns NUMERIC mart columns (including GPS
    # coordinates) as decimal.Decimal, which is neither int nor float.
    if isinstance(value, (int, float, _decimal.Decimal)):
        return round(float(value), 1)
    if isinstance(value, _dt.datetime):
        return value.date().replace(day=1).isoformat()
    if isinstance(value, _dt.date):
        return value.replace(day=1).isoformat()
    if isinstance(value, str):
        try:
            return round(float(value), 1)
        except ValueError:
            return None
    return None


def apply_masking(rows: list[dict[str, Any]],
                  colclass: dict[str, ColumnClass],
                  masked_columns: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """Transform the masked columns of `rows` in place. Returns
    (rows, dropped), where `dropped` lists columns removed entirely."""
    if not masked_columns or not rows:
        return rows, []

    nonce = _secrets.token_bytes(8)                      # per-response unlinkability for `hash`
    hash_key = (_HMAC_KEY.encode() + nonce) if _HMAC_KEY else None
    tok_key = _HMAC_KEY.encode() if _HMAC_KEY else None

    plan: dict[str, str] = {}
    for name in masked_columns:
        cc = colclass.get(name)
        mask = cc.mask_class if cc else "drop"
        if mask in ("hash", "tokenize") and not _HMAC_KEY:
            log.warning("masking: no MASKING_HMAC_KEY; %s degrades to drop for %r",
                        mask, name)
            mask = "drop"
        if mask not in ("none", "drop", "hash", "partial", "generalize", "tokenize"):
            log.warning("masking: unknown mask_class %r for %r; dropping (fail-closed)",
                        mask, name)
            mask = "drop"
        if mask != "none":
            plan[name] = mask

    dropped = sorted(n for n, m in plan.items() if m == "drop")
    generalize_failed: set[str] = set()

    for row in rows:
        for name, mask in plan.items():
            if name not in row:
                continue
            v = row[name]
            if mask == "drop":
                del row[name]
            elif v is None:
                continue
            elif mask == "hash":
                row[name] = _hmac_hex(hash_key, v)
            elif mask == "tokenize":
                row[name] = "tok_" + _hmac_hex(tok_key, v, 12)
            elif mask == "partial":
                s = str(v)
                row[name] = ("•••" + s[-4:]) if len(s) > 4 else "•••"
            elif mask == "generalize":
                g = _generalize(v)
                if g is None:
                    del row[name]
                    generalize_failed.add(name)
                else:
                    row[name] = g

    if generalize_failed:
        # A column that could not be generalised on some rows is removed from
        # all of them; a mixed column would leak by contrast.
        for row in rows:
            for name in generalize_failed:
                row.pop(name, None)
        dropped = sorted(set(dropped) | generalize_failed)

    return rows, dropped
