# Contributing

PRs welcome. Here's the workflow.

## Setup

```bash
git clone https://github.com/TheEpTic/hermes-ssh.git
cd hermes-ssh
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
```

## Development

```bash
ruff check src/ssh_tools/ tests/
ruff format src/ssh_tools/ tests/
mypy src/ssh_tools/
pytest
```

## Testing

Run the full test suite with:

```bash
pytest
```

For coverage:

```bash
pytest --cov=ssh_tools --cov-report=term-missing
```

Aim for **90%+ coverage** on new code. The test suite uses pytest fixtures defined in `tests/conftest.py` for mocking SSH connections and the manager. When adding a new tool or handler, write tests that cover:

- Happy path (valid input, successful execution)
- Error cases (missing params, connection failures, timeouts)
- Edge cases (empty output, very large output, special characters)

## Guidelines

- **Tests required for bug fixes.** Each fix gets a test that reproduces the issue.
- **Separate PRs for separate concerns.** Don't bundle unrelated changes.
- Run `ruff check` and `mypy` before pushing. CI will catch it if you don't.

## Project Structure

```
src/ssh_tools/
├── __init__.py          # Plugin registration + Hermes hooks
├── config.py            # SSHConfig dataclass (all settings)
├── manager.py           # SSHManager — machines, sessions, execution
├── models.py            # Machine, Session dataclasses
├── schemas.py           # Tool schemas (what the LLM sees)
├── utils.py             # Shared helpers (ok/err response builders)
├── py.typed             # PEP 561 marker
└── handlers/
    ├── __init__.py
    ├── terminal.py      # ssh_terminal tool handler
    ├── machines.py      # ssh_machines tool handler
    ├── sessions.py      # ssh_sessions tool handler
    └── slash.py         # /ssh slash command
tests/
├── conftest.py          # Shared fixtures
├── test_config.py
├── test_manager.py
└── test_tools.py
```

## Architecture

- **`SSHManager`** owns all state. Thread-safe. No module-level mutable state.
- **Handlers** are thin wrappers — validate params, dispatch to manager, return JSON.
- **`utils.py`** provides `ok()`, `err()`, `require()` to eliminate boilerplate.
- **`/ssh` slash command** uses a factory pattern (`create_slash_handler`) for testability.
- **Idle checker** runs as a background daemon thread, cleaning up stale sessions.
