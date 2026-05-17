from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
from pathlib import Path

from swallow.core.config import IngestConfig
from swallow.core.runner import IngestRunner

DEFAULT_TEXT = "swallow audio smoke test one two three"
DEFAULT_TOKENS = ("swallow",)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a real faster-whisper ASR runtime smoke test.")
    parser.add_argument("--audio", type=Path, help="Existing audio file to transcribe. If omitted, Windows SAPI is used.")
    parser.add_argument("--store", type=Path, help="Store root. Defaults to a temporary directory.")
    parser.add_argument("--model", default="tiny", help="faster-whisper model name.")
    parser.add_argument("--device", default="cpu", help="faster-whisper device.")
    parser.add_argument("--compute-type", default="int8", help="faster-whisper compute type.")
    parser.add_argument("--text", default=DEFAULT_TEXT, help="Text to synthesize on Windows when --audio is omitted.")
    args = parser.parse_args()

    store = args.store or Path(tempfile.mkdtemp(prefix="swallow-asr-smoke-"))
    store.mkdir(parents=True, exist_ok=True)

    audio_path = args.audio or store / "asr-smoke.wav"
    if args.audio is None:
        synthesize_speech(audio_path, args.text)

    config = IngestConfig.from_mapping(
        {
            "workers": {
                "faster_whisper": {
                    "model": args.model,
                    "device": args.device,
                    "compute_type": args.compute_type,
                }
            }
        }
    )
    result = IngestRunner(store_root=store, config=config).ingest_file(audio_path)
    assert_asr_result(result.document.content.markdown, DEFAULT_TOKENS)

    print(f"Store: {store}")
    print(f"Audio: {audio_path}")
    print(f"Job: {result.job.id}")
    print(f"Document: {store / result.document_path}")
    print("ASR runtime smoke: success")
    return 0


def synthesize_speech(path: Path, text: str) -> None:
    if os.name != "nt":
        raise RuntimeError("No --audio was provided and built-in synthesis is only implemented for Windows")
    escaped_path = str(path).replace("'", "''")
    escaped_text = text.replace("'", "''")
    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        (
            "$voice = New-Object -ComObject SAPI.SpVoice; "
            "$stream = New-Object -ComObject SAPI.SpFileStream; "
            f"$stream.Open('{escaped_path}', 3); "
            "$voice.AudioOutputStream = $stream; "
            f"$voice.Speak('{escaped_text}') | Out-Null; "
            "$stream.Close()"
        ),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=60)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "speech synthesis failed")
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError("speech synthesis did not create audio")


def assert_asr_result(markdown: str, required_tokens: tuple[str, ...] = DEFAULT_TOKENS) -> None:
    lowered = markdown.lower()
    missing = [token for token in required_tokens if token.lower() not in lowered]
    if missing:
        raise AssertionError(f"ASR output missing tokens {missing}. Markdown was:\n{markdown}")


if __name__ == "__main__":
    raise SystemExit(main())
