from __future__ import annotations


class IngestSdkError(Exception):
    """Base class for SDK invocation failures."""


class IngestSdkConfigError(IngestSdkError):
    """Raised when SDK configuration is invalid or unsupported."""


class IngestSdkInputError(IngestSdkError):
    """Raised before a job exists for invalid local SDK input."""


class IngestSandboxError(IngestSdkInputError):
    """Raised when SDK sandbox rules reject an invocation."""


class IngestSdkJobNotFound(IngestSdkError):
    """Raised when a requested job id is not present in the store."""


class IngestWaitTimeout(IngestSdkError):
    """Raised when waiting times out before a terminal job state is known."""


class IngestSdkProtocolError(IngestSdkError):
    """Raised when persisted job data cannot be mapped to the SDK contract."""


class IngestTransportError(IngestSdkError):
    """Raised when an SDK transport fails before producing a job result."""


class IngestUnsupportedCapability(IngestSdkConfigError):
    """Raised when a selected SDK backend does not support an operation."""


class IngestFacadeRegistryError(IngestSdkError):
    """Raised when the facade registry cannot be read or written."""


IngestConfigError = IngestSdkConfigError
IngestProtocolError = IngestSdkProtocolError
IngestTimeoutError = IngestWaitTimeout
