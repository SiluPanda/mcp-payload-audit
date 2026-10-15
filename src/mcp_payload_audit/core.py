"""Offline, read-only measurements for saved MCP tool results.

All JSON byte measurements use compact UTF-8 JSON with sorted object keys,
``ensure_ascii=False`` and no insignificant whitespace. They are not original
file sizes, token counts, model-context sizes, or promised savings. Leaf sizes
include JSON string quotes/escapes; ``text_utf8_bytes`` counts decoded text only.
Repeated subtrees can overlap. Their sizes must never be summed as savings.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

MAX_DEPTH = 64
MAX_NODES = 200_000
MAX_PATH_CHARACTERS = 8 * 1024 * 1024


class AuditError(ValueError):
    """An invalid or unsupported input, without echoing its values."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AuditError("Duplicate object keys are not supported.")
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise AuditError("Non-finite JSON numbers are not supported.")


def _utf8(value: str) -> bytes:
    try:
        return value.encode("utf-8")
    except UnicodeEncodeError:
        raise AuditError("Unpaired Unicode surrogates are not supported.") from None


def _validate_json(value: object) -> None:
    """Validate JSON values iteratively; root depth is zero, keys are not nodes.

    Object keys must be strings. Only the built-in JSON-compatible types are
    accepted, not subclasses, tuples, non-finite numbers, or cyclic graphs.
    Shared but acyclic Python containers are counted once per occurrence.
    """
    import math

    scheduled = 1
    ancestors: set[int] = set()
    stack: list[tuple[object, int, bool]] = [(value, 0, False)]
    while stack:
        current, depth, leaving = stack.pop()
        if leaving:
            ancestors.remove(id(current))
            continue
        if depth > MAX_DEPTH:
            raise AuditError("JSON nesting exceeds the maximum depth of 64.")
        kind = type(current)
        if kind is str:
            _utf8(current)
        elif kind is float:
            if not math.isfinite(current):
                raise AuditError("Non-finite JSON numbers are not supported.")
        elif current is None or kind in (bool, int):
            pass
        elif kind in (dict, list):
            identity = id(current)
            if identity in ancestors:
                raise AuditError("Cyclic Python containers are not JSON values.")
            scheduled += len(current)
            if scheduled > MAX_NODES:
                raise AuditError("JSON exceeds the maximum node count of 200000.")
            ancestors.add(identity)
            stack.append((current, depth, True))
            if kind is dict:
                for key, child in current.items():
                    if type(key) is not str:
                        raise AuditError("JSON object keys must be strings.")
                    _utf8(key)
                    stack.append((child, depth + 1, False))
            else:
                for child in current:
                    stack.append((child, depth + 1, False))
        else:
            raise AuditError("Input contains a non-JSON Python value.")


def loads_document(text: str) -> Any:
    """Parse strict JSON without including source values in error messages.

    Duplicate keys, NaN/Infinity, overflowing floats, unpaired surrogates,
    depth over 64 and more than 200,000 value nodes are rejected. Shape checking
    for MCP results is performed separately by :func:`analyze_result`.
    """
    if type(text) is not str:
        raise AuditError("JSON input must be text.")
    try:
        document = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except AuditError:
        raise
    except (ValueError, RecursionError, OverflowError):
        raise AuditError("Invalid JSON document or unsupported numeric value.") from None
    _validate_json(document)
    return document


def _json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (ValueError, UnicodeError, OverflowError):
        raise AuditError("Input cannot be represented as compact UTF-8 JSON.") from None


def _pointer_part(key: str) -> str:
    return key.replace("~", "~0").replace("/", "~1")


@dataclass(frozen=True)
class _Metric:
    size: int
    signature: int


class _Analyzer:
    """Measure each node once and intern exact, type-aware structural keys.

    Container keys refer to child signature IDs, not serialized subtrees. Thus
    a huge leaf under many ancestors is never serialized again per ancestor.
    Dictionary lookup resolves hash collisions with exact key equality.
    """

    def __init__(self, minimum: int):
        self.minimum = minimum
        self.interned: dict[tuple[Any, ...], int] = {}
        self.fields: list[dict[str, Any]] = []
        self.groups: dict[int, dict[str, Any]] = {}
        self.top_level: dict[str, _Metric] = {}
        self.path_characters = 0

    def measure(
        self,
        value: Any,
        path: str = "",
        *,
        collect: bool = False,
        structured: bool = False,
    ) -> _Metric:
        if collect:
            self.path_characters += len(path)
            if self.path_characters > MAX_PATH_CHARACTERS:
                raise AuditError("Derived JSON pointers exceed the 8 Mi character work limit.")
        kind = type(value)
        if kind is dict:
            entries = []
            size = 2 + max(0, len(value) - 1)
            for key in sorted(value):
                child = self.measure(
                    value[key],
                    path + "/" + _pointer_part(key) if collect else "",
                    collect=collect,
                    structured=structured
                    or (collect and path == "" and key == "structuredContent"),
                )
                size += len(_json_bytes(key)) + 1 + child.size
                entries.append((key, child.signature))
                if collect and path == "":
                    self.top_level[key] = child
            signature_key = (dict, tuple(entries))
        elif kind is list:
            children = [
                self.measure(
                    child,
                    path + "/" + str(index) if collect else "",
                    collect=collect,
                    structured=structured,
                )
                for index, child in enumerate(value)
            ]
            size = 2 + max(0, len(value) - 1) + sum(child.size for child in children)
            signature_key = (list, tuple(child.signature for child in children))
        else:
            encoded = _json_bytes(value)
            size = len(encoded)
            # Float bytes distinguish -0.0 from 0.0; the type tag distinguishes
            # true, 1 and 1.0. Strings are compared without Unicode normalization.
            signature_key = (kind, encoded if kind is float else value)
        signature = self.interned.get(signature_key)
        if signature is None:
            signature = len(self.interned) + 1
            self.interned[signature_key] = signature
        metric = _Metric(size, signature)
        if collect:
            if kind not in (dict, list) or not value:
                self.fields.append({"path": path, "bytes": size})
            if structured and kind in (dict, list) and value and size >= self.minimum:
                group = self.groups.setdefault(signature, {"paths": [], "value_bytes": size})
                group["paths"].append(path)
        return metric


def _extract_result(document: object) -> dict[str, Any]:
    if type(document) is not dict:
        raise AuditError("Expected one MCP result object, not a batch or scalar.")
    if "jsonrpc" in document:
        if "error" in document or "method" in document:
            raise AuditError("JSON-RPC errors and requests are not tool results.")
        if document["jsonrpc"] != "2.0" or "id" not in document or "result" not in document:
            raise AuditError("Expected a successful JSON-RPC 2.0 response with an id.")
        if type(document["id"]) not in (str, int, float, type(None)):
            raise AuditError("JSON-RPC response id must be a string, number, or null.")
        result = document["result"]
    else:
        result = document
    if type(result) is not dict or type(result.get("content")) is not list:
        raise AuditError("MCP result must contain a content list.")
    if "resultType" in result and result["resultType"] != "complete":
        raise AuditError("Only complete MCP results are supported.")
    if "isError" in result and type(result["isError"]) is not bool:
        raise AuditError("MCP isError must be a boolean.")
    for block in result["content"]:
        if type(block) is not dict or type(block.get("type")) is not str:
            raise AuditError("Each content block must be an object with a string type.")
        if block["type"] == "text" and type(block.get("text")) is not str:
            raise AuditError("Text content blocks must contain string text.")
    return result


def analyze_result(
    document: object,
    *,
    top: int = 10,
    min_duplicate_bytes: int = 64,
) -> dict[str, Any]:
    """Audit one raw CallToolResult or successful JSON-RPC response, offline.

    The response envelope is excluded from byte metrics and paths. All metrics
    use compact sorted-key UTF-8 JSON, except decoded ``text_utf8_bytes``.
    ``largest_fields`` contains primitives and empty containers, not aggregate
    subtree sizes. Pointers are RFC 6901 JSON pointers relative to the result.

    Mirrors compare a whole text block parsed as strict JSON with the complete
    ``structuredContent`` value. They ignore object ordering and whitespace,
    but preserve array ordering and primitive types (including 1 versus 1.0).
    Invalid/non-JSON text is simply not a mirror. Mirror size thresholds are
    not applied; ``min_duplicate_bytes`` only filters repeated nonempty
    container subtrees within ``structuredContent``.

    The three report lists are capped by ``top`` (zero gives counts only).
    Each duplicate group's first 20 sorted paths are retained, with omitted
    paths counted in ``paths_truncated`` and all occurrences in ``occurrences``.
    Groups can overlap. Counts precede truncation. Equal sizes sort by
    pointer(s), making output stable.
    No fields are rewritten and no remote systems or tools are contacted.
    """
    if type(top) is not int or top < 0:
        raise AuditError("top must be a nonnegative integer.")
    if type(min_duplicate_bytes) is not int or min_duplicate_bytes < 0:
        raise AuditError("min_duplicate_bytes must be a nonnegative integer.")
    _validate_json(document)
    result = _extract_result(document)
    analyzer = _Analyzer(min_duplicate_bytes)
    metric = analyzer.measure(result, collect=True)
    mirrors = []
    text_bytes = 0
    for index, block in enumerate(result["content"]):
        if block["type"] != "text":
            continue
        text = block["text"]
        text_bytes += len(_utf8(text))
        if "structuredContent" not in result:
            continue
        try:
            decoded = loads_document(text)
        except AuditError:
            continue
        candidate = analyzer.measure(decoded)
        structured_metric = analyzer.top_level["structuredContent"]
        if candidate.signature == structured_metric.signature:
            mirrors.append(
                {
                    "text_path": f"/content/{index}/text",
                    "structured_path": "/structuredContent",
                    "value_bytes": candidate.size,
                }
            )
    mirrors.sort(key=lambda item: (-item["value_bytes"], item["text_path"]))
    groups = []
    for group in analyzer.groups.values():
        if len(group["paths"]) > 1:
            paths = sorted(group["paths"])
            groups.append(
                {
                    "paths": paths[:20],
                    "value_bytes": group["value_bytes"],
                    "occurrences": len(paths),
                    "paths_truncated": max(0, len(paths) - 20),
                }
            )
    groups.sort(key=lambda item: (-item["value_bytes"], item["paths"]))
    fields = sorted(analyzer.fields, key=lambda item: (-item["bytes"], item["path"]))
    return {
        "schema_version": 1,
        "compact_result_bytes": metric.size,
        "content_json_bytes": analyzer.top_level["content"].size,
        "text_utf8_bytes": text_bytes,
        "structured_json_bytes": (
            analyzer.top_level["structuredContent"].size if "structuredContent" in result else None
        ),
        "meta_json_bytes": analyzer.top_level["_meta"].size if "_meta" in result else None,
        "is_error": result.get("isError", False),
        "largest_fields": fields[:top],
        "mirrors": mirrors[:top],
        "duplicate_subtrees": groups[:top],
        "mirror_count": len(mirrors),
        "duplicate_group_count": len(groups),
    }
