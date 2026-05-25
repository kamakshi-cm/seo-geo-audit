# SEO + GEO Strategy & Audit Tool

A complete strategic SEO + Generative Engine Optimization (GEO) audit tool. Takes only domain names as input, auto-pulls competitor metrics from DataForSEO, runs technical SEO + GEO checks on your site, and uses Google Gemini to write a 30–40 slide strategy deck.

## What it produces

A polished PowerPoint deck covering:
- Scope of Document + Table of Contents
- Domain Rating / Authority comparison + analysis
- Traffic, keywords, traffic-value comparison + analysis
- Per-competitor deep-dive slides
- Keyword research & page-level mapping (homepage, service pages, sub-services, additional pages)
- Recommended website navigation layout
- Content strategy with 4-8 topic silos × 10 MOFU blog ideas each
- Google My Business audit + modifications
- Technical SEO scorecard + per-page issue table
- Core Web Vitals (Lighthouse / PageSpeed Insights)
- Detailed GEO section (AI crawler access, citation readiness, AI Overviews strategy, action plan)
- Priority recommendations + closing

## Quick start (on any laptop)

### Prerequisites
- Python 3.10+ installed ([download here](https://www.python.org/downloads/))
- Git (optional, for cloning)

### Setup
```powershell
# 1. Open the project folder in a terminal
cd path\to\seo-geo-audit

# 2. Create virtual environment
python -m venv .venv

# 3. Activate it
.\.venv\Scripts\Activate.ps1   # Windows PowerShell
# OR  source .venv/bin/activate   # macOS / Linux

# 4. Install dependencies
pip install -r requirements.txt

# 5. Copy the env template and add your keys
copy .env.example .env          # Windows
# OR  cp .env.example .env      # macOS / Linux
# Then open .env and paste your API keys (see below)

# 6. Launch the app
streamlit run app.py
```

App opens at http://localhost:8501.

### API keys you need (all have generous free tiers)

| Key | Where to get | Cost |
|---|---|---|
| `GEMINI_API_KEY` | https://aistudio.google.com/apikey | **Free** (10 RPM on Flash) |
| `PAGESPEED_API_KEY` | https://console.cloud.google.com/apis/credentials → enable PageSpeed Insights API | **Free** (high quota) |
| `DATAFORSEO_LOGIN` + `DATAFORSEO_PASSWORD` | https://app.dataforseo.com/api/dashboard (sign-up + top-up) | Pay-as-you-go (~$0.05/audit, $5 minimum top-up) |

Only Gemini is strictly required — DataForSEO enables auto-pull of competitor metrics; without it you'd paste data manually in the UI.

## What it audits

**Traditional SEO (per page):**
- Title tag (length, presence)
- Meta description (length)
- H1-H6 heading structure & hierarchy
- Word count / thin content
- Canonical tag
- Robots meta (indexable?)
- Viewport meta + `<html lang>`
- Image alt text coverage
- Internal vs external link counts
- JSON-LD structured data (types detected: Article, FAQ, Product, etc.)
- Open Graph + Twitter Card tags

**GEO — Generative Engine Optimization (site-level):**
- robots.txt rules for **18 known AI crawlers**: GPTBot, ChatGPT-User, OAI-SearchBot, ClaudeBot, Claude-Web, anthropic-ai, PerplexityBot, Google-Extended, Googlebot, Bingbot, Applebot-Extended, CCBot, Meta-ExternalAgent, Bytespider, Amazonbot, DuckAssistBot
- `/llms.txt` and `/llms-full.txt` presence (emerging standard)
- AI-friendly composite score (0–100)

**GEO — citation readiness (per page):**
- FAQ / Article schema
- Author bylines + publish dates (E-E-A-T)
- Lists & tables (LLMs cite structured content)
- Definition-pattern detection in first paragraphs
- Optimal paragraph length

**Core Web Vitals** (sampled via Google PageSpeed Insights — no key needed)

**Claude-powered analysis:**
- Executive summary
- 8 prioritized recommendations (ranked by impact/effort)
- SEO + GEO strengths & weaknesses

## Setup

```powershell
# 1. Open the project in VS Code
code C:\Users\chopr\seo-geo-audit

# 2. Create a virtual environment (recommended)
python -m venv .venv
.venv\Scripts\Activate.ps1

# 3. Install dependencies
pip install -r requirements.txt

# 4. Get a free Claude API key
#    → Sign up at https://console.anthropic.com
#    → Create an API key
#    → New accounts get free credits

# 5. Configure your API key
copy .env.example .env
# Then edit .env and paste your key after ANTHROPIC_API_KEY=
```

## Run

```powershell
streamlit run app.py
```

The app opens at http://localhost:8501

1. Paste a domain (e.g. `example.com`)
2. Adjust the page-count slider (25 is a good demo size)
3. Click **Run Audit**
4. After ~30–90 seconds, click **Download PowerPoint Report**

## Free APIs used

| Service | Purpose | Free tier |
|---|---|---|
| **Anthropic Claude API** | Executive summary + recommendations | Free credits for new accounts; very cheap with Haiku model |
| **Google PageSpeed Insights** | Core Web Vitals | Free, no key needed for low volume |
| robots.txt / llms.txt | AI crawler rules | Free (public files) |
| Sitemap.xml | URL discovery | Free (public files) |

## File structure

```
seo-geo-audit/
├── app.py                       # Streamlit UI
├── requirements.txt
├── .env.example
├── audit/
│   ├── crawler.py               # Sitemap + page fetching
│   ├── seo_checks.py            # Per-page SEO audit
│   ├── geo_checks.py            # AI crawlers + llms.txt
│   ├── pagespeed.py             # Google PageSpeed Insights
│   └── claude_analyst.py        # Claude executive analysis
└── report/
    └── ppt_builder.py           # python-pptx deck (8 slides)
```

## Tips for the demo

- **Pick a recognizable domain** for the live demo — `nytimes.com`, `airbnb.com`, your company's site, or a client's. Big brands have richer audit data and the contrast (e.g. NYT allows AI crawlers, others block) tells a story.
- **Start with 15–25 pages** to keep the demo under a minute.
- **Set PageSpeed sample to 3–5** — PSI is the slow part. Skip it entirely if running short on time.
- The **GEO slide** (#4) is your differentiator. Walk through the AI crawler table — most agencies don't even know these exist.
