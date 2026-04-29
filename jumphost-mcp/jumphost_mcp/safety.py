"""Command safety filter for jumphost-mcp.

Three modes (env-controlled, applied to the `run` tool):
  preset_only          (default) — only run_safe(preset_id) is allowed.
  filtered_arbitrary   — `run` allowed but commands are checked against
                         a denylist of destructive patterns.
  unrestricted         — `run` accepts anything (use only with external approval).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# Destructive command patterns. Conservative — block if ANY matches.
_DENY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\brm\s+-[rRfF]"), "rm -r/-f"),
    (re.compile(r"\brm\s+-rf|\brm\s+-fr|\brm\s+-Rf|\brm\s+-fR"), "rm -rf"),
    (re.compile(r"\bDROP\s+(TABLE|DATABASE|SCHEMA|INDEX|VIEW|FUNCTION|USER)\b", re.I), "SQL DROP"),
    (re.compile(r"\bTRUNCATE\b", re.I), "SQL TRUNCATE"),
    (re.compile(r"\bDELETE\s+FROM\b", re.I), "SQL DELETE"),
    (re.compile(r"\bINSERT\s+INTO\b", re.I), "SQL INSERT"),
    (re.compile(r"\bUPDATE\b\s+\S+\s+SET\b", re.I), "SQL UPDATE"),
    (re.compile(r"\bALTER\s+(TABLE|DATABASE|USER|SCHEMA)\b", re.I), "SQL ALTER"),
    (re.compile(r"\bGRANT\b", re.I), "SQL GRANT"),
    (re.compile(r"\bREVOKE\b", re.I), "SQL REVOKE"),
    (re.compile(r"\bsystemctl\s+(stop|restart|disable|kill|reset-failed|mask)\b"), "systemctl mutating"),
    (re.compile(r"\bkill\s+-?(9|15|KILL|TERM|SIGKILL|SIGTERM)\b"), "kill signal"),
    (re.compile(r"\bpkill\b"), "pkill"),
    (re.compile(r"\bkubectl\s+(delete|apply|patch|create|edit|replace|scale|rollout|drain|cordon|taint|annotate|label)\b"), "kubectl mutating"),
    (re.compile(r"\bhelm\s+(install|upgrade|uninstall|rollback|delete)\b"), "helm mutating"),
    (re.compile(r"\bdocker\s+(rm|kill|stop|exec|run|build|push|pull|rmi)\b"), "docker mutating"),
    (re.compile(r"\bcrictl\s+(rm|stop|kill|exec)\b"), "crictl mutating"),
    (re.compile(r"\biptables\b"), "iptables"),
    (re.compile(r"\bnft\s+(add|delete|insert|replace)\b"), "nft mutating"),
    (re.compile(r"\bmkfs\."), "mkfs"),
    (re.compile(r"\bdd\b.*\bif=/dev"), "dd from /dev"),
    (re.compile(r"\b(reboot|shutdown|poweroff|halt|init\s+0|init\s+6)\b"), "reboot"),
    (re.compile(r"\bchmod\s+[0-7]{3,4}\s+/(?!tmp/|var/tmp/)"), "chmod outside /tmp"),
    (re.compile(r"\bchown\s+\S+\s+/(?!tmp/|var/tmp/)"), "chown outside /tmp"),
    # Command chaining + system writes
    (re.compile(r"&&"), "logical AND chain (&&)"),
    (re.compile(r"\|\|"), "logical OR chain (||)"),
    (re.compile(r";\s*\S"), "command separator (;)"),
    (re.compile(r">\s*/(?!tmp/|var/tmp/|dev/null)"), "redirect outside /tmp"),
    (re.compile(r">>\s*/(?!tmp/|var/tmp/|dev/null)"), "append outside /tmp"),
    # Substituting subshells can be exploited
    (re.compile(r"\$\("), "command substitution $(...)"),
    (re.compile(r"`[^`]*`"), "backtick command substitution"),
]


@dataclass
class SafetyResult:
    allowed: bool
    matched_pattern: Optional[str] = None
    reason: Optional[str] = None


def check_command(command: str) -> SafetyResult:
    """Return SafetyResult.allowed=False if command matches any deny pattern."""
    for pat, label in _DENY_PATTERNS:
        if pat.search(command):
            return SafetyResult(
                allowed=False,
                matched_pattern=label,
                reason=f"command contains destructive/risky pattern: {label}",
            )
    return SafetyResult(allowed=True)
