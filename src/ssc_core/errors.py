"""Structured failures at the untrusted record boundary."""


class ValidationError(ValueError):
    """An invalid value with a JSON-style field path and stable reason code."""

    def __init__(self, path: str, code: str, message: str) -> None:
        self.path = path
        self.code = code
        self.message = message
        super().__init__(f"{path}: {code}: {message}")
