"""对外只暴露稳定错误码，不回传上游响应、请求正文或服务地址。"""

VALIDATION_REASONS = frozenset(
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
