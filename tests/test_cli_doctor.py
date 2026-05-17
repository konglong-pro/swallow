from __future__ import annotations

import json

from typer.testing import CliRunner

from swallow.cli import main as cli_main
from swallow.cli.main import app
from swallow.core.doctor import DoctorCheck, DoctorReport


def test_cli_doctor_prints_human_report(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_main.doctor_module, "run_doctor", lambda config, *, deep=False: sample_report())

    result = CliRunner().invoke(app, ["--store", str(tmp_path), "doctor"])

    assert result.exit_code == 0, result.output
    assert "Python: 3.11.15" in result.output
    assert "[ok] python: ok" in result.output
    assert "[missing] crawl4ai: missing" in result.output
    assert "hint: uv sync --extra web" in result.output


def test_cli_doctor_prints_json(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_main.doctor_module, "run_doctor", lambda config, *, deep=False: sample_report())

    result = CliRunner().invoke(app, ["--store", str(tmp_path), "doctor", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["python_version"] == "3.11.15"
    assert payload["checks"][1]["status"] == "missing"


def test_cli_doctor_can_fail_on_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_main.doctor_module, "run_doctor", lambda config, *, deep=False: sample_report())

    result = CliRunner().invoke(app, ["--store", str(tmp_path), "doctor", "--fail-on-missing"])

    assert result.exit_code == 1
    assert "[missing] crawl4ai: missing" in result.output


def test_cli_doctor_passes_deep_flag(monkeypatch, tmp_path):
    seen: dict[str, bool] = {}

    def fake_run_doctor(config, *, deep=False):
        seen["deep"] = deep
        return DoctorReport(python_version="3.11.15", executable="python", platform="win", checks=[])

    monkeypatch.setattr(cli_main.doctor_module, "run_doctor", fake_run_doctor)

    result = CliRunner().invoke(app, ["--store", str(tmp_path), "doctor", "--deep"])

    assert result.exit_code == 0, result.output
    assert seen == {"deep": True}


def sample_report() -> DoctorReport:
    return DoctorReport(
        python_version="3.11.15",
        executable="python",
        platform="win",
        checks=[
            DoctorCheck(name="python", status="ok", detail="ok"),
            DoctorCheck(name="crawl4ai", status="missing", detail="missing", install_hint="uv sync --extra web"),
        ],
    )
