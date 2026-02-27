class ServiceError(Exception):
    def __init__(self, message: str, status_code: int = 500, code: str = "service_error") -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code

