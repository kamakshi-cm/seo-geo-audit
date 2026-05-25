"""Generative Engine Optimization (GEO) checks at site + page level."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Known AI/LLM crawlers — agency-relevant 2025/2026
AI_CRAWLERS = {
    "GPTBot": "OpenAI (ChatGPT crawler for training)",
    "ChatGPT-User": "OpenAI (ChatGPT browsing on user prompt)",
    "OAI-SearchBot": "OpenAI (ChatGPT Search)",
    "ClaudeBot": "Anthropic (Claude crawler)",
    "Claude-Web": "Anthropic (Claude browsing)",
    "anthropic-ai": "Anthropic (legacy)",
    "PerplexityBot": "Perplexity AI",
    "Perplexity-User": "Perplexity (user-initiated)",
    "Google-Extended": "Google (Gemini / Bard training opt-out)",
    "Googlebot": "Google Search (also feeds AI Overviews)",
    "Bingbot": "Microsoft Bing (feeds Copilot)",
    "Applebot-Extended": "Apple Intelligence",
    "CCBot": "Common Crawl (training data for many LLMs)",
    "Meta-ExternalAgent": "Meta AI",
    "Bytespider": "ByteDance (Doubao)",
    "Amazonbot": "Amazon (Alexa/AI)",
    "DuckAssistBot": "DuckDuckGo AI",
}


@dataclass
class CrawlerStatus:
    name: str
    purpose: str
    allowed: bool
    rule: str = ""  # short human-readable rule


@dataclass
class GeoSiteAudit:
    domain: str
    has_llms_txt: bool = False
    has_llms_full_txt: bool = False
    llms_txt_length: int = 0
    has_robots_txt: bool = False
    crawlers: list[CrawlerStatus] = field(default_factory=list)
    blocked_count: int = 0
    allowed_count: int = 0
    ai_friendly_score: int = 0  # 0-100
    notes: list[str] = field(default_factory=list)


def _parse_robots(robots_txt: str) -> dict[str, list[tuple[str, str]]]:
    """Return {user_agent: [(directive, value), ...]} preserving order."""
    groups: dict[str, list[tuple[str, str]]] = {}
    current_agents: list[str] = []
    last_was_agent = False
    for raw in robots_txt.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip().lower()
        val = val.strip()
        if key == "user-agent":
            if not last_was_agent:
                current_agents = []
            current_agents.append(val)
            groups.setdefault(val, [])
            last_was_agent = True
        else:
            for a in current_agents:
                groups.setdefault(a, []).append((key, val))
            last_was_agent = False
    return groups


def _is_blocked(ua: str, groups: dict[str, list[tuple[str, str]]]) -> tuple[bool, str]:
    """Return (blocked, rule_summary). Blocked = explicit Disallow: / for this UA or *."""
    relevant_keys = [k for k in groups.keys() if k.lower() == ua.lower()]
    if not relevant_keys:
        # fall back to * group
        for k in groups:
            if k.strip() == "*":
                relevant_keys = [k]
                break
    if not relevant_keys:
        return False, "No rule (default allow)"
    for k in relevant_keys:
        for directive, value in groups[k]:
            if directive == "disallow" and value == "/":
                return True, f"Disallow: / for {k}"
            if directive == "allow" and value == "/":
                return False, f"Allow: / for {k}"
    # If there are Disallow rules but not the whole site
    for k in relevant_keys:
        partials = [v for d, v in groups[k] if d == "disallow" and v]
        if partials:
            return False, f"Partial restrictions on {k}: {', '.join(partials[:3])}"
    return False, f"Listed in robots ({relevant_keys[0]})"


def audit_geo_site(domain: str, robots_txt: str, llms_txt: str, llms_full_txt: str) -> GeoSiteAudit:
    res = GeoSiteAudit(domain=domain)
    res.has_robots_txt = bool(robots_txt.strip())
    res.has_llms_txt = bool(llms_txt.strip())
    res.has_llms_full_txt = bool(llms_full_txt.strip())
    res.llms_txt_length = len(llms_txt)

    if not res.has_robots_txt:
        res.notes.append("No robots.txt found — assuming all crawlers allowed by default.")

    groups = _parse_robots(robots_txt) if res.has_robots_txt else {}
    for ua, purpose in AI_CRAWLERS.items():
        blocked, rule = _is_blocked(ua, groups)
        res.crawlers.append(CrawlerStatus(name=ua, purpose=purpose, allowed=not blocked, rule=rule))
        if blocked:
            res.blocked_count += 1
        else:
            res.allowed_count += 1

    total = len(AI_CRAWLERS)
    # AI-friendliness: crawler allow ratio (70%) + llms.txt bonus (20%) + robots present (10%)
    crawler_ratio = res.allowed_count / total if total else 0
    score = 70 * crawler_ratio
    score += 15 if res.has_llms_txt else 0
    score += 5 if res.has_llms_full_txt else 0
    score += 10 if res.has_robots_txt else 0
    res.ai_friendly_score = min(100, int(round(score)))

    if not res.has_llms_txt:
        res.notes.append("No /llms.txt found — recommended emerging standard for guiding LLMs to key content.")
    if res.blocked_count:
        res.notes.append(f"{res.blocked_count} AI crawler(s) explicitly blocked — reduces citation surface in AI answers.")
    return res


# Page-level GEO signals (cheap heuristics, no LLM needed)
@dataclass
class GeoPageSignals:
    url: str
    has_faq_schema: bool = False
    has_article_schema: bool = False
    has_author: bool = False
    has_date: bool = False
    has_lists: bool = False
    has_tables: bool = False
    avg_paragraph_words: int = 0
    has_definition_pattern: bool = False  # "X is Y" sentences in first 200 words
    citation_ready_score: int = 0


def score_geo_page(url: str, soup) -> GeoPageSignals:
    g = GeoPageSignals(url=url)
    if soup is None:
        return g

    g.has_lists = bool(soup.find_all(["ul", "ol"]))
    g.has_tables = bool(soup.find_all("table"))

    paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
    paragraphs = [p for p in paragraphs if p]
    if paragraphs:
        words_total = sum(len(p.split()) for p in paragraphs)
        g.avg_paragraph_words = int(round(words_total / len(paragraphs)))

    first_chunk = " ".join(paragraphs[:3])[:1200] if paragraphs else ""
    if re.search(r"\b(is|are|refers to|means|defined as)\b", first_chunk, re.I):
        g.has_definition_pattern = True

    signals = [
        g.has_faq_schema or g.has_article_schema,
        g.has_author,
        g.has_date,
        g.has_lists,
        g.has_tables,
        g.has_definition_pattern,
        15 <= g.avg_paragraph_words <= 70,
    ]
    g.citation_ready_score = int(round(100 * sum(bool(s) for s in signals) / len(signals)))
    return g
