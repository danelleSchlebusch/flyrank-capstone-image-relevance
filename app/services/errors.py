class NotFoundError(Exception):
    """A requested record does not exist (HTTP 404)."""


class ConflictError(Exception):
    """The request is valid, but the data is not in the right state yet (HTTP 409)."""