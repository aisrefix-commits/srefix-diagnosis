"""Run commands on remote hosts via OpenSSH.

Relies entirely on `~/.ssh/config` for ProxyJump / IdentityFile / User.
Never stores credentials inside the MCP process.
"""
from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from typing import Optional


_OUTPUT_CAP_BYTES = 1_000_000  # 1MB cap on stdout/stderr to protect LLM context


@dataclass
class ExecResult:
    host: str
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    truncated_stdout: bool
    truncated_stderr: bool
    dry_run: bool = False


def _trunc(s: bytes) -> tuple[str, bool]:
    if len(s) > _OUTPUT_CAP_BYTES:
        return s[:_OUTPUT_CAP_BYTES].decode("utf-8", errors="replace"), True
    return s.decode("utf-8", errors="replace"), False


def run_via_ssh(host: str, command: str, timeout: int = 30,
                ssh_options: Optional[list[str]] = None,
                dry_run: bool = False) -> ExecResult:
    """Execute a command on `host` via the local OpenSSH client.

    `host` should match an alias in ~/.ssh/config or be a directly-resolvable name.
    OpenSSH handles ProxyJump / auth via the system ssh-agent + ssh-config.
    """
    base_opts = [
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        "-o", "ServerAliveInterval=5",
        "-o", "ServerAliveCountMax=3",
    ]
    if ssh_options:
        base_opts.extend(ssh_options)
    argv = ["ssh", *base_opts, host, command]

    if dry_run:
        return ExecResult(
            host=host,
            command=command,
            exit_code=0,
            stdout=f"DRY RUN — would execute:\n{shlex.join(argv)}\n",
            stderr="",
            duration_ms=0,
            truncated_stdout=False,
            truncated_stderr=False,
            dry_run=True,
        )

    import time
    t0 = time.time()
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        elapsed = int((time.time() - t0) * 1000)
        return ExecResult(
            host=host,
            command=command,
            exit_code=124,
            stdout=(e.stdout or b"").decode("utf-8", errors="replace") if e.stdout else "",
            stderr=f"timeout after {timeout}s",
            duration_ms=elapsed,
            truncated_stdout=False,
            truncated_stderr=False,
        )
    elapsed = int((time.time() - t0) * 1000)
    stdout, t1 = _trunc(proc.stdout)
    stderr, t2 = _trunc(proc.stderr)
    return ExecResult(
        host=host,
        command=command,
        exit_code=proc.returncode,
        stdout=stdout,
        stderr=stderr,
        duration_ms=elapsed,
        truncated_stdout=t1,
        truncated_stderr=t2,
    )
