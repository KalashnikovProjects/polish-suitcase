class AgentError(Exception):
    """Sanitized, actionable application error (never include credentials)."""


class RetryDelivery(AgentError):
    pass


class PermanentFailure(AgentError):
    pass


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
