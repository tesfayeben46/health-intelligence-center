"""Upsert the read-only Trino 'marts' database connection into Superset's
metadata, exposed in SQL Lab. Idempotent; run in the init after `superset init`.

The `marts` Trino catalog is PostgreSQL nhdw_vault (bi_* marts, read with the
bi_reader catalog credentials). Trino has no HTTP authentication in-cluster
(the nhdw-gateway is the policy enforcement point), so the SQLAlchemy user is
a session identity without a password."""
import logging

from superset.app import create_app

log = logging.getLogger("nhdw.superset.trino")

NAME = "NHDW Marts (Trino)"
URI = "trino://superset@nhdw-trino.nhdw.svc.cluster.local:8080/marts"

app = create_app()
with app.app_context():
    from superset import db
    from superset.models.core import Database

    existing = db.session.query(Database).filter_by(database_name=NAME).first()
    if existing:
        existing.set_sqlalchemy_uri(URI)
        existing.expose_in_sqllab = True
        action = "updated"
    else:
        d = Database(database_name=NAME, expose_in_sqllab=True)
        d.set_sqlalchemy_uri(URI)
        db.session.add(d)
        action = "created"
    db.session.commit()
    print(f"[create_trino_db] {action} '{NAME}' -> {URI}")
