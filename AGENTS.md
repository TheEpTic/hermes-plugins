# installing these plugins as an agent

Follow this file when a user hands you the repository and asks you to install or configure a plugin.

## rule zero

This repository is a monorepo containing two independent Python distributions:

| requested plugin | PyPI distribution | Hermes plugin name |
|---|---|---|
| SSH | `hermes-ssh` | `hermes-ssh` |
| Socket Firewall | `hermes-sfw` | `hermes-sfw` |

Do **not** run `hermes plugins install TheEpTic/hermes-plugins`. Hermes's Git installer expects one installable plugin repository, while this repository contains two packages.

## installation procedure

1. Confirm which plugin the user requested. Do not install both unless they asked for both.
2. Locate Hermes with `command -v hermes`.
3. Inspect the executable and determine the exact Python environment that owns it.
4. Install the requested PyPI distribution into that environment.
5. Enable the corresponding Hermes plugin without granting built-in tool overrides.
6. Verify package installation and Hermes discovery.
7. Report the exact result. Never claim success from a zero exit code alone.

### resolving the Hermes environment

Prefer evidence in this order:

1. An absolute Python path in the first line of the `hermes` executable.
2. The active virtual environment that owns both `python` and `hermes`.
3. The environment managed by pipx or uv tool.
4. Hermes's documented installation environment supplied by the user.

Useful inspection commands:

```bash
command -v hermes
head -n 1 "$(command -v hermes)"
command -v python
python -c 'import sys; print(sys.executable)'
```

For a normal virtual environment or pip installation:

```bash
/path/to/hermes-python -m pip install hermes-ssh
# or
/path/to/hermes-python -m pip install hermes-sfw
```

If that interpreter has no `pip`, use an environment-aware tool such as:

```bash
uv pip install --python /path/to/hermes-python hermes-ssh
```

Do not use `sudo pip`, mutate the system Python blindly, or install into whichever `python` happens to appear first on `PATH`.

### enable and verify

```bash
hermes plugins enable hermes-ssh --no-allow-tool-override
# or
hermes plugins enable hermes-sfw --no-allow-tool-override

hermes plugins list --enabled --plain
```

Verify the distribution through the Hermes interpreter too:

```bash
/path/to/hermes-python -c 'from importlib.metadata import version; print(version("hermes-ssh"))'
```

Use `hermes-sfw` in the final command when that is the requested package.

Hermes imports plugin modules once. Run `/reset` in an active session or restart the Hermes process after installation or upgrade.

## plugin-specific setup

### hermes-ssh

Requirements:

- OpenSSH client available as `ssh`
- a user-approved host
- an explicit SSH username
- an approved key path or other existing non-interactive OpenSSH authentication

Ask for missing connection details. Never infer that `root` is acceptable.

A safe registration shape is:

```text
ssh_machines action=add name=<name> host=<host> user=<non-root-user> key=<key-path> port=<port>
```

Use `ssh_machines action=test name=<name>` only after the user approves the registration. Do not run a remote mutation merely to verify the plugin.

### hermes-sfw

Requirements:

```bash
sfw --version
```

If missing and the user authorised installation:

```bash
npm i -g sfw
```

Verify from the same environment and `PATH` used by the Hermes process. A shell finding `sfw` does not prove a systemd service or container can find it.

Use:

```text
sfw action=status
```

Do not install a throwaway dependency merely to test the plugin.

## completion report

Return:

- requested plugin
- Hermes executable path
- Python interpreter or environment used
- installed distribution and version
- enable command result
- `hermes plugins list --enabled --plain` result
- whether a Hermes reset or restart is still required
- any prerequisite still missing

If any check fails, state exactly which one failed and stop. Do not hide uncertainty behind “installed successfully”.
