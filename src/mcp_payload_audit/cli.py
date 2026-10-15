"""Read-only CLI; no network, SDKs, subprocesses, or automatic fixes."""

from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from . import __version__
from .core import AuditError, analyze_result, loads_document

MAX_INPUT_BYTES = 16 * 1024 * 1024


def _nonnegative_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a nonnegative integer") from exc
    if number < 0:
        raise argparse.ArgumentTypeError("must be a nonnegative integer")
    return number


def _positive_int(value: str) -> int:
    number = _nonnegative_int(value)
    if number == 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def _top(value: str) -> int:
    number = _positive_int(value)
    if number > 100:
        raise argparse.ArgumentTypeError("must be between 1 and 100")
    return number


def _percent(value: str) -> Decimal:
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("must be a finite nonnegative percentage") from exc
    if not number.is_finite() or number < 0 or number > 1_000_000:
        raise argparse.ArgumentTypeError("must be a finite percentage between 0 and 1000000")
    return number


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcp-payload-audit",
        description=(
            "Inspect saved MCP tool results without executing tools or sending data anywhere."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    audit = commands.add_parser("audit", help="inspect one saved tool result")
    audit.add_argument("file", help="UTF-8 JSON fixture, or - for stdin")
    compare = commands.add_parser("compare", help="compare two saved tool results")
    compare.add_argument("before", help="baseline UTF-8 JSON fixture")
    compare.add_argument("after", help="candidate UTF-8 JSON fixture, or - for stdin")
    compare.add_argument(
        "--max-growth-percent",
        type=_percent,
        help="fail if compact result bytes grow by more than this percentage",
    )
    for command in (audit, compare):
        command.add_argument("--format", choices=("text", "json"), default="text")
        command.add_argument(
            "--max-bytes", type=_nonnegative_int, help="opt-in compact result byte ceiling"
        )
        command.add_argument("--top", type=_top, default=10, help="entries per list (1-100)")
        command.add_argument(
            "--min-duplicate-bytes",
            type=_positive_int,
            default=64,
            help="minimum compact size of repeated structured containers (default: 64)",
        )
    return parser


def _read(path: str, args: argparse.Namespace) -> dict[str, Any]:
    try:
        if path == "-":
            raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        else:
            with Path(path).open("rb") as handle:
                raw = handle.read(MAX_INPUT_BYTES + 1)
    except OSError as exc:
        raise AuditError("cannot read input file") from exc
    if len(raw) > MAX_INPUT_BYTES:
        raise AuditError("input exceeds the 16 MiB limit")
    try:
        document = loads_document(raw.decode("utf-8"))
    except UnicodeError as exc:
        raise AuditError("input must be valid UTF-8") from exc
    report = analyze_result(document, top=args.top, min_duplicate_bytes=args.min_duplicate_bytes)
    return {"source": path, "file_bytes": len(raw), **report}


def _budget(report: dict[str, Any], ceiling: int | None) -> list[dict[str, Any]]:
    if ceiling is not None and report["compact_result_bytes"] > ceiling:
        return [
            {
                "rule": "max-bytes",
                "actual": report["compact_result_bytes"],
                "limit": ceiling,
            }
        ]
    return []


def _label(value: str) -> str:
    # Escape all control characters and non-ASCII for safe terminal rendering.
    return json.dumps(value, ensure_ascii=True)


def _text_report(report: dict[str, Any]) -> list[str]:
    lines = [
        f"Source: {_label(report['source'])}",
        f"  Compact result: {report['compact_result_bytes']:,} bytes",
        f"  Input file:     {report['file_bytes']:,} bytes (includes formatting/envelope)",
        f"  Content JSON:   {report['content_json_bytes']:,} bytes",
        f"  Text UTF-8:     {report['text_utf8_bytes']:,} bytes",
    ]
    for key, label in (
        ("structured_json_bytes", "Structured JSON"),
        ("meta_json_bytes", "Meta JSON"),
    ):
        value = report[key]
        lines.append(f"  {label}: {value:,} bytes" if value is not None else f"  {label}: absent")
    lines.append(f"  Tool isError:   {str(report['is_error']).lower()}")
    lines.append(f"\nText/structured mirrors: {report['mirror_count']} (informational)")
    for mirror in report["mirrors"]:
        lines.append(
            f"  {_label(mirror['text_path'])} matches "
            f"{_label(mirror['structured_path'])}: {mirror['value_bytes']:,} value bytes"
        )
    lines.append(f"\nRepeated structured subtrees: {report['duplicate_group_count']}")
    for group in report["duplicate_subtrees"]:
        paths = ", ".join(_label(path) for path in group["paths"])
        if group["paths_truncated"]:
            paths += f" (+{group['paths_truncated']} more paths)"
        lines.append(
            f"  {group['occurrences']} occurrences x {group['value_bytes']:,} value bytes: {paths}"
        )
    lines.append("\nLargest encoded leaf values:")
    for field in report["largest_fields"]:
        lines.append(f"  {field['bytes']:>10,} bytes  {_label(field['path'])}")
    return lines


def _render(output: dict[str, Any], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(output, indent=2, ensure_ascii=True, allow_nan=False))
        return
    if output["command"] == "audit":
        lines = _text_report(output["report"])
    else:
        lines = ["BASELINE", *_text_report(output["before"]), "\nCANDIDATE"]
        lines.extend(_text_report(output["after"]))
        delta = output["delta"]
        lines.append(f"\nCompact size change: {delta['bytes']:+,} bytes ({delta['percent']:+.2f}%)")
    if output["violations"]:
        lines.append("\nBudget: FAIL")
        for violation in output["violations"]:
            lines.append(
                f"  {violation['rule']}: actual={violation['actual']} limit={violation['limit']}"
            )
    else:
        lines.append("\nBudget: PASS" if output["budgets_enabled"] else "\nBudget: not configured")
    lines.append(
        "\nBytes are not tokens or billing. Size categories and duplicate groups may overlap.\n"
        "Mirroring may be required for client compatibility; no data was changed."
    )
    print("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        output: dict[str, Any] = {"schema_version": 1, "command": args.command}
        if args.command == "audit":
            report = _read(args.file, args)
            output.update(
                report=report,
                violations=_budget(report, args.max_bytes),
                budgets_enabled=args.max_bytes is not None,
            )
        else:
            if args.before == "-":
                raise AuditError("baseline must be a file; only the candidate may read stdin")
            before = _read(args.before, args)
            after = _read(args.after, args)
            difference = after["compact_result_bytes"] - before["compact_result_bytes"]
            # A valid CallToolResult always has a positive compact size.
            growth = Decimal(difference) * 100 / before["compact_result_bytes"]
            violations = _budget(after, args.max_bytes)
            if args.max_growth_percent is not None and growth > args.max_growth_percent:
                violations.append(
                    {
                        "rule": "max-growth-percent",
                        "actual": float(growth),
                        "limit": float(args.max_growth_percent),
                    }
                )
            output.update(
                before=before,
                after=after,
                delta={"bytes": difference, "percent": float(growth)},
                violations=violations,
                budgets_enabled=args.max_bytes is not None or args.max_growth_percent is not None,
            )
        _render(output, args.format)
        sys.stdout.flush()
        return 1 if output["violations"] else 0
    except (AuditError, RecursionError) as exc:
        message = str(exc) if isinstance(exc, AuditError) else "input nesting exceeds safe limits"
        if args.format == "json":
            print(json.dumps({"schema_version": 1, "error": message}), file=sys.stderr)
        else:
            print(f"mcp-payload-audit: {message}", file=sys.stderr)
        return 2
    except BrokenPipeError:
        # Prevent a second buffered-write error during interpreter shutdown.
        with open(os.devnull, "w") as sink:
            os.dup2(sink.fileno(), sys.stdout.fileno())
        return 2
