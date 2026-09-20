# Security

## trust boundary

Jev requests carry the conversation history with tool result bodies replaced
by short notes (`ok, N chars (omitted)`, or `ok, N chars, head: <first 500
chars> (truncated)` when excerpts are enabled — the default); message text is
abridged to fit the state budget, and call arguments are included (truncated
per the fit stage). Set `result_excerpt_chars: 0` to send size-only notes
(no result content leaves the host). Prompt the operator before enabling on
sessions carrying secrets outside the trust boundary of the Jev endpoint.

Any OpenAI-style `POST {base_url}{endpoint_path}` Decisions endpoint
works; TypeSafe's own API (https://api.typesafe.ai/v1 + `/systemone`) is the
reference, and OpenRouter's Decisions API (https://openrouter.ai +
`/api/alpha/decisions`, model `typesafe/jev-1.13`) speaks the same shape.
Self-hosted routers keep
the data in-house — point `base_url` at them.

## key handling

- The Jev API key is read at prune-call time via
  `agent.secret_scope.get_secret()` (profile-scope aware, multiplex
  fail-closed) and never cached on the engine instance or written to config.
  The shared plugin singleton never retains it (stripped in `__init__`,
  excluded from `__deepcopy__`); only per-agent copies hold the host chat key
  the summary path needs, exactly like the built-in compressor.
- The transport refuses redirects, embedded credentials, non-HTTP schemes,
  and cleartext HTTP outside loopback + private LAN ranges (RFC1918,
  Tailscale CGNAT, ULA/link-local IPv6) — a misconfigured `base_url` fails
  closed to the built-in prune instead of sending the bearer key somewhere
  surprising.
- Upstream error bodies and raw transport exceptions are never echoed into
  logs (status category only); response bodies are capped at 1 MiB.

## lossiness

Pruning is lossy by design (same as the built-in compressor). Dropped tool
results are unrecoverable; dropped calls can be re-run by the agent.

## reporting

Report vulnerabilities to the repository owner; do not open public issues
with sensitive details.
