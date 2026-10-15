# Security and data handling

The tool reads the file(s) you name or standard input. It does not discover local
sessions, read credentials, start MCP servers, evaluate payload code, fetch URLs,
or send telemetry. No third-party runtime packages are required.

Inputs are untrusted. Strict JSON parsing rejects duplicate keys and nonfinite
numbers; structural bounds limit deeply nested and oversized inputs. These are
practical limits, **not a resource-isolation sandbox**. Do not run a CLI on
untrusted named pipes or devices; use regular files or a bounded stdin producer.
An additional cumulative pointer-length budget limits memory amplification from
large field names shared by many descendant paths.

Reports contain sizes, field paths, input filenames, and structural diagnostics,
not raw values. Property names and filenames may contain sensitive information.
Neither reports nor fixtures are guaranteed redacted. Review before publishing.

If you find a vulnerability, use GitHub's private vulnerability reporting feature
when available. Otherwise open an issue asking for a private contact channel,
without including secrets, production payloads, or exploit-sensitive details.
