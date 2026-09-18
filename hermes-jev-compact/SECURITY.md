# Security

- The conduit client key is read at prune-call time via
  `agent.secret_scope.get_secret()` (profile-scope aware, multiplex
  fail-closed) and never cached on the engine instance or written to config.
- Jev requests carry the conversation history with tool results replaced by
  short notes; message text is abridged to fit the state budget. Prompt the
  operator before enabling on sessions carrying secrets outside the trust
  boundary of the Jev endpoint.
- Pruning is lossy by design (same as the built-in compressor). Dropped tool
  results are unrecoverable; dropped calls can be re-run.
- Report vulnerabilities to the repository owner; do not open public issues
  with sensitive details.
