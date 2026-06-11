# hermes-plugins

Community plugins for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

## Plugins

| Plugin | Description |
|--------|-------------|
| [hermes-sfw](hermes-sfw/) | Socket Firewall Free — block malicious dependencies at install time |
| [hermes-ssh](hermes-ssh/) | SSH machine management, sessions, and remote terminal |

## Installation

Each plugin is a standalone directory. To install:

```bash
# Clone the repo
git clone https://github.com/TheEpTic/hermes-plugins.git
cd hermes-plugins

# Symlink a plugin into Hermes
ln -s $(pwd)/hermes-sfw/src/hermes_sfw ~/.hermes/plugins/hermes-sfw
ln -s $(pwd)/hermes-ssh/src/ssh_tools ~/.hermes/plugins/hermes-ssh

# Enable in config
hermes config set plugins.enabled hermes-sfw hermes-ssh
```

Then restart the gateway or run `/reset`.

## License

MIT — see [LICENSE](LICENSE) for details.
