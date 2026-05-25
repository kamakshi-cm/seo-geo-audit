"""LLM-powered narrative generation for every analysis section of the deck.

Uses Google Gemini (free tier). File kept as `claude_analyst.py` for backwards
compatibility with imports — internally it's a Gemini client now.
"""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
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
    text = text.strip()
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
          max_tokens: int = 2500, want_json: bool = True, retries: int = 3) -> str:
    """Call Gemini with retry on rate-limit errors."""
    cfg_kwargs = {
        "system_instruction": system,
        "max_output_tokens": max_tokens,
        "temperature": 0.7,
    }
    if want_json:
        cfg_kwargs["response_mime_type"] = "application/json"
    cfg = types.GenerateContentConfig(**cfg_kwargs)

    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            resp = client.models.generate_content(model=model, contents=user, config=cfg)
            return (resp.text or "").strip()
        except genai_errors.APIError as e:
            last_err = e
            status = getattr(e, "code", None) or getattr(e, "status_code", None)
            # Rate limit / quota -> wait and retry
            if status in (429, 503) and attempt < retries - 1:
                time.sleep(2 ** attempt * 5)  # 5s, 10s, 20s
                continue
            raise
        except (ConnectionError, TimeoutError) as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(2 ** attempt * 3)
                continue
            raise
    if last_err:
        raise last_err
    return ""


# ---------- 1. SCOPE OF DOCUMENT ----------
def scope_text(client, model: str, domain: str, niche_hint: str = "") -> str:
    system = """You write SEO strategy document scope statements. Output ONE paragraph, 4-6 sentences,
formal business tone. Mention SEO improvement, organic traffic, conversions, target audience, and best practices.
Return ONLY the paragraph as plain text — no JSON, no markdown."""
    user = f"Write a scope statement for an SEO + GEO strategy document for {domain}. Niche hint: {niche_hint or 'unknown — infer from domain'}."
    return _call(client, model, system, user, max_tokens=600, want_json=False).strip()


# ---------- 2. DOMAIN AUTHORITY / RATING ANALYSIS ----------
def authority_analysis(client, model: str, your_site: dict, competitors: list[dict]) -> dict:
    system = """You are a senior SEO consultant writing the Domain Rating / Authority analysis section of a strategy deck.

Return STRICT JSON with this shape:
{
  "narrative": "5-7 sentences identifying our DR/DA stage, the strongest competitors and why (mention SPECIFIC competitor domains and their numbers), and the typical high-authority backlink sources (e.g. bbb.org, crunchbase.com, expertise.com, goodfirms.co) that drive their authority.",
  "objective": "One sentence with a specific numeric DR target over 6 months.",
  "reason": "One sentence on why increasing DA/DR matters.",
  "how_to_achieve": ["3-5 short bullet actions"]
}

Be specific. Cite the actual competitor domains and numbers from the input. Tone: factual, executive."""
    user = json.dumps({"your_site": your_site, "competitors": competitors}, indent=2)
    text = _call(client, model, system, user, max_tokens=1500)
    return _safe_json(text, {"narrative": text, "objective": "", "reason": "", "how_to_achieve": []})


# ---------- 3. TRAFFIC / KEYWORDS ANALYSIS ----------
def traffic_analysis(client, model: str, your_site: dict, competitors: list[dict],
                     your_top_pages: list[dict], competitor_top_pages: dict[str, list[dict]]) -> dict:
    system = """You are writing the Traffic / Ranking Keywords analysis section of an SEO strategy deck.

Return STRICT JSON:
{
  "our_facts": "2-3 sentences with our numbers and stage (e.g. early/mid/mature).",
  "traffic_source_analysis": "2-3 sentences comparing our traffic value to keyword count vs competitors. Highlight if traffic value is high-vs-low relative to keyword count — meaning high-intent vs informational mix.",
  "our_top_pages": [
    {"url": "...", "keyword": "...", "insight": "1 sentence on what this signals"}
  ],
  "competitor_summaries": [
    {"domain": "competitor.com", "insight": "2-3 sentences on what drives their organic traffic — blog content, location, etc.",
     "top_pages": [{"url": "...", "keyword": "..."}]}
  ]
}

Echo back URLs and keywords from the inputs. Don't invent URLs."""
    payload = {
        "your_site": your_site,
        "competitors": competitors,
        "your_top_pages": your_top_pages,
        "competitor_top_pages": competitor_top_pages,
    }
    text = _call(client, model, system, json.dumps(payload, indent=2), max_tokens=2500)
    return _safe_json(text, {"our_facts": text, "traffic_source_analysis": "", "our_top_pages": [], "competitor_summaries": []})


# ---------- 4. KEYWORD RESEARCH + MAPPING ----------
def keyword_map(client, model: str, domain: str, niche: str,
                service_pages_hints: list[str], existing_keywords: list[str]) -> dict:
    system = """You are a senior SEO strategist building a keyword map for a website.

Return STRICT JSON:
{
  "homepage": {
    "primary": "1 head keyword",
    "secondary": ["3-5 supporting keywords"]
  },
  "service_pages": [
    {"page_name": "Service name", "url_slug": "/service-name/",
     "primary": "primary keyword", "secondary": ["3-4 supporting keywords"],
     "sub_services": [
       {"name": "Sub-service", "url_slug": "/service-name/sub/", "primary": "kw", "secondary": ["kw"]}
     ]}
  ],
  "about_page": {"primary": "...", "secondary": ["..."]},
  "contact_page": {"primary": "...", "secondary": ["..."]},
  "additional_pages": [
    {"page_name": "e.g. Pricing", "url_slug": "/pricing/", "primary": "...", "secondary": ["..."]}
  ]
}

Include at least 5 service pages. Where it makes sense, add 2-4 sub-services per main service.
Keywords must be realistic, commercially valuable, and aligned with the niche."""
    payload = {
        "domain": domain,
        "niche": niche,
        "existing_service_hints": service_pages_hints,
        "existing_top_keywords": existing_keywords,
    }
    text = _call(client, model, system, json.dumps(payload, indent=2), max_tokens=3000)
    return _safe_json(text, {"homepage": {}, "service_pages": [], "additional_pages": []})


# ---------- 5. WEBSITE NAVIGATION LAYOUT ----------
def site_navigation(client, model: str, domain: str, niche: str,
                    competitor_nav_hints: list[dict]) -> dict:
    system = """You are designing the optimal information architecture for a website's main navigation.

Return STRICT JSON:
{
  "nav_items": [
    {"label": "Home", "children": []},
    {"label": "Services",
     "children": [
       {"label": "Service Group A", "children": [{"label": "Sub-service 1"}]},
       {"label": "Service Group B", "children": []}
     ]
    },
    {"label": "Industries", "children": []},
    {"label": "About", "children": [{"label": "Team"}, {"label": "Careers"}]},
    {"label": "Resources", "children": [{"label": "Blog"}, {"label": "Case Studies"}]},
    {"label": "Contact", "children": []}
  ],
  "rationale": "2-3 sentences explaining why this structure fits the niche and competitor patterns."
}

Aim for 5-7 top-level items, 3-6 children where appropriate, and 2-4 grandchildren for service groups."""
    payload = {"domain": domain, "niche": niche, "competitor_nav_hints": competitor_nav_hints}
    text = _call(client, model, system, json.dumps(payload, indent=2), max_tokens=2500)
    return _safe_json(text, {"nav_items": [], "rationale": ""})


# ---------- 6. CONTENT SILOS ----------
def content_silos(client, model: str, domain: str, niche: str,
                  competitor_top_blog_keywords: list[str], num_silos: int = 8, blogs_per_silo: int = 10) -> dict:
    system = ("""You are building a content strategy for an SEO strategy deck.

Focus on MIDDLE-OF-THE-FUNNEL (MOFU) blog ideas — comparison posts, how-to guides, decision aids — NOT
top-of-funnel definition posts and NOT bottom-of-funnel sales pages.

Return STRICT JSON:
{
  "silos": [
    {
      "silo_name": "Short topic cluster name (e.g. 'Cash Flow Management')",
      "pillar_keyword": "the head keyword this silo will dominate",
      "audience": "1 short phrase on who reads this",
      "blogs": [
        {"title": "Blog post title (~9-12 words, MOFU intent)", "primary_keyword": "kw", "intent": "informational|comparison|how-to"}
      ]
    }
  ]
}

Constraints:
- EXACTLY """ + str(num_silos) + """ silos
- EXACTLY """ + str(blogs_per_silo) + """ blog ideas per silo
- Blog titles must be specific, MOFU, and not generic
- Mix of comparison ("X vs Y"), how-to, "best", "checklist", "guide" formats
- Avoid duplicating competitor blog topics; suggest unique angles""")
    payload = {
        "domain": domain,
        "niche": niche,
        "competitor_top_blog_keywords": competitor_top_blog_keywords,
        "silos_required": num_silos,
        "blogs_per_silo": blogs_per_silo,
    }
    text = _call(client, model, system, json.dumps(payload, indent=2), max_tokens=8000)
    return _safe_json(text, {"silos": []})


# ---------- 7. BACKLINKS ANALYSIS + STRATEGY ----------
def backlinks_analysis(client, model: str, your_site: dict, competitors: list[dict],
                        competitor_backlink_samples: dict[str, list[dict]]) -> dict:
    system = """You are writing the Backlinks + Referring Domains analysis section of an SEO deck.

Return STRICT JSON:
{
  "narrative": "3-4 sentences identifying who has the most backlinks and most referring domains, and citing SPECIFIC sample URLs from the inputs.",
  "competitor_quality_links": [
    {"domain": "competitor.com", "sample_links": ["url1", "url2", "url3"], "comment": "brief note on type"}
  ],
  "spam_patterns": [
    {"domain": "competitor.com", "pattern": "e.g. '41 backlinks from yellowpages.com directory templates'",
     "recommendation": "Avoid this pattern; focus on contextual editorial links instead."}
  ],
  "objective": "One sentence with a specific monthly PR backlink target.",
  "methods": {
    "guest_posting": "2-3 sentences with niche/industry examples.",
    "competitor_backlinks": "2-3 sentences on replicating competitor sources.",
    "forum": "2-3 sentences on community engagement.",
    "citation_building": "2-3 sentences on directory submissions with NAP.",
    "local_listing": "2-3 sentences on local + global business directories and GBP."
  }
}

Use the actual competitor domains and URLs from the input. Don't invent backlinks."""
    payload = {
        "your_site": your_site,
        "competitors": competitors,
        "competitor_backlink_samples": competitor_backlink_samples,
    }
    text = _call(client, model, system, json.dumps(payload, indent=2), max_tokens=3500)
    return _safe_json(text, {
        "narrative": text, "competitor_quality_links": [], "spam_patterns": [],
        "objective": "", "methods": {},
    })


# ---------- 8. GMB AUDIT ----------
def gmb_audit(client, model: str, your_site: str, your_gmb_notes: str,
              competitors: list[dict]) -> dict:
    system = """You are auditing a Google My Business profile against competitors.

Return STRICT JSON:
{
  "missing_elements": ["Specific items missing from our GMB — categories, services, attributes, photos, posts, etc."],
  "plus_points": ["Things we are doing well based on the notes."],
  "competitor_strengths": [
    {"domain": "competitor.com", "strength": "1 short sentence"}
  ],
  "modifications": [
    {"area": "e.g. Business description", "action": "Specific change with example wording"},
    {"area": "Categories", "action": "Add/remove specific categories"},
    {"area": "Photos", "action": "Cadence + content recommendations"},
    {"area": "Posts", "action": "Posting frequency + topic ideas"},
    {"area": "Reviews", "action": "Review acquisition + response strategy"},
    {"area": "Q&A", "action": "Seed Q&A strategy"}
  ]
}

Be specific. If notes don't mention something, infer reasonable defaults for the niche."""
    payload = {"your_site": your_site, "your_gmb_notes": your_gmb_notes, "competitors": competitors}
    text = _call(client, model, system, json.dumps(payload, indent=2), max_tokens=2500)
    return _safe_json(text, {"missing_elements": [], "plus_points": [], "competitor_strengths": [], "modifications": []})


# ---------- 9. TECHNICAL SEO + GEO EXECUTIVE SUMMARY ----------
def technical_summary(client, model: str, payload: dict) -> dict:
    system = """You are summarizing technical SEO + GEO findings for a CEO.

Return STRICT JSON:
{
  "executive_summary": "3-4 sentences. Lead with most important finding. Quantify everything.",
  "overall_verdict": "One sentence judgment.",
  "seo_strengths": ["bullet", "bullet"],
  "seo_weaknesses": ["bullet", "bullet"],
  "geo_strengths": ["bullet", "bullet"],
  "geo_weaknesses": ["bullet", "bullet"],
  "priority_recommendations": [
    {"priority": 1, "title": "Action title (max 8 words)", "why": "1 sentence", "action": "1-2 sentences", "area": "SEO|GEO|Technical|Content"}
  ]
}

Provide EXACTLY 10 priority recommendations, ranked by impact-to-effort."""
    text = _call(client, model, system, json.dumps(payload, indent=2), max_tokens=3500)
    return _safe_json(text, {})


# ---------- 10. DETAILED GEO ANALYSIS ----------
def geo_detailed(client, model: str, payload: dict) -> dict:
    system = """You are writing the detailed Generative Engine Optimization (GEO) section of a strategy deck.

Return STRICT JSON:
{
  "overview": "3-4 sentences explaining what GEO is and why it matters for this site.",
  "ai_crawler_assessment": "3-4 sentences on which AI crawlers (GPTBot, ClaudeBot, PerplexityBot, Google-Extended) are allowed/blocked and the implications.",
  "llms_txt_recommendation": "2-3 sentences on llms.txt — what it is, whether the site has one, and what to put in it.",
  "citation_readiness_analysis": "3-4 sentences on whether the site's content is structured for citation by ChatGPT/Perplexity (quotable stats, lists, tables, definitions, authors, dates).",
  "ai_overview_strategy": "3-4 sentences on optimizing for Google AI Overviews specifically.",
  "geo_action_plan": [
    {"action": "...", "why": "...", "priority": "high|medium|low"}
  ]
}

Provide 8-10 action plan items."""
    text = _call(client, model, system, json.dumps(payload, indent=2), max_tokens=3500)
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
    backlinks: dict = field(default_factory=dict)
    gmb: dict = field(default_factory=dict)
    technical: dict = field(default_factory=dict)
    geo: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


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
    """Run all Gemini calls in parallel (limited concurrency for free-tier rate limits)."""
    insights = AllInsights()
    client = _client(api_key)
    if not client:
        insights.errors.append("No Gemini API key configured (set GEMINI_API_KEY in .env).")
        return insights

    tasks = {
        "scope": lambda: scope_text(client, model, domain, niche),
        "authority": lambda: authority_analysis(client, model, your_site_metrics, competitors_metrics),
        "traffic": lambda: traffic_analysis(client, model, your_site_metrics, competitors_metrics, your_top_pages, competitor_top_pages),
        "keyword_map": lambda: keyword_map(client, model, domain, niche, service_pages_hints, existing_keywords),
        "navigation": lambda: site_navigation(client, model, domain, niche, competitor_nav_hints),
        "silos": lambda: content_silos(client, model, domain, niche, competitor_top_blog_keywords, num_silos, blogs_per_silo),
        "gmb": lambda: gmb_audit(client, model, domain, your_gmb_notes, competitors_metrics),
        "technical": lambda: technical_summary(client, model, technical_payload),
        "geo": lambda: geo_detailed(client, model, geo_payload),
    }

    done = 0
    total = len(tasks)
    # Free tier rate limits — keep parallelism low. 2.5-flash = 10 RPM; 2.5-pro = 5 RPM.
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {ex.submit(fn): name for name, fn in tasks.items()}
        for fut in list(futures):
            name = futures[fut]
            try:
                result = fut.result(timeout=240)
                setattr(insights, name, result)
            except (RuntimeError, ValueError, ConnectionError, TimeoutError, genai_errors.APIError) as e:
                insights.errors.append(f"{name}: {e}")
            done += 1
            if progress_cb:
                progress_cb(name, done, total)
    return insights
