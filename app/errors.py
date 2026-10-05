"""统一错误类型：所有错误响应均为 {"message": "..."} 格式。"""


class ApiError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(message)
