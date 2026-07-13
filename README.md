# hermes-plugins

Community plugins for [Hermes Agent](https://github.com/NousResearch/hermes-agent). Each directory is independently installable; the repository keeps shared CI and release hygiene in one place.

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
