class Gns3ApiError(Exception):
    """Raised when the GNS3 controller API returns an error response."""

    def __init__(self, status_code: int, detail: str, method: str, path: str):
        self.status_code = status_code
        self.detail = detail
        self.method = method
        self.path = path
        super().__init__(f"{method} {path} -> {status_code}: {detail}")
