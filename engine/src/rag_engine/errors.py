"""对外只暴露稳定错误码，不回传上游响应、请求正文或服务地址。"""

_GRAPH_MODEL_POLICIES = {
    "MODEL_TIMEOUT": ("GRAPH_MODEL_TIMEOUT", True, 504),
    "MODEL_NETWORK": ("GRAPH_MODEL_NETWORK", True, 502),
    "MODEL_TLS": ("GRAPH_MODEL_NETWORK", False, 502),
    "MODEL_CONFIGURATION": ("GRAPH_MODEL_REQUEST_REJECTED", False, 502),
    "MODEL_RATE_LIMIT": ("GRAPH_MODEL_RATE_LIMITED", True, 502),
    "MODEL_SERVER_UNAVAILABLE": ("GRAPH_MODEL_UNAVAILABLE", True, 502),
    "MODEL_AUTHENTICATION": ("GRAPH_MODEL_AUTH_FAILED", False, 502),
    "MODEL_HTTP_REJECTED": ("GRAPH_MODEL_REQUEST_REJECTED", False, 502),
    "MODEL_RESPONSE_LIMIT": ("GRAPH_MODEL_RESPONSE_TOO_LARGE", False, 502),
    "MODEL_RESPONSE_JSON": ("GRAPH_MODEL_INVALID_RESPONSE", False, 502),
}
GRAPH_MODEL_CODES = frozenset(policy[0] for policy in _GRAPH_MODEL_POLICIES.values())
GRAPH_MODEL_REASONS = frozenset(_GRAPH_MODEL_POLICIES)

VALIDATION_REASONS = GRAPH_MODEL_REASONS | frozenset(
    {
        "OUTPUT_TYPE_OR_SIZE",
        "JSON_SYNTAX",
        "TRIPLE_LIST_TYPE",
        "TRIPLE_COUNT",
        "TRIPLE_SHAPE",
        "FIELD_TYPE",
        "FIELD_EMPTY",
        "FIELD_LENGTH",
        "FIELD_CONTROL",
        "MODEL_CHOICES",
        "MODEL_FINISH_REASON",
        "MODEL_CONTENT",
        "RESULT_STRUCTURE",
        "PATH_COUNT",
        "ENTITY_TYPE",
        "ENTITY_NOT_IN_SOURCE",
        "RELATION_ENDPOINT",
        "RELATION_TYPE",
        "REPAIR_EMPTY_RESULT",
    }
)


class EngineError(Exception):
    def __init__(self, code: str, status: int = 502):
        super().__init__(code)
        self.code = code
        self.status = status


class GraphModelError(EngineError):
    """只由模型HTTP边界构造；分类和可重试性均来自固定策略，不携带上游内容。"""

    def __init__(self, reason: str):
        if type(reason) is not str or reason not in _GRAPH_MODEL_POLICIES:
            raise ValueError("Unknown graph model reason")
        code, retryable, status = _GRAPH_MODEL_POLICIES[reason]
        super().__init__(code, status)
        self.reason = reason
        self._retryable = retryable

    @property
    def retryable(self) -> bool:
        return self._retryable
