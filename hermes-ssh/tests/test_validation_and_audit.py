"""Host validation and command-audit redaction."""

from __future__ import annotations

import pytest

from ssh_tools.audit import redact_command
from ssh_tools.models import Machine
from ssh_tools.transfers.transport import sftp_target
from ssh_tools.validate import validate_host, validate_machine


@pytest.mark.parametrize(
    "host",
    [
        "example.com",
        "example.com.",
        "db-1.internal",
        "10.0.0.5",
        "my_alias",
        "2001:db8::1",
        "[2001:db8::1]",
        "::1",
    ],
)
def test_valid_hosts(host: str) -> None:
    assert validate_host(host) is None


@pytest.mark.parametrize(
    "host",
    [
        "host:alias",
        "host::x",
        "example.com:22",
        "[example.com]",
        "-oProxyCommand=x",
        "user@host",
        "a b",
        "host/x",
        ".leading",
        "example.com..",
        ".",
        "trailing-",
        "",
    ],
)
def test_invalid_hosts(host: str) -> None:
    assert validate_host(host) is not None


def test_bracketed_ipv6_is_normalised_for_ssh_and_bracketed_for_sftp() -> None:
    machine = validate_machine(Machine(name="v6", host="[2001:db8::1]", user="ops"))
    assert machine.host == "2001:db8::1"
    assert sftp_target(machine) == "ops@[2001:db8::1]"


@pytest.mark.parametrize(
    "command",
    [
        "PGPASSWORD=hunter2 psql -h db",
        "MYSQL_PWD=hunter2 mysql",
        "mysql -uroot -phunter2 db",
        "curl -u admin:hunter2 https://x",
        "curl --user admin:hunter2 https://x",
        "sshpass -p hunter2 ssh h",
        "export AWS_SECRET_ACCESS_KEY=hunter2",
        "GITHUB_TOKEN=hunter2 gh api",
        "DB_PASSWORD='hunter2' ./run",
        "curl -H 'Authorization: Bearer hunter2' x",
        "psql postgresql://u:hunter2@db/x",
        "tool --api-key hunter2",
        "git clone https://tok:hunter2@github.com/x",
        "git clone https://user:p@hunter2@example.com/repo",
        "redis://:hunter2@host:6379/0",
        "redis://:p@hunter2@host:6379/0",
        "postgresql://u:p@hunter2@host/db",
        "curl 'https://user:hunter2@example.com/x'",
    ],
)
def test_audit_redacts_inline_credentials(command: str) -> None:
    assert "hunter2" not in redact_command(command)


@pytest.mark.parametrize(
    "command",
    [
        "ls -p /tmp",
        "mysql -p db",
        "grep pwd file",
        "cd $PWD && ls",
        "curl -u admin https://x",
        "rsync -a host:/x /y",
        "cp -a a:b c",
        "curl https://example.com:8443/a@b",
    ],
)
def test_audit_leaves_ordinary_commands_intact(command: str) -> None:
    assert redact_command(command) == command
