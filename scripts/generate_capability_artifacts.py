from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from swallow.capability import ArtifactRef, ProviderManifest, SwallowCapabilityProvider
from swallow.capability.schemas import CapabilityResult


ARTIFACTS = {
    "schemas/capability-manifest.schema.json": lambda repo_root: ProviderManifest.model_json_schema(),
    "schemas/capability-result.schema.json": lambda repo_root: CapabilityResult.model_json_schema(),
    "schemas/artifact-ref.schema.json": lambda repo_root: ArtifactRef.model_json_schema(),
    "swallow.capabilities.json": lambda repo_root: SwallowCapabilityProvider(
        store_root=repo_root,
        cwd=repo_root,
    )
    .discover()
    .model_dump(mode="json", by_alias=True, exclude_none=True),
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Swallow Capability Provider schema and manifest artifacts.")
    parser.add_argument("--check", action="store_true", help="Fail if generated artifacts differ from files on disk.")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd(), help="Repository root. Defaults to cwd.")
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    generated = generate_artifacts(repo_root)
    if args.check:
        check_artifacts(repo_root, generated)
        return
    write_artifacts(repo_root, generated)


def generate_artifacts(repo_root: Path) -> dict[str, Any]:
    return {relative_path: producer(repo_root) for relative_path, producer in ARTIFACTS.items()}


def check_artifacts(repo_root: Path, generated: dict[str, Any]) -> None:
    drift: list[str] = []
    for relative_path, payload in generated.items():
        path = repo_root / relative_path
        if not path.exists():
            drift.append(relative_path)
            continue
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != payload:
            drift.append(relative_path)
    if drift:
        joined = ", ".join(drift)
        raise SystemExit(f"Capability artifacts are out of date: {joined}")
    print("Capability artifacts are up to date.")


def write_artifacts(repo_root: Path, generated: dict[str, Any]) -> None:
    for relative_path, payload in generated.items():
        path = repo_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        print(f"wrote {relative_path}")


if __name__ == "__main__":
    main()
