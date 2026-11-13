# opa test suite for the NHDW authz decision (`data.nhdw.authz.decide`).
#
# Mirrors the gateway's startup self-test matrix (mart_attrs.py SELF_TESTS):
# those (role, resource) -> allow/deny expectations are the reference contract.
# Run with `opa test 7_security/opa/policies`. The deploy-time policy
# regeneration (bundle/scripts/regen-opa-policy.sh) gates on this suite: a
# DB-sourced policy that fails it does not reach OPA (fail-closed).
#
# Not shipped to the runtime OPA (the opa-policies ConfigMap lists only
# common.rego and role_r_*.rego).
package nhdw.authz

import rego.v1

# Resource fixtures (the self-test marts, with their mart_attrs attributes):
#   bi_programme_hiv_cascade_total = PRG / aggregate / DC.PRO / default
#   bi_service_flagship_attainment = PRG / aggregate / DC.PRO / default
#   bi_safety_aefi_lineage         = PT  / aggregate / DC.AEFI / restricted
#   enc_patient_visit (vault)      = PT  / raw       / DC.ENC  / restricted
_read(role, subj, gran, dc, sens) := {
	"user": {"role": role, "idp_trust": "high"},
	"action": {"verb": "read"},
	"resource": {"subject": subj, "granularity": gran, "data_class": dc, "sensitivity": sens},
	"context": {"purpose_of_use": "public-health"},
}

# PRG aggregate (bi_programme_hiv_cascade_total): who may read
test_dec_allows_prg_agg if { decide.allow with input as _read("R-DEC", "PRG", "aggregate", "DC.PRO", "default") }
test_cons_ana_allows_prg_agg if { decide.allow with input as _read("R-CONS-ANA", "PRG", "aggregate", "DC.PRO", "default") }
test_super_allows_prg_agg if { decide.allow with input as _read("R-SUPER", "PRG", "aggregate", "DC.PRO", "default") }
test_cons_prt_allows_prg_agg if { decide.allow with input as _read("R-CONS-PRT", "PRG", "aggregate", "DC.PRO", "default") }
test_pub_anon_allows_flagship if { decide.allow with input as _read("R-PUB-ANON", "PRG", "aggregate", "DC.PRO", "default") }

test_prod_submit_denies_prg_agg if { not decide.allow with input as _read("R-PROD-SUBMIT", "PRG", "aggregate", "DC.PRO", "default") }
test_ops_eng_denies_prg_agg if { not decide.allow with input as _read("R-OPS-ENG", "PRG", "aggregate", "DC.PRO", "default") }
test_ops_sec_denies_prg_agg if { not decide.allow with input as _read("R-OPS-SEC", "PRG", "aggregate", "DC.PRO", "default") }
test_stew_denies_prg_agg if { not decide.allow with input as _read("R-STEW", "PRG", "aggregate", "DC.PRO", "default") }
test_ops_adm_denies_prg_agg if { not decide.allow with input as _read("R-OPS-ADM", "PRG", "aggregate", "DC.PRO", "default") }
test_cons_sci_denies_prg_agg if { not decide.allow with input as _read("R-CONS-SCI", "PRG", "aggregate", "DC.PRO", "default") }
test_cons_res_denies_prg_agg if { not decide.allow with input as _read("R-CONS-RES", "PRG", "aggregate", "DC.PRO", "default") }
test_prod_clin_denies_prg_agg if { not decide.allow with input as _read("R-PROD-CLIN", "PRG", "aggregate", "DC.PRO", "default") }

# raw patient (enc_patient_visit): only the super-user
test_super_allows_raw_patient if { decide.allow with input as _read("R-SUPER", "PT", "raw", "DC.ENC", "restricted") }
test_dec_denies_raw_patient if { not decide.allow with input as _read("R-DEC", "PT", "raw", "DC.ENC", "restricted") }
test_cons_ana_denies_raw_patient if { not decide.allow with input as _read("R-CONS-ANA", "PT", "raw", "DC.ENC", "restricted") }
test_cons_prt_denies_raw_patient if { not decide.allow with input as _read("R-CONS-PRT", "PT", "raw", "DC.ENC", "restricted") }

# restricted AEFI: public is denied
test_pub_anon_denies_aefi if { not decide.allow with input as _read("R-PUB-ANON", "PT", "aggregate", "DC.AEFI", "restricted") }

# structural guards
test_unknown_role_denied if { not decide.allow with input as _read("R-BOGUS", "PRG", "aggregate", "DC.PRO", "default") }

# low-trust federated identity is denied a sensitive (raw) action even as R-SUPER
test_low_trust_denied_raw if {
	not decide.allow with input as {
		"user": {"role": "R-SUPER", "idp_trust": "low"},
		"action": {"verb": "read"},
		"resource": {"subject": "PT", "granularity": "raw", "data_class": "DC.ENC", "sensitivity": "restricted"},
		"context": {"purpose_of_use": "public-health"},
	}
}

# a scoped caller's geographies become a row_filter obligation
test_scope_row_filter if {
	d := decide with input as {
		"user": {"role": "R-DEC", "idp_trust": "high"},
		"action": {"verb": "read"},
		"resource": {"subject": "PRG", "granularity": "aggregate", "data_class": "DC.PRO", "sensitivity": "default"},
		"context": {"purpose_of_use": "public-health", "geographies": ["OR"]},
	}
	d.obligations.row_filter.geography == ["OR"]
}

# column masking (masked_columns obligation)
# The PEP sends the relation's classified columns; the policy returns the
# subset the caller may not see clear. bi_supply.mart_ct_nodes fixture:
# lat/lng restricted (generalize), plus a privileged patient column.
_read_cols(role, trust, cols) := {
	"user": {"role": role, "idp_trust": trust},
	"action": {"verb": "read"},
	"resource": {
		"subject": "PRD", "granularity": "aggregate",
		"data_class": "DC.STK", "sensitivity": "default",
		"columns": cols,
	},
	"context": {"purpose_of_use": "public-health"},
}

_gps := [
	{"name": "lat", "sensitivity": "restricted"},
	{"name": "lng", "sensitivity": "restricted"},
	{"name": "patient_id", "sensitivity": "privileged"},
]

# analyst reveals restricted (their maps plot GPS); privileged stays masked
test_mask_cons_ana_reveals_restricted_not_privileged if {
	d := decide with input as _read_cols("R-CONS-ANA", "high", _gps)
	d.masked_columns == ["patient_id"]
}

# decision maker (and its R-PD/R-RHB aliases route here): same reveal
test_mask_pd_hiv_alias_reveals_restricted if {
	d := decide with input as _read_cols("R-PD-HIV", "high", _gps)
	d.masked_columns == ["patient_id"]
}

# a role with no reveal grant masks every classified column
test_mask_pub_anon_masks_all_classified if {
	d := decide with input as _read_cols("R-PUB-ANON", "high", _gps)
	d.masked_columns == ["lat", "lng", "patient_id"]
}

# security officer reveals both tiers: nothing masked
test_mask_ops_sec_reveals_everything if {
	d := decide with input as _read_cols("R-OPS-SEC", "high", _gps)
	d.masked_columns == []
}

# R-SUPER wildcard: nothing masked
test_mask_super_reveals_everything if {
	d := decide with input as _read_cols("R-SUPER", "high", _gps)
	d.masked_columns == []
}

# a low-trust identity never sees a classified column clear, whatever the role
test_mask_low_trust_masks_despite_reveal if {
	d := decide with input as _read_cols("R-CONS-ANA", "low", _gps)
	d.masked_columns == ["lat", "lng", "patient_id"]
}

# unclassified relation (no columns sent) -> nothing masked
test_mask_no_columns_no_masking if {
	d := decide with input as _read_cols("R-PUB-ANON", "high", [])
	d.masked_columns == []
}

# unknown role -> the default deny fires; no data flows, so there is no
# masking obligation
test_mask_unknown_role_denied_outright if {
	d := decide with input as _read_cols("R-NOPE", "high", _gps)
	not d.allow
}
