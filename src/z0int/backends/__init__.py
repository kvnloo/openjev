"""DecisionBackend package — local intelligence engines behind one contract."""

from .base import (
    BackendCapabilities,
    BackendHealth,
    DecisionAnswer,
    DecisionBackend,
    DecisionOption,
    DecisionQuestion,
    DecisionRequest,
    DecisionResult,
    request_from_mapping,
    result_to_dict,
)
from .registry import (
    BackendSpec,
    create_backend,
    get_backend_spec,
    list_backend_specs,
    register_builtin_backends,
)

__all__ = [
    "BackendCapabilities",
    "BackendHealth",
    "BackendSpec",
    "DecisionAnswer",
    "DecisionBackend",
    "DecisionOption",
    "DecisionQuestion",
    "DecisionRequest",
    "DecisionResult",
    "request_from_mapping",
    "result_to_dict",
    "create_backend",
    "get_backend_spec",
    "list_backend_specs",
    "register_builtin_backends",
]
