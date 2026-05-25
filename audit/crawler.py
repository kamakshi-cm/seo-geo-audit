"""Sitemap discovery + page fetching."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup

USER_AGENT = "Mozilla/5.0 (compatible; SEO-GEO-Audit/1.0; +https://example.com/bot)"
TIMEOUT = 15


@dataclass
class FetchedPage:
    url: str
    status: int
    html: str = ""
    soup: BeautifulSoup | None = None
    error: str = ""
    response_time_ms: int = 0


@dataclass
class SiteContext:
    domain: str
    base_url: str
    robots_txt: str = ""
    llms_txt: str = ""
    llms_full_txt: str = ""
    sitemap_urls: list[str] = field(default_factory=list)
    discovered_urls: list[str] = field(default_factory=list)


def _http_get(url: str, timeout: int = TIMEOUT) -> requests.Response | None:
    try:
        return requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
            timeout=timeout,
            allow_redirects=True,
        )
    except requests.RequestException:
        return None


def normalize_domain(input_url: str) -> tuple[str, str]:
    """Return (domain, base_url) e.g. ('example.com', 'https://example.com')."""
    url = input_url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    domain = parsed.netloc
    base = f"{parsed.scheme}://{parsed.netloc}"
    return domain, base


def fetch_robots_and_llms(base_url: str) -> tuple[str, str, str, list[str]]:
    """Return (robots_txt, llms_txt, llms_full_txt, sitemap_urls_from_robots)."""
    robots = ""
    llms = ""
    llms_full = ""
    sitemaps: list[str] = []

    r = _http_get(urljoin(base_url, "/robots.txt"))
    if r is not None and r.status_code == 200:
        robots = r.text
        for line in robots.splitlines():
            if line.lower().startswith("sitemap:"):
                sitemaps.append(line.split(":", 1)[1].strip())

    l = _http_get(urljoin(base_url, "/llms.txt"))
    if l is not None and l.status_code == 200 and "<html" not in l.text.lower()[:500]:
        llms = l.text

    lf = _http_get(urljoin(base_url, "/llms-full.txt"))
    if lf is not None and lf.status_code == 200 and "<html" not in lf.text.lower()[:500]:
        llms_full = lf.text

    return robots, llms, llms_full, sitemaps


def _parse_sitemap_xml(xml_text: str) -> tuple[list[str], list[str]]:
    """Return (page_urls, nested_sitemap_urls)."""
    pages: list[str] = []
    nested: list[str] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return pages, nested

    tag = root.tag.lower()
    ns = ""
    if "}" in root.tag:
        ns = root.tag.split("}")[0] + "}"

    if "sitemapindex" in tag:
        for sm in root.findall(f"{ns}sitemap"):
            loc = sm.find(f"{ns}loc")
            if loc is not None and loc.text:
                nested.append(loc.text.strip())
    else:
        for url in root.findall(f"{ns}url"):
            loc = url.find(f"{ns}loc")
            if loc is not None and loc.text:
                pages.append(loc.text.strip())
    return pages, nested


def discover_sitemap_urls(base_url: str, hinted: list[str]) -> list[str]:
    """Try hinted sitemaps + common locations; return list of page URLs."""
    candidates = list(hinted) + [
        urljoin(base_url, "/sitemap.xml"),
        urljoin(base_url, "/sitemap_index.xml"),
        urljoin(base_url, "/sitemap-index.xml"),
        urljoin(base_url, "/sitemap1.xml"),
    ]
    seen_sm: set[str] = set()
    pages: list[str] = []
    queue: list[str] = []
    for c in candidates:
        if c not in seen_sm:
            seen_sm.add(c)
            queue.append(c)

    max_sitemaps = 10
    visited = 0
    while queue and visited < max_sitemaps:
        sm_url = queue.pop(0)
        visited += 1
        r = _http_get(sm_url)
        if r is None or r.status_code != 200:
            continue
        if "<" not in r.text[:200]:
            continue
        p, nested = _parse_sitemap_xml(r.text)
        pages.extend(p)
        for n in nested:
            if n not in seen_sm:
                seen_sm.add(n)
                queue.append(n)

    deduped: list[str] = []
    seen: set[str] = set()
    for u in pages:
        if u not in seen:
            seen.add(u)
            deduped.append(u)
    return deduped


def fetch_page(url: str) -> FetchedPage:
    import time
    t0 = time.time()
    try:
        r = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"},
            timeout=TIMEOUT,
            allow_redirects=True,
        )
        elapsed = int((time.time() - t0) * 1000)
        soup = BeautifulSoup(r.text, "lxml") if r.text else None
        return FetchedPage(
            url=url,
            status=r.status_code,
            html=r.text or "",
            soup=soup,
            response_time_ms=elapsed,
        )
    except requests.RequestException as e:
        return FetchedPage(url=url, status=0, error=str(e))


def build_site_context(input_url: str) -> SiteContext:
    domain, base = normalize_domain(input_url)
    robots, llms, llms_full, hinted_sm = fetch_robots_and_llms(base)
    pages = discover_sitemap_urls(base, hinted_sm)
    return SiteContext(
        domain=domain,
        base_url=base,
        robots_txt=robots,
        llms_txt=llms,
        llms_full_txt=llms_full,
        sitemap_urls=hinted_sm,
        discovered_urls=pages,
    )
