# Why this tool exists

Research date: **2026-10-15**. Discussion window: **2026-09-15 through
2026-10-15**, interpreted as the rolling last month rather than September alone.

## Decision

Build a small, offline auditor for **saved MCP tool-call result payloads**:
measure serialized bytes, attribute large fields, identify exact text/structured
mirrors and repeated structured subtrees, and compare fixtures against explicit
CI budgets. Do not build another agent framework, runtime compressor, or
general-purpose observability service.

The hypothesis is that a server maintainer can diagnose a bloated result before
changing its representation, then catch regressions without connecting an agent
or running a live server. Confidence is **modest**: we found specific problem
reports, not validated demand for this implementation. No interviews, user
testing, adoption, or willingness-to-pay claims are implied.

## How we searched

- **Hacker News:** searched the public [Algolia API](https://hn.algolia.com/api)
  with date-bounded `created_at_i` filters. Six broad story queries—`agent`,
  `MCP`, `LLM`, `Ask HN AI`, `AI cost`, and `AI testing`—returned 420 metadata
  hits, representing 407 distinct story IDs. These are retrieved results, not
  407 individually validated pain points. We skimmed titles, inspected eight
  selected comment trees, and followed up with narrower comment searches about
  tool results, retries, context, schemas, and debugging. Comment dates below
  were checked against API `created_at` timestamps.
- **Reddit:** searched public indexed discussions using `site:reddit.com`,
  AI-agent/MCP/debugging/retry terms, date constraints, and subreddit refinements;
  opened candidate threads and separated practical questions from product
  launches. Search filters and relative ages sometimes disagreed. The principal
  thread below was dated from its JSON `created_utc`, not its search label.
  This was not a full Reddit scrape or a representative sample.
- **X/Twitter:** eight public web searches combined `site:x.com` or
  `site:twitter.com` with agent/MCP/tool-call/debugging terms and September–October
  2026 dates or `after:`/`before:` bounds. No relevant, date-verified posts
  surfaced. **X is a coverage gap**, not corroborating evidence.
- **GitHub and protocol documentation:** inspected primary issue reports,
  competitor READMEs, and the MCP specification to check the proposed scope.
  Competitor capabilities are a snapshot of the pages inspected on the research
  date; this is not an exhaustive market survey.

## Evidence ledger

The measurements below are the respective authors' reports. We did not reproduce
their deployments. Community identities and independence were not authenticated.

| Date (UTC) | Primary discussion | Observed problem and strength of signal |
| --- | --- | --- |
| 2026-09-18 | [Reddit: retries resend the entire context](https://www.reddit.com/r/LangChain/comments/1wjlp3p/how_do_you_reduce_token_consumption_when_an_llm/) | An internal-workflow developer describes large metadata, generated dataflows, and MCP responses accumulating on validation retries, and considers smaller structured tool errors. Practical question without a product link in the original post. Adjacent support for result-size discipline, not evidence of text/structured mirroring. |
| 2026-09-20 | [HN: custom tool-output capture wrapper](https://news.ycombinator.com/item?id=49779486) | A practitioner reports building a wrapper that returns a preview plus a handle for querying omitted output because tools can return excessive content. Concrete workaround; not a request for our proposed auditor. No product link in that comment. |
| 2026-09-21 | [HN: repetitive JSON in MCP results](https://news.ycombinator.com/item?id=49789612) | A practitioner reports that JSON boilerplate accounted for at least 30% of some responses and that alternative formats reduced the overhead. Useful evidence for attribution; anecdotal, not a general token-saving benchmark. |
| 2026-09-30 | [HN: preprocessing large compiler output](https://news.ycombinator.com/item?id=49908981) | A commenter asks about reducing massive compiler output before it reaches the main agent. Adjacent context-volume pain; a workflow question rather than direct demand for an MCP-specific tool. |
| 2026-10-06 | [REA issue #723: result repetition while preserving contracts](https://github.com/morluto/rea/issues/723) | The original evaluation reports a 422-byte JavaScript fixture producing 303,361 CLI bytes versus 759,454 MCP bytes; the result repeated `evidence.normalized_result`, and text repeated the structured envelope. The author explicitly asks to preserve complete evidence and compatibility. Specific, measured engineering report; not measurements made by this project, and not proof of current behavior in every REA version. |
| 2026-10-12 | [Reddit: tool results too big for the context](https://www.reddit.com/r/mcp/comments/1x494na/how_do_you_handle_tool_results_that_are_too_big/) and [author's explanation](https://www.reddit.com/r/mcp/comments/1x494na/comment/pfhu16h/) | A flights-MCP maintainer reports a roughly 2.5 MB result breaking a result card. They attribute about half to text/structured mirroring and much of the remainder to repeated fare rows. A smaller result restored the card but lost coverage, requiring paid repeat searches. Strong fit for non-destructive diagnosis. The author names their own service, so this is an interested maintainer's report, not independent customer validation. |

The Reddit post's `created_utc=1791828320` corresponds to
`2026-10-12T18:05:20Z`; its explanatory comment is dated
`2026-10-12T18:10:50Z`. Some indexed renderings suggested October 13 or a relative
age, which we did not use as the publication date.
The LangChain post's JSON `created_utc=1789724603` corresponds to
`2026-09-18T09:43:23Z`.

These sources support a recurring **class of problem**: result volume and
repetition can make tools harder to consume. They do not establish that every
repeated value is unnecessary, that payload bytes equal model tokens, or that
maintainers prefer a standalone auditor over their existing tests.

## Alternatives considered

| Candidate | Evidence / existing solution | Decision |
| --- | --- | --- |
| **Offline tool-result payload auditor** | HN workarounds, the Reddit result-card failure, and REA's concrete repetition measurements. Existing size gates overlap. | **Selected:** one saved-result boundary, deterministic analysis, no live credentials or model calls. Differentiation is the convenient combination of field attribution, exact mirrors, repeated subtrees, and fixture comparison—not a new category. |
| Agent-instruction staleness checker | [October 4 HN report](https://news.ycombinator.com/item?id=49952046) describes outdated memories causing plans to include archived repositories. [agentslint](https://github.com/jyablonski/agentslint) already checks stale paths and command references. | Rejected: clear pain, but an almost exact existing tool. Semantic truth checking would also exceed a small deterministic scope. |
| MCP catalog/schema budget checker | Context overhead appears throughout the [September 30 MCP discussion](https://news.ycombinator.com/item?id=49906637). [mcp-size](https://github.com/zororaka00/mcp-size) already profiles tool-definition costs and provides budget/diff workflows. | Rejected: substantial direct overlap; catalog size is different from a particular `tools/call` result. |
| Runtime tool-output compressor | The HN capture-wrapper report is direct evidence. [Headroom](https://github.com/headroomlabs-ai/headroom) and [Spill](https://github.com/spill-ai/spill) already address runtime compression or externalized results. | Rejected: crowded and more intrusive; changing what the model receives needs behavioral evaluation. |
| Misbehaving MCP test server | [October 15 HN: MCP Test Kitchen](https://news.ycombinator.com/item?id=50105780) describes transport/tool errors and delay scenarios; a [reply](https://news.ycombinator.com/item?id=50106087) welcomes the capability. | Rejected: an unusually close new solution already exists. The launch author's motivation is not independent demand evidence. |

## Competitive overlap

| Existing project | What it already does | Our deliberately narrower distinction |
| --- | --- | --- |
| [mcp-assert](https://github.com/blackwell-systems/mcp-assert) | MCP assertions, fixtures, live transport testing, linting, fuzzing, and an oversized-response check. | Response-size gating is **not novel**. This project focuses on explaining one saved payload's internal repetition and comparing two saved results, without server setup or an assertion suite. |
| [mcp-fuzz](https://github.com/vishalhabib99/mcp-fuzz) | Live behavioral tests include response-size measurements and threshold-based failure. | No live tool invocation or generated test inputs here. Users wanting behavior testing should consider it instead. |
| [mcp-size](https://github.com/zororaka00/mcp-size) | Profiles `tools/list` definitions, attributes schema/description overhead, and supports budgets and diffs. | Similar diagnostic workflow, different object: `CallToolResult`, not the tool catalog. |
| [ctxdiff](https://github.com/salmanzafar949/ctxdiff) | Captures and compares whole agent-context traces, including block attribution, token budgets, and cache-prefix diagnostics. | Broader and already overlaps diff/budget needs. This tool needs only a saved result, without trace capture or SDK instrumentation. |
| [Headroom](https://github.com/headroomlabs-ai/headroom) / [Spill](https://github.com/spill-ai/spill) | Runtime compression and/or keeping large results outside the active model context. | Diagnose and regression-test payloads; do not transform them or change a running agent's behavior. |

An existing project's extension could be preferable to adopting another CLI.
The selection is a small, testable packaging hypothesis, not a claim that no
competitors exist.

## Protocol guardrails and scope

The [MCP 2026-07-28 tools specification](https://modelcontextprotocol.io/specification/2026-07-28/server/tools#structured-content)
allows `structuredContent` to be any JSON value and recommends a serialized text
copy for backward compatibility. Therefore:

- Text/structured mirroring can be **intentional and conformant**. A finding is
  diagnostic, not a protocol violation or an instruction to delete a field.
- A client may consume one representation, both, or neither as model text.
  Serialized UTF-8 bytes are not actual context tokens, billing, latency, or
  proven savings.
- Repeated structured values may represent legitimate repeated facts. Do not
  sum overlapping subtree observations as if they were independently removable.
- Compatibility, completeness, evidence provenance, and downstream consumers
  matter more than minimizing a number.

The selected input boundary is a **single saved `CallToolResult` or successful
JSON-RPC response containing one**. Intended operations are read-only analysis,
comparison of two fixtures, and explicit CI budgets. Live MCP connections,
automatic truncation/deduplication, protocol conformance certification, network
access, model calls, and token-billing estimates are outside the scope.

## What would validate—or invalidate—the hypothesis?

Next useful validation would be maintainers trying the tool on representative,
sanitized fixtures and reporting whether its findings change a real decision.
Measure useful findings, false positives, and whether a fixture gate catches an
unwanted regression. Keep client compatibility tests separate from byte checks.

If existing assertion tools already cover the workflow adequately, or repeated
data is predominantly required and the report offers no actionable attribution,
the right outcome may be an integration, a contribution upstream, or stopping
development—not adding a dashboard or another agent layer.
