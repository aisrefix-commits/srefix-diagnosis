"""Virtual adapter for techs that don't have "clusters" in the traditional sense.

Two categories handled:

  ① Meta-agents (14): roles / processes — emit one virtual "skill registry"
     cluster per role so list_clusters() can surface them as available skills.

  ② Tools / runtimes (18): docker / podman / containerd / terraform / etc.
     For these, the "cluster" is your local install (or the K8s nodes that
     run them). We probe the local environment and emit a single Cluster
     describing what's installed/available.

Both can be enabled via env VIRTUAL_DISCOVERY_ENABLED=1, and individually
disabled via VIRTUAL_DISCOVERY_DISABLE=docker,helm,...
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Optional

from ..core.models import Cluster, Host

META_AGENTS = [
    "comms", "diagnosis", "mitigation", "postmortem", "secops", "sentinel",
    "slo-guardian", "toil-buster", "capacity-planner", "change-risk",
    "chaos-engineer", "deployment-engineer", "observability",
    "platform-engineer", "triage",
]

# Tools whose "discovery" is "is this binary present locally / what version".
LOCAL_TOOLS = [
    ("docker", "docker --version"),
    ("docker-compose", "docker compose version"),
    ("podman", "podman --version"),
    ("containerd", "containerd --version"),
    ("helm", "helm version --short"),
    ("terraform", "terraform version -json"),
    ("nomad", "nomad version"),
]

# Tools that exist as a managed-service / network endpoint flagged via env.
# (Concept: the "cluster" is the inventory of what's connected.)
NETWORK_TOOLS = [
    "google-cloud-load-balancer", "cloudmap", "externaldns",
    "openshift", "rancher",  # also have their own adapters; this is a fallback
    "knative", "openfaas",
    "nfs", "nlb", "vpc",
]


def build_meta_agent_cluster(role: str) -> Cluster:
    """A meta-agent is a role / process, not a deployable system.
    We emit a logical "skill registry" cluster so list_clusters() shows
    it's available, but with role='conceptual'."""
    cid = f"meta/{role}"
    return Cluster(
        id=cid, tech=role,
        hosts=[Host(
            fqdn=role, role="conceptual",
            tags={"category": "meta-agent",
                  "purpose": "role/process definition (not a deployable system)"},
            cluster_id=cid,
            health="conceptual",
        )],
        discovery_source="virtual",
        metadata={"category": "meta-agent",
                  "tech_confidence": "definitional",
                  "tech_signal": "virtual:meta-agent",
                  "note": "Use diag-{role}.diagnose_symptom for guidance."},
    )


def build_local_tool_cluster(tool: str, version: Optional[str],
                            installed: bool) -> Cluster:
    cid = f"local/{tool}"
    return Cluster(
        id=cid, tech=tool,
        version=version,
        hosts=[Host(
            fqdn=os.uname().nodename if hasattr(os, "uname") else "localhost",
            role="local-install",
            tags={"installed": str(installed),
                  "version": version or "unknown",
                  "binary_path": shutil.which(tool.split("-")[0]) or ""},
            cluster_id=cid,
            health="installed" if installed else "missing",
        )],
        discovery_source="virtual",
        metadata={"category": "local-tool",
                  "tech_confidence": "high" if installed else "low",
                  "tech_signal": f"local:{tool}"},
    )


def build_network_tool_cluster(tool: str) -> Cluster:
    """For network-tier tools without a separate adapter: emit a placeholder
    Cluster acknowledging it's a known concept, role='abstract'."""
    cid = f"abstract/{tool}"
    return Cluster(
        id=cid, tech=tool,
        hosts=[Host(
            fqdn=tool, role="abstract",
            tags={"category": "network-concept",
                  "discovery_note": (
                      "abstract concept; use cloud-specific adapter "
                      "(aws / gcp / azure) for actual inventory"
                  )},
            cluster_id=cid,
            health="abstract",
        )],
        discovery_source="virtual",
        metadata={"category": "abstract-concept",
                  "tech_confidence": "definitional",
                  "tech_signal": "virtual:abstract"},
    )


class VirtualAdapter:
    """Discovers meta-agents + local tools so all 250 .md files have at least
    one Cluster discoverable. Most return placeholder/conceptual clusters."""

    def __init__(self, disabled: Optional[set[str]] = None,
                 include_meta: bool = True,
                 include_local_tools: bool = True,
                 include_network_tools: bool = True):
        self.disabled = disabled or set()
        self.include_meta = include_meta
        self.include_local_tools = include_local_tools
        self.include_network_tools = include_network_tools

    @classmethod
    def from_env(cls) -> "VirtualAdapter":
        disabled = {x.strip() for x in
                    os.environ.get("VIRTUAL_DISCOVERY_DISABLE", "").split(",")
                    if x.strip()}
        # All sub-types on by default; opt-out individually
        return cls(
            disabled=disabled,
            include_meta="meta" not in disabled,
            include_local_tools="local-tools" not in disabled,
            include_network_tools="network-tools" not in disabled,
        )

    def discover(self, tech_filter: Optional[str] = None) -> list[Cluster]:
        clusters: list[Cluster] = []

        if self.include_meta:
            for role in META_AGENTS:
                if tech_filter and tech_filter != role:
                    continue
                clusters.append(build_meta_agent_cluster(role))

        if self.include_local_tools:
            for tool, cmd in LOCAL_TOOLS:
                if tool in self.disabled:
                    continue
                if tech_filter and tech_filter != tool:
                    continue
                version, installed = self._probe_local(cmd)
                clusters.append(build_local_tool_cluster(tool, version, installed))

        if self.include_network_tools:
            for tool in NETWORK_TOOLS:
                if tool in self.disabled:
                    continue
                if tech_filter and tech_filter != tool:
                    continue
                clusters.append(build_network_tool_cluster(tool))

        return clusters

    @staticmethod
    def _probe_local(cmd: str) -> tuple[Optional[str], bool]:
        bin_name = cmd.split()[0]
        if not shutil.which(bin_name):
            return None, False
        try:
            r = subprocess.run(cmd.split(), capture_output=True, timeout=5,
                              text=True)
            output = (r.stdout or r.stderr).strip()
            return output[:120] if output else None, r.returncode == 0
        except Exception:  # noqa
            return None, True  # binary exists but version probe failed
