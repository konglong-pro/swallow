from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path


class SmokeCommand:
    def __init__(self, name: str, command: list[str]) -> None:
        self.name = name
        self.command = command


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    store = args.store or Path(tempfile.mkdtemp(prefix="swallow-runtime-smoke-"))
    store.mkdir(parents=True, exist_ok=True)

    commands = build_smoke_commands(args, store)
    if not commands:
        raise SystemExit("No smoke tests selected.")

    failures: list[tuple[str, int]] = []
    print(f"Runtime smoke store: {store}", flush=True)
    for smoke in commands:
        print(f"\n==> {smoke.name}", flush=True)
        completed = subprocess.run(smoke.command, cwd=repo_root(), check=False)
        if completed.returncode != 0:
            failures.append((smoke.name, completed.returncode))
            if not args.keep_going:
                break

    if failures:
        print("\nRuntime smoke failures:", flush=True)
        for name, code in failures:
            print(f"- {name}: exit {code}", flush=True)
        return 1

    print("\nRuntime smoke all: success", flush=True)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run all local Swallow runtime smoke tests.")
    parser.add_argument("--store", type=Path, help="Store root. Defaults to a temporary directory.")
    parser.add_argument("--skip-service", action="store_true", help="Skip FastAPI service smoke.")
    parser.add_argument("--skip-ocr", action="store_true", help="Skip PaddleOCR smoke.")
    parser.add_argument("--skip-asr", action="store_true", help="Skip faster-whisper smoke.")
    parser.add_argument("--skip-web", action="store_true", help="Skip real URL ingest smoke.")
    parser.add_argument("--keep-going", action="store_true", help="Run remaining smoke tests after a failure.")
    parser.add_argument("--web-url", default="https://example.com/", help="URL for the web smoke test.")
    parser.add_argument("--web-token", action="append", dest="web_tokens", help="Required token for web output.")
    parser.add_argument(
        "--web-target",
        action="append",
        dest="web_targets",
        choices=["router", "firecrawl", "crawl4ai", "playwright", "profile", "all"],
        help="Layered web smoke target to run. Can be repeated.",
    )
    parser.add_argument(
        "--web-require-firecrawl",
        action="store_true",
        help="Fail the web smoke when FIRECRAWL_API_KEY is missing.",
    )
    parser.add_argument("--web-profile-dir", type=Path, help="Profile directory for the web profile smoke.")
    parser.add_argument("--asr-audio", type=Path, help="Existing audio file for ASR smoke.")
    parser.add_argument("--asr-model", default="tiny", help="faster-whisper model for ASR smoke.")
    parser.add_argument("--asr-device", default="cpu", help="faster-whisper device for ASR smoke.")
    parser.add_argument("--asr-compute-type", default="int8", help="faster-whisper compute type for ASR smoke.")
    return parser.parse_args(argv)


def build_smoke_commands(args: argparse.Namespace, store: Path) -> list[SmokeCommand]:
    scripts = scripts_dir()
    commands: list[SmokeCommand] = []

    if not args.skip_service:
        commands.append(
            SmokeCommand(
                name="service",
                command=[sys.executable, str(scripts / "service_runtime_smoke.py"), "--store", str(store / "service")],
            )
        )

    if not args.skip_ocr:
        commands.append(
            SmokeCommand(
                name="ocr",
                command=[sys.executable, str(scripts / "ocr_runtime_smoke.py"), "--store", str(store / "ocr")],
            )
        )

    if not args.skip_asr:
        asr_command = [
            sys.executable,
            str(scripts / "asr_runtime_smoke.py"),
            "--store",
            str(store / "asr"),
            "--model",
            args.asr_model,
            "--device",
            args.asr_device,
            "--compute-type",
            args.asr_compute_type,
        ]
        if args.asr_audio:
            asr_command.extend(["--audio", str(args.asr_audio)])
        commands.append(SmokeCommand(name="asr", command=asr_command))

    if not args.skip_web:
        web_command = [
            sys.executable,
            str(scripts / "web_runtime_smoke.py"),
            "--store",
            str(store / "web"),
            "--url",
            args.web_url,
        ]
        for token in args.web_tokens or []:
            web_command.extend(["--token", token])
        for target in args.web_targets or []:
            web_command.extend(["--target", target])
        if args.web_require_firecrawl:
            web_command.append("--require-firecrawl")
        if args.web_profile_dir:
            web_command.extend(["--profile-dir", str(args.web_profile_dir)])
        commands.append(SmokeCommand(name="web", command=web_command))

    return commands


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def scripts_dir() -> Path:
    return repo_root() / "scripts"


if __name__ == "__main__":
    raise SystemExit(main())
