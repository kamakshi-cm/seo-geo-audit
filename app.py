"""Streamlit UI for the comprehensive SEO + GEO Strategy & Audit tool."""
from __future__ import annotations

import json
import os
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from audit.crawler import build_site_context, fetch_page
from audit.seo_checks import audit_page
from audit.geo_checks import audit_geo_site, score_geo_page
from audit.pagespeed import pagespeed
from audit.claude_analyst import generate_all
from audit import dataforseo
from report.ppt_builder import build_report

load_dotenv()

# When deployed on Streamlit Community Cloud, secrets come through st.secrets.
# Copy them into env vars so the rest of the app (dataforseo.py, claude_analyst.py, etc.) finds them.
try:
    for _k, _v in st.secrets.items():
        if isinstance(_v, str):
            os.environ.setdefault(_k, _v)
except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError, Exception):
    pass

st.set_page_config(page_title="SEO + GEO Strategy", page_icon="🚀", layout="wide")

st.markdown("""
<style>
.block-container { padding-top: 1.2rem; }
h1 { color: #0B1F3A; }
.stProgress > div > div > div > div { background-color: #14B8A6; }
[data-testid="stTabs"] button[role="tab"] { font-weight: 600; }
</style>
""", unsafe_allow_html=True)

st.title("SEO + GEO Strategy & Audit")
st.caption("Full strategic deck — competitor analysis, keyword research, content silos, backlink strategy, GMB, technical SEO, and Generative Engine Optimization. Exports to PowerPoint.")

# ---------- SIDEBAR ----------
with st.sidebar:
    st.header("Configuration")
    claude_key = st.text_input(
        "Gemini API key",
        type="password",
        value=os.getenv("GEMINI_API_KEY", "") or os.getenv("GOOGLE_API_KEY", ""),
        help="Get a free key at https://aistudio.google.com/apikey",
    )
    st.divider()

    psi_configured = bool(os.getenv("PAGESPEED_API_KEY", "").strip())
    dfs_configured = dataforseo.is_configured()
    st.markdown(f"**PageSpeed API**: {'✓ configured' if psi_configured else '○ free quota only'}")
    st.markdown(f"**DataForSEO API**: {'✓ configured (auto-pull on)' if dfs_configured else '○ not set — using manual paste'}")
    st.caption("Set keys in .env to enable.")
    st.divider()

    model_choice = st.selectbox(
        "Gemini model",
        ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-lite"],
        index=0,
        help="Flash = fast & free-tier friendly (10 RPM). Pro = better quality but only 5 RPM free. Flash-lite = highest free RPM.",
    )
    max_pages = st.slider("Max pages to crawl", 5, 100, 25, 5)
    psi_sample = st.slider("PageSpeed sample size", 0, 10, 5)
    num_silos = st.slider("Number of content silos", 4, 10, 8)
    blogs_per_silo = st.slider("Blogs per silo", 5, 12, 10)


# ---------- INPUT TABS ----------
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "1. Your Site",
    "2. Competitors",
    "3. Top Pages (optional)",
    "4. GMB Notes",
    "5. Run Audit",
])

with tab1:
    st.subheader("Your site & business context")
    your_domain = st.text_input("Your domain or homepage URL", placeholder="example.com")
    niche = st.text_input(
        "Niche / industry (1 short phrase)",
        placeholder="e.g. 'CPA firm in New Jersey serving SMBs'",
        help="Helps Claude generate keyword research and silos accurate to your industry.",
    )
    st.divider()
    st.markdown("**Your site's metrics** — paste from Ahrefs / Semrush. Leave blank to auto-pull via DataForSEO if configured.")
    c1, c2, c3 = st.columns(3)
    your_dr = c1.number_input("Domain Rating (DR)", min_value=0, max_value=100, value=0)
    your_da = c2.number_input("Domain Authority (DA)", min_value=0, max_value=100, value=0)
    your_keywords = c3.number_input("Ranking keywords", min_value=0, value=0)
    c4, c5, c6 = st.columns(3)
    your_traffic = c4.number_input("Organic traffic / mo", min_value=0, value=0)
    your_traffic_value = c5.number_input("Traffic value (USD)", min_value=0, value=0)
    your_backlinks = c6.number_input("Total backlinks", min_value=0, value=0)
    your_ref_domains = st.number_input("Referring domains", min_value=0, value=0)


with tab2:
    st.subheader("Competitors")
    st.caption("Add 3–5 competitors. The same metrics for each. Tool will auto-pull via DataForSEO if configured.")
    n_competitors = st.number_input("How many competitors?", min_value=1, max_value=6, value=3)
    competitor_inputs = []
    for i in range(int(n_competitors)):
        with st.expander(f"Competitor {i + 1}", expanded=i < 2):
            cdomain = st.text_input(f"Domain", key=f"cdomain_{i}", placeholder="competitor.com")
            c1, c2, c3 = st.columns(3)
            cdr = c1.number_input(f"DR", min_value=0, max_value=100, value=0, key=f"cdr_{i}")
            cda = c2.number_input(f"DA", min_value=0, max_value=100, value=0, key=f"cda_{i}")
            ckw = c3.number_input(f"Keywords", min_value=0, value=0, key=f"ckw_{i}")
            c4, c5, c6 = st.columns(3)
            ctraffic = c4.number_input(f"Organic traffic", min_value=0, value=0, key=f"ctraffic_{i}")
            ctv = c5.number_input(f"Traffic value (USD)", min_value=0, value=0, key=f"ctv_{i}")
            cbl = c6.number_input(f"Backlinks", min_value=0, value=0, key=f"cbl_{i}")
            crd = st.number_input(f"Referring domains", min_value=0, value=0, key=f"crd_{i}")
            competitor_inputs.append({
                "domain": cdomain, "dr": cdr, "da": cda,
                "ranking_keywords": ckw, "organic_traffic": ctraffic, "traffic_value": ctv,
                "backlinks": cbl, "referring_domains": crd,
            })


with tab3:
    st.subheader("Top traffic-driving pages (optional but recommended)")
    st.caption("Paste 3–6 top pages per site as URL → primary keyword. One per line, format: `URL | keyword`. Auto-pulled via DataForSEO if configured.")
    your_top_pages_raw = st.text_area(
        "Your site's top pages (URL | keyword per line)",
        height=120,
        placeholder="https://yoursite.com/services/x | service keyword\nhttps://yoursite.com/blog/post | blog keyword",
    )
    competitor_top_raw = {}
    for i, c in enumerate(competitor_inputs):
        if c["domain"]:
            competitor_top_raw[c["domain"]] = st.text_area(
                f"{c['domain']} top pages",
                key=f"comp_top_{i}",
                height=100,
                placeholder=f"https://{c['domain']}/page | keyword",
            )

    competitor_backlink_raw = {}

    st.divider()
    st.subheader("Competitor top blog keywords (optional)")
    st.caption("Helps Claude propose unique content silos that don't duplicate competitor topics. One keyword per line.")
    competitor_blog_keywords_raw = st.text_area(
        "Top blog topics across competitors",
        height=100,
        placeholder="construction in progress accounting\ntax accountant vs CPA\nUBIA of qualified property",
    )


with tab4:
    st.subheader("Your current GMB status (free-form notes)")
    st.caption("What's already on your Google Business Profile? Categories, services, photos, posts, reviews? Anything you want Claude to factor in.")
    your_gmb_notes = st.text_area(
        "Notes",
        height=180,
        placeholder=(
            "Categories: Accountant, Tax preparation service\n"
            "Description: Outdated, 200 chars, no keywords\n"
            "Photos: 3 photos, last uploaded 2024\n"
            "Posts: None in last 6 months\n"
            "Reviews: 8 reviews, 4.6 avg, no owner responses\n"
            "Q&A: 0 questions seeded\n"
            "Service areas: New Jersey only, missing nearby cities"
        ),
    )


with tab5:
    st.subheader("Ready to generate the strategy deck")
    st.markdown("""
This will:
1. Crawl your site (sitemap → up to N pages)
2. Run technical SEO + GEO checks
3. Sample PageSpeed Core Web Vitals
4. Auto-pull competitor metrics via DataForSEO (if configured) for any blank fields
5. Send everything to Gemini — parallel calls — for scope, DR analysis, traffic analysis, keyword map, navigation, content silos, backlink strategy, GMB audit, technical summary, and detailed GEO
6. Build a 30+ slide PowerPoint deck

Expected runtime: **2–5 minutes**.
""")
    run = st.button("Generate Full Strategy Deck", type="primary", width="stretch")


# ---------- HELPERS ----------
def parse_pages_lines(raw: str) -> list[dict]:
    pages = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        url, kw = line.split("|", 1)
        pages.append({"url": url.strip(), "keyword": kw.strip()})
    return pages


def parse_backlink_lines(raw: str) -> list[dict]:
    out = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if "|" in line:
            url, comment = line.split("|", 1)
            out.append({"url": url.strip(), "comment": comment.strip()})
        else:
            out.append({"url": line, "comment": ""})
    return out


def _apply_metrics(target_dict: dict, m) -> None:
    """Copy DomainMetrics fields into a flat dict, only filling blanks."""
    target_dict["dr"] = target_dict.get("dr") or m.dr
    target_dict["da"] = target_dict.get("da") or m.da
    target_dict["ranking_keywords"] = target_dict.get("ranking_keywords") or m.organic_keywords
    target_dict["organic_traffic"] = target_dict.get("organic_traffic") or m.organic_traffic
    target_dict["traffic_value"] = target_dict.get("traffic_value") or int(m.organic_traffic_value)
    target_dict["backlinks"] = target_dict.get("backlinks") or m.backlinks
    target_dict["referring_domains"] = target_dict.get("referring_domains") or m.referring_domains
    target_dict["_source_notes"] = m.source_notes


def autofill_from_dataforseo(your_domain: str, your_metrics: dict, competitors: list[dict],
                              log_fn=None) -> tuple[dict, list[dict]]:
    """Fill in 0-valued metrics from DataForSEO when available. Logs per-domain results."""
    if not dataforseo.is_configured():
        return your_metrics, competitors

    def _log(msg: str):
        if log_fn:
            log_fn(msg)

    targets = [(your_domain, your_metrics, True)] + [(c["domain"], c, False) for c in competitors if c.get("domain")]
    for domain, bucket, is_yours in targets:
        if not domain:
            continue
        m = dataforseo.domain_rank_overview(domain)
        if m is None:
            _log(f"  ⚠ Could not pull data for {domain} (see warnings below)")
            continue
        _apply_metrics(bucket, m)
        label = "you" if is_yours else "competitor"
        _log(f"  ✓ {domain} ({label}): {m.organic_keywords:,} kw • {m.organic_traffic:,} traffic • ${m.organic_traffic_value:,.0f} value • DR~{m.dr}/DA~{m.da} [{'+'.join(m.source_notes)}]")
    return your_metrics, competitors


def autofill_top_pages(domain: str, existing: list[dict]) -> list[dict]:
    if existing or not dataforseo.is_configured() or not domain:
        return existing
    kws = dataforseo.top_ranked_keywords(domain, limit=6)
    return [{"url": k.url, "keyword": k.keyword} for k in kws if k.url]


def autofill_backlinks(domain: str, existing: list[dict]) -> list[dict]:
    if existing or not dataforseo.is_configured() or not domain:
        return existing
    samples = dataforseo.top_backlink_samples(domain, limit=6)
    return [{"url": s.source_url, "comment": f"Rank {s.rank}, from {s.source_domain}"} for s in samples]


# ---------- RUN ----------
if run:
    if not your_domain.strip():
        st.error("Please enter your domain in Tab 1.")
        st.stop()
    if not claude_key.strip():
        st.error("Please add your Gemini API key in the sidebar.")
        st.stop()

    os.environ["GEMINI_API_KEY"] = claude_key.strip()

    overall = st.progress(0, text="Starting audit…")
    log_box = st.empty()
    logs: list[str] = []

    def log(msg: str):
        logs.append(f"`{datetime.now().strftime('%H:%M:%S')}`  {msg}")
        log_box.markdown("\n\n".join(logs[-15:]))

    t_start = time.time()

    # ---- Step 1: Build your site metrics ----
    your_metrics = {
        "domain": your_domain.strip(),
        "dr": your_dr, "da": your_da,
        "ranking_keywords": your_keywords, "organic_traffic": your_traffic,
        "traffic_value": your_traffic_value, "backlinks": your_backlinks,
        "referring_domains": your_ref_domains,
    }
    competitors_metrics = [c for c in competitor_inputs if c["domain"]]

    if dataforseo.is_configured():
        log("DataForSEO configured — auto-filling missing metrics…")
        dataforseo.reset_errors()
        your_metrics, competitors_metrics = autofill_from_dataforseo(your_domain, your_metrics, competitors_metrics, log_fn=log)
        if dataforseo.LAST_ERRORS:
            log("**DataForSEO warnings**:")
            for err in dataforseo.LAST_ERRORS[:8]:
                log(f"  • {err}")
            if any("backlinks" in e.lower() for e in dataforseo.LAST_ERRORS):
                log("  ℹ Your DataForSEO plan doesn't include the Backlinks API. DR/DA shown are estimated from organic metrics. To get exact DR + real backlink counts, activate the **Backlinks API** at app.dataforseo.com/api/dashboard.")

    overall.progress(8, text="Discovering site & sitemap…")
    ctx_site = build_site_context(your_domain)
    log(f"Resolved: **{ctx_site.domain}**  •  robots.txt: {'yes' if ctx_site.robots_txt else 'no'}  •  llms.txt: {'yes' if ctx_site.llms_txt else 'no'}")
    log(f"Discovered **{len(ctx_site.discovered_urls)}** URLs from sitemap(s).")

    geo_site = audit_geo_site(ctx_site.domain, ctx_site.robots_txt, ctx_site.llms_txt, ctx_site.llms_full_txt)
    log(f"AI-friendly score: **{geo_site.ai_friendly_score}/100**  ({geo_site.allowed_count}/{len(geo_site.crawlers)} AI crawlers allowed)")

    urls = ctx_site.discovered_urls[:max_pages] or [ctx_site.base_url]
    overall.progress(15, text=f"Auditing {len(urls)} pages…")
    page_audits = []
    citation_scores = []
    service_pages_hints = []
    existing_top_keywords = []

    for i, url in enumerate(urls):
        fp = fetch_page(url)
        pa = audit_page(url, fp.status, fp.response_time_ms, fp.soup)
        page_audits.append(pa)
        gp = score_geo_page(url, fp.soup)
        gp.has_faq_schema = pa.has_faq_schema
        gp.has_article_schema = pa.has_article_schema
        gp.has_author = pa.has_author_byline
        gp.has_date = pa.has_date_published
        signals = [
            gp.has_faq_schema or gp.has_article_schema,
            gp.has_author, gp.has_date, gp.has_lists, gp.has_tables,
            gp.has_definition_pattern, 15 <= gp.avg_paragraph_words <= 70,
        ]
        gp.citation_ready_score = int(round(100 * sum(bool(s) for s in signals) / len(signals)))
        citation_scores.append(gp.citation_ready_score)

        # Heuristic: service-page-looking URLs
        if any(t in url.lower() for t in ["/services/", "/service/", "/solutions/", "/products/"]):
            if pa.title:
                service_pages_hints.append(pa.title)

        if i % 4 == 0 or i == len(urls) - 1:
            pct = 15 + int(35 * (i + 1) / len(urls))
            overall.progress(pct, text=f"Auditing {i + 1}/{len(urls)}: {url[:80]}")

    log(f"Audited {len(page_audits)} pages. Avg SEO score: **{round(sum(p.score for p in page_audits) / len(page_audits))}/100**")

    # ---- PageSpeed sample ----
    psi_results = []
    if psi_sample > 0:
        overall.progress(52, text=f"Running PageSpeed Insights on {min(psi_sample, len(urls))} pages…")
        sampled = urls[:psi_sample]
        for i, u in enumerate(sampled):
            log(f"PSI {i + 1}/{len(sampled)}: {u[:80]}")
            r = pagespeed(u)
            if r.ok:
                psi_results.append({
                    "url": r.url, "performance": r.performance, "seo": r.seo,
                    "lcp_ms": r.lcp_ms, "cls": r.cls, "inp_ms": r.inp_ms,
                })
            elif r.error:
                log(f"  PSI error: {r.error}")
            pct = 52 + int(18 * (i + 1) / len(sampled))
            overall.progress(pct, text=f"PSI {i + 1}/{len(sampled)}")

    # ---- Top pages auto-fill via DataForSEO ----
    your_top_pages = parse_pages_lines(your_top_pages_raw)
    if not your_top_pages and dataforseo.is_configured():
        log("Auto-pulling your top pages from DataForSEO…")
        your_top_pages = autofill_top_pages(your_domain, your_top_pages)
    if not your_top_pages:
        your_top_pages = [{"url": p.url, "keyword": ""} for p in sorted(page_audits, key=lambda x: -x.word_count)[:5]]

    competitor_top_pages = {}
    for c in competitors_metrics:
        d = c["domain"]
        pages = parse_pages_lines(competitor_top_raw.get(d, ""))
        if not pages:
            pages = autofill_top_pages(d, pages)
        competitor_top_pages[d] = pages

    competitor_backlink_samples = {}
    for c in competitors_metrics:
        d = c["domain"]
        bls = parse_backlink_lines(competitor_backlink_raw.get(d, ""))
        if not bls:
            bls = autofill_backlinks(d, bls)
        competitor_backlink_samples[d] = bls

    competitor_blog_kws = [k.strip() for k in (competitor_blog_keywords_raw or "").splitlines() if k.strip()]

    # ---- Aggregates ----
    n = len(page_audits)
    def pct(getter):
        return int(round(100 * sum(1 for p in page_audits if getter(p)) / n))

    seo_check_rates = [
        ("Title (30–60 chars)", pct(lambda p: p.title_ok)),
        ("Meta description", pct(lambda p: p.meta_description_ok)),
        ("Single H1", pct(lambda p: p.h1_ok)),
        ("Heading hierarchy", pct(lambda p: p.heading_hierarchy_ok)),
        ("Word count ≥300", pct(lambda p: p.word_count_ok)),
        ("Canonical tag", pct(lambda p: p.canonical_ok)),
        ("Indexable", pct(lambda p: p.indexable)),
        ("Viewport meta", pct(lambda p: p.viewport_ok)),
        ("HTML lang", pct(lambda p: bool(p.lang_attr))),
        ("Images have alt", pct(lambda p: p.images_alt_ok)),
        ("Internal linking", pct(lambda p: p.internal_links > 0)),
        ("Structured data", pct(lambda p: p.has_schema)),
        ("Open Graph", pct(lambda p: p.has_open_graph)),
    ]

    pages_with_issues = sorted(
        [{"url": p.url, "issues": p.issues, "score": p.score} for p in page_audits if p.issues],
        key=lambda r: (-len(r["issues"]), r["score"]),
    )

    schema_counter = Counter()
    for p in page_audits:
        for t in p.schema_types:
            schema_counter[t] += 1
        if not existing_top_keywords and p.title:
            existing_top_keywords.append(p.title)

    technical_payload = {
        "domain": ctx_site.domain,
        "pages_analyzed": n,
        "seo_average_score": int(round(sum(p.score for p in page_audits) / n)),
        "seo_pass_rates": dict(seo_check_rates),
        "top_issues": [{"url": p["url"], "issues": p["issues"][:5]} for p in pages_with_issues[:10]],
        "core_web_vitals_sample": psi_results,
        "schema_types": dict(schema_counter.most_common(10)),
    }
    geo_payload = {
        "domain": ctx_site.domain,
        "ai_friendly_score": geo_site.ai_friendly_score,
        "has_llms_txt": geo_site.has_llms_txt,
        "has_robots_txt": geo_site.has_robots_txt,
        "blocked_ai_crawlers": [c.name for c in geo_site.crawlers if not c.allowed],
        "allowed_ai_crawlers": [c.name for c in geo_site.crawlers if c.allowed],
        "citation_signal_avg": int(round(sum(citation_scores) / max(1, len(citation_scores)))),
        "schema_coverage": dict(schema_counter.most_common(10)),
    }

    competitor_nav_hints = [{"domain": c["domain"]} for c in competitors_metrics]

    # ---- Claude generation (parallel) ----
    overall.progress(72, text="Generating analysis with Gemini (parallel calls)…")
    log("Gemini is now writing scope, DR analysis, traffic analysis, keyword map, navigation, silos, backlinks strategy, GMB audit, technical summary, and detailed GEO…")

    def progress_cb(name, done, total):
        pct_ = 72 + int(22 * done / total)
        overall.progress(pct_, text=f"Gemini: {name} ({done}/{total})")
        log(f"  ✓ {name}")

    insights = generate_all(
        api_key=claude_key.strip(),
        model=model_choice,
        domain=ctx_site.domain,
        niche=niche.strip(),
        your_site_metrics=your_metrics,
        competitors_metrics=competitors_metrics,
        your_top_pages=your_top_pages,
        competitor_top_pages=competitor_top_pages,
        competitor_top_blog_keywords=competitor_blog_kws,
        service_pages_hints=service_pages_hints,
        existing_keywords=existing_top_keywords,
        competitor_nav_hints=competitor_nav_hints,
        competitor_backlink_samples={k: [b for b in v] for k, v in competitor_backlink_samples.items()},
        your_gmb_notes=your_gmb_notes,
        technical_payload=technical_payload,
        geo_payload=geo_payload,
        num_silos=num_silos,
        blogs_per_silo=blogs_per_silo,
        progress_cb=progress_cb,
    )

    if insights.errors:
        for e in insights.errors:
            st.warning(f"Claude warning: {e}")

    # ---- Build PPT ----
    overall.progress(95, text="Building PowerPoint deck…")
    crawlers_payload = [{"name": c.name, "purpose": c.purpose, "allowed": c.allowed, "rule": c.rule} for c in geo_site.crawlers]

    ctx_payload = {
        "domain": ctx_site.domain,
        "pages_analyzed": n,
        "your_site_metrics": your_metrics,
        "competitors_metrics": competitors_metrics,
        "seo_check_rates": seo_check_rates,
        "pages_with_issues": pages_with_issues,
        "pagespeed_results": psi_results,
        "crawlers": crawlers_payload,
        "geo_score": geo_site.ai_friendly_score,
        "citation_avg": geo_payload["citation_signal_avg"],
        "has_llms_txt": geo_site.has_llms_txt,
        "ai_allowed": geo_site.allowed_count,
        "ai_total": len(geo_site.crawlers),
        "insights": {
            "scope": insights.scope,
            "authority": insights.authority,
            "traffic": insights.traffic,
            "keyword_map": insights.keyword_map,
            "navigation": insights.navigation,
            "silos": insights.silos,
            "backlinks": insights.backlinks,
            "gmb": insights.gmb,
            "technical": insights.technical,
            "geo": insights.geo,
        },
    }

    out_dir = Path("reports")
    out_dir.mkdir(exist_ok=True)
    safe_domain = ctx_site.domain.replace(".", "_").replace(":", "_")
    out_path = out_dir / f"{safe_domain}_strategy_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pptx"
    build_report(str(out_path), ctx_payload)

    overall.progress(100, text="Done!")
    elapsed = int(time.time() - t_start)
    log(f"**Done in {elapsed}s** → `{out_path}`")

    st.success(f"Strategy deck generated in {elapsed}s.")

    # KPI row
    k1, k2, k3, k4 = st.columns(4)
    seo_avg = int(round(sum(p.score for p in page_audits) / n))
    k1.metric("Avg SEO Score", f"{seo_avg}/100")
    k2.metric("GEO Readiness", f"{geo_site.ai_friendly_score}/100")
    k3.metric("Pages Audited", n)
    k4.metric("Silos Generated", len(insights.silos.get("silos", []) if isinstance(insights.silos, dict) else []))

    with open(out_path, "rb") as f:
        st.download_button(
            label="Download Full Strategy Deck (.pptx)",
            data=f.read(),
            file_name=out_path.name,
            mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            type="primary",
        )

    # Preview sections
    if insights.scope:
        with st.expander("Scope (preview)"):
            st.write(insights.scope)
    if insights.authority:
        with st.expander("DR/DA Analysis (preview)"):
            st.write(insights.authority.get("narrative", ""))
            st.write("**Objective:**", insights.authority.get("objective", ""))
            st.write("**How:**")
            for h in insights.authority.get("how_to_achieve", []):
                st.write(f"- {h}")
    if insights.silos and insights.silos.get("silos"):
        with st.expander("Content Silos (preview)"):
            for s in insights.silos["silos"]:
                st.markdown(f"**{s.get('silo_name', '')}** — pillar: `{s.get('pillar_keyword', '')}`")
                for b in s.get("blogs", [])[:3]:
                    st.caption(f"  • {b.get('title', '')}  ({b.get('primary_keyword', '')})")
    with st.expander("Per-page SEO issues (table)"):
        st.dataframe(
            [{"URL": p["url"], "Score": p["score"], "Issues": len(p["issues"]), "Top issues": " | ".join(p["issues"][:3])}
             for p in pages_with_issues],
            width="stretch", hide_index=True,
        )
    if psi_results:
        with st.expander("Core Web Vitals (table)"):
            st.dataframe(psi_results, width="stretch", hide_index=True)
    with st.expander("AI Crawler Access"):
        st.dataframe(
            [{"Crawler": c["name"], "Purpose": c["purpose"], "Status": "ALLOWED" if c["allowed"] else "BLOCKED"} for c in crawlers_payload],
            width="stretch", hide_index=True,
        )

else:
    st.info("Fill in tabs 1–4, then go to **Tab 5** and click **Generate Full Strategy Deck**. Expected runtime: 2–5 minutes.")
