"""Slash command handler for /ssh."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..manager import SSHManager

_HELP = """\
/ssh — SSH session management

Subcommands:
  (no args)              List machines and active sessions
  <machine>              Show machine details
  <machine> <command>    Run command on machine
  <machine> -bg <cmd>    Run command in background
  test                   Test connectivity to all machines
  cleanup                Kill all idle sessions (>30 min)
  help                   Show this help

Background commands:
  Use ssh_terminal with background=true to run long commands.
  Output is truncated to max_output_chars (default: 50000).
  Use ssh_sessions poll/read_output to check status and get results.
"""


def create_slash_handler(
    get_manager: Callable[[], SSHManager],
) -> Callable[[str], str | None]:
    """Create the /ssh slash command handler.

    Takes a callable that returns the SSHManager instance,
    avoiding module-level state coupling.
    """

    def _handle_slash(raw_args: str) -> str | None:
        manager = get_manager()
        args = raw_args.strip().split()

        if not args or args[0] in ("help", "-h", "--help"):
            return _HELP

        if args[0] == "test":
            machines = manager.list_machines()
            if not machines:
                return "No machines registered. Use ssh_machines to add one."
            lines = ["Testing connectivity:"]
            for name, machine in machines.items():
                result = manager.test_machine(name)
                icon = "✓" if result["success"] else "✗"
                error = f" — {result.get('error', '')}" if not result["success"] else ""
                lines.append(f"  {icon} {name} ({machine.host}){error}")
            return "\n".join(lines)

        if args[0] == "cleanup":
            result = manager.cleanup_idle()
            if result["count"] == 0:
                return "No idle sessions to clean up."
            lines = [f"Killed {result['count']} idle session(s):"]
            for item in result["killed"]:
                lines.append(f"  - {item['session_id']} on {item.get('machine', '?')}")
            return "\n".join(lines)

        name = args[0]
        target = manager.get_machine(name)
        if not target:
            return f"Machine '{name}' not found in registry."

        # Run command if provided
        if len(args) > 1:
            command = " ".join(args[1:])
            result = manager.run_command(name, command)
            parts = []
            if result.get("stdout"):
                parts.append(result["stdout"].rstrip())
            if result.get("stderr"):
                parts.append(f"stderr: {result['stderr'].rstrip()}")
            parts.append(
                f"exit: {result.get('exit_code', '?')} ({result.get('elapsed_secs', '?')}s)"
            )
            return "\n".join(parts)

        # Inspect machine
        canonical = manager.resolve_name(name)
        if canonical is None:
            return f"Machine '{name}' found but resolution failed"
        lines = [
            f"Machine: {canonical}",
            f"  Host: {target.host}",
            f"  User: {target.user}",
            f"  Port: {target.port}",
        ]
        if target.key:
            lines.append(f"  Key: {target.key}")
        if target.aliases:
            lines.append(f"  Aliases: {', '.join(target.aliases)}")
        if target.tags:
            lines.append(f"  Tags: {', '.join(target.tags)}")
        if target.description:
            lines.append(f"  Desc: {target.description}")

        active = manager.list_sessions("active")
        machine_sessions = {k: v for k, v in active.items() if v.machine == canonical}
        if machine_sessions:
            lines.append(f"  Active sessions: {len(machine_sessions)}")
            for sid, s in machine_sessions.items():
                lines.append(f"    - {sid} (idle: {s.idle_human}, commands: {s.command_count})")

        return "\n".join(lines)

    return _handle_slash
