# hermes-plugins

Community plugins for [Hermes Agent](https://github.com/NousResearch/hermes-agent). Each directory is independently installable; the repository keeps shared CI and release hygiene in one place.

## Plugins

| Plugin | What it does | PyPI | Docs |
|--------|--------------|------|------|
| [hermes-sfw](hermes-sfw/) | Socket Firewall Free wrapper for dependency installs | planned | [readme](hermes-sfw/README.md) |
| [hermes-ssh](hermes-ssh/) | SSH machines, sessions, and remote terminal | planned | [readme](hermes-ssh/README.md) |

Every plugin directory must be independently releasable: `pyproject.toml`, `README.md`, `CHANGELOG.md`, `SECURITY.md`, `LICENSE`, tests, and a Hermes `plugin.yaml`. New plugins join this table and release through the same trusted-publishing workflow.

## Installation

Each plugin is a standalone directory. To install:

```bash
# Clone the repo
git clone https://github.com/TheEpTic/hermes-plugins.git
cd hermes-plugins

# Install either plugin (or both)
./hermes-sfw/deploy.sh
./hermes-ssh/deploy.sh

# Hermes discovers third-party plugins but does not run them until enabled
hermes plugins enable hermes-sfw
hermes plugins enable hermes-ssh
```

Then restart the gateway or run `/reset`.

## Releases

Each plugin publishes independently to PyPI when a matching protected tag is pushed. The workflow uses short-lived GitHub OIDC credentials, never a stored PyPI token. [Release setup and checklist](RELEASING.md).

## License

MIT — see [LICENSE](LICENSE) for details.
