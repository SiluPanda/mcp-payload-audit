"""Independent adversarial checks for the public, offline analysis API.

The fixtures are synthetic. Nothing here contacts an MCP server or an LLM.
"""

import copy
import json
import math
import random
import time
import unittest

from mcp_payload_audit.core import AuditError, analyze_result, loads_document


def compact_bytes(value):
    return len(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    )


def result(structured, *, texts=()):
    return {
        "content": [{"type": "text", "text": text} for text in texts],
        "structuredContent": structured,
    }


class StrictLoadingTests(unittest.TestCase):
    def assert_private_error(self, text, marker="PRIVATE_VALUE_DO_NOT_PRINT"):
        with self.assertRaises(AuditError) as caught:
            loads_document(text)
        self.assertNotIn(marker, str(caught.exception))

    def test_duplicate_keys_rejected_at_any_depth(self):
        samples = [
            '{"content":[],"content":[],"marker":"PRIVATE_VALUE_DO_NOT_PRINT"}',
            '{"content":[],"structuredContent":{"x":1,"x":"PRIVATE_VALUE_DO_NOT_PRINT"}}',
            '{"content":[],"structuredContent":[{"x":1,"x":"PRIVATE_VALUE_DO_NOT_PRINT"}]}',
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assert_private_error(sample)

    def test_escaped_duplicate_keys_rejected(self):
        self.assert_private_error(
            '{"content":[],"structuredContent":{"x":1,"\\u0078":"PRIVATE_VALUE_DO_NOT_PRINT"}}'
        )

    def test_nonfinite_literals_rejected(self):
        for literal in ("NaN", "Infinity", "-Infinity", "1e9999", "-1e9999"):
            with self.subTest(literal=literal):
                self.assert_private_error(
                    '{"content":[],"structuredContent":'
                    + literal
                    + ',"marker":"PRIVATE_VALUE_DO_NOT_PRINT"}'
                )

    def test_malformed_json_error_does_not_echo_source(self):
        for sample in (
            '{"content":[], "x": PRIVATE_VALUE_DO_NOT_PRINT}',
            '{"content":[], "x": "PRIVATE_VALUE_DO_NOT_PRINT"} trailing',
            '{"content":[], "x": "PRIVATE_VALUE_DO_NOT_PRINT',
        ):
            with self.subTest(sample=sample):
                self.assert_private_error(sample)

    def test_blank_input_is_an_error(self):
        for sample in ("", " ", "\n\t"):
            with self.subTest(sample=sample):
                with self.assertRaises(AuditError):
                    loads_document(sample)

    def test_deep_parser_input_has_a_bounded_public_error(self):
        # json.loads itself may reach its recursion limit before schema analysis.
        text = '{"content":[],"structuredContent":' + "[" * 2000 + "0" + "]" * 2000 + "}"
        with self.assertRaises(AuditError):
            loads_document(text)

    def test_large_integer_failure_is_not_an_unhandled_value_error(self):
        text = '{"content":[],"structuredContent":' + "9" * 5000 + "}"
        with self.assertRaises(AuditError):
            loads_document(text)

    def test_surrogate_pair_is_valid_unicode(self):
        document = loads_document('{"content":[],"structuredContent":"\\ud83d\\ude80"}')
        report = analyze_result(document)
        self.assertEqual(report["structured_json_bytes"], 6)

    def test_unpaired_surrogate_has_a_public_error(self):
        for escape in ("\\ud800", "\\udfff"):
            with self.subTest(escape=escape):
                with self.assertRaises(AuditError):
                    document = loads_document('{"content":[],"structuredContent":"' + escape + '"}')
                    analyze_result(document)


class StructureAndPrivacyTests(unittest.TestCase):
    def test_raw_and_successful_jsonrpc_wrapper_are_equivalent(self):
        payload = result({"answer": ["é", None, True, 1.0]}, texts=("plain text",))
        raw_report = analyze_result(payload)
        wrapped_report = analyze_result({"jsonrpc": "2.0", "id": 123, "result": payload})
        self.assertEqual(raw_report, wrapped_report)

    def test_invalid_envelopes_rejected(self):
        payload = {"content": []}
        samples = [
            None,
            [],
            "PRIVATE_VALUE_DO_NOT_PRINT",
            {},
            {"content": "PRIVATE_VALUE_DO_NOT_PRINT"},
            {"jsonrpc": "1.0", "id": 1, "result": payload},
            {"jsonrpc": "2.0", "result": payload},
            {"jsonrpc": "2.0", "id": 1, "error": {"message": "PRIVATE_VALUE_DO_NOT_PRINT"}},
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "result": payload},
            {"jsonrpc": "2.0", "id": 1, "error": {}, "result": payload},
        ]
        for document in samples:
            with self.subTest(document=document):
                with self.assertRaises(AuditError) as caught:
                    analyze_result(document)
                self.assertNotIn("PRIVATE_VALUE_DO_NOT_PRINT", str(caught.exception))

    def test_absent_and_explicit_null_remain_distinct(self):
        absent = analyze_result({"content": []})
        explicit = analyze_result(result(None, texts=("null",)))
        self.assertIsNone(absent["structured_json_bytes"])
        self.assertEqual(explicit["structured_json_bytes"], 4)
        self.assertEqual(explicit["mirror_count"], 1)

    def test_direct_nonfinite_python_inputs_rejected(self):
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value):
                with self.assertRaises(AuditError):
                    analyze_result(result({"nested": value}))

    def test_non_json_python_values_rejected(self):
        for value in (b"private", {1, 2}, (1, 2), object(), {1: "value"}):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(AuditError):
                    analyze_result(result(value))

    def test_cyclic_containers_have_a_bounded_public_error(self):
        cyclic_list = []
        cyclic_list.append(cyclic_list)
        cyclic_dict = {}
        cyclic_dict["self"] = cyclic_dict
        for value in (cyclic_list, cyclic_dict):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(AuditError):
                    analyze_result(result(value))

    def test_report_contains_no_source_values(self):
        marker = "PRIVATE_VALUE_DO_NOT_PRINT_37b190da"
        payload = {"value": marker, "inner": {"items": [marker]}}
        document = result(
            {"a": payload, "b": payload}, texts=(json.dumps({"a": payload, "b": payload}), marker)
        )
        document["_meta"] = {"private": marker}
        report = analyze_result(document, min_duplicate_bytes=0)
        serialized = json.dumps(report)
        self.assertNotIn(marker, serialized)
        self.assertNotIn(marker, repr(report))

    def test_analysis_does_not_mutate_nested_inputs(self):
        document = result({"b": [None, {"z": 1, "a": 2}], "a": "x"}, texts=('{"a":2}',))
        document["_meta"] = {"metadata": [True, {"empty": []}]}
        original = copy.deepcopy(document)
        analyze_result(document, top=100, min_duplicate_bytes=0)
        self.assertEqual(document, original)
        self.assertEqual(list(document["structuredContent"]), ["b", "a"])

    def test_json_field_names_use_rfc6901_escaping(self):
        report = analyze_result(result({"a/b": {"~": "é"}, "": 1}), top=100)
        leaves = {item["path"]: item["bytes"] for item in report["largest_fields"]}
        self.assertEqual(leaves["/structuredContent/a~1b/~0"], 4)
        self.assertEqual(leaves["/structuredContent/"], 1)

    def test_unicode_sizes_are_utf8_not_python_character_counts(self):
        structured = {"非ASCII": "é🚀\n"}
        text = "é🚀\n"
        document = result(structured, texts=(text,))
        report = analyze_result(document)
        self.assertEqual(report["text_utf8_bytes"], len(text.encode("utf-8")))
        self.assertEqual(report["structured_json_bytes"], compact_bytes(structured))
        self.assertEqual(report["compact_result_bytes"], compact_bytes(document))

    def test_key_order_and_document_whitespace_do_not_change_report(self):
        first = '{"structuredContent":{"b":[2,1],"a":{"v":"é"}},"content":[]}'
        second = '{\n "content" : [ ], "structuredContent" : {"a": {"v":"é"}, "b": [2,1]}\n}'
        self.assertEqual(
            analyze_result(loads_document(first), top=100, min_duplicate_bytes=0),
            analyze_result(loads_document(second), top=100, min_duplicate_bytes=0),
        )

    def test_tool_error_result_is_still_a_successful_protocol_result(self):
        document = {"content": [{"type": "text", "text": "not found"}], "isError": True}
        report = analyze_result(document)
        self.assertTrue(report["is_error"])
        self.assertEqual(report["compact_result_bytes"], compact_bytes(document))


class MirrorAndDuplicateTests(unittest.TestCase):
    def test_mirror_comparison_is_type_aware(self):
        for structured, texts, expected_index in (
            (1, ("true", "1.0", "1"), 2),
            (True, ("1", "1.0", "true"), 2),
            (1.0, ("1", "true", "1.0"), 2),
            (-0.0, ("0", "0.0", "-0.0"), 2),
        ):
            with self.subTest(structured=structured):
                report = analyze_result(result(structured, texts=texts))
                self.assertEqual(report["mirror_count"], 1)
                self.assertEqual(
                    report["mirrors"][0]["text_path"], f"/content/{expected_index}/text"
                )

    def test_mirror_matches_complete_json_despite_internal_formatting(self):
        structured = {"b": [True, None], "a": "é"}
        report = analyze_result(
            result(structured, texts=('  { "a": "\\u00e9", "b": [true,null] } \n',))
        )
        self.assertEqual(report["mirror_count"], 1)
        self.assertEqual(report["mirrors"][0]["value_bytes"], compact_bytes(structured))

    def test_partial_json_and_non_json_text_are_not_mirrors(self):
        structured = {"a": 1}
        texts = (
            'prefix {"a":1}',
            '{"a":1} trailing',
            '```json\n{"a":1}\n```',
            '{"a":1,"a":1}',
            "NaN",
        )
        report = analyze_result(result(structured, texts=texts))
        self.assertEqual(report["mirror_count"], 0)

    def test_nonadjacent_object_duplicates_are_found(self):
        payload = {"nested": ["x" * 100, {"a": 1, "b": 2}]}
        reordered = {"nested": ["x" * 100, {"b": 2, "a": 1}]}
        report = analyze_result(
            result({"first": payload, "middle": [1, 2], "last": reordered}), top=100
        )
        groups = report["duplicate_subtrees"]
        matching = [
            group
            for group in groups
            if set(group["paths"]) == {"/structuredContent/first", "/structuredContent/last"}
        ]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["occurrences"], 2)
        self.assertEqual(matching[0]["value_bytes"], compact_bytes(payload))

    def test_array_order_changes_identity(self):
        report = analyze_result(result({"a": [1, 2], "b": [2, 1]}), min_duplicate_bytes=0)
        self.assertEqual(report["duplicate_group_count"], 0)

    def test_scalar_repetition_is_not_a_duplicate_subtree(self):
        report = analyze_result(result(["x" * 100] * 10), min_duplicate_bytes=0)
        self.assertEqual(report["duplicate_group_count"], 0)

    def test_boolean_integer_float_containers_are_distinct(self):
        report = analyze_result(
            result({"a": [True], "b": [1], "c": [1.0], "d": [-0.0], "e": [0.0]}),
            min_duplicate_bytes=0,
        )
        self.assertEqual(report["duplicate_group_count"], 0)

    def test_duplicate_scope_is_structured_content_only(self):
        document = {
            "content": [{"type": "text", "text": "repeat"}, {"type": "text", "text": "repeat"}],
            "_meta": {"a": [1, 2], "b": [1, 2]},
            "structuredContent": {"single": [1, 2]},
        }
        self.assertEqual(
            analyze_result(document, min_duplicate_bytes=0)["duplicate_group_count"], 0
        )

    def test_duplicate_size_threshold_includes_exact_boundary(self):
        payload = {"x": "a" * 80}
        document = result([payload, copy.deepcopy(payload)])
        size = compact_bytes(payload)
        self.assertEqual(
            analyze_result(document, min_duplicate_bytes=size)["duplicate_group_count"], 1
        )
        self.assertEqual(
            analyze_result(document, min_duplicate_bytes=size + 1)["duplicate_group_count"], 0
        )

    def test_top_zero_keeps_summary_counts(self):
        structured = {"a": [1, 2], "b": [1, 2]}
        document = result(structured, texts=(json.dumps(structured),))
        report = analyze_result(document, top=0, min_duplicate_bytes=0)
        self.assertEqual(report["largest_fields"], [])
        self.assertEqual(report["mirrors"], [])
        self.assertEqual(report["duplicate_subtrees"], [])
        self.assertEqual(report["mirror_count"], 1)
        self.assertEqual(report["duplicate_group_count"], 1)

    def test_duplicate_path_cap_retains_true_occurrence_count(self):
        report = analyze_result(result([[1, 2]] * 40), min_duplicate_bytes=0)
        group = report["duplicate_subtrees"][0]
        self.assertEqual(group["occurrences"], 40)
        self.assertEqual(len(group["paths"]), 20)
        self.assertEqual(group["paths_truncated"], 20)
        self.assertEqual(
            group["paths"], sorted(f"/structuredContent/{index}" for index in range(40))[:20]
        )

    def test_randomized_sizes_and_duplicates_match_independent_json_oracle(self):
        rng = random.Random(85017)

        def tree(depth):
            if depth == 0 or rng.random() < 0.45:
                return rng.choice(
                    [None, True, False, 0, 1, -25, 1.0, -0.0, 2.5, "", "é🚀", 'x\ny\\z"', "漢字"]
                )
            if rng.random() < 0.5:
                return [tree(depth - 1) for _ in range(rng.randrange(5))]
            return {
                f"{key}/{index}~": tree(depth - 1)
                for index, key in enumerate(rng.sample(["a", "é", "z", ""], rng.randrange(5)))
            }

        def collect(value, path, groups):
            if isinstance(value, (list, dict)) and value:
                canonical = json.dumps(
                    value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                groups.setdefault(canonical, []).append(path)
            if isinstance(value, dict):
                for key, child in value.items():
                    collect(child, path + "/" + key.replace("~", "~0").replace("/", "~1"), groups)
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    collect(child, path + "/" + str(index), groups)

        for index in range(120):
            with self.subTest(seed_case=index):
                value = tree(4)
                structured = {"first": value, "repeat": copy.deepcopy(value), "other": tree(3)}
                document = result(
                    structured, texts=(json.dumps(structured, ensure_ascii=True, indent=2),)
                )
                report = analyze_result(document, top=10_000, min_duplicate_bytes=0)
                self.assertEqual(report["compact_result_bytes"], compact_bytes(document))
                self.assertEqual(report["content_json_bytes"], compact_bytes(document["content"]))
                self.assertEqual(report["structured_json_bytes"], compact_bytes(structured))
                self.assertEqual(report["mirror_count"], 1)
                oracle = {}
                collect(structured, "/structuredContent", oracle)
                expected = {
                    (tuple(sorted(paths)[:20]), len(paths), len(encoded.encode("utf-8")))
                    for encoded, paths in oracle.items()
                    if len(paths) > 1
                }
                observed = {
                    (tuple(group["paths"]), group["occurrences"], group["value_bytes"])
                    for group in report["duplicate_subtrees"]
                }
                self.assertEqual(observed, expected)
                self.assertEqual(report["duplicate_group_count"], len(expected))


class ResourceBoundTests(unittest.TestCase):
    def test_invalid_analysis_parameters_rejected(self):
        for kwargs in (
            {"top": -1},
            {"top": True},
            {"top": 1.5},
            {"min_duplicate_bytes": -1},
            {"min_duplicate_bytes": True},
            {"min_duplicate_bytes": 1.5},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(AuditError):
                    analyze_result({"content": []}, **kwargs)

    def test_direct_deep_document_rejected(self):
        nested = 0
        for _ in range(100):
            nested = [nested]
        with self.assertRaises(AuditError):
            analyze_result(result(nested))

    def test_reasonable_depth_succeeds(self):
        nested = 0
        for _ in range(40):
            nested = [nested]
        self.assertGreater(analyze_result(result(nested))["compact_result_bytes"], 0)

    def test_node_limit_rejects_oversized_arrays(self):
        with self.assertRaises(AuditError):
            analyze_result(result([0] * 200_100), top=0)

    def test_exact_depth_boundary_is_inclusive(self):
        nested = 0
        for _ in range(63):
            nested = [nested]
        # Result root is depth 0; structuredContent's leaf is at depth 64.
        analyze_result(result(nested), top=0)
        with self.assertRaises(AuditError):
            analyze_result(result([nested]), top=0)

    def test_exact_node_boundary_is_inclusive(self):
        # Root, content array, structured array, then one node per scalar.
        document = result([0] * 199_997)
        loads_document(json.dumps(document))
        document["structuredContent"].append(0)
        with self.assertRaises(AuditError):
            loads_document(json.dumps(document))

    def test_several_megabyte_payload_is_bounded_and_private(self):
        # Enough data to expose accidental quadratic repeated serialization.
        marker = "PRIVATE_LARGE_VALUE_"
        item = {"payload": marker + "x" * 1024, "flags": [True, False, None]}
        document = result({"rows": [copy.deepcopy(item) for _ in range(4000)]})
        start = time.monotonic()
        report = analyze_result(document, top=5)
        elapsed = time.monotonic() - start
        self.assertGreater(report["compact_result_bytes"], 4_000_000)
        self.assertNotIn(marker, json.dumps(report))
        self.assertLess(
            elapsed, 15.0, "A roughly 4 MB / 32k-node fixture should finish in bounded time"
        )
        self.assertLessEqual(len(report["largest_fields"]), 5)
        self.assertLessEqual(len(report["duplicate_subtrees"]), 5)


if __name__ == "__main__":
    unittest.main()
