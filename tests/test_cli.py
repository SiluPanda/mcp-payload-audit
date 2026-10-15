from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mcp_payload_audit import analyze_result

ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    return subprocess.run(
        [sys.executable, "-m", "mcp_payload_audit", *args],
        input=input_text,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


@pytest.fixture
def fixture(tmp_path: Path) -> Path:
    path = tmp_path / "fixture.json"
    path.write_text('{"content":[{"type":"text","text":"secret-value"}]}', encoding="utf-8")
    return path


def test_version():
    result = run_cli("--version")
    assert result.returncode == 0
    assert result.stdout.strip() == "mcp-payload-audit 0.1.0"


def test_json_report_and_privacy(fixture):
    result = run_cli("audit", str(fixture), "--format", "json")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == 1
    assert report["report"]["file_bytes"] == fixture.stat().st_size
    assert not report["budgets_enabled"]
    assert "secret-value" not in result.stdout
    assert result.stderr == ""


def test_text_report(fixture):
    result = run_cli("audit", str(fixture))
    assert result.returncode == 0
    assert "Bytes are not tokens" in result.stdout
    assert "Budget: not configured" in result.stdout
    assert "secret-value" not in result.stdout


def test_absolute_budget_boundary(fixture):
    size = analyze_result(json.loads(fixture.read_text()))["compact_result_bytes"]
    assert run_cli("audit", str(fixture), "--max-bytes", str(size)).returncode == 0
    result = run_cli("audit", str(fixture), "--max-bytes", str(size - 1), "--format", "json")
    assert result.returncode == 1
    assert json.loads(result.stdout)["violations"] == [
        {"rule": "max-bytes", "actual": size, "limit": size - 1}
    ]


def test_formatted_file_does_not_change_budget(fixture, tmp_path):
    document = json.loads(fixture.read_text())
    size = analyze_result(document)["compact_result_bytes"]
    pretty = tmp_path / "pretty.json"
    pretty.write_text(json.dumps(document, indent=8) + "\n")
    result = run_cli("audit", str(pretty), "--max-bytes", str(size), "--format", "json")
    assert result.returncode == 0
    assert json.loads(result.stdout)["report"]["file_bytes"] > size


def test_compare_growth_and_absolute_budget(tmp_path):
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    before.write_text('{"content":[]}')
    after.write_text('{"content":[],"isError":false}')
    result = run_cli(
        "compare", str(before), str(after), "--max-growth-percent", "99.99", "--format", "json"
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["delta"] == {"bytes": 16, "percent": 114.28571428571429}
    result = run_cli("compare", str(before), str(after), "--max-growth-percent", "115")
    assert result.returncode == 0
    assert "Compact size change" in result.stdout
    result = run_cli("compare", str(after), str(before), "--max-growth-percent", "0")
    assert result.returncode == 0
    assert run_cli("compare", str(before), str(after), "--max-bytes", "1").returncode == 1


def test_compare_same_at_zero(fixture):
    assert (
        run_cli("compare", str(fixture), str(fixture), "--max-growth-percent", "0").returncode == 0
    )


def test_compare_no_budget(fixture):
    result = run_cli("compare", str(fixture), str(fixture), "--format", "json")
    assert result.returncode == 0
    assert not json.loads(result.stdout)["budgets_enabled"]


def test_stdin_and_compare_stdin(fixture):
    content = fixture.read_text()
    assert run_cli("audit", "-", input_text=content).returncode == 0
    assert run_cli("compare", str(fixture), "-", input_text=content).returncode == 0
    assert run_cli("compare", "-", str(fixture), input_text=content).returncode == 2


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "{",
        "[]",
        '{"content":{},"private":"DO_NOT_ECHO"}',
        '{"content":[],"private":NaN}',
        '{"content":[],"content":[]}',
    ],
)
def test_bad_inputs_fail_closed(payload):
    result = run_cli("audit", "-", "--format", "json", input_text=payload)
    assert result.returncode == 2
    assert result.stdout == ""
    assert "DO_NOT_ECHO" not in result.stderr
    assert "error" in json.loads(result.stderr)
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    "args",
    [
        ["--max-bytes", "-1"],
        ["--max-bytes", "1.5"],
        ["--top", "0"],
        ["--top", "101"],
        ["--min-duplicate-bytes", "0"],
    ],
)
def test_bad_audit_options(fixture, args):
    assert run_cli("audit", str(fixture), *args).returncode == 2


@pytest.mark.parametrize("percent", ["NaN", "Infinity", "-1", "oops", "1000001"])
def test_bad_growth_options(fixture, percent):
    assert (
        run_cli("compare", str(fixture), str(fixture), "--max-growth-percent", percent).returncode
        == 2
    )


def test_missing_file_and_directory(tmp_path):
    assert run_cli("audit", str(tmp_path / "missing.json")).returncode == 2
    assert run_cli("audit", str(tmp_path)).returncode == 2


def test_bad_utf8(tmp_path):
    path = tmp_path / "bad.json"
    path.write_bytes(b"\xff")
    result = run_cli("audit", str(path))
    assert result.returncode == 2
    assert "UTF-8" in result.stderr


def test_size_limit(tmp_path):
    path = tmp_path / "large.json"
    with path.open("wb") as handle:
        handle.truncate(16 * 1024 * 1024 + 1)
    result = run_cli("audit", str(path))
    assert result.returncode == 2
    assert "16 MiB" in result.stderr


def test_terminal_escaping(tmp_path):
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps({"content": [], "\x1b[31m\nsecret-key": "secret-value"}))
    result = run_cli("audit", str(path))
    assert result.returncode == 0
    assert "\x1b" not in result.stdout
    assert "secret-value" not in result.stdout


def test_synthetic_examples_and_truncation():
    result = run_cli(
        "audit", str(ROOT / "examples/mirrored.json"), "--top", "1", "--format", "json"
    )
    assert result.returncode == 0
    report = json.loads(result.stdout)["report"]
    assert report["mirror_count"] == 1
    assert report["duplicate_group_count"] >= 1
    assert len(report["largest_fields"]) == 1
    assert len(report["duplicate_subtrees"]) == 1
    result = run_cli(
        "compare",
        str(ROOT / "examples/lean.json"),
        str(ROOT / "examples/mirrored.json"),
        "--max-growth-percent",
        "10",
    )
    assert result.returncode == 1


def test_tool_error_is_not_size_error():
    result = run_cli("audit", "-", input_text='{"content":[],"isError":true}')
    assert result.returncode == 0
    assert "Tool isError:   true" in result.stdout
