class AgentError(Exception):
    """Sanitized, actionable application error (never include credentials)."""


class RetryDelivery(AgentError):
    pass


class PermanentFailure(AgentError):
    pass


class PatchRejected(AgentError):
    """The backend definitively rejected an agent message or patch."""

    def __init__(
        self, status: int, code: str, message: str = "", details: dict | None = None
    ):
        self.status = status
        self.code = code
        self.message = message[:500]
        self.details = details or {}
        super().__init__(f"Backend rejected patch (HTTP {status}, code={code})")


class AuthenticationFailure(PermanentFailure):
    pass


class ChatDeleted(AgentError):
    pass


class StaleGeneration(AgentError):
    pass


class SynchronizationLost(RetryDelivery):
    pass


class UncertainWrite(RetryDelivery):
    pass


class Conflict(AgentError):
    def __init__(self, code: str, revision: int | None = None):
        self.code = code
        self.revision = revision
        super().__init__(code)


class SourceUnavailable(AgentError):
    pass
