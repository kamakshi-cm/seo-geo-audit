"""Per-page SEO checks producing a structured audit row."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from bs4 import BeautifulSoup


@dataclass
class PageAudit:
    url: str
    status: int = 0
    response_time_ms: int = 0

    title: str = ""
    title_length: int = 0
    title_ok: bool = False

    meta_description: str = ""
    meta_description_length: int = 0
    meta_description_ok: bool = False

    h1_count: int = 0
    h1_text: str = ""
    h1_ok: bool = False

    heading_hierarchy_ok: bool = False
    heading_outline: list[tuple[str, str]] = field(default_factory=list)

    word_count: int = 0
    word_count_ok: bool = False

    canonical: str = ""
    canonical_ok: bool = False

    robots_meta: str = ""
    indexable: bool = True

    viewport_ok: bool = False
    lang_attr: str = ""

    images_total: int = 0
    images_missing_alt: int = 0
    images_alt_ok: bool = False

    internal_links: int = 0
    external_links: int = 0

    has_schema: bool = False
    schema_types: list[str] = field(default_factory=list)
    has_faq_schema: bool = False
    has_article_schema: bool = False

    has_open_graph: bool = False
    has_twitter_card: bool = False

    has_author_byline: bool = False
    has_date_published: bool = False

    issues: list[str] = field(default_factory=list)
    score: int = 0


_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _text_word_count(soup: BeautifulSoup) -> int:
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    return len(_WORD_RE.findall(text))


def _extract_jsonld_types(soup: BeautifulSoup) -> list[str]:
    types: list[str] = []
    for s in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = s.string or s.get_text() or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        items = data if isinstance(data, list) else [data]
        for it in items:
            if isinstance(it, dict):
                t = it.get("@type")
                if isinstance(t, list):
                    types.extend([str(x) for x in t])
                elif t:
                    types.append(str(t))
                graph = it.get("@graph")
                if isinstance(graph, list):
                    for g in graph:
                        if isinstance(g, dict):
                            gt = g.get("@type")
                            if isinstance(gt, list):
                                types.extend([str(x) for x in gt])
                            elif gt:
                                types.append(str(gt))
    return types


def _heading_hierarchy_ok(headings: list[tuple[str, str]]) -> bool:
    """No skipped levels (e.g. H1 → H3 without H2)."""
    if not headings:
        return False
    last_level = 0
    for tag, _ in headings:
        level = int(tag[1])
        if last_level and level > last_level + 1:
            return False
        last_level = level
    return any(t == "h1" for t, _ in headings)


def audit_page(url: str, status: int, response_ms: int, soup: BeautifulSoup | None) -> PageAudit:
    pa = PageAudit(url=url, status=status, response_time_ms=response_ms)

    if soup is None or status >= 400 or status == 0:
        pa.issues.append(f"Page unreachable or HTTP {status}")
        return pa

    # Title
    title_tag = soup.find("title")
    if title_tag and title_tag.string:
        pa.title = title_tag.string.strip()
        pa.title_length = len(pa.title)
        pa.title_ok = 30 <= pa.title_length <= 60
    if not pa.title:
        pa.issues.append("Missing <title>")
    elif not pa.title_ok:
        pa.issues.append(f"Title length {pa.title_length} (ideal 30–60)")

    # Meta description
    md = soup.find("meta", attrs={"name": "description"})
    if md and md.get("content"):
        pa.meta_description = md["content"].strip()
        pa.meta_description_length = len(pa.meta_description)
        pa.meta_description_ok = 70 <= pa.meta_description_length <= 160
    if not pa.meta_description:
        pa.issues.append("Missing meta description")
    elif not pa.meta_description_ok:
        pa.issues.append(f"Meta description length {pa.meta_description_length} (ideal 70–160)")

    # Headings
    headings: list[tuple[str, str]] = []
    for level in range(1, 7):
        for h in soup.find_all(f"h{level}"):
            txt = h.get_text(strip=True)
            headings.append((f"h{level}", txt))
    pa.heading_outline = headings
    h1s = [h for h in headings if h[0] == "h1"]
    pa.h1_count = len(h1s)
    if h1s:
        pa.h1_text = h1s[0][1]
    pa.h1_ok = pa.h1_count == 1
    if pa.h1_count == 0:
        pa.issues.append("No H1 tag")
    elif pa.h1_count > 1:
        pa.issues.append(f"Multiple H1 tags ({pa.h1_count})")
    pa.heading_hierarchy_ok = _heading_hierarchy_ok(headings)
    if not pa.heading_hierarchy_ok and headings:
        pa.issues.append("Heading hierarchy skips levels")

    # Word count
    pa.word_count = _text_word_count(soup)
    pa.word_count_ok = pa.word_count >= 300
    if not pa.word_count_ok:
        pa.issues.append(f"Thin content ({pa.word_count} words)")

    # Canonical
    canonical = soup.find("link", attrs={"rel": "canonical"})
    if canonical and canonical.get("href"):
        pa.canonical = canonical["href"].strip()
        pa.canonical_ok = True
    else:
        pa.issues.append("Missing canonical tag")

    # Robots meta
    rb = soup.find("meta", attrs={"name": "robots"})
    if rb and rb.get("content"):
        pa.robots_meta = rb["content"].strip().lower()
        if "noindex" in pa.robots_meta:
            pa.indexable = False
            pa.issues.append("Page is noindex")

    # Viewport + lang
    vp = soup.find("meta", attrs={"name": "viewport"})
    pa.viewport_ok = vp is not None and "width" in (vp.get("content") or "").lower()
    if not pa.viewport_ok:
        pa.issues.append("Missing/invalid viewport meta")
    html_tag = soup.find("html")
    if html_tag and html_tag.get("lang"):
        pa.lang_attr = html_tag["lang"].strip()
    if not pa.lang_attr:
        pa.issues.append("Missing <html lang>")

    # Images alt
    imgs = soup.find_all("img")
    pa.images_total = len(imgs)
    pa.images_missing_alt = sum(1 for i in imgs if not (i.get("alt") or "").strip())
    pa.images_alt_ok = pa.images_total == 0 or pa.images_missing_alt == 0
    if pa.images_missing_alt:
        pa.issues.append(f"{pa.images_missing_alt} image(s) missing alt")

    # Links
    page_host = urlparse(url).netloc
    internal = external = 0
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        if href.startswith("/") or page_host in href:
            internal += 1
        elif href.startswith(("http://", "https://")):
            external += 1
    pa.internal_links = internal
    pa.external_links = external
    if internal == 0:
        pa.issues.append("No internal links")

    # Schema
    schema_types = _extract_jsonld_types(soup)
    pa.schema_types = sorted(set(schema_types))
    pa.has_schema = bool(schema_types)
    pa.has_faq_schema = any("FAQ" in t for t in schema_types)
    pa.has_article_schema = any(t in {"Article", "NewsArticle", "BlogPosting"} for t in schema_types)
    if not pa.has_schema:
        pa.issues.append("No structured data (JSON-LD)")

    # Open Graph + Twitter Card
    pa.has_open_graph = bool(soup.find("meta", attrs={"property": "og:title"}))
    pa.has_twitter_card = bool(soup.find("meta", attrs={"name": "twitter:card"}))
    if not pa.has_open_graph:
        pa.issues.append("Missing Open Graph tags")

    # Author/date signals (GEO/E-E-A-T)
    author_indicators = ["author", "byline", "by-author"]
    pa.has_author_byline = bool(
        soup.find(attrs={"rel": "author"})
        or soup.find("meta", attrs={"name": "author"})
        or any(soup.find(class_=re.compile(ind, re.I)) for ind in author_indicators)
    )
    pa.has_date_published = bool(
        soup.find("meta", attrs={"property": "article:published_time"})
        or soup.find("time")
    )

    # Score: % of binary checks that pass
    checks = [
        pa.title_ok, pa.meta_description_ok, pa.h1_ok, pa.heading_hierarchy_ok,
        pa.word_count_ok, pa.canonical_ok, pa.indexable, pa.viewport_ok,
        bool(pa.lang_attr), pa.images_alt_ok, pa.internal_links > 0,
        pa.has_schema, pa.has_open_graph,
    ]
    pa.score = int(round(100 * sum(checks) / len(checks)))
    return pa
