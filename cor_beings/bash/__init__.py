"""Tiny subprocess tool Being for Lions-heart."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence

from cor_being import Being, Life, World


class BashBeing(Being):
    """Run one argv-style command and return a compact text result."""

    name = "bash"
    description = "Run one command without an intermediate shell."

    def birth(self, world: World, life: Life) -> None:
        # TODO: Route command execution through a separate sandbox/policy Being.
        return None

    def run(self, arguments: Mapping[str, object]) -> str:
        command = arguments.get("command")
        cwd = arguments.get("cwd")
        timeout = arguments.get("timeout", 30.0)

        if (
            not isinstance(command, Sequence)
            or isinstance(command, (str, bytes))
            or not command
            or any(not isinstance(part, str) or not part for part in command)
        ):
            raise ValueError("bash requires command as a non-empty sequence of strings")
        if cwd is not None and not isinstance(cwd, str):
            raise ValueError("bash cwd must be a string or None")
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise ValueError("bash timeout must be a positive number")

        completed = subprocess.run(
            list(command),
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=float(timeout),
            check=False,
        )
        return (
            f"exit={completed.returncode}\n"
            f"stdout={completed.stdout}\n"
            f"stderr={completed.stderr}"
        )
