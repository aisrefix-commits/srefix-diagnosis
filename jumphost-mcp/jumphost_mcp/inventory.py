"""Host + preset registries loaded from YAML files.

Inventory yaml ($JUMPHOST_INVENTORY):
    hosts:
      pg-prod-1:
        tags: {env: prod, tech: postgres, role: primary}
      pg-prod-2:
        tags: {env: prod, tech: postgres, role: replica}
      hbase-rs-001:
        tags: {env: prod, tech: hbase, role: regionserver}

Preset yaml ($JUMPHOST_PRESETS):
    postgres:
      pg-replication-status:
        description: Replication status from primary
        command: 'psql -At -c "SELECT pid, state, sent_lsn FROM pg_stat_replication"'
        allowed_roles: [primary]
        timeout: 10
"""
from __future__ import annotations

import os
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:  # PyYAML is optional; fall back to JSON if missing
    import yaml  # type: ignore
    _YAML_OK = True
except ImportError:  # pragma: no cover
    _YAML_OK = False


def _load_yaml(path: Path) -> dict:
    text = path.read_text()
    if _YAML_OK:
        return yaml.safe_load(text) or {}
    import json
    return json.loads(text)


@dataclass
class HostEntry:
    name: str
    tags: dict[str, str] = field(default_factory=dict)


@dataclass
class Preset:
    tech: str
    name: str
    command: str
    description: str = ""
    allowed_roles: list[str] = field(default_factory=list)
    allowed_args: list[str] = field(default_factory=list)
    timeout: int = 30


class Inventory:
    def __init__(self, hosts: dict[str, HostEntry]):
        self.hosts = hosts

    @classmethod
    def load(cls, path: Optional[str] = None) -> "Inventory":
        path = path or os.environ.get("JUMPHOST_INVENTORY")
        if not path or not Path(path).exists():
            return cls(hosts={})
        data = _load_yaml(Path(path))
        hosts: dict[str, HostEntry] = {}
        for name, body in (data.get("hosts") or {}).items():
            hosts[name] = HostEntry(
                name=name,
                tags={k: str(v) for k, v in (body.get("tags") or {}).items()},
            )
        return cls(hosts=hosts)

    def filter(self, tech: Optional[str] = None, role: Optional[str] = None,
               env: Optional[str] = None) -> list[HostEntry]:
        out: list[HostEntry] = []
        for h in self.hosts.values():
            if tech and h.tags.get("tech") != tech:
                continue
            if role and h.tags.get("role") != role:
                continue
            if env and h.tags.get("env") != env:
                continue
            out.append(h)
        return out

    def get(self, name: str) -> Optional[HostEntry]:
        return self.hosts.get(name)


class PresetRegistry:
    def __init__(self, presets: dict[tuple[str, str], Preset]):
        self.presets = presets

    @classmethod
    def load(cls, path: Optional[str] = None) -> "PresetRegistry":
        path = path or os.environ.get("JUMPHOST_PRESETS")
        if not path or not Path(path).exists():
            return cls(presets={})
        data = _load_yaml(Path(path))
        presets: dict[tuple[str, str], Preset] = {}
        for tech, body in (data or {}).items():
            for name, p in (body or {}).items():
                presets[(tech, name)] = Preset(
                    tech=tech,
                    name=name,
                    command=p["command"],
                    description=p.get("description", ""),
                    allowed_roles=p.get("allowed_roles") or [],
                    allowed_args=p.get("allowed_args") or [],
                    timeout=int(p.get("timeout", 30)),
                )
        return cls(presets=presets)

    def get(self, tech: str, name: str) -> Optional[Preset]:
        return self.presets.get((tech, name))

    def list_for_tech(self, tech: Optional[str] = None) -> list[Preset]:
        if tech:
            return [p for (t, _), p in self.presets.items() if t == tech]
        return list(self.presets.values())

    @staticmethod
    def render(preset: Preset, args: Optional[dict] = None) -> str:
        """Substitute {var} placeholders only for whitelisted args (allowed_args)."""
        args = args or {}
        if not preset.allowed_args:
            if args:
                raise ValueError(
                    f"preset '{preset.name}' has no allowed_args but received: {list(args)}"
                )
            return preset.command
        # Strict allowlist
        for k in args:
            if k not in preset.allowed_args:
                raise ValueError(f"preset '{preset.name}' does not allow arg '{k}'")
        # Sanitize values: only printable ascii, no shell metacharacters
        for k, v in args.items():
            sv = str(v)
            if any(c in sv for c in "`$;&|<>(){}\"'\\\n\r\t"):
                raise ValueError(f"arg '{k}' contains forbidden shell metacharacter")
        # Render via str.format with strict KeyError on unknown placeholders
        try:
            return preset.command.format(**{k: args.get(k, "") for k in preset.allowed_args})
        except KeyError as e:
            raise ValueError(f"preset '{preset.name}' missing required arg: {e}")
