"""Focused, dependency-free tests for the saved MCP result analyzer."""

import copy
import json
import math
import unittest

from mcp_payload_audit.core import AuditError, analyze_result, loads_document


def compact_bytes(value):
    return len(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    )


class LoadingTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(loads_document('{"content":[]}'), {"content": []})

    def test_duplicate_keys_rejected_in_nested_object(self):
        for source in ['{"secret":"redacted","secret":0}', '{"a":{"x":1,"x":2}}']:
            with self.subTest(source=source), self.assertRaises(AuditError) as caught:
                loads_document(source)
            self.assertNotIn("secret", str(caught.exception))
            self.assertNotIn("redacted", str(caught.exception))

    def test_nonfinite_numbers(self):
        for number in ["NaN", "Infinity", "-Infinity", "1e999"]:
            with self.subTest(number=number), self.assertRaises(AuditError):
                loads_document(number)

    def test_errors_do_not_echo_source(self):
        with self.assertRaises(AuditError) as caught:
            loads_document('{"sensitive-token":NOPE}')
        self.assertNotIn("sensitive-token", str(caught.exception))
        self.assertNotIn("NOPE", str(caught.exception))

    def test_unicode(self):
        self.assertEqual(loads_document('"\\ud83d\\ude00"'), "😀")
        with self.assertRaises(AuditError):
            loads_document('"\\ud800"')


class ShapeTests(unittest.TestCase):
    def test_raw_and_wrapped_have_same_report(self):
        raw = {"content": [{"type": "text", "text": "hello"}]}
        wrapper = {"jsonrpc": "2.0", "id": 1, "result": raw}
        self.assertEqual(analyze_result(raw), analyze_result(wrapper))

    def test_reject_invalid_shapes(self):
        values = [
            None,
            [],
            {},
            {"content": {}},
            {"content": ["x"]},
            {"content": [{"type": 3}]},
            {"content": [{"type": "text"}]},
            {"content": [], "isError": 1},
            {"content": [], "resultType": "task"},
            {"jsonrpc": "2.0", "id": 1, "error": {}},
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call"},
            {"jsonrpc": "2.0", "result": {"content": []}},
            {"jsonrpc": "1.0", "id": 1, "result": {"content": []}},
        ]
        for value in values:
            with self.subTest(value=value), self.assertRaises(AuditError):
                analyze_result(value)

    def test_nontext_blocks_need_only_string_type(self):
        result = {"content": [{"type": "image", "data": "abcd"}], "resultType": "complete"}
        self.assertEqual(analyze_result(result)["text_utf8_bytes"], 0)

    def test_non_json_python_values(self):
        for value in [(1, 2), {1: "x"}, {"x": math.inf}, {"x": object()}, {"x": "\ud800"}]:
            with self.subTest(kind=type(value)), self.assertRaises(AuditError):
                analyze_result({"content": [], "structuredContent": value})

    def test_cycles_rejected_but_acyclic_aliases_allowed(self):
        cycle = []
        cycle.append(cycle)
        with self.assertRaises(AuditError):
            analyze_result({"content": [], "structuredContent": cycle})
        repeated = {"message": "hello"}
        report = analyze_result(
            {"content": [], "structuredContent": [repeated, repeated]}, min_duplicate_bytes=0
        )
        self.assertEqual(report["duplicate_group_count"], 1)


class MeasurementTests(unittest.TestCase):
    def test_sizes_match_reference_serialization(self):
        result = {
            "content": [{"type": "text", "text": '雪😀\n"x"'}],
            "structuredContent": {"b": [True, 1, 1.0, None], "a": "é"},
            "_meta": {"secret": "stays-local"},
            "isError": True,
        }
        original = copy.deepcopy(result)
        report = analyze_result(result)
        self.assertEqual(report["compact_result_bytes"], compact_bytes(result))
        self.assertEqual(report["content_json_bytes"], compact_bytes(result["content"]))
        self.assertEqual(
            report["structured_json_bytes"], compact_bytes(result["structuredContent"])
        )
        self.assertEqual(report["meta_json_bytes"], compact_bytes(result["_meta"]))
        self.assertEqual(report["text_utf8_bytes"], len(result["content"][0]["text"].encode()))
        self.assertTrue(report["is_error"])
        self.assertEqual(result, original)
        self.assertNotIn("stays-local", json.dumps(report))

    def test_absent_and_null_are_distinct(self):
        absent = analyze_result({"content": []})
        present = analyze_result({"content": [], "structuredContent": None, "_meta": None})
        self.assertIsNone(absent["structured_json_bytes"])
        self.assertIsNone(absent["meta_json_bytes"])
        self.assertEqual(present["structured_json_bytes"], 4)
        self.assertEqual(present["meta_json_bytes"], 4)

    def test_pointer_escape_and_leaf_only(self):
        result = {"content": [], "structuredContent": {"a/b~c": "large" * 50, "empty": {}}}
        report = analyze_result(result)
        self.assertEqual(
            report["largest_fields"][0],
            {"path": "/structuredContent/a~1b~0c", "bytes": compact_bytes("large" * 50)},
        )
        self.assertNotIn(
            "/structuredContent", [field["path"] for field in report["largest_fields"]]
        )
        self.assertIn({"path": "/structuredContent/empty", "bytes": 2}, report["largest_fields"])

    def test_mirrors_ignore_object_order_and_whitespace(self):
        value = {"a": [1, 2], "b": "snow 雪"}
        result = {
            "content": [
                {"type": "text", "text": json.dumps({"b": "snow 雪", "a": [1, 2]}, indent=2)}
            ],
            "structuredContent": value,
        }
        report = analyze_result(result)
        self.assertEqual(
            report["mirrors"],
            [
                {
                    "text_path": "/content/0/text",
                    "structured_path": "/structuredContent",
                    "value_bytes": compact_bytes(value),
                }
            ],
        )

    def test_mirrors_accept_all_json_types(self):
        for value in [None, True, False, 1, 1.0, "x", [], {}, [1, "x"]]:
            result = {
                "content": [{"type": "text", "text": json.dumps(value)}],
                "structuredContent": value,
            }
            with self.subTest(value=value):
                self.assertEqual(analyze_result(result)["mirror_count"], 1)

    def test_mirrors_require_exact_typed_json(self):
        pairs = [
            (True, "1"),
            (1, "1.0"),
            (-0.0, "0.0"),
            ([1, 2], "[2,1]"),
            ({"x": 2}, '{"x":1,"x":2}'),
            ({"x": 2}, 'prefix {"x":2}'),
            ({"x": 2}, '{"x":2,"extra":0}'),
        ]
        for value, text in pairs:
            with self.subTest(value=value, text=text):
                self.assertEqual(
                    analyze_result(
                        {"content": [{"type": "text", "text": text}], "structuredContent": value}
                    )["mirror_count"],
                    0,
                )

    def test_repeated_containers_only_and_no_cross_channel_groups(self):
        row = {"long": "x" * 70}
        result = {
            "content": [{"type": "text", "text": json.dumps(row)}],
            "structuredContent": {
                "a": row,
                "b": dict(row),
                "empty": [{}, {}],
                "strings": ["repeat", "repeat"],
            },
            "_meta": {"copy": row},
        }
        report = analyze_result(result)
        self.assertEqual(
            report["duplicate_subtrees"],
            [
                {
                    "paths": ["/structuredContent/a", "/structuredContent/b"],
                    "value_bytes": compact_bytes(row),
                    "occurrences": 2,
                    "paths_truncated": 0,
                }
            ],
        )

    def test_duplicate_paths_are_bounded(self):
        row = {"x": "y"}
        result = {"content": [], "structuredContent": [row] * 100}
        group = analyze_result(result, min_duplicate_bytes=0)["duplicate_subtrees"][0]
        self.assertEqual(group["occurrences"], 100)
        self.assertEqual(group["paths_truncated"], 80)
        self.assertEqual(len(group["paths"]), 20)
        self.assertEqual(group["paths"], sorted(f"/structuredContent/{i}" for i in range(100))[:20])

    def test_overlapping_groups_are_not_summed(self):
        row = {"body": "x" * 100}
        value = {"one": [row, row], "two": [row, row]}
        report = analyze_result({"content": [], "structuredContent": value})
        self.assertEqual(report["duplicate_group_count"], 2)
        self.assertEqual([group["occurrences"] for group in report["duplicate_subtrees"]], [2, 4])
        self.assertNotIn("savings", report)

    def test_threshold_inclusive_and_top_counts_before_truncation(self):
        row = {"x": "y"}
        result = {"content": [{"type": "text", "text": "null"}] * 3, "structuredContent": None}
        report = analyze_result(result, top=1)
        self.assertEqual(report["mirror_count"], 3)
        self.assertEqual(len(report["mirrors"]), 1)
        value = {"content": [], "structuredContent": [row, row]}
        self.assertEqual(
            analyze_result(value, min_duplicate_bytes=compact_bytes(row))["duplicate_group_count"],
            1,
        )
        self.assertEqual(
            analyze_result(value, min_duplicate_bytes=compact_bytes(row) + 1)[
                "duplicate_group_count"
            ],
            0,
        )
        summary = analyze_result(value, top=0, min_duplicate_bytes=0)
        self.assertEqual(summary["duplicate_group_count"], 1)
        self.assertEqual(summary["duplicate_subtrees"], [])
        self.assertEqual(summary["largest_fields"], [])

    def test_option_validation(self):
        for kwargs in [
            {"top": -1},
            {"top": True},
            {"min_duplicate_bytes": -1},
            {"min_duplicate_bytes": 1.5},
        ]:
            with self.subTest(kwargs=kwargs), self.assertRaises(AuditError):
                analyze_result({"content": []}, **kwargs)


if __name__ == "__main__":
    unittest.main()
