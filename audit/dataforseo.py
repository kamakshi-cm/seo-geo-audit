"""DataForSEO REST API client — auto-pulls competitor data when credentials are configured.

Uses base64 Basic Auth. Each method returns a dict with normalized fields the rest of
the app uses, or None on failure (so the app can fall back to manual paste).
Failures are recorded in LAST_ERRORS for UI surfacing.

DataForSEO docs: https://docs.dataforseo.com/v3/
"""
from __future__ import annotations

import base64
import math
import os
import threading
from dataclasses import dataclass, field
from typing import Any

import requests

BASE = "https://api.dataforseo.com/v3"
TIMEOUT = 60

# Thread-safe error log for the current run
_lock = threading.Lock()
LAST_ERRORS: list[str] = []


def reset_errors():
    with _lock:
        LAST_ERRORS.clear()


def _record_error(msg: str):
    with _lock:
        LAST_ERRORS.append(msg)


def _auth_header() -> dict[str, str] | None:
    login = os.getenv("DATAFORSEO_LOGIN", "").strip()
    pwd = os.getenv("DATAFORSEO_PASSWORD", "").strip()
    if not login or not pwd:
        return None
    token = base64.b64encode(f"{login}:{pwd}".encode()).decode()
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}


def is_configured() -> bool:
    return _auth_header() is not None


def _clean_target(target: str) -> str:
    target = (target or "").strip()
    for prefix in ("https://", "http://"):
        if target.startswith(prefix):
            target = target[len(prefix):]
    if target.startswith("www."):
        target = target[4:]
    return target.rstrip("/")


def _post(path: str, payload: list[dict]) -> dict | None:
    headers = _auth_header()
    if not headers:
        return None
    try:
        r = requests.post(f"{BASE}{path}", json=payload, headers=headers, timeout=TIMEOUT)
        if r.status_code != 200:
            _record_error(f"{path}: HTTP {r.status_code}")
            return None
        data = r.json()
        if data.get("status_code") != 20000:
            _record_error(f"{path}: API status {data.get('status_code')} {data.get('status_message', '')}")
            return None
        return data
    except requests.RequestException as e:
        _record_error(f"{path}: {e}")
        return None


def _first_result(resp: dict | None, path_for_error: str = "") -> dict | None:
    if not resp:
        return None
    tasks = resp.get("tasks") or []
    if not tasks:
        return None
    task = tasks[0]
    if task.get("status_code") != 20000:
        _record_error(f"{path_for_error}: task {task.get('status_code')} {task.get('status_message', '')}")
        return None
    result = task.get("result") or []
    if not result:
        return None
    return result[0]


def synthetic_authority(organic_keywords: int, organic_traffic: float) -> tuple[int, int]:
    """When Backlinks API is unavailable, approximate DR/DA from organic-traffic metrics.

    Empirically calibrated against known sites (e.g. cfostrategiesllc.com ~ DR22/DA25).
    Returns (dr, da) on 0-100 scale.
    """
    kw = max(organic_keywords or 0, 0)
    tr = max(organic_traffic or 0, 0)
    if kw == 0 and tr == 0:
        return 0, 0
    # Logarithmic blend: keywords + traffic
    raw = 5.5 * math.log10(max(kw, 1)) + 4.0 * math.log10(max(tr, 1))
    dr = max(0, min(100, int(round(raw))))
    da = max(0, min(100, dr + 1))  # Moz DA tends to track Ahrefs DR closely
    return dr, da


@dataclass
class DomainMetrics:
    target: str
    dr: int = 0  # synthesised from organic if backlinks subscription unavailable
    da: int = 0
    organic_keywords: int = 0
    organic_traffic: int = 0
    organic_traffic_value: float = 0.0
    backlinks: int = 0
    referring_domains: int = 0
    source_notes: list[str] = field(default_factory=list)


def domain_rank_overview(target: str) -> DomainMetrics | None:
    """Pull keywords + traffic + traffic-value + DR/DA-equivalent for a single domain."""
    target = _clean_target(target)
    if not target:
        return None
    path = "/dataforseo_labs/google/domain_rank_overview/live"
    payload = [{"target": target, "location_code": 2840, "language_code": "en"}]
    resp = _post(path, payload)
    r = _first_result(resp, path)
    if not r:
        return None
    items = r.get("items") or []
    if not items:
        _record_error(f"{path}: no items for {target}")
        return None
    item = items[0]
    metrics = (item.get("metrics") or {}).get("organic") or {}
    organic_keywords = int(metrics.get("count", 0) or 0)
    organic_traffic = float(metrics.get("etv", 0) or 0)
    organic_traffic_value = float(metrics.get("estimated_paid_traffic_cost", 0) or 0)

    # DR/DA synthesised from organic-traffic metrics (Backlinks API not used)
    dr, da = synthetic_authority(organic_keywords, organic_traffic)

    return DomainMetrics(
        target=target,
        dr=dr,
        da=da,
        organic_keywords=organic_keywords,
        organic_traffic=int(organic_traffic),
        organic_traffic_value=organic_traffic_value,
        backlinks=0,
        referring_domains=0,
        source_notes=["authority-synth-from-organic"],
    )


@dataclass
class RankedKeyword:
    keyword: str
    position: int
    url: str
    search_volume: int
    cpc: float
    traffic: int


def top_ranked_keywords(target: str, limit: int = 20) -> list[RankedKeyword]:
    """Top keywords driving organic traffic to a domain, sorted by traffic."""
    target = _clean_target(target)
    if not target:
        return []
    path = "/dataforseo_labs/google/ranked_keywords/live"
    payload = [{
        "target": target,
        "location_code": 2840,
        "language_code": "en",
        "limit": limit,
        "order_by": ["ranked_serp_element.serp_item.etv,desc"],
        "filters": [["ranked_serp_element.serp_item.rank_group", "<=", 30]],
    }]
    resp = _post(path, payload)
    r = _first_result(resp, path)
    if not r:
        return []
    out: list[RankedKeyword] = []
    for item in r.get("items", [])[:limit]:
        kw = item.get("keyword_data", {}) or {}
        info = kw.get("keyword_info", {}) or {}
        serp = item.get("ranked_serp_element", {}).get("serp_item", {}) or {}
        out.append(RankedKeyword(
            keyword=kw.get("keyword", ""),
            position=serp.get("rank_group", 0),
            url=serp.get("url", ""),
            search_volume=info.get("search_volume", 0) or 0,
            cpc=info.get("cpc", 0.0) or 0.0,
            traffic=int(serp.get("etv", 0) or 0),
        ))
    return out


def top_competitors(target: str, limit: int = 10) -> list[str]:
    """Return list of domains competing with `target` in organic search."""
    target = _clean_target(target)
    if not target:
        return []
    path = "/dataforseo_labs/google/competitors_domain/live"
    payload = [{
        "target": target,
        "location_code": 2840,
        "language_code": "en",
        "limit": limit,
    }]
    resp = _post(path, payload)
    r = _first_result(resp, path)
    if not r:
        return []
    return [it.get("domain", "") for it in r.get("items", []) if it.get("domain")]


def keyword_ideas(seed_keywords: list[str], limit: int = 30) -> list[dict]:
    """Return keyword ideas with search volume + difficulty."""
    if not seed_keywords:
        return []
    path = "/dataforseo_labs/google/keyword_ideas/live"
    payload = [{
        "keywords": seed_keywords[:5],
        "location_code": 2840,
        "language_code": "en",
        "limit": limit,
        "order_by": ["keyword_info.search_volume,desc"],
    }]
    resp = _post(path, payload)
    r = _first_result(resp, path)
    if not r:
        return []
    out = []
    for item in r.get("items", []):
        info = item.get("keyword_info", {}) or {}
        out.append({
            "keyword": item.get("keyword", ""),
            "search_volume": info.get("search_volume", 0) or 0,
            "cpc": info.get("cpc", 0.0) or 0.0,
            "competition": info.get("competition_level", ""),
        })
    return out


@dataclass
class BacklinkSample:
    source_url: str
    source_domain: str
    target_url: str
    rank: int = 0
    is_nofollow: bool = False


def top_backlink_samples(target: str, limit: int = 15) -> list[BacklinkSample]:
    """Return high-quality sample backlinks for a domain. Requires Backlinks API subscription."""
    target = _clean_target(target)
    if not target:
        return []
    path = "/backlinks/backlinks/live"
    payload = [{
        "target": target,
        "limit": limit,
        "order_by": ["rank,desc"],
        "filters": ["dofollow", "=", True],
    }]
    resp = _post(path, payload)
    r = _first_result(resp, path)
    if not r:
        return []
    out = []
    for it in r.get("items", []):
        out.append(BacklinkSample(
            source_url=it.get("url_from", ""),
            source_domain=it.get("domain_from", ""),
            target_url=it.get("url_to", ""),
            rank=it.get("rank", 0) or 0,
            is_nofollow=it.get("dofollow") is False,
        ))
    return out
