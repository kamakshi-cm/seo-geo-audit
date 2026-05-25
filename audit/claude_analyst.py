"""LLM-powered narrative generation for every analysis section of the deck.

Uses Google Gemini. File kept as `claude_analyst.py` for backwards compatibility.

Key reliability fixes:
- Thinking tokens disabled (Gemini 2.5 would otherwise eat the output budget)
- Sequential execution (avoids free-tier rate limits)
- Per-call retry with exponential back-off on 429 / 503
- Niche injected into every prompt
- Output sizes generous so silos / keyword maps don't get truncated
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

from google import genai
from google.genai import types
from google.genai import errors as genai_errors

DEFAULT_MODEL = "gemini-2.5-flash"


def _client(api_key: str | None) -> genai.Client | None:
    key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        return None
    return genai.Client(api_key=key)


def _strip_json(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[-1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rstrip("`").strip()
    return text


def _safe_json(text: str, fallback):
    text = _strip_json(text)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        start = text.find("{") if isinstance(fallback, dict) else text.find("[")
        end = text.rfind("}") if isinstance(fallback, dict) else text.rfind("]")
        if start != -1 and end != -1:
            try:
                return json.loads(text[start:end + 1])
            except (json.JSONDecodeError, ValueError):
                pass
    return fallback


def _call(client: genai.Client, model: str, system: str, user: str,
          max_tokens: int = 4000, want_json: bool = True, retries: int = 4) -> str:
    """Call Gemini with thinking disabled + retry on rate-limit errors."""
    cfg_kwargs = {
        "system_instruction": system,
        "max_output_tokens": max_tokens,
        "temperature": 0.7,
        # CRITICAL: Disable thinking so all max_tokens go to actual output
        "thinking_config": types.ThinkingConfig(thinking_budget=0),
    }
    if want_json:
        cfg_kwargs["response_mime_type"] = "application/json"
    cfg = types.GenerateContentConfig(**cfg_kwargs)

    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            resp = client.models.generate_content(model=model, contents=user, config=cfg)
            text = (resp.text or "").strip()
            if not text:
                raise ValueError("Gemini returned empty response")
            return text
        except genai_errors.APIError as e:
            last_err = e
            status = getattr(e, "code", None) or getattr(e, "status_code", None)
            msg = str(e).lower()
            if (status in (429, 503) or "rate" in msg or "quota" in msg or "resource_exhausted" in msg) and attempt < retries - 1:
                time.sleep(8 * (2 ** attempt))  # 8s, 16s, 32s
                continue
            if attempt < retries - 1:
                time.sleep(3)
                continue
            raise
        except (ConnectionError, TimeoutError, ValueError) as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(2 ** attempt * 3)
                continue
            raise
    if last_err:
        raise last_err
    return ""


def _niche_context(domain: str, niche: str) -> str:
    if niche:
        return f"Target domain: {domain}. Industry / niche: {niche}. Tailor every recommendation specifically to this niche — do not produce generic SEO advice."
    return f"Target domain: {domain}. Infer the niche from the domain name and tailor recommendations to it."


# ---------- 1. SCOPE OF DOCUMENT ----------
def scope_text(client, model: str, domain: str, niche_hint: str = "") -> str:
    system = """You write SEO strategy document scope statements. Output ONE paragraph, 4-6 sentences,
formal business tone. Mention SEO improvement, organic traffic, conversions, target audience, and best practices.
Return ONLY the paragraph as plain text — no JSON, no markdown."""
    user = f"Write a scope statement for an SEO + GEO strategy document for {domain}. {_niche_context(domain, niche_hint)}"
    return _call(client, model, system, user, max_tokens=800, want_json=False).strip()


# ---------- 2. DOMAIN AUTHORITY / RATING ANALYSIS ----------
def authority_analysis(client, model: str, your_site: dict, competitors: list[dict], niche: str = "") -> dict:
    system = f"""You are a senior SEO consultant writing the Domain Rating / Authority analysis section of a strategy deck.

Niche context: {niche or 'inferred from domains'}.

Return STRICT JSON with this shape:
{{
  "narrative": "5-7 sentences identifying our DR/DA stage, the strongest competitors and why (mention SPECIFIC competitor domains and their numbers), and the typical high-authority backlink sources (e.g. bbb.org, crunchbase.com, expertise.com, goodfirms.co, niche-specific authority sites) that drive their authority.",
  "objective": "One sentence with a specific numeric DR target over 6 months.",
  "reason": "One sentence on why increasing DA/DR matters.",
  "how_to_achieve": ["4-6 short bullet actions tailored to the niche"]
}}

Be specific. Cite the actual competitor domains and numbers from the input. Tone: factual, executive."""
    user = json.dumps({"your_site": your_site, "competitors": competitors, "niche": niche}, indent=2)
    text = _call(client, model, system, user, max_tokens=2500)
    return _safe_json(text, {"narrative": text, "objective": "", "reason": "", "how_to_achieve": []})


# ---------- 3. TRAFFIC / KEYWORDS ANALYSIS ----------
def traffic_analysis(client, model: str, your_site: dict, competitors: list[dict],
                     your_top_pages: list[dict], competitor_top_pages: dict[str, list[dict]],
                     niche: str = "") -> dict:
    system = f"""You are writing the Traffic / Ranking Keywords analysis section of an SEO strategy deck.

Niche: {niche or 'inferred'}.

Return STRICT JSON:
{{
  "our_facts": "2-3 sentences with our exact numbers and stage (early/mid/mature). Quote the input numbers.",
  "traffic_source_analysis": "2-3 sentences comparing our traffic value to keyword count vs competitors. Highlight if traffic value is high-vs-low relative to keyword count — meaning high-intent commercial keywords vs informational mix.",
  "our_top_pages": [
    {{"url": "...", "keyword": "...", "insight": "1 sentence on what this signals about our SEO position"}}
  ],
  "competitor_summaries": [
    {{"domain": "competitor.com", "insight": "2-3 sentences on what drives their organic traffic — blog content, location, services, etc.",
     "top_pages": [{{"url": "...", "keyword": "..."}}]}}
  ]
}}

Use ALL competitors from the input. For each, summarize their top pages. Echo URLs and keywords from the inputs verbatim — DO NOT invent URLs."""
    payload = {
        "your_site": your_site,
        "competitors": competitors,
        "your_top_pages": your_top_pages,
        "competitor_top_pages": competitor_top_pages,
        "niche": niche,
    }
    text = _call(client, model, system, json.dumps(payload, indent=2), max_tokens=4000)
    return _safe_json(text, {"our_facts": "", "traffic_source_analysis": "", "our_top_pages": [], "competitor_summaries": []})


# ---------- 4. KEYWORD RESEARCH + MAPPING ----------
def keyword_map(client, model: str, domain: str, niche: str,
                service_pages_hints: list[str], existing_keywords: list[str]) -> dict:
    system = f"""You are a senior SEO strategist building a detailed keyword map for {domain}.

Niche: {niche or 'inferred from domain'}.

Return STRICT JSON:
{{
  "homepage": {{
    "primary": "1 head keyword",
    "secondary": ["4-6 supporting keywords"]
  }},
  "service_pages": [
    {{"page_name": "Service name", "url_slug": "/service-name/",
     "primary": "primary keyword", "secondary": ["4-5 supporting keywords"],
     "sub_services": [
       {{"name": "Sub-service", "url_slug": "/service-name/sub/", "primary": "kw", "secondary": ["3-4 kw"]}}
     ]}}
  ],
  "about_page": {{"primary": "...", "secondary": ["3-4 kw"]}},
  "contact_page": {{"primary": "...", "secondary": ["3-4 kw"]}},
  "additional_pages": [
    {{"page_name": "e.g. Pricing", "url_slug": "/pricing/", "primary": "...", "secondary": ["3-4 kw"]}}
  ]
}}

REQUIREMENTS:
- Include EXACTLY 6 service pages (or more if the niche demands)
- Each service page needs 2-4 sub-services
- Include 3 additional pages (Resources, Pricing, Industries — choose what fits the niche)
- Keywords MUST be specific to the niche, commercial in intent, and realistic (not made-up)
- Use real service names — for a CPA firm: "Tax Preparation Services", "Audit & Assurance", "Bookkeeping", etc."""
    payload = {
        "domain": domain,
        "niche": niche,
        "existing_service_hints": service_pages_hints,
        "existing_top_keywords": existing_keywords,
    }
    text = _call(client, model, system, json.dumps(payload, indent=2), max_tokens=6000)
    return _safe_json(text, {"homepage": {}, "service_pages": [], "additional_pages": []})


# ---------- 5. WEBSITE NAVIGATION LAYOUT ----------
def site_navigation(client, model: str, domain: str, niche: str,
                    competitor_nav_hints: list[dict]) -> dict:
    system = f"""You are designing the optimal primary navigation for {domain}.

Niche: {niche or 'inferred'}.

Return STRICT JSON:
{{
  "nav_items": [
    {{"label": "Home", "children": []}},
    {{"label": "Services",
     "children": [
       {{"label": "Service Group A", "children": [{{"label": "Sub-service 1"}}, {{"label": "Sub-service 2"}}]}},
       {{"label": "Service Group B", "children": []}}
     ]}},
    {{"label": "Industries", "children": []}},
    {{"label": "About", "children": [{{"label": "Team"}}, {{"label": "Careers"}}]}},
    {{"label": "Resources", "children": [{{"label": "Blog"}}, {{"label": "Case Studies"}}]}},
    {{"label": "Contact", "children": []}}
  ],
  "rationale": "3-4 sentences explaining why this structure fits this specific niche and competitor patterns."
}}

REQUIREMENTS:
- 6-7 top-level items
- Services: 4-6 service group children, each with 2-4 grandchildren
- All labels must be niche-specific (e.g. for a CPA firm use "Tax Services", "Audit", "Advisory" not generic labels)"""
    payload = {"domain": domain, "niche": niche, "competitor_nav_hints": competitor_nav_hints}
    text = _call(client, model, system, json.dumps(payload, indent=2), max_tokens=4000)
    return _safe_json(text, {"nav_items": [], "rationale": ""})


# ---------- 6. CONTENT SILOS ----------
def content_silos(client, model: str, domain: str, niche: str,
                  competitor_top_blog_keywords: list[str], num_silos: int = 8, blogs_per_silo: int = 10) -> dict:
    system = f"""You are building a content strategy for {domain}'s SEO strategy deck.

Niche: {niche or 'inferred from domain'}.

Focus on MIDDLE-OF-THE-FUNNEL (MOFU) blog ideas — comparison posts, how-to guides, decision aids — NOT
top-of-funnel definition posts and NOT bottom-of-funnel sales pages.

Return STRICT JSON:
{{
  "silos": [
    {{
      "silo_name": "Short topic cluster name specific to the niche",
      "pillar_keyword": "the head keyword this silo will dominate",
      "audience": "1 short phrase on who reads this",
      "blogs": [
        {{"title": "Blog post title (~9-12 words, MOFU intent)", "primary_keyword": "kw", "intent": "informational|comparison|how-to"}}
      ]
    }}
  ]
}}

CRITICAL CONSTRAINTS:
- EXACTLY {num_silos} silos — count them
- EXACTLY {blogs_per_silo} blog ideas per silo — count them
- Blog titles must be specific to the niche, MOFU, and not generic
- Mix of formats: "X vs Y", how-to, "best X for Y", "checklist", "step-by-step guide"
- Avoid duplicating competitor blog topics; suggest unique angles
- Tailor everything to the niche — e.g. for a CPA firm, silos might be "Cash Flow Management", "Tax Strategy", "Audit Preparation", "QuickBooks Mastery", "Construction Accounting", "Nonprofit Finance", "Business Valuation", "Multi-State Tax Compliance"."""
    payload = {
        "domain": domain,
        "niche": niche,
        "competitor_top_blog_keywords": competitor_top_blog_keywords,
        "silos_required": num_silos,
        "blogs_per_silo": blogs_per_silo,
    }
    text = _call(client, model, system, json.dumps(payload, indent=2), max_tokens=14000)
    return _safe_json(text, {"silos": []})


# ---------- 8. GMB AUDIT ----------
def gmb_audit(client, model: str, your_site: str, your_gmb_notes: str,
              competitors: list[dict], niche: str = "") -> dict:
    system = f"""You are auditing a Google My Business profile for {your_site} against competitors.

Niche: {niche or 'inferred'}.

Return STRICT JSON:
{{
  "missing_elements": ["6-8 SPECIFIC items missing — categories, services, attributes, photos, posts, Q&A seeds, products, descriptions"],
  "plus_points": ["3-5 things they're doing well, inferred from notes"],
  "competitor_strengths": [
    {{"domain": "competitor.com", "strength": "1 specific sentence on their GMB strength"}}
  ],
  "modifications": [
    {{"area": "Business description", "action": "Specific change with example 200-char description text written for this niche"}},
    {{"area": "Primary category", "action": "Recommended primary category name"}},
    {{"area": "Additional categories", "action": "List 4-6 additional categories to add"}},
    {{"area": "Services", "action": "List 10-15 specific services to seed (with brief descriptions)"}},
    {{"area": "Photos", "action": "Cadence + specific photo content recommendations for the niche"}},
    {{"area": "Posts", "action": "Posting frequency + 5 specific post topic ideas"}},
    {{"area": "Reviews", "action": "Review acquisition + response strategy with sample wording"}},
    {{"area": "Q&A", "action": "5 seed Q&A pairs with answers"}},
    {{"area": "Products", "action": "Niche-specific product/service tile recommendations"}},
    {{"area": "Attributes", "action": "Specific attributes to enable (e.g. 'Online appointments', 'Free consultation')"}}
  ]
}}

Be HIGHLY specific. Every action should have niche-specific wording. Don't be generic."""
    payload = {"your_site": your_site, "your_gmb_notes": your_gmb_notes, "competitors": competitors, "niche": niche}
    text = _call(client, model, system, json.dumps(payload, indent=2), max_tokens=5000)
    return _safe_json(text, {"missing_elements": [], "plus_points": [], "competitor_strengths": [], "modifications": []})


# ---------- 9. TECHNICAL SEO + GEO EXECUTIVE SUMMARY ----------
def technical_summary(client, model: str, payload: dict) -> dict:
    system = """You are summarizing technical SEO + GEO findings for a CEO.

Return STRICT JSON:
{
  "executive_summary": "3-4 sentences. Lead with most important finding. Quantify everything.",
  "overall_verdict": "One sentence judgment.",
  "seo_strengths": ["3-5 bullets"],
  "seo_weaknesses": ["3-5 bullets"],
  "geo_strengths": ["3-5 bullets"],
  "geo_weaknesses": ["3-5 bullets"],
  "priority_recommendations": [
    {"priority": 1, "title": "Action title (max 8 words)", "why": "1 sentence", "action": "1-2 sentences", "area": "SEO|GEO|Technical|Content"}
  ]
}

Provide EXACTLY 10 priority recommendations, ranked by impact-to-effort."""
    text = _call(client, model, system, json.dumps(payload, indent=2), max_tokens=4500)
    return _safe_json(text, {})


# ---------- 10. DETAILED GEO ANALYSIS ----------
def geo_detailed(client, model: str, payload: dict, niche: str = "") -> dict:
    system = f"""You are writing the detailed Generative Engine Optimization (GEO) section of a strategy deck.

Niche: {niche or 'inferred'}.

Return STRICT JSON:
{{
  "overview": "4-5 sentences explaining what GEO is and why it specifically matters for this niche.",
  "ai_crawler_assessment": "4-5 sentences on which AI crawlers (GPTBot, ChatGPT-User, ClaudeBot, PerplexityBot, Google-Extended, OAI-SearchBot) are allowed/blocked and the implications. Cite the actual numbers from the audit.",
  "llms_txt_recommendation": "3-4 sentences on llms.txt — what it is, whether the site has one, and a concrete starter template for this niche.",
  "citation_readiness_analysis": "4-5 sentences on whether the site's content is structured for citation by ChatGPT/Perplexity (quotable stats, lists, tables, definitions, authors, dates). Cite specific findings from the audit.",
  "ai_overview_strategy": "4-5 sentences on optimizing for Google AI Overviews specifically in this niche.",
  "structured_data_strategy": "3-4 sentences on which Schema.org types (FAQPage, HowTo, Article, LocalBusiness, Service, Review) to prioritize for the niche.",
  "entity_optimization": "3-4 sentences on building entity authority — Wikipedia, Wikidata, Knowledge Graph, niche citations.",
  "geo_action_plan": [
    {{"action": "Specific action", "why": "Why it matters", "priority": "high|medium|low", "owner_role": "SEO|Content|Dev"}}
  ]
}}

Provide EXACTLY 12 action plan items, all niche-specific."""
    payload_with_niche = {**payload, "niche": niche}
    text = _call(client, model, system, json.dumps(payload_with_niche, indent=2), max_tokens=5500)
    return _safe_json(text, {})


# ---------- ORCHESTRATOR ----------
@dataclass
class AllInsights:
    scope: str = ""
    authority: dict = field(default_factory=dict)
    traffic: dict = field(default_factory=dict)
    keyword_map: dict = field(default_factory=dict)
    navigation: dict = field(default_factory=dict)
    silos: dict = field(default_factory=dict)
    gmb: dict = field(default_factory=dict)
    technical: dict = field(default_factory=dict)
    geo: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def _short_size(obj) -> str:
    if isinstance(obj, dict):
        return f"dict({len(obj)} keys)"
    if isinstance(obj, list):
        return f"list({len(obj)})"
    if isinstance(obj, str):
        return f"text({len(obj)} chars)"
    return type(obj).__name__


def generate_all(
    api_key: str | None,
    model: str,
    domain: str,
    niche: str,
    your_site_metrics: dict,
    competitors_metrics: list[dict],
    your_top_pages: list[dict],
    competitor_top_pages: dict[str, list[dict]],
    competitor_top_blog_keywords: list[str],
    service_pages_hints: list[str],
    existing_keywords: list[str],
    competitor_nav_hints: list[dict],
    competitor_backlink_samples: dict[str, list[dict]],
    your_gmb_notes: str,
    technical_payload: dict,
    geo_payload: dict,
    num_silos: int = 8,
    blogs_per_silo: int = 10,
    progress_cb=None,
) -> AllInsights:
    """Run all Gemini calls SEQUENTIALLY to stay within free-tier rate limits."""
    insights = AllInsights()
    client = _client(api_key)
    if not client:
        insights.errors.append("No Gemini API key configured (set GEMINI_API_KEY in .env or Streamlit secrets).")
        return insights

    tasks = [
        ("scope", lambda: scope_text(client, model, domain, niche)),
        ("authority", lambda: authority_analysis(client, model, your_site_metrics, competitors_metrics, niche)),
        ("traffic", lambda: traffic_analysis(client, model, your_site_metrics, competitors_metrics, your_top_pages, competitor_top_pages, niche)),
        ("keyword_map", lambda: keyword_map(client, model, domain, niche, service_pages_hints, existing_keywords)),
        ("navigation", lambda: site_navigation(client, model, domain, niche, competitor_nav_hints)),
        ("silos", lambda: content_silos(client, model, domain, niche, competitor_top_blog_keywords, num_silos, blogs_per_silo)),
        ("gmb", lambda: gmb_audit(client, model, domain, your_gmb_notes, competitors_metrics, niche)),
        ("technical", lambda: technical_summary(client, model, technical_payload)),
        ("geo", lambda: geo_detailed(client, model, geo_payload, niche)),
    ]

    total = len(tasks)
    for done, (name, fn) in enumerate(tasks, start=1):
        try:
            result = fn()
            setattr(insights, name, result)
            size = _short_size(result)
            if progress_cb:
                progress_cb(f"✓ {name} → {size}", done, total)
        except (RuntimeError, ValueError, ConnectionError, TimeoutError, genai_errors.APIError) as e:
            err_msg = f"{name}: {type(e).__name__}: {str(e)[:200]}"
            insights.errors.append(err_msg)
            if progress_cb:
                progress_cb(f"⚠ {name} FAILED: {str(e)[:120]}", done, total)
        # Small breathing gap between calls — keeps us under 10 RPM
        if done < total:
            time.sleep(2)

    return insights
