class AriError(Exception):
    """Base exception for expected ARI failures."""


class NotFoundError(AriError):
    pass


class InvalidStateError(AriError):
    pass


class NonRetryableJobError(AriError):
    """An expected job outcome that should be dead-lettered without automatic retry."""


class CaseValidationError(AriError):
    pass


class ProviderError(AriError):
    def __init__(self, message: str, *, execution: object, retryable: bool = False) -> None:
        super().__init__(message)
        self.execution = execution
        self.retryable = retryable
