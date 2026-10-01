"""Context-local migration identity and separate integrity-check diagnostics."""

import logging
from contextvars import ContextVar


logger = logging.getLogger("twicc.db.migrations")
current_migration = ContextVar("sqlite_migration", default=None)


def log_fk_check(decision, duration, success):
    identity = current_migration.get()
    migration, direction = identity if identity is not None else (None, None)
    reason = "; ".join(decision.reasons) or (
        "observed relation effects" if decision.scope == "tables" else "no observed relation effects"
    )
    tables = tuple(sorted(decision.tables))
    logger.info(
        "Migration FK check migration=%s direction=%s scope=%s tables=%s reason=%s fk_check_seconds=%.6f success=%s",
        migration, direction, decision.scope, tables, reason, duration, success,
        extra={
            "migration_event": "fk_check", "migration": migration, "direction": direction,
            "scope": decision.scope, "tables": tables, "reason": reason,
            "fk_check_seconds": duration, "success": success,
        },
    )
