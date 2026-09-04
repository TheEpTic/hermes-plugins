# Contributing

PRs welcome. Here's the workflow.

## Setup

```bash
git clone https://github.com/TheEpTic/hermes-plugins.git
cd hermes-plugins/hermes-sfw
uv sync --extra dev --locked
```

## Development

```bash
uv run black src/hermes_sfw/ tests/
uv run mypy src/hermes_sfw/
uv run pytest
```

## Testing

Run the full test suite with:

```bash
uv run pytest
```

Aim for **90%+ coverage** on new code. The test suite uses pytest fixtures defined in `tests/conftest.py` for mocking subprocess calls. When adding new functionality, write tests that cover:

- Happy path (valid input, successful execution)
- Error cases (missing params, invalid commands, timeouts)
- Edge cases (empty output, long output, special characters)

## Guidelines

- **Tests required for bug fixes.** Each fix gets a test that reproduces the issue.
- **Separate PRs for separate concerns.** Don't bundle unrelated changes.
- Run `black` and `mypy` before pushing. CI will catch it if you don't.

## Project Structure

```
src/hermes_sfw/
├── __init__.py          # Plugin registration + Hermes hooks
├── manager.py           # SFWManager — command execution, parsing, validation
├── schemas.py           # Tool schema (what the LLM sees)
├── utils.py             # ok(), err(), require() helpers
├── py.typed             # PEP 561 marker
└── handlers/
    ├── __init__.py
    └── sfw.py           # sfw tool handler
tests/
├── conftest.py          # Shared fixtures
└── test_sfw.py          # Full test suite
```

## Architecture

- **`SFWManager`** owns all state. No module-level mutable state.
- **Handlers** are thin wrappers — validate params, dispatch to manager, return JSON.
- **`utils.py`** provides `ok()`, `err()`, `require()` to eliminate boilerplate.
- **Command allowlist** prevents arbitrary command execution through sfw.
