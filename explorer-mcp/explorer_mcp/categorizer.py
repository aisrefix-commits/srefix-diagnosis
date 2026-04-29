"""Map free-text symptoms to canonical exploration categories via keyword regex.

A single symptom can match multiple categories — we return the full set so the
plan covers every dimension (e.g. "slow + memory leak" → latency + memory).
"""
from __future__ import annotations

import re

# Canonical categories → list of regex patterns (case-insensitive).
# English uses \b word boundaries; Chinese terms are matched as substrings
# (CJK characters don't have ASCII word boundaries).
SYMPTOM_PATTERNS: dict[str, list[str]] = {
    "latency": [
        r"\bslow\b", r"\blag\b", r"\blat[ea]ncy\b", r"\btime[\s-]?out\b",
        r"\bp9[59]\b", r"\bdegrad", r"\bslowdown\b", r"\bhang(ing|s)?\b",
        r"\bstall\b", r"\bspike\b", r"\bhigh\s+rt\b",
        r"慢", r"延迟", r"超时", r"卡顿", r"卡住", r"抖动", r"毛刺",
    ],
    "errors": [
        r"\berror(s|ing)?\b", r"\bfail(ure|ing|s|ed)?\b", r"\b5\d\d\b",
        r"\bexception\b", r"\bcrash(es|ing|ed)?\b", r"\bpanic\b",
        r"\bfatal\b", r"\bsevere\b", r"\babort",
        r"错误", r"失败", r"异常", r"报错", r"崩溃",
    ],
    "down": [
        r"\bdown\b", r"\bunreachable\b", r"\bunavailable\b",
        r"\boff[\s-]?line\b", r"\bdead\b", r"\bnot\s+responding\b",
        r"\bmissing\b", r"\bdisappeared\b",
        r"宕机", r"挂了", r"挂掉", r"不可用", r"失联", r"离线", r"无响应",
    ],
    "memory": [
        r"\boom\b", r"\bmemory\b", r"\bleak(s|ing|ed)?\b",
        r"\bheap\b", r"\brss\b", r"\bgc\s+pause\b", r"\bevict",
        r"内存", r"堆栈?", r"泄[漏露]", r"溢出",
    ],
    "cpu": [
        r"\bcpu\b", r"\bload\s+(avg|average|high)\b",
        r"\bsaturat(ed|ion)\b", r"\bbusy\b", r"\bthrottl",
        r"负载", r"打满",
    ],
    "disk": [
        r"\bdisk\b", r"\bspace\b", r"\bwal\b", r"\bcheckpoint\b",
        r"\bfull\b", r"\binode\b", r"\bvolume\b", r"\bmount\b",
        r"\biops?\b", r"\bio\s*wait\b",
        r"磁盘", r"空间", r"写满", r"存储",
    ],
    "replication": [
        r"\breplicat", r"\bsync\b", r"\bsplit[\s-]brain\b",
        r"\bstandby\b", r"\breplica\b", r"\bfailover\b", r"\blagging\b",
        r"复制", r"同步", r"主从", r"切换", r"备库", r"副本", r"脑裂",
    ],
    "hot_query": [
        r"\bhot[\s_-]?key\b", r"\bslow[\s-]?quer", r"\bhot[\s_-]?partition\b",
        r"\bskew\b", r"\bn\+1\b", r"\bfull\s+scan\b",
        r"热点", r"慢查询", r"热分区", r"全表扫描",
    ],
    "config_change": [
        r"\bdeploy(ed|ment|ing)?\b", r"\brollout\b", r"\bchange\b",
        r"\bconfig\b", r"\brelease\b", r"\bmigration\b",
        r"发布", r"上线", r"变更", r"配置", r"灰度", r"回滚",
    ],
    "network": [
        r"\bnetwork\b", r"\bdns\b", r"\btls\b", r"\bcert(ificate)?\b",
        r"\bconnect(ion|ivity)?\b", r"\brefused\b", r"\breset\b",
        r"\bproxy\b", r"\brouting\b", r"\bhandshake\b",
        r"网络", r"连接", r"证书", r"路由", r"丢包", r"重传",
    ],
    "saturation": [
        r"\bcapacity\b", r"\bquota\b", r"\blimit\b", r"\bexhaust",
        r"\bback[\s-]?press", r"\bqueue\b", r"\bbacklog\b",
        r"队列", r"积压", r"限流", r"限速", r"配额", r"打爆",
    ],
    "data_consistency": [
        r"\bcorrupt", r"\binconsistent\b", r"\bmismatch\b",
        r"\bchecksum\b", r"\bdiverg", r"\bstale\b",
        r"损坏", r"不一致", r"校验",
    ],
    "security": [
        r"\bauth(entication|orization)?\b", r"\bunauthor(ized|ised)\b",
        r"\bforbidden\b", r"\b401\b", r"\b403\b", r"\baccess\s+denied\b",
        r"\bcredential\b", r"\btoken\b", r"\bexpired\b",
        r"鉴权", r"认证", r"无权限", r"过期", r"凭证",
    ],
}

_COMPILED = {cat: [re.compile(p, re.IGNORECASE) for p in pats]
             for cat, pats in SYMPTOM_PATTERNS.items()}


def categorize(symptom: str) -> list[str]:
    """Return matching category names (multiple may match).

    Always returns at least ['unknown'] if no patterns hit, so callers always
    have something to map to.
    """
    text = symptom or ""
    matches: list[str] = []
    for cat, patterns in _COMPILED.items():
        if any(p.search(text) for p in patterns):
            matches.append(cat)
    return matches or ["unknown"]
