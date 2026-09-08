"""Structured use-case rejection shared with the northbound controller."""

class ProtocolError(Exception):
    def __init__(self, code: int, reason: str, message: str, retryable: bool = False, details: dict | None = None) -> None:
        self.code, self.reason, self.message, self.retryable, self.details = code, reason, message, retryable, details or {}
