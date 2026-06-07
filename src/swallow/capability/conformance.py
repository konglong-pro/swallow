from __future__ import annotations

from swallow.capability.schemas import ArtifactRef, CapabilityResult, ProviderManifest


def validate_manifest(payload: object) -> ProviderManifest:
    return ProviderManifest.model_validate(payload)


def validate_result(payload: object) -> CapabilityResult:
    return CapabilityResult.model_validate(payload)


def validate_artifact_ref(payload: object) -> ArtifactRef:
    return ArtifactRef.model_validate(payload)
