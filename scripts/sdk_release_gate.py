from __future__ import annotations

import os
import subprocess
import sys


GATES = [
    ("contract", ["-m", "contract", "-q"]),
    ("transport", ["-m", "transport", "-q"]),
    ("security", ["-m", "security", "-q"]),
    ("adapter", ["-m", "adapter", "-q"]),
    ("concurrency", ["-m", "concurrency", "-q"]),
    ("full", ["-q"]),
]

NETWORK_GATE = (
    "mcp-network",
    ["tests/test_mcp_server.py::test_mcp_url_ingest_real_network_example_dot_com", "-q"],
)


def main() -> int:
    gates = list(GATES)
    if os.getenv("RUN_SWALLOW_NETWORK_TESTS") == "1":
        gates.append(NETWORK_GATE)

    for name, args in gates:
        print(f"==> SDK release gate: {name}", flush=True)
        result = subprocess.run([sys.executable, "-m", "pytest", *args])
        if result.returncode != 0:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
