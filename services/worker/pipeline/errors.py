from __future__ import annotations


class PipelineError(Exception):
    """User-readable pipeline failure."""

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}
