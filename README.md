# hermes-plugins

[![build](https://github.com/TheEpTic/hermes-plugins/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/TheEpTic/hermes-plugins/actions/workflows/ci.yml)
[![stars](https://img.shields.io/github/stars/TheEpTic/hermes-plugins?style=flat&label=stars)](https://github.com/TheEpTic/hermes-plugins/stargazers)

Practical community plugins for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

- **hermes-ssh** gives Hermes named, multi-host SSH operations with a machine registry, reusable connections, background sessions, and audit history.
- **hermes-sfw** routes dependency changes through Socket Firewall Free instead of trusting raw package-manager installs.

Want the lazy route? Give your agent this repository and point it at [AGENTS.md](AGENTS.md).

## choose a plugin

| plugin | use it when | package |
|---|---|---|
| [hermes-ssh](hermes-ssh/) | Hermes needs to operate one or more registered machines over SSH | `hermes-ssh` |
| [hermes-sfw](hermes-sfw/) | Hermes installs or updates npm, Python, or Rust dependencies | `hermes-sfw` |

They are independent. Install either one or both.

## install in about a minute

Install only the section you need. The Python package must go into the **same environment that runs `hermes`**.

### hermes-ssh

```bash
python -m pip install hermes-ssh
hermes plugins enable hermes-ssh --no-allow-tool-override
python -m pip show hermes-ssh
```

### hermes-sfw

```bash
npm i -g sfw
sfw --version
python -m pip install hermes-sfw
hermes plugins enable hermes-sfw --no-allow-tool-override
python -m pip show hermes-sfw
```

Run `/reset` or restart Hermes, then confirm the selected plugin is enabled:

```bash
hermes plugins list --enabled --plain
```

### `python` might be the wrong environment

If `pip show` succeeds but Hermes cannot see the plugin, you installed it beside Hermes rather than inside Hermes.

Use the Python interpreter from the `hermes` executable's shebang, or let an agent follow [AGENTS.md](AGENTS.md). Do not keep reinstalling into random Python environments until one happens to work.

## hand this to your agent

Paste this into a coding or terminal-capable agent:

```text
Install the Hermes plugin I ask for from https://github.com/TheEpTic/hermes-plugins.

Read AGENTS.md first. Identify the exact Python environment used by the `hermes` executable, install the matching PyPI distribution into that environment, enable it with `hermes plugins enable <name> --no-allow-tool-override`, restart or reset Hermes if possible, and verify it appears in `hermes plugins list --enabled --plain`.

Do not use `hermes plugins install TheEpTic/hermes-plugins`; this is a monorepo with two independent PyPI packages. Do not guess SSH credentials, register root accounts, connect to a host, or run a dependency operation merely to prove installation. Report the commands used, detected paths, installed version, and verification result.
```

For machine-readable instructions, agents can also read [llms.txt](llms.txt).

## first use

### SSH

Register a machine explicitly, preferably with a dedicated non-root account:

```text
ssh_machines action=add name=web1 host=192.0.2.10 user=deploy key=~/.ssh/id_ed25519
ssh_machines action=test name=web1
ssh_terminal machine=web1 command="uptime"
```

Or from chat:

```text
/ssh web1 uptime
```

### protected dependency work

```text
sfw action=status
sfw action=run command="npm install express" workdir="/path/to/project"
sfw action=run command="uv pip install requests" workdir="/path/to/project"
sfw action=run command="cargo fetch" workdir="/path/to/project"
```

`hermes-sfw` is a guard, not a sandbox. Allowed package managers can still execute lifecycle scripts and build backends.

## quick troubleshooting

| symptom | likely cause | fix |
|---|---|---|
| plugin missing from `hermes plugins list` | package installed into the wrong Python environment | use the interpreter that launches `hermes` |
| plugin listed but tools are absent | plugin is discovered but disabled | run `hermes plugins enable <name> --no-allow-tool-override`, then `/reset` |
| `sfw` reports not installed | Socket Firewall Free is not on Hermes's `PATH` | run `npm i -g sfw`, then verify `sfw --version` from the same service environment |
| SSH says permission denied | wrong user, key, or server authorization | verify the same connection with OpenSSH outside Hermes |
| SSH host key changed | the saved host key no longer matches | investigate the host before changing `known_hosts` |

## security boundary

`hermes-ssh` executes commands with the credentials available to Hermes. Keep the machine registry tight, prefer dedicated non-root accounts, and use least privilege.

`hermes-sfw` only accepts a restricted dependency-operation grammar and sends accepted operations through Socket Firewall Free. It does not make package lifecycle code harmless.

See [SECURITY.md](SECURITY.md) before exposing either plugin to untrusted prompts or repositories.

## development

```bash
git clone https://github.com/TheEpTic/hermes-plugins.git
cd hermes-plugins

cd hermes-ssh
uv sync --extra dev --locked
uv run pytest

cd ../hermes-sfw
uv sync --extra dev --locked
uv run pytest
```

CI runs tests, Black, and strict mypy across Python 3.11 to 3.13. Releases are built from package-scoped tags and published to PyPI through GitHub OIDC.

## license

MIT. See [LICENSE](LICENSE).
