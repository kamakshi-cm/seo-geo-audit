"""Smoke test: run the full audit pipeline against a tiny site WITHOUT calling Claude.

This verifies:
- All modules import
- Crawler can fetch a real site
- SEO checks produce data
- GEO checks produce data
- PPT renders to disk
"""
from collections import Counter
from datetime import datetime
from pathlib import Path

from audit.crawler import build_site_context, fetch_page
from audit.seo_checks import audit_page
from audit.geo_checks import audit_geo_site, score_geo_page
from audit.claude_analyst import analyze
from report.ppt_builder import build_report


def main():
    target = "https://www.iana.org/help/example-domains"  # tiny, stable, sitemap-free
    print(f"Auditing: {target}")

    ctx = build_site_context(target)
    print(f"Domain: {ctx.domain}")
    print(f"robots.txt: {bool(ctx.robots_txt)}, llms.txt: {bool(ctx.llms_txt)}")
    print(f"Discovered URLs: {len(ctx.discovered_urls)}")

    urls = ctx.discovered_urls[:3] if ctx.discovered_urls else [ctx.base_url]
    print(f"Auditing {len(urls)} page(s)")

    page_audits = []
    geo_pages = []
    citation_scores = []
    for u in urls:
        fp = fetch_page(u)
        pa = audit_page(u, fp.status, fp.response_time_ms, fp.soup)
        page_audits.append(pa)
        gp = score_geo_page(u, fp.soup)
        gp.has_faq_schema = pa.has_faq_schema
        gp.has_article_schema = pa.has_article_schema
        gp.has_author = pa.has_author_byline
        gp.has_date = pa.has_date_published
        geo_pages.append(gp)
        citation_scores.append(gp.citation_ready_score)
        print(f"  - {u}  status={pa.status}  score={pa.score}  issues={len(pa.issues)}")

    geo = audit_geo_site(ctx.domain, ctx.robots_txt, ctx.llms_txt, ctx.llms_full_txt)
    print(f"GEO score: {geo.ai_friendly_score}  allowed={geo.allowed_count}/{len(geo.crawlers)}")

    n = len(page_audits)

    def pct(getter):
        return int(round(100 * sum(1 for p in page_audits if getter(p)) / n))

    seo_check_rates = [
        ("Title (30-60 chars)", pct(lambda p: p.title_ok)),
        ("Meta description", pct(lambda p: p.meta_description_ok)),
        ("Single H1", pct(lambda p: p.h1_ok)),
        ("Heading hierarchy", pct(lambda p: p.heading_hierarchy_ok)),
        ("Word count >=300", pct(lambda p: p.word_count_ok)),
        ("Canonical tag", pct(lambda p: p.canonical_ok)),
        ("Indexable", pct(lambda p: p.indexable)),
        ("Viewport meta", pct(lambda p: p.viewport_ok)),
        ("HTML lang", pct(lambda p: bool(p.lang_attr))),
        ("Images have alt", pct(lambda p: p.images_alt_ok)),
        ("Internal linking", pct(lambda p: p.internal_links > 0)),
        ("Structured data", pct(lambda p: p.has_schema)),
        ("Open Graph", pct(lambda p: p.has_open_graph)),
    ]

    seo_avg = int(round(sum(p.score for p in page_audits) / n))
    citation_avg = int(round(sum(citation_scores) / max(1, len(citation_scores))))

    # No API key -> claude_analyst returns a stub Insight
    insight = analyze({"domain": ctx.domain, "test": True}, api_key=None)
    print(f"Claude insight (no key): {insight.executive_summary[:60]}")

    top_issue_pages = sorted(
        [{"url": p.url, "issues": p.issues, "score": p.score} for p in page_audits],
        key=lambda r: (-len(r["issues"]), r["score"]),
    )[:10]
    crawlers = [{"name": c.name, "purpose": c.purpose, "allowed": c.allowed, "rule": c.rule} for c in geo.crawlers]

    ctx_payload = {
        "domain": ctx.domain,
        "pages_analyzed": n,
        "seo_avg_score": seo_avg,
        "geo_score": geo.ai_friendly_score,
        "perf_avg": 0,
        "citation_avg": citation_avg,
        "has_llms_txt": geo.has_llms_txt,
        "ai_allowed": geo.allowed_count,
        "ai_total": len(geo.crawlers),
        "seo_check_rates": seo_check_rates,
        "crawlers": crawlers,
        "pagespeed_results": [],
        "top_issue_pages": top_issue_pages,
        "insight": {
            "executive_summary": insight.executive_summary,
            "overall_verdict": insight.overall_verdict,
            "seo_strengths": ["Test strength 1", "Test strength 2"],
            "seo_weaknesses": ["Test weakness 1"],
            "geo_strengths": [],
            "geo_weaknesses": ["No llms.txt"],
            "recommendations": [
                {"priority": 1, "title": "Add llms.txt", "why": "Test", "action": "Test action"},
                {"priority": 2, "title": "Allow GPTBot", "why": "Test", "action": "Test action"},
            ],
        },
    }

    out_dir = Path("reports")
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"SMOKE_TEST_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pptx"
    build_report(str(out_path), ctx_payload)
    size = out_path.stat().st_size
    print(f"\nPPT generated: {out_path}  ({size:,} bytes)")
    print("OK")


if __name__ == "__main__":
    main()
