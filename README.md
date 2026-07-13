# hermes-plugins

[![build](https://github.com/TheEpTic/hermes-plugins/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/TheEpTic/hermes-plugins/actions/workflows/ci.yml)
[![stars](https://img.shields.io/github/stars/TheEpTic/hermes-plugins?style=flat&label=stars)](https://github.com/TheEpTic/hermes-plugins/stargazers)

Community plugins for [Hermes Agent](https://github.com/NousResearch/hermes-agent). Each directory is independently installable; the repository keeps shared CI and release hygiene in one place.

## Plugins

| Plugin | What it does | PyPI | Docs |
|--------|--------------|------|------|
| [hermes-sfw](hermes-sfw/) | Socket Firewall Free wrapper for dependency installs | [![PyPI](https://img.shields.io/pypi/v/hermes-sfw?logo=pypi&label=PyPI)](https://pypi.org/project/hermes-sfw/) | [readme](hermes-sfw/README.md) |
| [hermes-ssh](hermes-ssh/) | SSH machines, sessions, and remote terminal | [![PyPI](https://img.shields.io/pypi/v/hermes-ssh?logo=pypi&label=PyPI)](https://pypi.org/project/hermes-ssh/) | [readme](hermes-ssh/README.md) |

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

## License

MIT — see [LICENSE](LICENSE) for details.
