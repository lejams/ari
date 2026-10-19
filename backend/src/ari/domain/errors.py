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


class ConflictError(AriError):
    """The request clashes with what is already running, for example a second voice session."""


class QuotaExceededError(AriError):
    """A quota of the offer is used up; the message says when it resets."""


class UsageCeilingError(AriError):
    """The optional per-learner abuse breaker (`ARI_LEARNER_COST_CEILING_EUR`) tripped."""


class EmailDeliveryError(AriError):
    """The e-mail provider refused or could not be reached; nothing was sent."""


class ProviderError(AriError):
    def __init__(self, message: str, *, execution: object, retryable: bool = False) -> None:
        super().__init__(message)
        self.execution = execution
        self.retryable = retryable
