import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mcp_payload_audit import AuditError, analyze_result, loads_document

ROOT = Path(__file__).resolve().parents[1]


def test_raw_extension_fields_are_not_rpc_envelopes():
    document = {"content": [], "error": {"detail": "domain diagnostic"}, "method": "search"}
    report = analyze_result(document)
    assert report["compact_result_bytes"] == len(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    )
    envelope = {"jsonrpc": "2.0", "id": 1, "result": document}
    assert analyze_result(envelope) == report
    envelope["error"] = {"code": -32600, "message": "failed"}
    with pytest.raises(AuditError):
        analyze_result(envelope)


def test_long_paths_cannot_multiply_into_unbounded_memory():
    # Only ~11 KB of JSON, but fully expanded per-leaf pointers exceed 8 Mi chars.
    document = {"content": [], "structuredContent": {"K" * 8192: [0] * 1100}}
    assert len(json.dumps(document)) < 12_000
    with pytest.raises(AuditError, match="work limit"):
        analyze_result(document, top=1)


def test_nonmatching_mirror_does_not_materialize_unused_paths():
    # Parsed text has no report fields, so it should not build per-leaf pointers.
    text = json.dumps({"K" * 8192: [0] * 4000})
    report = analyze_result(
        {"content": [{"type": "text", "text": text}], "structuredContent": None}
    )
    assert report["mirror_count"] == 0


def test_closed_pipe_has_controlled_exit():
    process = subprocess.Popen(
        [sys.executable, "-m", "mcp_payload_audit", "audit", str(ROOT / "examples/lean.json")],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    process.stdout.close()
    stderr = process.stderr.read()
    assert process.wait(timeout=30) == 2
    assert stderr == b""


def test_output_oserror_has_controlled_exit():
    # Exercise the EINVAL path even on POSIX; Windows closed pipes use this form.
    script = (
        "import mcp_payload_audit.cli as cli\n"
        "def fail(*args):\n"
        "    raise OSError(22, 'output unavailable')\n"
        "cli._render = fail\n"
        "raise SystemExit(cli.main(['audit', '-']))\n"
    )
    process = subprocess.run(
        [sys.executable, "-c", script],
        input='{"content":[]}',
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        timeout=30,
    )
    assert process.returncode == 2
    assert process.stderr == ""


def test_numeric_matching_uses_documented_parsed_float_semantics():
    result = loads_document('{"content":[{"type":"text","text":"1e-999"}],"structuredContent":0.0}')
    assert analyze_result(result)["mirror_count"] == 1


def test_text_report_marks_omitted_paths():
    row = {"value": "x" * 70}
    raw = json.dumps({"content": [], "structuredContent": [row] * 25})
    result = subprocess.run(
        [sys.executable, "-m", "mcp_payload_audit", "audit", "-"],
        input=raw,
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        timeout=30,
    )
    assert result.returncode == 0
    assert "+5 more paths" in result.stdout
