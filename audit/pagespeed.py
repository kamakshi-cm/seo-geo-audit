"""Google PageSpeed Insights (Lighthouse) lookups. Uses PAGESPEED_API_KEY env var if set."""
from __future__ import annotations

import os
from dataclasses import dataclass

import requests

PSI_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


@dataclass
class PageSpeedResult:
    url: str
    ok: bool = False
    performance: int = 0  # 0-100
    seo: int = 0  # 0-100
    lcp_ms: int = 0
    cls: float = 0.0
    inp_ms: int = 0
    tbt_ms: int = 0
    fcp_ms: int = 0
    error: str = ""


def _num(audit: dict) -> float:
    v = audit.get("numericValue")
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def pagespeed(url: str, strategy: str = "mobile", timeout: int = 60) -> PageSpeedResult:
    res = PageSpeedResult(url=url)
    try:
        params = {
            "url": url,
            "strategy": strategy,
            "category": ["performance", "seo"],
        }
        api_key = os.getenv("PAGESPEED_API_KEY", "").strip()
        if api_key:
            params["key"] = api_key
        r = requests.get(PSI_ENDPOINT, params=params, timeout=timeout)
        if r.status_code != 200:
            res.error = f"PSI HTTP {r.status_code}"
            return res
        data = r.json()
        lhr = data.get("lighthouseResult", {})
        cats = lhr.get("categories", {})
        res.performance = int(round((cats.get("performance", {}).get("score") or 0) * 100))
        res.seo = int(round((cats.get("seo", {}).get("score") or 0) * 100))

        audits = lhr.get("audits", {})
        res.lcp_ms = int(round(_num(audits.get("largest-contentful-paint", {}))))
        res.cls = round(_num(audits.get("cumulative-layout-shift", {})), 3)
        res.tbt_ms = int(round(_num(audits.get("total-blocking-time", {}))))
        res.fcp_ms = int(round(_num(audits.get("first-contentful-paint", {}))))

        # INP from CrUX field data when available
        loading = data.get("loadingExperience", {}).get("metrics", {})
        inp = loading.get("INTERACTION_TO_NEXT_PAINT", {}).get("percentile")
        if inp is not None:
            res.inp_ms = int(inp)

        res.ok = True
    except requests.RequestException as e:
        res.error = str(e)
    except (ValueError, KeyError) as e:
        res.error = f"Parse error: {e}"
    return res
