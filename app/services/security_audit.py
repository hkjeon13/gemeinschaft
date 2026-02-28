import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict

_audit_logger = logging.getLogger("security.audit")


def emit_security_event(event_type: str, outcome: str, **fields: Any) -> None:
    payload: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "event_type": event_type,
        "outcome": outcome,
    }
    payload.update(fields)

    try:
        _audit_logger.info(json.dumps(payload, separators=(",", ":"), default=str))
    except Exception:
        # Auditing failure must not break auth flow.
        pass
