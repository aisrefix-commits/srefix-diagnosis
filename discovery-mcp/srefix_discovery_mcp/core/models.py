from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class Host:
    fqdn: str
    address: Optional[str] = None
    port: Optional[int] = None
    role: str = "unknown"
    tags: dict = field(default_factory=dict)
    cluster_id: str = ""
    health: str = "unknown"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Cluster:
    id: str
    tech: str
    hosts: list[Host] = field(default_factory=list)
    version: Optional[str] = None
    discovery_source: str = "unknown"
    dependencies: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d
