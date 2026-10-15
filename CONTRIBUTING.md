# Contributing

Keep this a small offline diagnostic, not a runtime proxy or an agent framework.
Start with a bug report or focused pull request, ideally including a minimal
**synthetic** fixture, expected measurements, and a regression test.

Use Python 3.11+ and `uv sync --locked --group dev`. Before submitting, run:

```sh
uv run ruff check .
uv run ruff format --check .
uv run pytest --cov=mcp_payload_audit
uv build
```

Update documentation when changing measurement semantics. Keep report ordering
deterministic and exit codes stable. A new report shape requires consideration of
`schema_version`. Never make compatibility duplication a default failure or
claim byte reduction is guaranteed token/cost reduction.

Do not commit production captures, credentials, personal data, or source payloads
from someone else's issue. All checked-in examples and test data are synthetic.
