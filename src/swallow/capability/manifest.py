from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from swallow.capability.errors import CapabilityConfigError
from swallow.capability.schemas import CapabilityManifest, CapabilityRisk, ProviderManifest, ProviderPolicy


PROVIDER_ID = "swallow"
P1_CAPABILITIES = [
    "swallow.ingest.file",
    "swallow.ingest.url",
    "swallow.ingest.browser_capture",
    "swallow.ingest.archive",
    "swallow.ingest.batch",
]
PROVIDER_OPERATIONS = [
    "discover",
    "doctor",
    "plan",
    "submit",
    "submit_batch",
    "get_job",
    "get_batch",
    "wait",
    "wait_batch",
    "get_result",
    "get_batch_result",
    "get_artifact",
    "cancel",
    "cancel_batch",
]
ARTIFACT_KINDS = [
    "raw_original",
    "raw_metadata",
    "markdown_candidate",
    "ingest_document",
    "manifest",
    "trace",
    "intermediate_artifact",
    "batch_summary",
]
EVIDENCE_REQUIRED = [
    "raw_store/<sha256>/original.<ext>",
    "raw_store/<sha256>/original.meta.json",
    "jobs/<job_id>/job.json",
    "jobs/<job_id>/document.md",
    "jobs/<job_id>/ingest_document.json",
    "jobs/<job_id>/trace.jsonl",
    "jobs/<job_id>/manifest.json",
]
BATCH_EVIDENCE_REQUIRED = [
    "batch_runs/<batch_id>/summary.json",
    "batch_runs/<batch_id>/trace.jsonl",
    "jobs/<job_id>/job.json",
    "jobs/<job_id>/document.md",
    "jobs/<job_id>/ingest_document.json",
    "jobs/<job_id>/trace.jsonl",
    "jobs/<job_id>/manifest.json",
]


@dataclass(frozen=True)
class ProviderProfile:
    name: str
    backend_order: tuple[str, ...]
    allowed_source_types: tuple[str, ...]
    allow_network: bool
    allow_ocr: bool
    allow_asr: bool
    max_file_size_mb: int | None = None
    content_mode_default: str = "preview"
    requires_service_url: bool = False


BUILTIN_PROFILES: dict[str, ProviderProfile] = {
    "local_light": ProviderProfile(
        name="local_light",
        backend_order=("local",),
        allowed_source_types=("file",),
        allow_network=False,
        allow_ocr=False,
        allow_asr=False,
        max_file_size_mb=20,
    ),
    "local_isolated": ProviderProfile(
        name="local_isolated",
        backend_order=("cli",),
        allowed_source_types=("file", "browser_capture", "archive"),
        allow_network=False,
        allow_ocr=True,
        allow_asr=True,
    ),
    "service": ProviderProfile(
        name="service",
        backend_order=("http",),
        allowed_source_types=("file", "url", "browser_capture", "archive"),
        allow_network=False,
        allow_ocr=True,
        allow_asr=True,
        requires_service_url=True,
    ),
    "heavy_queue": ProviderProfile(
        name="heavy_queue",
        backend_order=("queue",),
        allowed_source_types=("file", "url", "browser_capture", "archive", "batch"),
        allow_network=True,
        allow_ocr=True,
        allow_asr=True,
    ),
}


def profile_policy_defaults(profile: ProviderProfile) -> ProviderPolicy:
    return ProviderPolicy(
        allow_filesystem_read=True,
        allow_network=profile.allow_network,
        allow_heavy_runtime=profile.allow_ocr or profile.allow_asr,
        allow_raw_artifact_read=False,
        allow_full_content=False,
        expose_local_paths=False,
    )


def load_profile(name: str, overrides: Mapping[str, Any] | None = None) -> ProviderProfile:
    if name not in BUILTIN_PROFILES:
        raise CapabilityConfigError(f"Unsupported provider profile: {name}")
    profile = BUILTIN_PROFILES[name]
    if not overrides:
        return profile

    allowed_keys = {
        "backend_order",
        "allowed_source_types",
        "allow_network",
        "allow_ocr",
        "allow_asr",
        "max_file_size_mb",
        "content_mode_default",
        "requires_service_url",
    }
    unknown = set(overrides) - allowed_keys
    if unknown:
        raise CapabilityConfigError(f"Unsupported profile override keys: {sorted(unknown)}")

    normalized = dict(overrides)
    for key in ("backend_order", "allowed_source_types"):
        if key in normalized:
            normalized[key] = tuple(str(item) for item in normalized[key])
    return replace(profile, **normalized)


def build_provider_manifest(
    *,
    provider_version: str,
    profile: ProviderProfile,
    policy: ProviderPolicy,
) -> ProviderManifest:
    return ProviderManifest(
        provider_id=PROVIDER_ID,
        provider_version=provider_version,
        profile=profile.name,
        capabilities=[
            build_capability_manifest(
                capability_id=capability_id,
                provider_version=provider_version,
                profile=profile,
                policy=policy,
            )
            for capability_id in P1_CAPABILITIES
        ],
        operations=PROVIDER_OPERATIONS,
    )


def build_capability_manifest(
    *,
    capability_id: str,
    provider_version: str,
    profile: ProviderProfile,
    policy: ProviderPolicy,
) -> CapabilityManifest:
    source_type = source_type_for_capability(capability_id)
    required_policy = required_policy_for_source(source_type, profile)
    available, unavailable_reason = capability_availability(source_type, profile, policy, required_policy)
    return CapabilityManifest(
        capability_id=capability_id,  # type: ignore[arg-type]
        provider_id=PROVIDER_ID,
        version=provider_version,
        title=title_for_capability(capability_id),
        description=description_for_capability(capability_id),
        input_schema=input_schema_for_capability(capability_id),
        output_schema=capability_output_schema_for_source(source_type),
        artifact_kinds=ARTIFACT_KINDS,
        side_effects=side_effects_for_source(source_type),
        risk=risk_for_source(source_type),
        evidence_required=evidence_required_for_source(source_type),
        available=available,
        unavailable_reason=unavailable_reason,
        requires_policy=required_policy,
    )


def source_type_for_capability(capability_id: str) -> str:
    return {
        "swallow.ingest.file": "file",
        "swallow.ingest.url": "url",
        "swallow.ingest.browser_capture": "browser_capture",
        "swallow.ingest.archive": "archive",
        "swallow.ingest.batch": "batch",
    }[capability_id]


def capability_for_source(source_type: str) -> str:
    return {
        "file": "swallow.ingest.file",
        "url": "swallow.ingest.url",
        "browser_capture": "swallow.ingest.browser_capture",
        "archive": "swallow.ingest.archive",
        "batch": "swallow.ingest.batch",
    }[source_type]


def required_policy_for_source(source_type: str, profile: ProviderProfile) -> list[str]:
    required: list[str] = []
    if source_type in {"file", "browser_capture", "archive", "batch"}:
        required.append("allow_filesystem_read")
    if source_type == "url":
        required.append("allow_network")
    if source_type in {"file", "batch"} and (profile.allow_ocr or profile.allow_asr):
        required.append("allow_heavy_runtime")
    return required


def capability_availability(
    source_type: str,
    profile: ProviderProfile,
    policy: ProviderPolicy,
    required_policy: list[str],
) -> tuple[bool, str | None]:
    if source_type not in profile.allowed_source_types:
        return False, f"{source_type}_disabled_by_profile"
    missing = [permission for permission in required_policy if not bool(getattr(policy, permission))]
    if missing:
        return False, f"missing_policy:{','.join(missing)}"
    return True, None


def title_for_capability(capability_id: str) -> str:
    return {
        "swallow.ingest.file": "Ingest local file",
        "swallow.ingest.url": "Ingest URL",
        "swallow.ingest.browser_capture": "Ingest browser capture",
        "swallow.ingest.archive": "Ingest export archive",
        "swallow.ingest.batch": "Ingest file batch",
    }[capability_id]


def description_for_capability(capability_id: str) -> str:
    return {
        "swallow.ingest.file": "Convert a local file into a traceable Markdown ingest candidate.",
        "swallow.ingest.url": "Convert an allowed URL into a traceable Markdown ingest candidate.",
        "swallow.ingest.browser_capture": "Convert local browser capture JSON into Markdown.",
        "swallow.ingest.archive": "Convert a supported export archive into Markdown.",
        "swallow.ingest.batch": "Queue a local file batch and produce traceable ingest candidates.",
    }[capability_id]


def input_schema_for_capability(capability_id: str) -> dict[str, Any]:
    if capability_id == "swallow.ingest.batch":
        return {
            "type": "object",
            "required": ["patterns"],
            "properties": {
                "patterns": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "workers": {"type": "integer", "minimum": 1, "default": 1},
                "routing_policy": {"type": "object"},
            },
        }
    if capability_id == "swallow.ingest.url":
        return {
            "type": "object",
            "required": ["url"],
            "properties": {
                "url": {"type": "string"},
                "content_mode": content_mode_schema(),
                "routing_policy": {"type": "object"},
            },
        }
    return {
        "type": "object",
        "required": ["source_path"],
        "properties": {
            "source_path": {"type": "string"},
            "content_mode": content_mode_schema(),
            "routing_policy": {"type": "object"},
        },
    }


def capability_output_schema_for_source(source_type: str) -> dict[str, Any]:
    if source_type == "batch":
        return {
            "type": "object",
            "required": ["batch_id", "status", "job_ids"],
            "properties": {
                "batch_id": {"type": "string"},
                "status": {"enum": ["queued", "running", "success", "partial", "failed", "canceled"]},
                "job_ids": {"type": "array"},
                "warnings": {"type": "array"},
                "errors": {"type": "array"},
            },
        }
    return {
        "type": "object",
        "required": ["job_id", "status", "artifacts"],
        "properties": {
            "job_id": {"type": "string"},
            "status": {"enum": ["queued", "running", "success", "partial", "failed", "canceled"]},
            "artifacts": {"type": "array"},
            "warnings": {"type": "array"},
            "errors": {"type": "array"},
        },
    }


def evidence_required_for_source(source_type: str) -> list[str]:
    if source_type == "batch":
        return BATCH_EVIDENCE_REQUIRED
    return EVIDENCE_REQUIRED


def content_mode_schema() -> dict[str, Any]:
    return {"type": "string", "enum": ["none", "preview", "full"], "default": "preview"}


def side_effects_for_source(source_type: str) -> list[str]:
    if source_type == "batch":
        return ["read_source_file", "write_batch_run", "write_job_artifacts", "write_raw_store"]
    side_effects = ["write_job_artifacts", "write_raw_store"]
    if source_type in {"file", "browser_capture", "archive"}:
        side_effects.insert(0, "read_source_file")
    if source_type == "url":
        side_effects.insert(0, "network_fetch")
    return side_effects


def risk_for_source(source_type: str) -> CapabilityRisk:
    return CapabilityRisk(
        filesystem_read=source_type in {"file", "browser_capture", "archive", "batch"},
        network=source_type == "url",
        heavy_runtime_possible=source_type in {"file", "url", "archive", "batch"},
        requires_approval=True,
    )
