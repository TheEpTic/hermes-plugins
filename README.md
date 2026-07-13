# hermes-plugins

[![build](https://github.com/TheEpTic/hermes-plugins/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/TheEpTic/hermes-plugins/actions/workflows/ci.yml)
[![stars](https://img.shields.io/github/stars/TheEpTic/hermes-plugins?style=flat&label=stars)](https://github.com/TheEpTic/hermes-plugins/stargazers)

Practical community plugins for [Hermes Agent](https://github.com/NousResearch/hermes-agent). Remote operations when you need reach, guarded dependency work when you need restraint.

## install

Install each plugin into the **same Python environment that runs Hermes**, then explicitly enable it:

```bash
python -m pip install hermes-ssh
python -m pip install hermes-sfw

hermes plugins enable hermes-ssh
hermes plugins enable hermes-sfw
```

Restart Hermes or use `/reset` after enabling a plugin. Install either package independently if you only need one.

For source installs, development, and full tool references, use the package documentation below.

## plugins

| plugin | use it for | ships | install |
|---|---|---|---|
| [hermes-ssh](hermes-ssh/) | operating machines over SSH from a Hermes session | machine registry, connection reuse, persisted sessions, `/ssh` | [![PyPI](https://img.shields.io/pypi/v/hermes-ssh?logo=pypi&label=PyPI)](https://pypi.org/project/hermes-ssh/) |
| [hermes-sfw](hermes-sfw/) | checking dependency operations with Socket Firewall Free | restricted package-manager operations, approval gate, `sfw` tool | [![PyPI](https://img.shields.io/pypi/v/hermes-sfw?logo=pypi&label=PyPI)](https://pypi.org/project/hermes-sfw/) |

## security boundary

`hermes-sfw` is a dependency guard, not a sandbox. It sends supported dependency operations through Socket Firewall Free and rejects runner-style package-manager commands, but package managers can still execute lifecycle scripts and build backends. Run Hermes with least privilege and treat untrusted dependencies as host code.

`hermes-ssh` executes remote commands with the permissions and credentials available to the Hermes process. Keep the machine registry tight, use non-root accounts, and review the repository [security policy](SECURITY.md) plus the...[truncated]

## compatibility and release discipline

- Python 3.11+
- Linux and macOS
- Hermes pip entry-point plugin discovery
- CI runs tests, formatting, and types across Python 3.11–3.13
- PyPI releases are built from scoped tags and published through GitHub OIDC, with no stored PyPI token

Every package owns its own metadata, documentation, changelog, license, security policy, tests, and Hermes manifest. The monorepo only shares quality and release infrastructure.

## develop from source

```bash
git clone https://github.com/TheEpTic/hermes-plugins.git
cd hermes-plugins

# choose one package
cd hermes-ssh && ./deploy.sh
# or
cd ../hermes-sfw && ./deploy.sh
```

See [hermes-ssh](hermes-ssh/README.md) and [hermes-sfw](hermes-sfw/README.md) for configuration, tool references, and development commands.

## license

MIT. See [LICENSE](LICENSE).
