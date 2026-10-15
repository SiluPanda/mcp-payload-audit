# Input, measurements, and report contract

## Supported input

One UTF-8 JSON document, either:

```json
{"content": [{"type": "text", "text": "hello"}], "isError": false}
```

or a JSON-RPC 2.0 success envelope containing that result:

```json
{"jsonrpc": "2.0", "id": 1, "result": {"content": []}}
```

The result must have a `content` array of objects with string `type` fields.
Text blocks need string `text` fields. Unknown/non-text content is measured as
JSON, not interpreted or decoded. `isError`, if present, must be a Boolean;
`resultType`, if present, must be `complete`. Input-required responses, JSON-RPC
errors/requests, batches, empty documents, and unrelated JSON are rejected.

This is deliberately a **shape check**, not validation against a complete MCP
JSON Schema. It does not verify media validity, tool output schemas, annotations,
client compatibility, or the truth of a tool response.

`structuredContent` may contain any JSON value, including `null`, `false`, `0`,
strings, and arrays, as permitted by
[MCP 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/server/tools#structured-content).
It need not be an object. Absence is different from an explicit null.

## Byte definitions

Canonical measurement here means Python JSON encoding with `sort_keys=True`,
`ensure_ascii=False`, separators `(',', ':')`, and finite numbers only, encoded
as UTF-8. **This is not RFC 8785 canonical JSON** or an interoperable signing
format. Numeric spellings are parsed first, so source `1e0` becomes `1.0`;
`1` and `1.0` remain distinct, as do `false` and `0`.
Noninteger numbers use Python binary floating-point parsing, not arbitrary
precision. Rounding/underflow can merge different source decimals (for example,
`1e-999` and `0.0`). "Exact" matching means exact **parsed representations**, not
mathematical equivalence of arbitrary-precision source numbers.

| Field | Definition |
| --- | --- |
| `file_bytes` | CLI only: original input bytes, including formatting, trailing newline, and JSON-RPC envelope |
| `compact_result_bytes` | Entire compact result object; excludes the JSON-RPC envelope and source formatting |
| `content_json_bytes` | Encoded `content` array, including brackets and metadata inside it |
| `text_utf8_bytes` | Sum of the UTF-8 lengths of top-level text-block strings, without JSON string escaping/quotes |
| `structured_json_bytes` | Encoded `structuredContent` value; JSON null in the report means absent; integer `4` means explicit null |
| `meta_json_bytes` | Encoded result-level `_meta`, or null if absent |
| `largest_fields[].bytes` | Size of one encoded leaf value; includes JSON quotes/escapes for strings, excludes property names and ancestor punctuation |

An empty object or array is a leaf. JSON Pointer paths are relative to the
unwrapped result. `/` in a property name is escaped as `~1`, and `~` as `~0`.
Paths/filenames are terminal-escaped in text reports.

**Do not sum the size categories.** Text bytes are already represented inside
content JSON, and structured/meta/content are included in compact result bytes.
Largest-field attribution is not a complete accounting of keys and punctuation.
Source formatting and escaped Unicode may make the file significantly larger
than the compact result. Base64 is counted in serialized form, not decoded size.

## Exact duplication

**Mirrors:** Each top-level text block is independently parsed as a complete JSON
value and compared with `structuredContent`. Object order and whitespace do not
matter; array order, value types, and integer/float distinctions do. A textual
prefix, code fence, or partial copy is not a whole-value mirror. Malformed JSON in
a text block is ordinary text, not a document-level input error.

**Repeated subtrees:** Repeated nonempty objects/arrays anywhere beneath
`structuredContent`, including copies under different keys. The default minimum
container size is 64 compact bytes; use `--min-duplicate-bytes` to change it.
Individual repeated primitive strings/numbers are not reported as groups.

`value_bytes` is the size of **one** copy, not a guaranteed saving. Duplicate
groups may overlap (a repeated object may contain a repeated list). There is no
aggregate "wasted bytes" metric: adding groups would double-count data and imply
that all duplication is removable. The text mirror is intentionally not included
as another structured-subtree occurrence.

Lists are ranked by descending byte size and deterministic path tie-breakers.
`--top` caps each display list (1–100; default 10). `mirror_count` and
`duplicate_group_count` count all matches before truncation. Each duplicate group
shows at most 20 paths; `paths_truncated` counts omitted paths while `occurrences`
retains the full count. The Python API additionally permits `top=0` for summaries.

## Budgets and comparisons

All budgets use **compact_result_bytes**. Growth is
`100 * (candidate_bytes - baseline_bytes) / baseline_bytes`. Only strictly greater
values fail; equality passes. Valid result objects cannot have zero bytes.
Growth checks use decimal arithmetic; the JSON display uses floating-point
percentages. Budgets are opt-in; mirrors alone do not cause exit 1.

`compare` does not assert semantic equivalence or input-workload equivalence.
Keep those invariant in your own test harness. A smaller but incomplete response
is not a successful optimization.

## Bounds and failure behavior

The CLI reads at most 16 MiB plus one byte to detect overflow. Core validation
allows at most 200,000 JSON values and depth 64 (root depth 0). Strict parsing
rejects duplicate keys, NaN, Infinity, floating-point overflow, unpaired Unicode
surrogates, and invalid JSON. The library rejects non-JSON Python values too.
These limits are protective bounds, not a denial-of-service guarantee.
In addition, expanding JSON Pointers has an 8 Mi-character cumulative work limit
across visited result values. This prevents a long ancestor key repeated in many
leaf paths from turning a small file into a huge in-memory report. Such inputs
fail explicitly rather than returning silently incomplete analysis.

Document/read errors exit 2, with a concise diagnostic on stderr and no success
report on stdout. Under `--format json` they use
`{"schema_version": 1, "error": "..."}`. Invalid CLI arguments use standard
argparse usage text. No document values are included in these diagnostics.

JSON reports have `schema_version: 1`. The CLI audit envelope has `command`,
`report`, `violations`, and `budgets_enabled`; comparisons have `before`, `after`,
`delta`, `violations`, and `budgets_enabled`. Treat new fields as additive.

## What the measurements cannot tell you

- What any particular host injects into model context, including `_meta` handling.
- Actual tokens, billable usage, caching behavior, model quality, or latency.
- Whether a duplicate is safe to delete, a resource link will be followed, or a
  truncated result preserves relevant facts.
- Whether your fixtures represent production workloads.

All provided examples are synthetic. `lean.json` and `mirrored.json` deliberately
differ in redundant rows/evidence structure; they are not a proven compatible
rewrite of a production server. Both retain a structured/text fallback.
