# releasing plugins

this repository is a monorepo. each top-level `hermes-*` directory is a self-contained Python package and can be released independently.

## one-time PyPI setup

1. create a PyPI account at <https://pypi.org/account/register/> and enable 2fa.
2. create the GitHub Actions environment named `pypi` in `TheEpTic/hermes-plugins` and restrict it to protected release tags. add required reviewers if you want a human approval gate.
3. on PyPI, register a **pending trusted publisher** for each project:

| PyPI project | owner | repository | workflow | environment |
| --- | --- | --- | --- | --- |
| `hermes-ssh` | `TheEpTic` | `hermes-plugins` | `pypi-publish.yml` | `pypi` |
| `hermes-sfw` | `TheEpTic` | `hermes-plugins` | `pypi-publish.yml` | `pypi` |

PyPI uses GitHub OIDC. do not create or store a long-lived `PYPI_TOKEN` secret.

## release checklist

1. make the changes and add tests.
2. set the package version in that plugin's `pyproject.toml` and update its `CHANGELOG.md`.
3. regenerate its lockfile if dependencies changed:

   ```bash
   cd hermes-ssh  # or hermes-sfw
   uv lock
   uv sync --extra dev --locked
   uv run pytest
   uv run black --check src tests
   uv run mypy src
   uv build
   ```

4. merge the release commit to `main` and wait for CI.
5. create and push exactly one matching annotated tag:

   ```bash
   git tag -a hermes-ssh-v0.3.1 -m "hermes-ssh 0.3.1"
   git push origin hermes-ssh-v0.3.1
   ```

   tags must be `hermes-ssh-v<version>` or `hermes-sfw-v<version>`. the workflow rejects a tag whose version does not exactly match the package metadata.

6. GitHub builds the selected package, runs `twine check`, pauses at the `pypi` environment if approvals are configured, then publishes through PyPI trusted publishing.
7. verify the published wheel in a clean virtual environment before announcing it:

   ```bash
   uv venv /tmp/hermes-plugin-check
   /tmp/hermes-plugin-check/bin/pip install hermes-ssh==0.3.1
   /tmp/hermes-plugin-check/bin/python -c 'import ssh_tools; print("ok")'
   ```

## rollback

PyPI releases are immutable. do not delete or overwrite a broken release. publish a corrected patch version, then mark the bad release as withdrawn on PyPI if needed.
