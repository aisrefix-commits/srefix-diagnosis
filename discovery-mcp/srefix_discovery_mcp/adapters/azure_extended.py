"""Azure service builders — 13 additional services.

Output `tech` aligns with diag-{tech}.md filenames.
"""
from __future__ import annotations

from typing import Optional

from ..core.models import Cluster, Host


def _logical(sub_id: str, rg: str, tech: str, name: str,
             extra_tags: Optional[dict] = None,
             metadata: Optional[dict] = None) -> Cluster:
    cid = f"azure/{sub_id}/{rg}/{tech}/{name}"
    return Cluster(
        id=cid, tech=tech,
        hosts=[Host(fqdn=name, role="resource",
                    tags={"sub_id": sub_id, "rg": rg, **(extra_tags or {})},
                    cluster_id=cid)],
        discovery_source=f"azure-{tech.removeprefix('azure-')}",
        metadata={"sub_id": sub_id, "resource_group": rg,
                  "tech_confidence": "high", "tech_signal": tech,
                  **(metadata or {})},
    )


def _rg_from_id(rid: str) -> str:
    parts = (rid or "").split("/")
    if "resourceGroups" in parts:
        i = parts.index("resourceGroups")
        if i + 1 < len(parts):
            return parts[i + 1]
    return ""


def build_azure_function_app(app, sub_id: str) -> Cluster:
    d = app if isinstance(app, dict) else getattr(app, "as_dict", lambda: app.__dict__)()
    name = d.get("name", "func-unknown")
    rg = _rg_from_id(d.get("id", ""))
    return _logical(sub_id, rg, "azure-functions", name,
        extra_tags={"location": d.get("location", "")})


def build_azure_vm(vm, sub_id: str) -> Cluster:
    d = vm if isinstance(vm, dict) else getattr(vm, "as_dict", lambda: vm.__dict__)()
    name = d.get("name", "vm-unknown")
    rg = _rg_from_id(d.get("id", ""))
    return _logical(sub_id, rg, "azure-vm", name,
        extra_tags={"location": d.get("location", ""),
                    "vm_size": d.get("hardware_profile", {}).get("vm_size", "")})


def build_azure_vms_classified(vms, sub_id: str) -> list[Cluster]:
    """Tag-aware VM grouping using the Azure VM `tags` dict.

    Azure tags are case-sensitive but the classifier folds case anyway, so
    `Service=HBase`, `service=hbase`, `SERVICE=hbase` all classify the same.
    """
    from ._classify import group_instances_into_clusters, normalize_azure_tags
    items = [vm if isinstance(vm, dict)
             else getattr(vm, "as_dict", lambda: vm.__dict__)() for vm in (vms or [])]
    sample_rg = _rg_from_id(items[0].get("id", "")) if items else ""
    return group_instances_into_clusters(
        items,
        tag_extractor=lambda i: normalize_azure_tags(i.get("tags")),
        fqdn_extractor=lambda i: i.get("name", ""),
        instance_id_extractor=lambda i: i.get("name", "vm-unknown"),
        cluster_id_prefix=f"azure/{sub_id}/{sample_rg}",
        discovery_source="azure-vm-tagged",
        region=sample_rg, account=sub_id, default_tech="azure-vm",
        extra_host_tags=lambda i: {
            "location": i.get("location", ""),
            "vm_size": (i.get("hardware_profile") or {}).get("vm_size", ""),
            "rg": _rg_from_id(i.get("id", "")),
        },
    )


def build_azure_vnet(vnet, sub_id: str) -> Cluster:
    d = vnet if isinstance(vnet, dict) else getattr(vnet, "as_dict", lambda: vnet.__dict__)()
    name = d.get("name", "vnet-unknown")
    rg = _rg_from_id(d.get("id", ""))
    return _logical(sub_id, rg, "azure-vnet", name,
        extra_tags={"location": d.get("location", ""),
                    "address_space": ",".join(
                        d.get("address_space", {}).get("address_prefixes", []) or [])})


def build_azure_dns_zone(z, sub_id: str) -> Cluster:
    d = z if isinstance(z, dict) else getattr(z, "as_dict", lambda: z.__dict__)()
    name = d.get("name", "zone-unknown")
    rg = _rg_from_id(d.get("id", ""))
    return _logical(sub_id, rg, "azure-dns", name,
        metadata={"record_count": d.get("number_of_record_sets")})


def build_azure_service_bus_namespace(ns, sub_id: str) -> Cluster:
    d = ns if isinstance(ns, dict) else getattr(ns, "as_dict", lambda: ns.__dict__)()
    name = d.get("name", "sb-unknown")
    rg = _rg_from_id(d.get("id", ""))
    return _logical(sub_id, rg, "azure-service-bus", name,
        extra_tags={"sku": d.get("sku", {}).get("name", "")})


def build_azure_key_vault(kv, sub_id: str) -> Cluster:
    d = kv if isinstance(kv, dict) else getattr(kv, "as_dict", lambda: kv.__dict__)()
    name = d.get("name", "kv-unknown")
    rg = _rg_from_id(d.get("id", ""))
    return _logical(sub_id, rg, "azure-key-vault", name,
        extra_tags={"sku": d.get("properties", {}).get("sku", {}).get("name", ""),
                    "uri": d.get("properties", {}).get("vault_uri", "")})


def build_azure_app_gateway(ag, sub_id: str) -> Cluster:
    d = ag if isinstance(ag, dict) else getattr(ag, "as_dict", lambda: ag.__dict__)()
    name = d.get("name", "agw-unknown")
    rg = _rg_from_id(d.get("id", ""))
    return _logical(sub_id, rg, "azure-application-gateway", name,
        extra_tags={"sku": d.get("sku", {}).get("name", "")})


def build_azure_front_door(fd, sub_id: str) -> Cluster:
    d = fd if isinstance(fd, dict) else getattr(fd, "as_dict", lambda: fd.__dict__)()
    name = d.get("name", "fd-unknown")
    rg = _rg_from_id(d.get("id", ""))
    return _logical(sub_id, rg, "azure-front-door", name)


def build_azure_aks(c, sub_id: str) -> Cluster:
    d = c if isinstance(c, dict) else getattr(c, "as_dict", lambda: c.__dict__)()
    name = d.get("name", "aks-unknown")
    rg = _rg_from_id(d.get("id", ""))
    return _logical(sub_id, rg, "aks", name,
        extra_tags={"k8s_version": d.get("kubernetes_version", ""),
                    "node_count": str(sum(p.get("count", 0)
                                          for p in d.get("agent_pool_profiles", [])))})


def build_azure_traffic_manager(tm, sub_id: str) -> Cluster:
    d = tm if isinstance(tm, dict) else getattr(tm, "as_dict", lambda: tm.__dict__)()
    name = d.get("name", "tm-unknown")
    rg = _rg_from_id(d.get("id", ""))
    return _logical(sub_id, rg, "traffic-manager", name,
        extra_tags={"routing_method": d.get("traffic_routing_method", ""),
                    "fqdn": d.get("dns_config", {}).get("fqdn", "")})


def build_azure_logic_app(la, sub_id: str) -> Cluster:
    d = la if isinstance(la, dict) else getattr(la, "as_dict", lambda: la.__dict__)()
    name = d.get("name", "la-unknown")
    rg = _rg_from_id(d.get("id", ""))
    return _logical(sub_id, rg, "logic-apps", name,
        extra_tags={"state": d.get("state", "")})


def build_azure_acr(reg, sub_id: str) -> Cluster:
    d = reg if isinstance(reg, dict) else getattr(reg, "as_dict", lambda: reg.__dict__)()
    name = d.get("name", "acr-unknown")
    rg = _rg_from_id(d.get("id", ""))
    return _logical(sub_id, rg, "acr", name,
        extra_tags={"sku": d.get("sku", {}).get("name", ""),
                    "login_server": d.get("login_server", "")})


def build_azure_apim(apim, sub_id: str) -> Cluster:
    d = apim if isinstance(apim, dict) else getattr(apim, "as_dict", lambda: apim.__dict__)()
    name = d.get("name", "apim-unknown")
    rg = _rg_from_id(d.get("id", ""))
    return _logical(sub_id, rg, "api-management", name,
        extra_tags={"sku": d.get("sku", {}).get("name", "")})


def build_azure_app_config(ac, sub_id: str) -> Cluster:
    d = ac if isinstance(ac, dict) else getattr(ac, "as_dict", lambda: ac.__dict__)()
    name = d.get("name", "ac-unknown")
    rg = _rg_from_id(d.get("id", ""))
    return _logical(sub_id, rg, "app-config", name)


def build_azure_app_insights(ai, sub_id: str) -> Cluster:
    d = ai if isinstance(ai, dict) else getattr(ai, "as_dict", lambda: ai.__dict__)()
    name = d.get("name", "ai-unknown")
    rg = _rg_from_id(d.get("id", ""))
    return _logical(sub_id, rg, "application-insights", name)
