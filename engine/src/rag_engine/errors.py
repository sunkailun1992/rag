"""对外只暴露稳定错误码，不回传上游响应、请求正文或服务地址。"""


class EngineError(Exception):
    def __init__(self, code: str, status: int = 502):
        super().__init__(code)
        self.code = code
        self.status = status
