from ari.domain.errors import InvalidStateError


class ConflictError(InvalidStateError):
    """The row moved since the caller read it (optimistic concurrency) or the state forbids it."""
