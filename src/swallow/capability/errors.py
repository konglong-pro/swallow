from __future__ import annotations


class CapabilityProviderError(Exception):
    """Base class for provider invocation failures."""


class CapabilityConfigError(CapabilityProviderError):
    """Raised when provider configuration is invalid."""


class CapabilityPolicyError(CapabilityProviderError):
    """Raised when profile or policy rejects an invocation."""


class CapabilityInputError(CapabilityProviderError):
    """Raised before a job exists for invalid provider input."""


class CapabilityJobNotFound(CapabilityProviderError):
    """Raised when a provider-created job cannot be found."""


class CapabilityRegistryError(CapabilityProviderError):
    """Raised when provider registry read/write fails."""


class CapabilityArtifactError(CapabilityProviderError):
    """Raised when an artifact reference cannot be resolved or read."""
