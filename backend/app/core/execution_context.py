"""
Execution Context Management for CrazzyDocs Autonomous Production Studio.
Enforces strict boundary separation between PRODUCTION, TEST, DEVELOPMENT, and MAINTENANCE environments.
"""

from enum import Enum
import os
import logging

logger = logging.getLogger(__name__)


class ExecutionContext(str, Enum):
    PRODUCTION = "PRODUCTION"
    TEST = "TEST"
    DEVELOPMENT = "DEVELOPMENT"
    MAINTENANCE = "MAINTENANCE"

    @classmethod
    def from_string(cls, val: str) -> "ExecutionContext":
        if not val or not isinstance(val, str):
            raise ValueError(f"Invalid execution context value: {val!r}. Must be non-empty string.")
        norm = val.strip().upper()
        for member in cls:
            if member.value == norm:
                return member
        raise ValueError(
            f"Unrecognized execution context: {val!r}. Allowed contexts: {[m.value for m in cls]}"
        )


def get_current_execution_context() -> ExecutionContext:
    """Reads execution context from environment or defaults to PRODUCTION."""
    raw = os.environ.get("CRAZZYDOCS_EXECUTION_CONTEXT")
    if raw:
        return ExecutionContext.from_string(raw)
    return ExecutionContext.PRODUCTION


def require_production_context(context: ExecutionContext, action_name: str = "Production operation") -> None:
    """Fails closed if the given execution context is not PRODUCTION."""
    if not isinstance(context, ExecutionContext):
        try:
            context = ExecutionContext.from_string(str(context))
        except Exception as e:
            raise PermissionError(f"[FAIL-CLOSED] Invalid execution context {context!r} for {action_name}: {e}")
    if context != ExecutionContext.PRODUCTION:
        raise PermissionError(
            f"[FAIL-CLOSED] {action_name} is strictly prohibited in {context.value} context! "
            f"Only PRODUCTION context is authorized for real publishing, ledger persistence, or live notifications."
        )
