from __future__ import annotations

import re
from pathlib import Path

from mcp.server.fastmcp import FastMCP

AGENTS_DIR = Path(__file__).parent / "agents"


# ---------------------------------------------------------------------------
# Command classifier — used by extract_diagnostic_queries
# ---------------------------------------------------------------------------

_CLI_PREFIX_RULES: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"^\s*kubectl\b"), "kubectl", "jumphost-mcp"),
    (re.compile(r"^\s*helm\b"), "helm", "jumphost-mcp"),
    (re.compile(r"^\s*crictl\b"), "crictl", "jumphost-mcp"),
    (re.compile(r"^\s*docker\b"), "docker", "jumphost-mcp"),
    (re.compile(r"^\s*podman\b"), "podman", "jumphost-mcp"),
    (re.compile(r"^\s*nerdctl\b"), "nerdctl", "jumphost-mcp"),
    (re.compile(r"^\s*redis-cli\b"), "redis-cli", "jumphost-mcp"),
    (re.compile(r"^\s*psql\b"), "psql-cli", "jumphost-mcp"),
    (re.compile(r"^\s*mysql\b"), "mysql-cli", "jumphost-mcp"),
    (re.compile(r"^\s*mongo(sh)?\b"), "mongosh", "jumphost-mcp"),
    (re.compile(r"^\s*etcdctl\b"), "etcdctl", "jumphost-mcp"),
    (re.compile(r"^\s*aws\b"), "aws-cli", "jumphost-mcp"),
    (re.compile(r"^\s*gcloud\b"), "gcloud", "jumphost-mcp"),
    (re.compile(r"^\s*az\b"), "az-cli", "jumphost-mcp"),
    (re.compile(r"^\s*pscale\b"), "pscale", "jumphost-mcp"),
    (re.compile(r"^\s*influx\b"), "influx-cli", "jumphost-mcp"),
    (re.compile(r"^\s*nomad\b"), "nomad-cli", "jumphost-mcp"),
    (re.compile(r"^\s*consul\b"), "consul-cli", "jumphost-mcp"),
    (re.compile(r"^\s*vault\b"), "vault-cli", "jumphost-mcp"),
    (re.compile(r"^\s*systemctl\b"), "systemctl", "jumphost-mcp"),
    (re.compile(r"^\s*journalctl\b"), "journalctl", "jumphost-mcp"),
]

_SHELL_TOOLS_RE = re.compile(
    r"^\s*(iostat|vmstat|mpstat|sar|top|htop|atop|dstat|free|df|du|dmesg|netstat|ss|"
    r"tcpdump|ngrep|ip\b|route\b|arp\b|ping\b|mtr|dig|nslookup|host\b|curl|wget|"
    r"tail\b|head\b|grep|awk|sed|find|service\b|ls\b|lsof|ps\b|strace|ltrace|uptime|"
    r"uname|hostname|jq|yq|nc\b|telnet|openssl|ifconfig|ethtool|iptables|nft|"
    r"sysctl|fdisk|blkid|smartctl|nvme\b|fio\b|stress|stress-ng|perf\b|bpftrace|"
    r"strace|tcpconnect|ipvsadm|conntrack)\b"
)

_SQL_VERBS_RE = re.compile(
    r"^\s*(SELECT|INSERT|UPDATE|DELETE|SHOW|EXPLAIN|VACUUM|ANALYZE|COPY|CREATE|DROP|"
    r"GRANT|REVOKE|BEGIN|COMMIT|ROLLBACK|TRUNCATE|REINDEX|CLUSTER|CHECKPOINT|ALTER|"
    r"WITH|VALUES|CALL|EXECUTE|PREPARE|DEALLOCATE|LISTEN|UNLISTEN)\b",
    re.IGNORECASE,
)

_PROMQL_HINTS_RE = re.compile(
    r"(\brate\s*\(|\birate\s*\(|\bsum\s+by\b|\bavg\s+by\b|\bmax\s+by\b|"
    r"\bmin\s+by\b|\bsum_over_time\b|\bavg_over_time\b|\bhistogram_quantile\s*\(|"
    r"\bincrease\s*\(|\bdelta\s*\(|\bderiv\s*\(|\bpredict_linear\b|"
    r"\[\d+[smhdwy]\]|\bnode_[a-z_]+|\bcontainer_[a-z_]+|\bkube_[a-z_]+|"
    r"\bup\s*\{|\bgo_[a-z_]+|\bprocess_[a-z_]+)"
)

_LOGQL_HINTS_RE = re.compile(r"\{\s*[a-z_]+=\"[^\"]+\"[^}]*\}\s*\|")

_SQL_CONFIRM_RE = re.compile(r"\bFROM\b|\bWHERE\b|\bGROUP\s+BY\b|\bJOIN\b|;\s*$", re.IGNORECASE)

_LANG_HINT_TYPE: dict[str, tuple[str, str]] = {
    "promql": ("promql", "prometheus-mcp"),
    "logql": ("logql", "loki-mcp"),
    "sql": ("psql", "jumphost-mcp"),
    "psql": ("psql", "jumphost-mcp"),
    "shell": ("shell", "jumphost-mcp"),
    "bash": ("shell", "jumphost-mcp"),
    "sh": ("shell", "jumphost-mcp"),
    "console": ("shell", "jumphost-mcp"),
    "yaml": ("yaml-config", None),
    "json": ("json-data", None),
}


def _classify_command(cmd: str, lang_hint: str = "") -> tuple[str, str | None] | None:
    """Classify a command. Returns (type, suggested_mcp) or None to filter out."""
    s = cmd.strip()
    if len(s) < 5:
        return None
    if lang_hint and lang_hint in _LANG_HINT_TYPE:
        return _LANG_HINT_TYPE[lang_hint]

    # Filter obvious identifiers (single word, no spaces / parens / braces)
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.\-/]*", s):
        return None

    for pat, type_, mcp in _CLI_PREFIX_RULES:
        if pat.match(s):
            return type_, mcp

    if _SHELL_TOOLS_RE.match(s):
        return "shell", "jumphost-mcp"

    # SQL: must have a SQL verb AND another SQL keyword or terminating semicolon
    if _SQL_VERBS_RE.match(s) and _SQL_CONFIRM_RE.search(s):
        return "psql", "jumphost-mcp"

    # LogQL: stream selector with pipe filter
    if _LOGQL_HINTS_RE.search(s):
        return "logql", "loki-mcp"

    # PromQL: function/operator hints
    if _PROMQL_HINTS_RE.search(s):
        return "promql", "prometheus-mcp"

    return None


def _extract_queries(body: str, case_title: str) -> list[dict]:
    """Walk a markdown block, pull out backtick / fenced commands, classify each."""
    results: list[dict] = []
    lines = body.split("\n")

    in_fence = False
    fence_lang = ""
    fence_buf: list[str] = []
    fence_ctx = ""
    current_block = ""

    for i, line in enumerate(lines):
        bt = re.match(r"^\*\*([^*]+?):\*\*", line)
        if bt and not in_fence:
            current_block = bt.group(1).strip()

        fm = re.match(r"^\s*```(\w*)", line)
        if fm:
            if not in_fence:
                in_fence = True
                fence_lang = fm.group(1).lower()
                fence_buf = []
                # context = closest preceding non-empty non-fence line
                ctx = ""
                for j in range(i - 1, -1, -1):
                    if lines[j].strip() and not lines[j].lstrip().startswith("```"):
                        ctx = lines[j].strip()
                        break
                fence_ctx = ctx
            else:
                in_fence = False
                cmd = "\n".join(fence_buf).strip()
                if cmd:
                    # For multi-line fences, split by newline to classify each line separately
                    # for shell-style scripts; treat as single block for SQL/PromQL.
                    if fence_lang in ("bash", "sh", "shell", "console", ""):
                        for sub in cmd.split("\n"):
                            sub_s = sub.strip()
                            if not sub_s or sub_s.startswith("#"):
                                continue
                            cls = _classify_command(sub_s, fence_lang or "")
                            if cls:
                                type_, mcp = cls
                                results.append({
                                    "type": type_,
                                    "cmd": sub_s,
                                    "context": fence_ctx,
                                    "block": current_block,
                                    "case": case_title,
                                    "suggested_mcp": mcp,
                                })
                    else:
                        cls = _classify_command(cmd, fence_lang)
                        if cls:
                            type_, mcp = cls
                            results.append({
                                "type": type_,
                                "cmd": cmd,
                                "context": fence_ctx,
                                "block": current_block,
                                "case": case_title,
                                "suggested_mcp": mcp,
                            })
            continue
        if in_fence:
            fence_buf.append(line)
            continue

        for m in re.finditer(r"`([^`\n]+)`", line):
            cmd = m.group(1).strip()
            cls = _classify_command(cmd)
            if cls:
                type_, mcp = cls
                results.append({
                    "type": type_,
                    "cmd": cmd,
                    "context": line.strip(),
                    "block": current_block,
                    "case": case_title,
                    "suggested_mcp": mcp,
                })

    # Deduplicate by (type, cmd) preserving first occurrence (keeps best context)
    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for r in results:
        key = (r["type"], r["cmd"])
        if key not in seen:
            seen.add(key)
            deduped.append(r)
    return deduped


def _find_agent_file(tech: str) -> Path:
    for cand in (AGENTS_DIR / f"{tech}.md", AGENTS_DIR / f"{tech}-agent.md"):
        if cand.exists():
            return cand
    raise FileNotFoundError(f"No agent file for '{tech}' under {AGENTS_DIR}")


def _split_h2_sections(content: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {"_preamble": []}
    current = "_preamble"
    in_code = False
    for line in content.split("\n"):
        if line.lstrip().startswith("```"):
            in_code = not in_code
            sections[current].append(line)
            continue
        if not in_code:
            m = re.match(r"^## (.+?)\s*$", line)
            if m:
                current = m.group(1)
                sections[current] = [line]
                continue
        sections[current].append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items() if "\n".join(v).strip()}


def _split_h3_subsections(section_body: str) -> dict[str, str]:
    subs: dict[str, list[str]] = {"_intro": []}
    current = "_intro"
    in_code = False
    for line in section_body.split("\n"):
        if line.lstrip().startswith("```"):
            in_code = not in_code
            subs[current].append(line)
            continue
        if not in_code:
            m = re.match(r"^### (.+?)\s*$", line)
            if m:
                current = m.group(1)
                subs[current] = [line]
                continue
        subs[current].append(line)
    return {k: "\n".join(v).strip() for k, v in subs.items() if "\n".join(v).strip()}


def make_server(tech: str) -> FastMCP:
    md_path = _find_agent_file(tech)
    content = md_path.read_text()
    sections = _split_h2_sections(content)

    mcp = FastMCP(f"diag-{tech}")

    @mcp.tool()
    def list_sections() -> list[str]:
        """List H2 section titles available in this technology's diagnosis manual."""
        return [t for t in sections if t != "_preamble"]

    @mcp.tool()
    def get_section(title: str) -> str:
        """Return the markdown of a section by title. Falls back to case-insensitive substring match."""
        if title in sections:
            return sections[title]
        q = title.lower()
        matches = [t for t in sections if q in t.lower() and t != "_preamble"]
        if len(matches) == 1:
            return sections[matches[0]]
        if matches:
            return f"Multiple sections match '{title}':\n" + "\n".join(f"- {m}" for m in matches)
        return f"No section matched '{title}'. Call list_sections() to discover available titles."

    @mcp.tool()
    def list_subsections(section_title: str) -> list[str]:
        """List H3 subsection titles inside a given H2 section (e.g. individual scenarios or alerts)."""
        if section_title not in sections:
            return [f"Section '{section_title}' not found. Call list_sections()."]
        subs = _split_h3_subsections(sections[section_title])
        return [t for t in subs if t != "_intro"]

    @mcp.tool()
    def get_subsection(section_title: str, subsection_title: str) -> str:
        """Return one H3 subsection inside an H2 section."""
        if section_title not in sections:
            return f"Section '{section_title}' not found."
        subs = _split_h3_subsections(sections[section_title])
        if subsection_title in subs:
            return subs[subsection_title]
        q = subsection_title.lower()
        matches = [t for t in subs if q in t.lower() and t != "_intro"]
        if len(matches) == 1:
            return subs[matches[0]]
        if matches:
            return f"Multiple subsections match '{subsection_title}':\n" + "\n".join(f"- {m}" for m in matches)
        return f"No subsection matched '{subsection_title}'."

    @mcp.tool()
    def search(query: str, max_results: int = 10) -> list[dict]:
        """Substring-search across all sections; returns matching section titles with surrounding snippet."""
        q = query.lower()
        hits: list[dict] = []
        for title, body in sections.items():
            if title == "_preamble":
                continue
            lower_body = body.lower()
            idx = lower_body.find(q)
            if idx >= 0:
                start = max(0, idx - 80)
                end = min(len(body), idx + len(query) + 200)
                snippet = " ".join(body[start:end].split())
                hits.append({"section": title, "snippet": f"...{snippet}..."})
                if len(hits) >= max_results:
                    break
        return hits

    @mcp.tool()
    def diagnose_symptom(symptom: str, max_results: int = 5) -> list[dict]:
        """Find diagnosis blocks whose **Symptoms:** description matches the given symptom keywords."""
        q = symptom.lower()
        hits: list[dict] = []
        for title, body in sections.items():
            if title == "_preamble":
                continue
            for sub_title, sub_body in _split_h3_subsections(body).items():
                if sub_title == "_intro":
                    continue
                m = re.search(r"\*\*Symptoms?:\*\*\s*(.+?)(?=\n\*\*|\n###|\n##|\Z)", sub_body, re.DOTALL)
                if not m:
                    continue
                sympt_text = m.group(1).lower()
                if any(tok in sympt_text for tok in q.split()):
                    hits.append({
                        "section": title,
                        "subsection": sub_title,
                        "matched_symptom": " ".join(m.group(1).split())[:300],
                    })
                    if len(hits) >= max_results:
                        return hits
        return hits

    @mcp.tool()
    def extract_diagnostic_queries(case_or_section_title: str) -> list[dict]:
        """Extract executable diagnostic queries from a case or H2 section.

        Parses inline backtick commands and fenced code blocks, classifies each
        as one of: promql / logql / psql / kubectl / redis-cli / aws-cli /
        gcloud / az-cli / etcdctl / mongosh / pscale / shell / etc., and tags
        the suggested downstream MCP (prometheus-mcp / loki-mcp / jumphost-mcp).

        Use this so the LLM can route queries directly to the right telemetry
        MCP without re-parsing markdown.
        """
        body: str | None = None
        resolved_title = case_or_section_title

        if case_or_section_title in sections:
            body = sections[case_or_section_title]
        else:
            for section_title, section_body in sections.items():
                if section_title == "_preamble":
                    continue
                subs = _split_h3_subsections(section_body)
                if case_or_section_title in subs:
                    body = subs[case_or_section_title]
                    resolved_title = case_or_section_title
                    break

        if body is None:
            q = case_or_section_title.lower()
            for section_title, section_body in sections.items():
                if section_title == "_preamble":
                    continue
                if q in section_title.lower():
                    body = section_body
                    resolved_title = section_title
                    break
                for sub_title, sub_body in _split_h3_subsections(section_body).items():
                    if sub_title == "_intro":
                        continue
                    if q in sub_title.lower():
                        body = sub_body
                        resolved_title = sub_title
                        break
                if body is not None:
                    break

        if body is None:
            return []

        return _extract_queries(body, resolved_title)

    @mcp.resource(f"agent://{tech}/full")
    def full_manual() -> str:
        """Full diagnosis manual."""
        return content

    return mcp


def run(tech: str) -> None:
    make_server(tech).run()
