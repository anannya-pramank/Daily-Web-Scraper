import os
import re
import json
import time
import hashlib
import requests
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin
from email.utils import parsedate_to_datetime

# Gemini is called via the REST API using requests — no SDK needed
GEMINI_AVAILABLE = True

# ── Gemini availability banner (always visible in Actions log) ────────────────
print(f"[GEMINI] API key set: {bool(os.environ.get('GEMINI_API_KEY', ''))}")

try:
    import feedparser
    FEEDPARSER_AVAILABLE = True
except ImportError:
    FEEDPARSER_AVAILABLE = False

# ==========================================
# 1. CONFIGURATION
# ==========================================

POWER_AUTOMATE_URL = "https://defaultfd7143fa1107460d98b18ef251b16d.50.environment.api.powerplatform.com:443/powerautomate/automations/direct/workflows/83c582d1339848bf82bb44367f463879/triggers/manual/paths/invoke?api-version=1&sp=%2Ftriggers%2Fmanual%2Frun&sv=1.0&sig=G0QNUbenz6wjlJLNkAW0j0b34BMc6aB7i58Lfmy-8Jg"

TODAY_STR = datetime.now().strftime("%d %B %Y")
HISTORY_PATH = "Historical_Matches.csv"

# Items older than this many days are ignored (prevents stale SEBI dumps on first run)
RECENCY_DAYS = 60
RECENCY_CUTOFF = datetime.now(timezone.utc) - timedelta(days=RECENCY_DAYS)

# Tighter window for ESG news articles in the daily digest.
# 72 hours (3 days) covers the weekend gap: on Monday mornings, articles
# published Friday–Saturday would be excluded by a 48h cutoff.
NEWS_CUTOFF = datetime.now(timezone.utc) - timedelta(hours=72)

# Max articles returned per source per run — prevents any single outlet from
# flooding the digest and guarantees diversity across tracked sites.
MAX_PER_SOURCE = 3


def parse_fuzzy_date(text: str):
    """
    Try to parse a date string into an aware datetime.
    Handles: RFC-2822 (RSS), 'Jan 30, 2026', 'Apr 07, 2026', '10-Jan-2024',
             'dd/mm/yyyy', 'dd-mm-yyyy', 'YYYY-MM-DD'.
    Returns None if unparseable.
    """
    if not text:
        return None
    text = text.strip()
    # RFC-2822 (RSS feeds: "Thu, 04 Jun 2026 00:00:00 +0000")
    try:
        return parsedate_to_datetime(text)
    except Exception:
        pass
    # Extract the first date-like token from a longer string (e.g. SEBI row text)
    # Try common date-only formats directly against full string — no slicing
    candidates = [text]
    # Also try pulling out just the first 12–16 chars in case of trailing garbage
    if len(text) > 16:
        candidates.append(text[:16].strip())
        candidates.append(text[:12].strip())
    for candidate in candidates:
        for fmt in (
            "%b %d, %Y",   # Jan 30, 2026  /  Apr 07, 2026
            "%B %d, %Y",   # January 30, 2026
            "%d %b %Y",    # 30 Jan 2026
            "%d %B %Y",    # 30 January 2026
            "%d-%b-%Y",    # 30-Jan-2026
            "%d/%m/%Y",    # 30/01/2026
            "%d-%m-%Y",    # 30-01-2026
            "%Y-%m-%d",    # 2026-01-30
            "%m/%d/%Y",    # 01/30/2026 (US)
        ):
            try:
                dt = datetime.strptime(candidate, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def fmt_date(raw: str) -> str:
    """Return a clean 'DD Mon YYYY' string, or the raw string if unparseable."""
    dt = parse_fuzzy_date(raw)
    if dt:
        return dt.strftime("%d %b %Y")
    return raw

TENDER_KEYWORDS = [
    "Carbon Credit", "Carbon Offset", "Carbon Trading", "Carbon Footprint", "Carbon Neutral",
    "Net Zero", "Carbon Sequestration", "Scope 1", "Scope 2", "Scope 3", "GHG",
    "Green House Gas", "Green House Gases", "ESG", "ESG Disclosure", "Climate Change",
    "Green Finance", "Sustainable Finance", "BRSR", "Assurance", "Assessment",
    "Sustainab", "Sustainability", "Carbon Market"
]

REALTIME_KEYWORDS = [
    # ── Carbon & Emissions (specific first) ──────────────────────────────────
    "Carbon Border Adjustment", "Voluntary Carbon Market", "Joint Crediting Mechanism",
    "Carbon Sequestration", "Carbon Offsetting", "Emissions Reduction", "Carbon Removal",
    "Carbon Footprint", "Carbon Credits", "Carbon Trading", "Carbon Neutral", "Carbon Offset",
    "Carbon Credit", "Carbon Market", "Carbon Border", "Carbon Leakage", "Carbon Emissions",
    "Carbon Price", "Carbon Tax", "Net Emissions", "Carbon Standard", "Carbon Registry",
    "Net Zero",
    # ── Climate (specific first) ─────────────────────────────────────────────
    "Clean Energy Transition", "Global Plastics Treaty", "Decarbonisation",
    "Climate Finance", "Climate Policy", "Climate Action", "Climate Change",
    "Climate Risk", "Climate Tech", "Paris Agreement", "Global Warming",
    # ── Reporting Standards ───────────────────────────────────────────────────
    "Listing Obligations and Disclosure Requirements",
    "Extended Producer Responsibility", "Biodiversity Net Gain",
    "Nature Based Solutions", "Ecosystem Services", "Integrated Reporting",
    "Double Materiality", "Greenhouse Gases", "Greenhouse Gas",
    "Sustainability Bond", "Sustainability Summit", "Sustainable Finance",
    "Green Finance Summit", "Biodiversity Credits", "ESG Disclosure",
    "ESG Reporting", "ESG Investing", "ESG Framework", "ESG Portfolio",
    "ESG Conference", "ESG Rating", "ESG Score", "ESG Fund",
    "BRSR Core", "IFRS S1", "IFRS S2", "Article 6.2", "Article 6",
    "CSRD", "ISSB", "LODR", "TCFD", "BRSR", "SASB", "TNFD", "SBTN",
    "GRI", "GHG", "COP",
    # ── Finance & Investment ─────────────────────────────────────────────────
    "India Sustainability", "Transition Finance", "Blended Finance",
    "Impact Investing", "Green Investment", "India Net Zero", "India ESG",
    "Carbon Summit", "Green Bond", "Taxonomy", "Greenwashing",
    "Compliance Carbon", "Gold Standard", "Verra",
    # ── Energy ───────────────────────────────────────────────────────────────
    "Waste to Energy", "Energy Transition", "Renewable Energy", "Green Hydrogen",
    "Wind Energy", "Battery Storage", "Electric Vehicle", "Clean Tech",
    # ── Nature & Water ───────────────────────────────────────────────────────
    "Water Stewardship", "Water Security", "Water Footprint", "Water Stress",
    "Water Risk", "Blue Carbon", "Ocean Carbon", "Forest Carbon",
    "Kunming Montreal", "Deforestation", "Nature Loss", "Biodiversity",
    "EUDR",
    # ── Circular / Trade ─────────────────────────────────────────────────────
    "Circular Economy", "Plastic Pollution", "Plastic Credit",
    "Carbon Border", "EU Green Deal", "EU Carbon Tax", "EU ETS", "CBAM",
    # ── Other specific ───────────────────────────────────────────────────────
    "Bioenergy", "Biochar", "Biomass", "BECCS", "Methane", "Scope 1", "Scope 2", "Scope 3",
    "Assurance", "Assessment",
    "JCM", "EPR",
    # ── Biomass & Bioenergy (explicit) ────────────────────────────────────────
    "Biomass Energy", "Biomass Power", "Biomass Carbon", "Biomass Pellets",
    "Biomass Gasification", "Forest Biomass", "Biomass Co-firing",
    "Agricultural Residue", "Biofuel", "BECCS", "Bio-CCS", "Biomass", "RED III",
    # ── Water (explicit additions) ────────────────────────────────────────────
    "Water Credits", "Watershed", "CDP Water", "Groundwater", "Water Recycling",
    # ── ESG Tech & Ratings (explicit) ─────────────────────────────────────────
    "ESG Tech", "ESG SaaS", "ESG KPI", "Materiality Matrix", "ESG Benchmark",
    "ESG Maturity", "Climate Fintech", "Green Fintech",
    # ── Markets & Finance (explicit additions) ────────────────────────────────
    "Nature Finance", "Single Use Plastic", "CCTS",
    # ── Biodiversity (explicit additions) ────────────────────────────────────
    "Wetlands", "Wildlife",
    "Sustainability", "Green Finance", "ESG", "Emissions", "Solar",
]

SEBI_KEYWORDS = [
    # Matches tracking list exactly: BRSR/LODR/Assurance/Assessment/BRSR Core only
    "BRSR Core", "BRSR",
    "Listing Obligations and Disclosure Requirements",
    "LODR",
    "Assurance", "Assessment",
]

# Tighter keyword set for PIB India — strips broad terms that catch generic govt press
# releases (zoo apps, environment-day ceremonies). Spirit of tracker "all ESG related
# real time updates" but limited to high-signal regulatory/policy terms only.
PIB_INDIA_KEYWORDS = [
    "BRSR", "BRSR Core", "ESG Disclosure", "ESG Reporting", "ESG Framework", "ESG",
    "Green Bond", "Sustainability Bond", "Social Bond", "Green Finance",
    "Carbon Credit", "Carbon Market", "Carbon Trading", "Carbon Tax", "Carbon Neutral",
    "Carbon Offset", "Net Zero", "Carbon Footprint", "Emissions Trading", "GHG",
    "Scope 1", "Scope 2", "Scope 3", "Climate Finance", "Renewable Energy",
    "Energy Transition", "Circular Economy", "Extended Producer Responsibility", "EPR",
    "CBAM", "Paris Agreement", "Greenwashing", "Green Hydrogen", "Carbon Sequestration",
]

# Each source: org name, homepage URL, RSS feed URL (or None), keywords, category, parser type
SOURCES = [
    # ── Tenders ──────────────────────────────────────────────────────────────
    {
        "org": "GeM CPPP",
        "url": "https://gem.gov.in/cppp",
        "rss": None,
        "keywords": TENDER_KEYWORDS,
        "category": "Tenders",
        "parser": "cppp",
    },
    {
        # JS-rendered SPA — parse_gem_bidplus tries GeM search APIs then
        # falls back to eprocure keyword search (CPPP aggregates GeM tenders).
        "org": "GeM List of Bids",
        "url": "https://bidplus.gem.gov.in/all-bids",
        "rss": None,
        "keywords": TENDER_KEYWORDS,
        "category": "Tenders",
        "parser": "gem_bidplus",
    },
    {
        "org": "CPPP Active Tenders",
        "url": (
            "https://eprocure.gov.in/cppp/latestactivetendersnew/cpppdata"
            "/byYzJWc1pXTjBBMTNoMWMyVnNaV04wQTEzaDFjSFZpYkdsemFIVmtYMlJo"
            "ZEdVPUExM2gxUWxKVFVnPT0="
        ),
        "rss": None,
        "keywords": TENDER_KEYWORDS,
        "category": "Tenders",
        "parser": "cppp",
    },
    {
        "org": "CPPP Active Tenders – Central",
        "url": "https://eprocure.gov.in/cppp/latestactivetendersnew/cpppdata",
        "rss": None,
        "keywords": TENDER_KEYWORDS,
        "category": "Tenders",
        "parser": "cppp",
    },
    {
        "org": "CPPP Active Tenders – State",
        "url": "https://eprocure.gov.in/cppp/latestactivetendersnew/mmpdata",
        "rss": None,
        "keywords": TENDER_KEYWORDS,
        "category": "Tenders",
        "parser": "cppp",
    },
    # ── ESG News — one entry per tracked source ───────────────────────────────
    # Primary: each site's own RSS feed.
    # Fallback (rss_gnews): Google News scoped to that domain only — so only
    # articles from the tracked site are returned, never third-party outlets.
    # No gnews=True: org label always comes from the source dict, not article metadata.
    {
        "org": "ESG Today",
        "url": "https://www.esgtoday.com/",
        "rss": "https://www.esgtoday.com/feed/",
        "rss_gnews": (
            "https://news.google.com/rss/search"
            "?q=site:esgtoday.com&hl=en-US&gl=US&ceid=US:en"
        ),
        "keywords": REALTIME_KEYWORDS,
        "category": "ESG News",
        "parser": "rss_news",
    },
    {
        "org": "GreenBiz / Trellis",
        "url": "https://trellis.net/",
        "rss": "https://trellis.net/feed/",
        "rss_gnews": (
            "https://news.google.com/rss/search"
            "?q=site:trellis.net&hl=en-US&gl=US&ceid=US:en"
        ),
        "keywords": REALTIME_KEYWORDS,
        "category": "ESG News",
        "parser": "rss_news",
    },
    {
        "org": "ESG News",
        "url": "https://esgnews.com/",
        "rss": "https://esgnews.com/feed/",
        "rss_gnews": (
            "https://news.google.com/rss/search"
            "?q=site:esgnews.com&hl=en-US&gl=US&ceid=US:en"
        ),
        "keywords": REALTIME_KEYWORDS,
        "category": "ESG News",
        "parser": "rss_news",
    },
    {
        # BizClik CMS — no native RSS feed exists.
        # Primary: Google News site-scoped search (with ESG terms to improve relevance).
        # HTML fallback: scrape the /esg topic listing page directly.
        "org": "Sustainability Magazine",
        "url": "https://sustainabilitymag.com/esg",
        "rss": None,
        "rss_gnews": (
            "https://news.google.com/rss/search"
            "?q=site:sustainabilitymag.com+ESG+OR+sustainability+OR+carbon+OR+net+zero"
            "&hl=en-US&gl=US&ceid=US:en"
        ),
        "keywords": REALTIME_KEYWORDS,
        "category": "ESG News",
        "parser": "rss_news",
    },
    {
        "org": "ESG Dive",
        "url": "https://www.esgdive.com/",
        "rss": "https://www.esgdive.com/feeds/news/",
        "rss_gnews": (
            "https://news.google.com/rss/search"
            "?q=site:esgdive.com&hl=en-US&gl=US&ceid=US:en"
        ),
        "keywords": REALTIME_KEYWORDS,
        "category": "ESG News",
        "parser": "rss_news",
    },
    {
        "org": "ESG Investing",
        "url": "https://www.esginvesting.co.uk/",
        "rss": "https://www.esginvesting.co.uk/feed/",
        "rss_gnews": (
            "https://news.google.com/rss/search"
            "?q=site:esginvesting.co.uk&hl=en-US&gl=US&ceid=US:en"
        ),
        "keywords": REALTIME_KEYWORDS,
        "category": "ESG News",
        "parser": "rss_news",
    },
    {
        "org": "Financial Advisor Magazine",
        "url": "https://www.fa-mag.com/",
        "rss": "https://www.fa-mag.com/rss.php",
        "rss_gnews": (
            "https://news.google.com/rss/search"
            "?q=site:fa-mag.com+ESG+OR+sustainability+OR+carbon"
            "&hl=en-US&gl=US&ceid=US:en"
        ),
        "keywords": REALTIME_KEYWORDS,
        "category": "ESG News",
        "parser": "rss_news",
    },
    {
        "org": "Environmental Finance",
        "url": "https://www.environmental-finance.com/",
        "rss": "https://www.environmental-finance.com/content/news/rss",
        "rss_gnews": (
            "https://news.google.com/rss/search"
            "?q=site:environmental-finance.com&hl=en-US&gl=US&ceid=US:en"
        ),
        "keywords": REALTIME_KEYWORDS,
        "category": "ESG News",
        "parser": "rss_news",
    },
    {
        "org": "GreenMoney",
        "url": "https://greenmoney.com/",
        "rss": "https://greenmoney.com/feed/",
        "rss_gnews": (
            "https://news.google.com/rss/search"
            "?q=site:greenmoney.com&hl=en-US&gl=US&ceid=US:en"
        ),
        "keywords": REALTIME_KEYWORDS,
        "category": "ESG News",
        "parser": "rss_news",
    },
    {
        "org": "Mondaq",
        "url": "https://www.mondaq.com/",
        "rss": "https://www.mondaq.com/rss/ESGandSustainability",
        "rss_gnews": (
            "https://news.google.com/rss/search"
            "?q=site:mondaq.com+ESG+OR+sustainability+OR+carbon&hl=en-US&gl=US&ceid=US:en"
        ),
        "keywords": REALTIME_KEYWORDS,
        "category": "ESG News",
        "parser": "rss_news",
    },
    # ── Consulting & Advisory firm insights (Real Time Updates) ───────────────
    # None of these publish a usable ESG-specific RSS feed, so each is scraped
    # directly from its insights/thought-leadership page via parser="html"
    # (no RSS, no Google News). org label always comes from the source dict.
    # Caveat: most of these are JS-rendered SPAs, so a static GET may return few
    # or no article links — see parse_html's docstring for the Playwright path.
    {
        "org": "PwC",
        "url": "https://www.pwc.com/us/en/services/esg/sustainability-news-brief.html",
        "rss": None,
        "keywords": REALTIME_KEYWORDS,
        "category": "ESG News",
        "parser": "html",
    },
    {
        "org": "EY",
        "url": "https://www.ey.com/en_in/insights",
        "rss": None,
        "keywords": REALTIME_KEYWORDS,
        "category": "ESG News",
        "parser": "html",
    },
    {
        "org": "KPMG",
        "url": "https://kpmg.com/in/en/insights/esg.html",
        "rss": None,
        "keywords": REALTIME_KEYWORDS,
        "category": "ESG News",
        "parser": "html",
    },
    {
        "org": "Accenture",
        "url": "https://www.accenture.com/in-en/insights-index?filter=Sustainability",
        "rss": None,
        "keywords": REALTIME_KEYWORDS,
        "category": "ESG News",
        "parser": "html",
    },
    {
        "org": "Deloitte",
        "url": "https://www.deloitte.com/us/en/insights/topics/environmental-social-governance.html",
        "rss": None,
        "keywords": REALTIME_KEYWORDS,
        "category": "ESG News",
        "parser": "html",
    },
    {
        "org": "Khaitan & Co",
        "url": "https://www.khaitanco.com/thought-leadership",
        "rss": None,
        "keywords": REALTIME_KEYWORDS,
        "category": "ESG News",
        "parser": "html",
    },
    {
        "org": "McKinsey",
        "url": "https://www.mckinsey.com/capabilities/sustainability/our-insights",
        "rss": None,
        "keywords": REALTIME_KEYWORDS,
        "category": "ESG News",
        "parser": "html",
    },
    {
        "org": "BCG",
        "url": "https://www.bcg.com/capabilities/climate-change-sustainability/insights",
        "rss": None,
        "keywords": REALTIME_KEYWORDS,
        "category": "ESG News",
        "parser": "html",
    },
    {
        "org": "Bain",
        "url": "https://www.bain.com/insights/?filters=%7Cservices%28285%29",
        "rss": None,
        "keywords": REALTIME_KEYWORDS,
        "category": "ESG News",
        "parser": "html",
    },
    {
        "org": "PIB India",
        "url": "https://www.pib.gov.in/allRel.aspx?reg=1&lang=1",
        "rss": "https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3",
        "rss_gnews": (
            "https://news.google.com/rss/search"
            "?q=site:pib.gov.in+ESG+OR+sustainability+OR+climate+OR+carbon"
            "&hl=en-IN&gl=IN&ceid=IN:en"
        ),
        "keywords": PIB_INDIA_KEYWORDS,
        "category": "ESG News",
        "parser": "rss_news",
    },
    # ── Regulatory ────────────────────────────────────────────────────────────
    {
        "org": "SEBI Master Circular",
        "url": "https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=1&ssid=6&smid=0",
        "rss": None,
        "keywords": SEBI_KEYWORDS,
        "category": "Regulatory",
        "parser": "sebi",
    },
    {
        "org": "SEBI Circulars",
        "url": "https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=1&ssid=7&smid=0",
        "rss": None,
        "keywords": SEBI_KEYWORDS,
        "category": "Regulatory",
        "parser": "sebi",
    },
    {
        "org": "SEBI Advisory/Guidance",
        "url": "https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=1&ssid=96&smid=0",
        "rss": None,
        "keywords": SEBI_KEYWORDS,
        "category": "Regulatory",
        "parser": "sebi",
    },
    {
        "org": "SEBI Gazette Notification",
        "url": "https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=1&ssid=82&smid=0",
        "rss": None,
        "keywords": SEBI_KEYWORDS,
        "category": "Regulatory",
        "parser": "sebi",
    },
]

# ==========================================
# 2. HTTP SESSION SETUP
# ==========================================

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
})

# Warm SEBI session once at startup
try:
    SESSION.get("https://www.sebi.gov.in/", timeout=15)
except Exception:
    pass

# ==========================================
# 3. HELPER UTILITIES
# ==========================================

def make_uid(url: str, title: str = "") -> str:
    """Stable hash for dedup – based on URL (title as fallback)."""
    content = url.strip() if url and url.startswith("http") else title.strip()
    return hashlib.md5(content.encode("utf-8")).hexdigest()[:14]

def first_keyword_match(text: str, keywords: list) -> str | None:
    """
    Return the most specific (longest) keyword whose whole-word form appears in text.
    Sorting by length descending ensures 'ESG Disclosure' wins over 'ESG',
    'BRSR Core' wins over 'BRSR', 'Carbon Border Adjustment' wins over 'Carbon Border', etc.
    """
    text_lower = text.lower()
    for kw in sorted(keywords, key=len, reverse=True):
        pattern = rf"(?<![a-zA-Z0-9]){re.escape(kw.lower())}(?![a-zA-Z0-9])"
        if re.search(pattern, text_lower):
            return kw
    return None


# ─────────────────────────────────────────────────────────────────────────────
# CODE-LEVEL RELEVANCE GATE  (ESG News only — no API involved)
# ─────────────────────────────────────────────────────────────────────────────
# Relevance filtering happens HERE, deterministically, at scrape time.
# The Gemini step downstream is used ONLY to write summaries — it never
# decides what is included in or excluded from the digest.

# Title patterns that mark low-signal PR items with no compliance content:
# executive appointments, awards/recognition, anniversaries, webinars/event
# promos. Matched against the TITLE only, and only for ESG News sources
# (never tenders/SEBI, where e.g. "wins contract" is the whole point).
NOISE_TITLE_RE = re.compile(
    r"\b("
    r"appoints?|"
    r"names\s+\w+(\s+\w+){0,3}\s+as\b|"          # "Names Jane Doe as CSO"
    r"joins\s+(\w+\s+)?as\b|"                     # "Joins Acme as Head of ESG"
    r"promotes?\s+\w+|hires\s+\w+|"
    r"new\s+(chief|head\s+of)\b|"
    r"award(s|ed)?\b|wins\s+(award|prize|recognition)|"
    r"recogni[sz]ed\s+(as|for|by)|"
    r"named\s+(to|among|one\s+of)\b|"             # "Named to Fortune list"
    r"anniversar(y|ies)|celebrates?\b|honou?red\b|"
    r"webinar|register\s+now|join\s+us\b"
    r")",
    re.IGNORECASE,
)

# Bare catch-all terms: matching ONE of these alone is not evidence of
# compliance relevance — almost any corporate press release mentions
# "sustainability" or "ESG" once. An ESG News item passes the gate only if
# it matches at least one SPECIFIC keyword, or at least TWO distinct
# generic ones.
GENERIC_KEYWORDS = frozenset(k.lower() for k in [
    "ESG", "Sustainability", "Sustainab", "Sustainable Finance",
    "Green Finance", "Emissions", "Solar", "Net Zero", "Climate Change",
    "Climate Action", "Renewable Energy", "Assurance", "Assessment",
    "Biodiversity", "Biomass", "Deforestation", "Greenhouse Gas",
    "Greenhouse Gases", "GHG", "COP",
])


def all_keyword_matches(text: str, keywords: list) -> list[str]:
    """All whole-word keyword matches in text, most specific (longest) first."""
    text_lower = text.lower()
    found = []
    for kw in sorted(keywords, key=len, reverse=True):
        pattern = rf"(?<![a-zA-Z0-9]){re.escape(kw.lower())}(?![a-zA-Z0-9])"
        if re.search(pattern, text_lower):
            found.append(kw)
    return found


def news_keyword_gate(title: str, body_text: str, keywords: list) -> str | None:
    """
    Relevance gate for ESG News items. Returns the keyword to tag the item
    with if it passes, else None.

    Rules:
      1. Title matching NOISE_TITLE_RE (appointment/award/anniversary/webinar
         PR) → rejected outright.
      2. At least one SPECIFIC keyword match (anything not in
         GENERIC_KEYWORDS) → passes, tagged with the most specific match.
      3. Otherwise, two or more DISTINCT generic matches → passes.
         A single bare "Sustainability" or "ESG" mention is not enough.
    """
    if NOISE_TITLE_RE.search(title or ""):
        return None
    matches = all_keyword_matches(f"{title} {body_text}", keywords)
    if not matches:
        return None
    specific = [m for m in matches if m.lower() not in GENERIC_KEYWORDS]
    if specific:
        return specific[0]          # longest-first ordering preserved
    if len(set(m.lower() for m in matches)) >= 2:
        return matches[0]
    return None


# ─────────────────────────────────────────────────────────────────────────────
# RELEVANCE RANKING  (deterministic, code-side — no API involved)
# ─────────────────────────────────────────────────────────────────────────────
# Items are scored after dedup and sorted descending within each category, so
# the most reader-relevant items render at the top of every section. The score
# only affects ORDER — it never drops anything (filtering already happened at
# the news_keyword_gate).

# Regulatory instruments and binding-framework terms: the highest-signal
# content for a compliance reader. Matching any of these earns the top boost.
HIGH_SIGNAL_TERMS = frozenset(k.lower() for k in [
    "BRSR", "BRSR Core", "LODR", "Listing Obligations and Disclosure Requirements",
    "CBAM", "Carbon Border Adjustment", "EU ETS", "EU Carbon Tax", "EU Green Deal",
    "CSRD", "ISSB", "IFRS S1", "IFRS S2", "TCFD", "TNFD", "EUDR", "RED III",
    "EPR", "Extended Producer Responsibility", "Global Plastics Treaty",
    "Article 6", "Article 6.2", "Joint Crediting Mechanism", "JCM", "CCTS",
    "Carbon Tax", "Carbon Price", "Compliance Carbon", "Taxonomy",
    "Paris Agreement", "Kunming Montreal",
])

# India-relevance signal: explicit India hooks in the text get a strong boost
# because the digest's readers are India-focused counsel.
INDIA_RELEVANCE_RE = re.compile(
    r"\b(india|indian|sebi|rbi|brsr|lodr|ccts|niti\s+aayog|moefcc|cpcb|"
    r"gift\s+city|nse|bse|rupee|inr)\b",
    re.IGNORECASE,
)

# Sources whose coverage is inherently India-centric get a smaller flat boost,
# so e.g. a PIB or Khaitan item outranks an equally-scored global wire piece.
INDIA_FOCUSED_ORGS = frozenset([
    "PIB India", "Khaitan & Co",
    "SEBI Master Circular", "SEBI Circulars",
    "SEBI Advisory/Guidance", "SEBI Gazette Notification",
])


def relevance_score(title: str, snippet: str, org: str = "") -> int:
    """
    Deterministic relevance score for ordering items within a section.
    Components (max ~15):
      +4  any high-signal regulatory/instrument term present
      +4  explicit India hook in title or snippet
      +2  at least one specific (non-generic) keyword match
      +2  source is an India-focused org
      +1..4  breadth: number of distinct keywords matched (capped)
      +1  a tracked keyword appears in the TITLE itself (not just body)
    """
    title = title or ""
    snippet = snippet or ""
    text = f"{title} {snippet}"
    matches = set(m.lower() for m in all_keyword_matches(text, REALTIME_KEYWORDS))

    score = 0
    if matches & HIGH_SIGNAL_TERMS:
        score += 4
    if INDIA_RELEVANCE_RE.search(text):
        score += 4
    if matches - GENERIC_KEYWORDS:
        score += 2
    if org in INDIA_FOCUSED_ORGS:
        score += 2
    score += min(len(matches), 4)
    if all_keyword_matches(title, REALTIME_KEYWORDS):
        score += 1
    return score

def clean_snippet(text: str, max_len: int = 260) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len] + "…" if len(text) > max_len else text

def fetch_soup(url: str, extra_headers: dict | None = None) -> BeautifulSoup | None:
    """GET a page and return its parsed soup, or None on failure."""
    try:
        hdrs = {**(extra_headers or {})}
        if "sebi.gov.in" in url:
            hdrs["Referer"] = "https://www.sebi.gov.in/"
        r = SESSION.get(url, headers=hdrs, timeout=20)
        if r.status_code == 200 and len(r.text) > 50:
            return BeautifulSoup(r.text, "html.parser")
        print(f"    ⚠  HTTP {r.status_code} from {url}")
    except Exception as e:
        print(f"    ✗  Fetch error for {url}: {e}")
    return None


def fetch_rss(rss_url: str):
    """
    Fetch an RSS/Atom feed robustly:
    1. Try SESSION (browser UA, avoids 403s from feedparser's default UA).
       Pass raw bytes to feedparser so it can detect encoding from the XML
       declaration — r.text decoded by requests can corrupt multi-byte chars
       when the Content-Type charset is wrong or absent.
    2. If SESSION fetch fails (non-200 / network error), fall back to letting
       feedparser make its own request (handles redirects, etags, etc).
    Returns a feedparser FeedParserDict or None on total failure.
    """
    if not FEEDPARSER_AVAILABLE:
        return None
    try:
        r = SESSION.get(
            rss_url,
            headers={"Accept": "application/rss+xml,application/xml,text/xml,*/*;q=0.8"},
            timeout=20,
        )
        if r.status_code == 200 and len(r.content) > 100:
            feed = feedparser.parse(r.content)   # ← bytes, not r.text
            if feed.entries:
                return feed
            # Feed parsed but empty — still a "success" fetch, return it
            return feed
        print(f"    ⚠  RSS HTTP {r.status_code} from {rss_url}")
    except Exception as e:
        print(f"    ✗  RSS fetch error for {rss_url}: {e}")
    # Last resort: feedparser's own transport (handles some edge cases SESSION misses)
    try:
        feed = feedparser.parse(rss_url)
        if not getattr(feed, "bozo", False) or feed.entries:
            return feed
    except Exception:
        pass
    return None

# ==========================================
# 4. PARSERS
# ==========================================

def _scrape_article_links(
    soup: BeautifulSoup, base_url: str, keywords: list, org: str,
    category: str, seen: set, recency_cutoff=NEWS_CUTOFF,
) -> list[dict]:
    """
    Shared HTML article extractor used by both parse_html (direct blog/insights
    scraping) and parse_rss's HTML fallback.

    Walks three progressively broader strategies over the parsed page:
      A. <article> containers,
      B. div/section elements whose class hints at a post/card/article/story,
      C. <h2>/<h3> heading links (broadest catch-all).
    For each candidate it keyword-matches title + body, applies the recency
    gate when a date is extractable (undated items are kept — age unknown),
    and dedupes against the caller-supplied `seen` set (mutated in place).
    Returns an uncapped list of hit dicts; callers apply MAX_PER_SOURCE.
    """
    hits = []

    # Strategy A: <article> containers
    containers = soup.find_all("article") or []

    # Strategy B: divs/sections with post/card/article in class
    if not containers:
        containers = soup.select(
            'div[class*="post"], div[class*="card"], div[class*="article"], '
            'div[class*="entry"], div[class*="item"], div[class*="story"], '
            'div[class*="news"], section[class*="article"]'
        )

    # Strategy C: all h2/h3 heading links (broadest catch-all)
    heading_links = []
    for h in soup.find_all(["h2", "h3"]):
        a = h.find("a", href=True)
        if a:
            heading_links.append((a, h.parent or h))

    def _consider(title: str, href: str, body_text: str, ctx) -> None:
        if not href.startswith("http"):
            href = urljoin(base_url, href)
        if href in seen or len(title) < 8:
            return
        # ESG News goes through the deterministic relevance gate (noise-title
        # filter + generic-keyword rule); other categories keep plain matching.
        if category == "ESG News":
            kw = news_keyword_gate(title, body_text, keywords)
        else:
            kw = first_keyword_match(f"{title} {body_text}", keywords)
        if not kw:
            return
        date_el = None
        if ctx is not None:
            date_el = (ctx.find(attrs={"class": re.compile(r"date|time|publish", re.I)})
                       or ctx.find("time"))
        raw_date = date_el.get_text(strip=True) if date_el else ""
        # Recency gate: skip only if a parseable date is older than the cutoff.
        if raw_date:
            item_dt = parse_fuzzy_date(raw_date)
            if item_dt and item_dt < recency_cutoff:
                return
        seen.add(href)
        hits.append({
            "org": org,
            "category": category,
            "keyword": kw,
            "title": title,
            "article_url": href,
            "date": fmt_date(raw_date) if raw_date else "",
            "snippet": clean_snippet(body_text),
            "uid": make_uid(href, title),
        })

    for container in containers:
        a = container.find("a", href=True)
        if not a:
            continue
        _consider(
            a.get_text(strip=True), a["href"],
            container.get_text(separator=" ", strip=True), container,
        )

    for a, context in heading_links:
        body_text = context.get_text(separator=" ", strip=True) if context else a.get_text(strip=True)
        _consider(a.get_text(strip=True), a["href"], body_text, context)

    return hits


def parse_html(source: dict) -> list[dict]:
    """
    Direct HTML scraper for blog / insights / thought-leadership pages that do
    not expose a usable RSS feed (the consulting & advisory firms). No RSS and
    no Google News — fetches the insights page itself and extracts article
    links via the shared _scrape_article_links strategies, then caps the result
    at MAX_PER_SOURCE.

    NOTE: several of these sites (McKinsey, BCG, Bain, PwC, Deloitte, Accenture,
    EY, KPMG) render their listing pages client-side. A plain static GET may
    return a near-empty shell, in which case this parser yields few or no hits.
    To reliably surface articles from those SPAs, a JS-rendering path
    (Playwright, as in parse_gem_bidplus) would be needed.
    """
    hits, seen = [], set()
    soup = fetch_soup(source["url"])
    if not soup:
        print(f"    ⚠  could not fetch {source['url']}")
        return []

    hits = _scrape_article_links(
        soup, source["url"], source["keywords"], source["org"],
        source["category"], seen,
    )
    print(f"    ✓ HTML scrape: {len(hits)} match(es) (capped at {MAX_PER_SOURCE})")
    return hits[:MAX_PER_SOURCE]


def parse_rss(source: dict) -> list[dict]:
    """
    Primary parser for all news/blog sites.
    Strategy:
      1. Try primary RSS feed (direct site feed, browser UA) — collect all keyword hits.
      2. Also try rss_gnews (Google News RSS for the domain) in ALL cases — not just as
         a fallback. Results are merged via a shared `seen` set so no article is doubled.
         Google News often indexes articles from a longer window than the site's own feed,
         adding coverage that primary RSS alone would miss.
      3. Apply MAX_PER_SOURCE cap to prevent any single outlet flooding the digest.
      4. If both RSS paths yield zero hits, fall back to HTML scraping.
    """
    hits, seen = [], set()
    keywords = source["keywords"]
    org = source["org"]
    base_url = source["url"]

    def _process_feed(feed) -> list[dict]:
        """Extract keyword-matching hits from a feedparser feed object.

        For Google News RSS feeds (gnews=True in source), each entry carries
        the real publisher in entry.source.title (e.g. 'ESG Today', 'Reuters').
        We use that as the 'org' label so the email shows the original outlet,
        not 'Google News'.
        """
        is_gnews = source.get("gnews", False)
        result = []
        for entry in feed.entries:
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            summary = BeautifulSoup(entry.get("summary", ""), "html.parser").get_text()
            pub_date = entry.get("published", entry.get("updated", ""))

            # Resolve per-entry publisher for Google News feeds
            if is_gnews:
                src_info = entry.get("source", {})
                # feedparser exposes source as a FeedParserDict; .get() works on it
                entry_org = (
                    src_info.get("title", "")
                    or getattr(src_info, "title", "")
                    or org
                )
            else:
                entry_org = org

            # Skip items older than NEWS_LOOKBACK_DAYS
            dt = parse_fuzzy_date(pub_date)
            if dt and dt < NEWS_CUTOFF:
                continue

            # Skip event-calendar entries: two "@HH:MM am/pm" patterns (start + end time)
            # are the structural fingerprint of calendar listings, not news articles.
            _combined_check = f"{title} {summary}"
            if re.search(
                r'@\s*\d{1,2}:\d{2}\s*(?:am|pm).{0,80}@\s*\d{1,2}:\d{2}\s*(?:am|pm)',
                _combined_check, re.IGNORECASE
            ):
                continue

            # ESG News goes through the deterministic relevance gate (noise-title
            # filter + generic-keyword rule); other categories keep plain matching.
            if source["category"] == "ESG News":
                kw = news_keyword_gate(title, summary, keywords)
            else:
                kw = first_keyword_match(f"{title} {summary}", keywords)
            if kw and link not in seen:
                seen.add(link)
                result.append({
                    "org": entry_org,
                    "category": source["category"],
                    "keyword": kw,
                    "title": title,
                    "article_url": link,
                    "date": fmt_date(pub_date),
                    "snippet": clean_snippet(summary),
                    "uid": make_uid(link, title),
                })
        return result

    # ─ Primary RSS ────────────────────────────────────────────────────────────
    # Always collect from the primary RSS feed when available.
    rss_url = source.get("rss")
    if rss_url:
        feed = fetch_rss(rss_url)
        if feed and feed.entries:
            primary_hits = _process_feed(feed)   # updates shared `seen` set
            hits.extend(primary_hits)
            print(f"    ✓ primary RSS: {len(feed.entries)} entries → {len(primary_hits)} match(es)")
        else:
            print(f"    ⚠  primary RSS failed or 0 entries")

    # ─ Google News RSS — always try for additional coverage ───────────────────
    # No longer a pure fallback: runs regardless of whether primary RSS succeeded.
    # The shared `seen` set in _process_feed deduplicates across both passes,
    # so only net-new articles are added. This surfaces articles that are in
    # Google's index but may have dropped off the site's own RSS window.
    rss_gnews = source.get("rss_gnews")
    if rss_gnews:
        feed = fetch_rss(rss_gnews)
        if feed and feed.entries:
            gnews_hits = _process_feed(feed)     # shared `seen` deduplicates
            hits.extend(gnews_hits)
            print(f"    ✓ Google News RSS: {len(feed.entries)} entries → {len(gnews_hits)} new match(es)")
        else:
            print(f"    ⚠  Google News RSS also empty")

    # Apply per-source cap: no single outlet dominates the digest.
    if hits:
        return hits[:MAX_PER_SOURCE]

    # ─ HTML fallback ───────────────────────────────────────────────────────
    # Skip for Google News primary sources: their base_url is news.google.com,
    # which is not HTML-scrapeable in the conventional sense.
    if source.get("gnews"):
        return []

    soup = fetch_soup(base_url)
    if not soup:
        return []

    # Reuse the shared extractor (same three strategies as before). The `seen`
    # set is carried over from the RSS passes so already-seen links are skipped.
    hits.extend(_scrape_article_links(
        soup, base_url, keywords, org, source["category"], seen,
    ))
    return hits


def parse_sebi(source: dict) -> list[dict]:
    """
    SEBI government portal parser.
    Pages are JSP-rendered server-side; content is in <table> rows.
    Requires a warmed session (homepage cookie) to avoid 403.
    Only returns items published within RECENCY_DAYS to avoid stale dumps.
    """
    hits, seen = [], set()
    keywords = source["keywords"]
    org = source["org"]
    base_url = source["url"]

    soup = fetch_soup(base_url, extra_headers={"Referer": "https://www.sebi.gov.in/"})
    if not soup:
        return []

    DATE_RE = re.compile(
        r"(\d{1,2}[-/]\w{3}[-/]\d{4}|\w{3,9}\s+\d{1,2},?\s+\d{4}"
        r"|\d{2}[-/]\d{2}[-/]\d{4}|\d{1,2}\s+\w{3,9}\s+\d{4})",
        re.IGNORECASE,
    )

    def _process(title, href, row_text, date_str):
        """Shared logic: dedup, recency check, keyword match, append."""
        nonlocal hits, seen
        if href in seen or len(title) < 5:
            return
        # Recency gate — skip if date is parseable but older than cutoff
        dt = parse_fuzzy_date(date_str)
        if dt and dt < RECENCY_CUTOFF:
            return
        kw = first_keyword_match(title, keywords) or first_keyword_match(row_text, keywords)
        if not kw:
            return
        seen.add(href)
        hits.append({
            "org": org,
            "category": source["category"],
            "keyword": kw,
            "title": title,
            "article_url": href,
            "date": fmt_date(date_str) if date_str else "",
            "snippet": clean_snippet(row_text),
            "uid": make_uid(href, title),
        })

    for row in soup.find_all("tr"):
        a = row.find("a", href=True)
        if not a:
            continue
        title = a.get_text(strip=True)
        href = a["href"]
        if not href.startswith("http"):
            href = urljoin("https://www.sebi.gov.in", href)
        row_text = row.get_text(separator=" ", strip=True)
        date_match = DATE_RE.search(row_text)
        _process(title, href, row_text, date_match.group(1) if date_match else "")

    # Also catch list-item format pages
    for li in soup.find_all("li"):
        a = li.find("a", href=True)
        if not a:
            continue
        title = a.get_text(strip=True)
        href = a["href"]
        if not href.startswith("http"):
            href = urljoin("https://www.sebi.gov.in", href)
        li_text = li.get_text(separator=" ", strip=True)
        date_match = DATE_RE.search(li_text)
        _process(title, href, li_text, date_match.group(1) if date_match else "")

    return hits


# ─────────────────────────────────────────────────────────────────────────────
# TENDER HELPERS  (module-level so all three tender parsers can share them)
# ─────────────────────────────────────────────────────────────────────────────

# Broad date regex: 26/01/2026  26-01-2026  05-Jun-2026  25 Jun 2026
_TENDER_DATE_RE = re.compile(
    r'\b(\d{1,2}[-/]\w{3}[-/]\d{4}|\d{1,2}[-/]\d{2}[-/]\d{4}|\d{1,2}\s+\w{3,9}\s+\d{4})\b',
    re.IGNORECASE,
)


def _extract_deadline(row_text: str, now: datetime):
    """
    Return (deadline_dt, deadline_str) for the nearest future date in row_text,
    or (None, '') when all dates are in the past (tender already closed).
    """
    candidates = []
    for m in _TENDER_DATE_RE.finditer(row_text):
        dt = parse_fuzzy_date(m.group(1))
        if dt and dt > now:
            candidates.append((dt, m.group(1)))
    if not candidates:
        return None, ""
    candidates.sort(key=lambda x: x[0])
    dt, raw = candidates[0]
    return dt, fmt_date(raw)


def _eprocure_keyword_hits(org: str, keywords: list, seen: set, now: datetime) -> list[dict]:
    """
    Use eprocure.gov.in's classic JSP keyword-search (always server-side rendered,
    no JavaScript required) to find active ESG tenders.  Results are deduped
    against the caller-supplied `seen` set (modified in-place).
    """
    hits = []
    SEARCH_URL = (
        "https://eprocure.gov.in/eprocure/app"
        "?component=%24SearchString"
        "&page=FrontEndLatestActiveTenders"
        "&service=page"
        "&searchString={kw}"
        "&Search=Search"
    )
    # Core ESG clusters — each term covers a family of related tenders
    TERMS = [
        "ESG", "sustainability", "carbon", "BRSR",
        "net+zero", "climate", "GHG", "renewable",
    ]
    for term in TERMS:
        url = SEARCH_URL.format(kw=requests.utils.quote(term))
        soup = fetch_soup(url)
        if not soup:
            continue
        for row in soup.find_all("tr"):
            row_text = row.get_text(separator=" ", strip=True)
            if len(row_text) < 10:
                continue
            a = row.find("a", href=True)
            if not a:
                continue
            title = a.get_text(strip=True)
            href = a["href"]
            if not href.startswith("http"):
                href = urljoin("https://eprocure.gov.in", href)
            if href in seen or len(title) < 5:
                continue
            kw_match = first_keyword_match(f"{title} {row_text}", keywords)
            if not kw_match:
                continue
            deadline_dt, deadline_str = _extract_deadline(row_text, now)
            if deadline_dt is None:
                continue
            seen.add(href)
            hits.append({
                "org": org,
                "category": "Tenders",
                "keyword": kw_match,
                "title": title[:150],
                "article_url": href,
                "date": f"Deadline: {deadline_str}",
                "snippet": clean_snippet(row_text),
                "uid": make_uid(href, title),
            })
    return hits


# ─────────────────────────────────────────────────────────────────────────────

def parse_gem(source: dict) -> list[dict]:
    """
    Legacy GeM / government tender parser — direct HTML table scrape.
    Kept for any source that still uses parser="gem".  parse_cppp and
    parse_gem_bidplus call this internally as their first attempt.
    """
    hits, seen = [], set()
    keywords = source["keywords"]
    org = source["org"]
    base_url = source["url"]
    now = datetime.now(timezone.utc)

    soup = fetch_soup(base_url)
    if not soup:
        return []

    for row in soup.find_all("tr"):
        row_text = row.get_text(separator=" ", strip=True)
        if len(row_text) < 10:
            continue
        kw = first_keyword_match(row_text, keywords)
        if not kw:
            continue
        deadline_dt, deadline_str = _extract_deadline(row_text, now)
        if deadline_dt is None:
            continue
        a = row.find("a", href=True)
        href = urljoin(base_url, a["href"]) if a else base_url
        title = a.get_text(strip=True) if a else row_text[:150]
        if href in seen:
            continue
        seen.add(href)
        hits.append({
            "org": org,
            "category": source["category"],
            "keyword": kw,
            "title": title[:150],
            "article_url": href,
            "date": f"Deadline: {deadline_str}",
            "snippet": clean_snippet(row_text),
            "uid": make_uid(href, title),
        })
    return hits


def parse_cppp(source: dict) -> list[dict]:
    """
    CPPP / eprocure tender parser covering all five tracking-list portals:
      • gem.gov.in/cppp
      • eprocure.gov.in/cppp/latestactivetendersnew/cpppdata  (Central)
      • eprocure.gov.in/cppp/latestactivetendersnew/mmpdata   (State)
      • eprocure.gov.in/cppp/latestactivetendersnew/cpppdata/<base64>

    Strategy
    --------
    1. Direct HTML GET of the source URL (works if the page uses SSR/JSP).
    2. If the direct GET yields 0 keyword-matching rows (page is likely
       AJAX-rendered or returns a near-empty skeleton), fall back to the
       eprocure classic keyword-search API — JSP-rendered, always scrapable.
    3. The eprocure search is queried for each ESG term cluster, collecting
       all active tenders (deadline in future) that match TENDER_KEYWORDS.
    """
    org = source["org"]
    keywords = source["keywords"]
    base_url = source["url"]
    now = datetime.now(timezone.utc)
    hits: list[dict] = []
    seen: set[str] = set()

    # ── Step 1: direct HTML scrape ────────────────────────────────────────────
    soup = fetch_soup(base_url)
    if soup:
        for row in soup.find_all("tr"):
            row_text = row.get_text(separator=" ", strip=True)
            if len(row_text) < 10:
                continue
            kw_match = first_keyword_match(row_text, keywords)
            if not kw_match:
                continue
            deadline_dt, deadline_str = _extract_deadline(row_text, now)
            if deadline_dt is None:
                continue
            a = row.find("a", href=True)
            href = urljoin(base_url, a["href"]) if a else base_url
            title = a.get_text(strip=True) if a else row_text[:150]
            if href in seen:
                continue
            seen.add(href)
            hits.append({
                "org": org,
                "category": "Tenders",
                "keyword": kw_match,
                "title": title[:150],
                "article_url": href,
                "date": f"Deadline: {deadline_str}",
                "snippet": clean_snippet(row_text),
                "uid": make_uid(href, title),
            })

    if hits:
        print(f"    ✓ direct HTML: {len(hits)} active tender(s)")
        return hits

    # ── Step 2: eprocure classic keyword-search fallback ──────────────────────
    print(f"    ⚠  direct HTML returned 0 rows (likely AJAX-rendered) — "
          f"falling back to eprocure keyword search")
    fallback = _eprocure_keyword_hits(org, keywords, seen, now)
    if fallback:
        print(f"    ✓ eprocure search: {len(fallback)} active tender(s)")
    hits.extend(fallback)
    return hits


def parse_gem_bidplus(source: dict) -> list[dict]:
    """
    GeM Bidding Portal parser for bidplus.gem.gov.in/all-bids.

    The page is a React SPA — a static GET returns an empty shell.
    Playwright renders the JavaScript, then two extraction paths run in order:

    1. JSON interception  — Playwright captures every XHR/fetch response the
       React app makes while loading.  If any carry bid data, we use that
       directly (clean, stable, no HTML parsing needed).

    2. DOM scraping  — if no JSON was captured, we read the fully-rendered
       HTML via BeautifulSoup and look for bid card/row elements.

    3. eprocure fallback  — if Playwright isn't installed or both paths above
       yield nothing, fall back to the eprocure keyword-search API (same as
       before).  Install with:
           pip install playwright
           playwright install chromium
    """
    org = source["org"]
    keywords = source["keywords"]
    now = datetime.now(timezone.utc)
    hits: list[dict] = []
    seen: set[str] = set()

    ESG_TERMS = ["ESG", "sustainability", "carbon", "BRSR", "net+zero", "climate", "GHG"]
    BASE = "https://bidplus.gem.gov.in"

    # ── Playwright availability check ─────────────────────────────────────────
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
        PLAYWRIGHT_OK = True
    except ImportError:
        PLAYWRIGHT_OK = False

    if not PLAYWRIGHT_OK:
        print("    ⚠  playwright not installed — falling back to eprocure search")
        print("       To scrape GeM directly: pip install playwright && playwright install chromium")
        fallback = _eprocure_keyword_hits(org, keywords, seen, now)
        if fallback:
            print(f"    ✓ eprocure fallback: {len(fallback)} active tender(s)")
        return fallback

    # ── Playwright scrape ─────────────────────────────────────────────────────
    def _parse_json_item(item: dict) -> dict | None:
        """Extract a standardised hit dict from a raw JSON bid object."""
        bid_no   = str(item.get("bidNumber") or item.get("bid_number") or item.get("id") or "")
        title    = str(item.get("bidTitle") or item.get("title") or item.get("name") or bid_no)
        closing  = str(item.get("bidSubmissionClosingDate") or item.get("closingDate") or "")
        org_name = str(item.get("orgName") or item.get("ministry") or org)
        detail   = f"{BASE}/bidlisting/{bid_no}" if bid_no else f"{BASE}/all-bids"

        if detail in seen or len(title) < 5:
            return None
        kw = first_keyword_match(title, keywords)
        if not kw:
            return None
        dl_dt = parse_fuzzy_date(closing)
        if dl_dt and dl_dt <= now:
            return None   # already closed

        seen.add(detail)
        return {
            "org": f"{org} — {org_name}" if org_name != org else org,
            "category": "Tenders",
            "keyword": kw,
            "title": title[:150],
            "article_url": detail,
            "date": f"Deadline: {fmt_date(closing)}" if closing else "",
            "snippet": clean_snippet(f"Bid: {bid_no} | Closing: {closing} | {title}"),
            "uid": make_uid(detail, title),
        }

    def _parse_dom(html: str) -> list[dict]:
        """DOM fallback: extract from the rendered HTML page."""
        soup = BeautifulSoup(html, "html.parser")
        dom_hits = []

        # GeM renders bids as card divs and/or table rows.
        # Try progressively broader selectors so a redesign degrades gracefully.
        containers = (
            soup.select(".bid-card")
            or soup.select("[class*='bidCard']")
            or soup.select("[class*='bid-item']")
            or soup.find_all("tr", class_=lambda c: c and "bid" in c.lower())
            or soup.find_all(
                "div",
                class_=lambda c: c and any(
                    k in c.lower() for k in ("bid", "tender", "card", "item")
                ),
            )
        )

        for container in containers:
            a = container.find("a", href=True)
            text = container.get_text(separator=" ", strip=True)
            title = a.get_text(strip=True) if a else text[:120]
            href = a["href"] if a else ""
            if not href.startswith("http"):
                href = urljoin(BASE, href) if href else f"{BASE}/all-bids"
            if href in seen or len(title) < 5:
                continue
            kw = first_keyword_match(f"{title} {text}", keywords)
            if not kw:
                continue
            dl_dt, dl_str = _extract_deadline(text, now)
            if dl_dt is None:
                continue
            seen.add(href)
            dom_hits.append({
                "org": org,
                "category": "Tenders",
                "keyword": kw,
                "title": title[:150],
                "article_url": href,
                "date": f"Deadline: {dl_str}",
                "snippet": clean_snippet(text),
                "uid": make_uid(href, title),
            })
        return dom_hits

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = ctx.new_page()

        # Capture every JSON response the SPA makes while loading.
        # We collect items into a list; the response handler runs synchronously
        # between Playwright events so no threading issues arise.
        intercepted: list[dict] = []

        def _on_response(resp):
            try:
                if resp.status != 200:
                    return
                if "json" not in resp.headers.get("content-type", ""):
                    return
                data = resp.json()
                # Unwrap common API envelope shapes
                items = None
                for key in ("data", "bids", "result", "content", "bidList",
                            "items", "records", "tenderList"):
                    candidate = data.get(key) if isinstance(data, dict) else None
                    if isinstance(candidate, list) and candidate:
                        items = candidate
                        break
                if items is None and isinstance(data, list) and data:
                    items = data
                if items:
                    intercepted.extend(items)
            except Exception:
                pass

        page.on("response", _on_response)

        for term in ESG_TERMS:
            intercepted.clear()
            search_url = f"{BASE}/advance-search?searchedKeyword={requests.utils.quote(term)}"

            try:
                page.goto(search_url, wait_until="networkidle", timeout=30_000)
            except PWTimeout:
                print(f"    ⚠  GeM timeout for '{term}', skipping")
                continue
            except Exception as e:
                print(f"    ⚠  GeM error for '{term}': {e}")
                continue

            # ── Path 1: JSON interception ─────────────────────────────────
            if intercepted:
                for item in intercepted:
                    hit = _parse_json_item(item)
                    if hit:
                        hits.append(hit)
                if hits:
                    print(f"    ✓ GeM JSON ('{term}'): {len(hits)} so far")
                    continue   # move to next term; JSON was sufficient

            # ── Path 2: DOM scraping ──────────────────────────────────────
            dom_hits = _parse_dom(page.content())
            if dom_hits:
                hits.extend(dom_hits)
                print(f"    ✓ GeM DOM ('{term}'): {len(dom_hits)} tender(s)")

        browser.close()

    if hits:
        print(f"    ✓ Playwright total: {len(hits)} active GeM tender(s)")
        return hits

    # ── Fallback: eprocure keyword-search ────────────────────────────────────
    print("    ⚠  Playwright found 0 results — falling back to eprocure search")
    fallback = _eprocure_keyword_hits(org, keywords, seen, now)
    if fallback:
        print(f"    ✓ eprocure fallback: {len(fallback)} active tender(s)")
    hits.extend(fallback)
    return hits


PARSER_MAP = {
    "rss_news": parse_rss,
    "html": parse_html,
    "sebi": parse_sebi,
    "gem": parse_gem,
    "cppp": parse_cppp,
    "gem_bidplus": parse_gem_bidplus,
}

# ==========================================
# 5. MAIN SCRAPING LOOP
# ==========================================

print(f"\n{'='*60}")
print(f"  ESG Intelligence Sweep — {TODAY_STR}")
print(f"  Sources: {len(SOURCES)}")
print(f"{'='*60}\n")

all_results: list[dict] = []

for source in SOURCES:
    org = source["org"]
    parser_fn = PARSER_MAP.get(source["parser"], parse_rss)
    print(f"  → {org}")
    hits = parser_fn(source)
    print(f"    ✓ {len(hits)} match(es)")
    all_results.extend(hits)

print(f"\n  Total raw matches: {len(all_results)}")

# ==========================================
# 6. DEDUPLICATION AGAINST HISTORY (URL-LEVEL)
# ==========================================

df_today = pd.DataFrame(all_results) if all_results else pd.DataFrame(
    columns=["org", "category", "keyword", "title", "article_url", "date", "snippet", "uid"]
)

# Within-run dedup: same article can appear from multiple Google News queries.
# Keep the first occurrence (usually the best keyword match, since queries are
# ordered from most-specific to broadest).
if not df_today.empty:
    before = len(df_today)
    df_today = df_today.drop_duplicates(subset="uid", keep="first").reset_index(drop=True)
    dupes_dropped = before - len(df_today)
    if dupes_dropped:
        print(f"  Cross-query duplicates removed: {dupes_dropped}")

if os.path.exists(HISTORY_PATH) and not df_today.empty:
    df_history = pd.read_csv(HISTORY_PATH)
    if "uid" in df_history.columns:
        known_uids = set(df_history["uid"].dropna())
        df_new = df_today[~df_today["uid"].isin(known_uids)].copy()
    else:
        # Old history format (org + keyword) — migrate gracefully
        print("  ⚠  Old history format detected; migrating to URL-based dedup.")
        df_new = df_today.copy()
        df_history = pd.DataFrame(columns=["uid", "date_seen"])
else:
    df_new = df_today.copy()
    df_history = pd.DataFrame(columns=["uid", "date_seen"])

print(f"  New items after dedup: {len(df_new)}")

# ── Relevance ordering ────────────────────────────────────────────────────
# Score every item and sort descending. Sections filter by category at render
# time, so a single global sort yields most-relevant-first within each section.
# Stable sort: equal scores keep their original scrape order. Order only —
# nothing is dropped here.
if not df_new.empty:
    df_new["relevance"] = df_new.apply(
        lambda r: relevance_score(r.get("title", ""), r.get("snippet", ""), r.get("org", "")),
        axis=1,
    )
    df_new = df_new.sort_values(
        "relevance", ascending=False, kind="stable"
    ).reset_index(drop=True)
    top = df_new.iloc[0]
    print(f"  Ranked by relevance — top item [{top['relevance']}]: {top['title'][:70]}")

# ==========================================
# 7. PERSIST HISTORY
# ==========================================

if not df_new.empty:
    new_hist = pd.DataFrame({
        "uid": df_new["uid"].tolist(),
        "date_seen": TODAY_STR,
    })
    pd.concat([df_history, new_hist]).drop_duplicates(subset="uid").to_csv(
        HISTORY_PATH, index=False
    )

# ==========================================
# 7.5  AI DIGEST SUMMARY  (Gemini REST API via requests)
# ==========================================
#
# No SDK needed — uses the Gemini v1beta REST API directly via requests.
# API key:   set GEMINI_API_KEY environment variable (GitHub Secret).
#
# Generates two things in a single API call:
#   • digest_summary  – 2-3 sentence overview of today's most significant themes,
#                       rendered as a highlighted block at the top of the email.
#   • article_summary – one crisp sentence per item, shown below the snippet in
#                       each article card (labelled "🤖 AI Summary").
#
# Falls back gracefully (returns empty strings/dict) on any failure so the
# email pipeline is never blocked.

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models/"

# Primary model first, then fallbacks tried in order when the primary returns
# 503/429/500 on every retry attempt.  Add or reorder as new models become available.
GEMINI_MODELS = [
    "gemini-2.5-flash",   # primary — latest stable Flash
    "gemini-2.0-flash",   # first fallback
    "gemini-1.5-flash",   # last-resort stable fallback
]

def generate_ai_summaries(df_new: pd.DataFrame) -> tuple[str, dict]:
    """
    SUMMARIZATION ONLY. The API never filters, ranks, or excludes items —
    relevance is decided deterministically at scrape time (news_keyword_gate).
    Every item passed in gets a summary; if the API call fails, the email
    still renders with all items, just without Overview blocks.

    Returns:
        digest_summary  : str   – 4-6 sentence briefing across all items
        uid_summaries   : dict  – {uid: summary} for every item
    """
    if df_new.empty:
        return "", {}

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print("  ⚠  GEMINI_API_KEY not set — skipping AI summaries.")
        return "", {}

    print("  → Generating AI summaries via Gemini REST API…")

    # Build a numbered item list for the prompt
    rows = list(df_new.itertuples(index=False))
    numbered = []
    for i, row in enumerate(rows, start=1):
        snippet = (getattr(row, "snippet", "") or "")[:500]
        numbered.append(
            f'{i}. [{row.category}] "{row.title}" — {row.org}. {snippet}'
        )

    prompt = (
        "You are a senior ESG legal analyst preparing a daily intelligence digest for "
        "in-house counsel and compliance officers at large Indian and multinational corporations. "
        "Your readers are tier-1 legal professionals — they need precision, not padding.\n\n"
        f"Below are {len(rows)} items scraped today:\n\n"
        + "\n".join(numbered)
        + "\n\n"
        "Your ONLY job is to summarize. Do not select, rank, exclude, or skip any item — "
        "write a summary for EVERY numbered item.\n\n"
        "═══ PART 1: PER-ITEM SUMMARIES ═══\n"
        "TARGET LENGTH FOR ALL SUMMARIES: 60-70 words. This is a hard target — "
        "do not stop at 30 words because the source is thin; use all available facts "
        "and expand each point fully before moving to the next.\n\n"
        "Each summary is a dense prose paragraph of 60-70 words (the FACTS), "
        "followed by a separate INDIA IMPACT analysis of 1-2 sentences (see below).\n"
        "FACTS paragraph — extract and fully expand every concrete fact the source provides:\n"
        "  • For regulatory/policy items: exact instrument name, issuing authority, and "
        "jurisdiction; the specific change in full (what the current requirement is and "
        "precisely what is proposed or now mandated in its place); full scope — every "
        "entity type, product class, asset class, sector, or geography explicitly "
        "mentioned; all explicit timelines, effective dates, consultation windows, or "
        "phase-in periods; penalties or enforcement mechanisms if stated; all figures, "
        "thresholds, percentages, or quantitative parameters; the issuing body's stated "
        "rationale in their own terms.\n"
        "  • For market/industry items: what the development is, who published it, and "
        "in what context; every concrete detail the source gives — figures, rankings, "
        "named companies, specific findings, methodology, scope; which entities, sectors, "
        "or markets are covered and what specifically changes, is disclosed, or is measured.\n"
        "If the source is thin on a category, omit that category — never write "
        "placeholder sentences. Reach the 60-70 word target by elaborating the facts "
        "that ARE present, not by padding with generic statements. "
        "Pure fact only in the FACTS paragraph.\n\n"
        "═══ PART 2: INDIA IMPACT (every item where plausible) ═══\n"
        "After the FACTS paragraph, append 1-2 sentences (wherever the development "
        "plausibly supports it) that analyse how this development impacts ONE — and only "
        "the single most relevant one — of the following angles:\n"
        "  (1) Indian firms, or multinational firms doing business in India — what the "
        "development means for their operations, disclosure, supply chains, or exposure;\n"
        "  (2) Investors looking to deploy capital into or out of India — what it signals "
        "for capital flows, asset allocation, due diligence, or risk pricing;\n"
        "  (3) How the development compares competitively or on a regulatory basis to "
        "India's current ESG landscape — e.g. how a global regulatory shift (CSRD, CBAM, "
        "ISSB, SEC, EU taxonomy) compares to SEBI/BRSR/BRSR Core/LODR frameworks, or how "
        "global banking/financial-sector climate commitments compare to Indian banking "
        "and RBI actions.\n"
        "Begin this analysis with the literal prefix 'India impact: ' so it is visually "
        "distinct from the facts. Pick the angle the source most directly supports; do not "
        "force all three. This analytical sentence MAY draw a reasoned comparison or "
        "implication (this is the one place analysis is permitted), but it must remain "
        "grounded in the facts of the item and India's known ESG framework — never invent "
        "specific figures, dates, or events not supportable from the material. "
        "If the development has no plausible India angle whatsoever (e.g. a purely local "
        "US municipal matter), omit the India impact line rather than forcing one. "
        "Keep the India impact to 1-2 sentences maximum.\n\n"
        "═══ PART 3: DIGEST ═══\n"
        "Write a 4-6 sentence factual briefing that spans ALL items "
        "across every category (Regulatory, Tenders, ESG News). "
        "Do NOT focus on a single article — the digest must reflect the full breadth "
        "of today's items.\n"
        "Structure: lead with any regulatory or policy developments (instrument, "
        "issuing body, jurisdiction, scope, dates if stated); then cover market/"
        "industry themes by grouping related articles into a single factual thread rather "
        "than listing each article individually. End on a concrete fact, not a directive.\n"
        "If there are no regulatory items, cover the market items in 4-5 factual sentences "
        "grouped by theme. Every sentence must name a specific development, organisation, "
        "figure, or jurisdiction from the source material. "
        "No advice, no calls to action, no filler.\n\n"
        "CRITICAL RULES:\n"
        "• Every numbered item MUST receive a summary string — never null, never "
        "an empty string, never a skipped key.\n"
        "• In the FACTS paragraph, only report facts explicitly stated in the source. "
        "Never infer, speculate, or add context not in the text. The reasoned comparison "
        "permitted in the 'India impact' line is the SOLE exception, and even there you "
        "must not invent specific figures, dates, or events.\n"
        "• Write in plain declarative prose. No bullet points inside summaries.\n"
        "• ABSOLUTE BAN on directive/advisory language anywhere, including the India "
        "impact line: 'should', 'must monitor', 'recommended', 'compliance teams should', "
        "'it is advised', 'watch this space', 'teams are urged', or any equivalent "
        "phrasing. The India impact line states implications and comparisons as analysis "
        "(e.g. 'Indian issuers face a wider disclosure gap than under BRSR Core'), never "
        "as instructions to the reader.\n"
        "• No negative placeholder sentences for missing information.\n\n"
        "Respond ONLY with a valid JSON object — no markdown fences, no preamble:\n"
        "{\n"
        '  "digest_summary": "...",\n'
        '  "article_summaries": {\n'
        '    "1": "...",\n'
        '    "2": "..."\n'
        "  }\n"
        "}"
    )

    # Model + retry strategy:
    #   • Try each model in GEMINI_MODELS in order.
    #   • Per model: up to 2 retries (3 total attempts) on 503/429/500,
    #     with a short backoff.  These are transient server-side errors.
    #   • On persistent failure, move to the next model in the list.
    #   • Only give up entirely once all models are exhausted.
    RETRYABLE_CODES = {429, 500, 503}
    PER_MODEL_ATTEMPTS = 3
    RETRY_DELAYS = [15, 30]   # seconds before attempt 2 and 3

    data = None
    for model in GEMINI_MODELS:
        api_url = f"{GEMINI_BASE_URL}{model}:generateContent"
        print(f"    [GEMINI] trying model: {model}")
        model_success = False

        for attempt in range(1, PER_MODEL_ATTEMPTS + 1):
            try:
                resp = requests.post(
                    api_url,
                    params={"key": api_key},
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {
                            "responseMimeType": "application/json",
                            # 32768 gives safe headroom for 60-70 word summaries across
                            # up to 80+ articles (≈ 6000 words of summary content).
                            # The old 8192 cap was fine for shorter 1-sentence summaries
                            # but overflows with 60-70 word targets on busy days,
                            # causing finishReason=MAX_TOKENS and silently dropping all summaries.
                            "maxOutputTokens": 32768,
                        },
                    },
                    timeout=120,
                )
                print(f"    [GEMINI] {model} attempt {attempt}/{PER_MODEL_ATTEMPTS} — HTTP {resp.status_code}")

                if resp.status_code in RETRYABLE_CODES:
                    if attempt < PER_MODEL_ATTEMPTS:
                        delay = RETRY_DELAYS[attempt - 1]
                        print(f"    [GEMINI] {resp.status_code} — retrying in {delay}s…")
                        time.sleep(delay)
                        continue
                    else:
                        # All retries exhausted for this model — try next model
                        print(f"    [GEMINI] {model} unavailable after {PER_MODEL_ATTEMPTS} attempts — trying next model…")
                        break

                resp.raise_for_status()
                payload = resp.json()

                # Guard against truncated output: Gemini sets finishReason="MAX_TOKENS"
                # when it hits the output cap. 60-70 word summaries across 30-60 articles
                # can push output past a low token cap, so we use 32768.
                candidate = payload["candidates"][0]
                finish_reason = candidate.get("finishReason", "")
                if finish_reason == "MAX_TOKENS":
                    print(f"  ⚠  Gemini hit output token limit (MAX_TOKENS) — "
                          f"response is truncated. Skipping AI summaries.")
                    return "", {}

                raw = candidate["content"]["parts"][0]["text"].strip()
                print(f"    [GEMINI] raw response (first 300 chars): {raw[:300]}")

                # Strip markdown code fences — some Gemini model versions wrap the JSON
                # in ```json ... ``` even when responseMimeType is set to application/json.
                # This causes json.loads to raise JSONDecodeError and silently drops all
                # summaries. Strip fences defensively before parsing.
                if raw.startswith("```"):
                    raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
                    raw = re.sub(r"\s*```$", "", raw)
                    raw = raw.strip()

                try:
                    data = json.loads(raw)
                    model_success = True
                    break   # success — exit per-model retry loop
                except json.JSONDecodeError as json_err:
                    print(f"  ⚠  AI summary — JSON parse failed: {json_err}")
                    print(f"     Raw response snippet: {raw[:500]}")
                    return "", {}

            except requests.exceptions.RequestException as req_err:
                if attempt < PER_MODEL_ATTEMPTS:
                    delay = RETRY_DELAYS[attempt - 1]
                    print(f"    [GEMINI] request error on attempt {attempt}: {req_err} — retrying in {delay}s…")
                    time.sleep(delay)
                else:
                    print(f"    [GEMINI] {model} failed after {PER_MODEL_ATTEMPTS} attempts: {req_err} — trying next model…")
                    break

            except Exception as exc:
                import traceback
                print(f"  ⚠  AI summary — unexpected error: {exc}")
                traceback.print_exc()
                return "", {}

        if model_success:
            break   # don't try remaining models

    if data is None:
        print("  ⚠  AI summary — all models exhausted. Skipping.")
        return "", {}

    digest_summary = data.get("digest_summary", "")
    article_summaries = data.get("article_summaries", {})

    uid_summaries: dict[str, str] = {}

    for str_idx, summary in article_summaries.items():
        try:
            idx = int(str_idx) - 1
            if 0 <= idx < len(rows) and summary:
                uid_summaries[rows[idx].uid] = summary
        except (ValueError, IndexError):
            pass

    print(f"    ✓ AI summaries: {len(uid_summaries)}/{len(rows)} items summarized")
    return digest_summary, uid_summaries


# ==========================================
# 8. EMAIL HTML BUILDER  (Outlook-safe table layout — Trilegal theme)
# ==========================================
#
# Outlook desktop uses Word's HTML renderer, which ignores:
#   border-radius, box-shadow, overflow:hidden, opacity, rgba(),
#   max-width + margin:auto on divs, CSS border-left on <td>, and many CSS shorthands.
#
# Rules applied here:
#   • All layout via <table> — no <div> containers
#   • bgcolor attribute on every <td> alongside CSS background
#   • Explicit font-family on every text element
#   • Left-border accents via a narrow coloured <td> (CSS border-left ignored by Outlook)
#   • No border-radius, box-shadow, overflow:hidden, opacity, rgba()
#   • MSO conditional comment wrapper for centering the outer table
#   • Full <!DOCTYPE> + <html> envelope so Outlook parses correctly

# ── Trilegal brand palette ────────────────────────────────────────────────────
_NAVY      = "#0c1b33"   # primary deep navy
_NAVY_MED  = "#1a3560"   # medium navy (Tenders)
_TEAL_DARK = "#0d3325"   # dark teal-navy (ESG News)
_GOLD      = "#c9a047"   # Trilegal gold
_GOLD_BG   = "#faf4e3"   # very light gold (AI block / highlights)
_PAGE_BG   = "#eef0f5"   # outer page background
_WHITE     = "#ffffff"
_DIVIDER   = "#e4e9f0"   # card separator
_TEXT      = "#1a2744"   # primary body text
_MUTED     = "#7a8a9e"   # dates, secondary metadata
_SNIP      = "#4a5568"   # snippet text

CATEGORY_STYLE = {
    "Regulatory": {
        "header_bg": _NAVY,
        "badge_bg":  "#7b1d1d",
        "badge_fg":  "#fff8e7",
        "border":    _GOLD,
    },
    "Tenders": {
        "header_bg": _NAVY_MED,
        "badge_bg":  "#163870",
        "badge_fg":  "#dceeff",
        "border":    _GOLD,
    },
    "ESG News": {
        "header_bg": _TEAL_DARK,
        "badge_bg":  "#0a4f28",
        "badge_fg":  "#d4f7e3",
        "border":    _GOLD,
    },
}

_FONT  = "font-family:Arial,Helvetica,sans-serif;"
_SERIF = "font-family:Georgia,'Times New Roman',serif;"


def render_header(count: int) -> str:
    return (
        '<table width="100%" cellspacing="0" cellpadding="0" border="0">'
        # Gold top accent stripe
        f'<tr><td height="5" bgcolor="{_GOLD}" style="background:{_GOLD};font-size:0;line-height:0;">&nbsp;</td></tr>'
        # Main header
        f'<tr><td bgcolor="{_NAVY}" style="background:{_NAVY};padding:26px 32px 22px 32px;">'
        f'<p style="margin:0 0 7px 0;font-size:28px;font-weight:700;color:#ffffff;'
        f'letter-spacing:0.12em;{_FONT}">'
        "ESG DIGEST"
        "</p>"
        f'<p style="margin:0;font-size:13px;color:{_GOLD};letter-spacing:0.06em;'
        f'text-transform:uppercase;{_FONT}">'
        f"{TODAY_STR}&nbsp;&nbsp;&middot;&nbsp;&nbsp;"
        f'{count} new item{"s" if count != 1 else ""} identified today'
        "</p>"
        "</td></tr></table>"
    )


def render_summary_bar(df: pd.DataFrame) -> str:
    cells = [
        f'<td style="padding:0 12px 0 0;">'
        f'<span style="font-size:11px;color:{_MUTED};letter-spacing:0.07em;'
        f'text-transform:uppercase;{_FONT}">Today</span></td>'
    ]
    for cat, cfg in CATEGORY_STYLE.items():
        n = len(df[df["category"] == cat])
        if n:
            pill_bg, pill_fg, pill_w = _GOLD, _NAVY, "font-weight:700;"
        else:
            pill_bg, pill_fg, pill_w = "#c5cdd9", "#6b7a8d", ""
        cells.append(
            f'<td style="padding:0 10px 0 0;">'
            f'<span style="background:{pill_bg};color:{pill_fg};font-size:12px;'
            f'{pill_w}padding:4px 14px;display:inline-block;{_FONT}">'
            f'{n}&nbsp;{cat}'
            "</span></td>"
        )
    return (
        '<table width="100%" cellspacing="0" cellpadding="0" border="0">'
        f'<tr><td bgcolor="{_WHITE}" style="background:{_WHITE};padding:11px 28px;'
        f'border-bottom:2px solid {_GOLD};">'
        f'<table cellspacing="0" cellpadding="0" border="0"><tr>{"".join(cells)}</tr></table>'
        "</td></tr></table>"
    )


def _split_india_impact(summary: str) -> tuple[str, str]:
    """
    Split an AI summary into (facts, india_impact).

    The prompt instructs Gemini to prefix the analysis with 'India impact:'.
    We locate that marker case-insensitively (tolerating an optional leading
    space/newline and variants like 'India Impact -'), return the text before it
    as the facts paragraph and the text after the colon as the analysis line.
    If no marker is found, the whole string is treated as facts and the second
    element is empty.
    """
    if not summary:
        return "", ""
    # Match 'India impact' followed by ':' or '-' (with optional surrounding space)
    m = re.search(r"\s*India\s+impact\s*[:\-]\s*", summary, flags=re.IGNORECASE)
    if not m:
        return summary.strip(), ""
    facts = summary[: m.start()].strip()
    india = summary[m.end():].strip()
    # Guard: if the split produced an empty facts side, keep everything as facts
    if not facts:
        return summary.strip(), ""
    return facts, india


def render_article_card(row: pd.Series, cfg: dict, ai_summary: str = "") -> str:
    # Keyword tag pill — sits above title in a horizontal row
    tag_pill = (
        f'<span style="background:{cfg["badge_bg"]};color:{cfg["badge_fg"]};'
        f'font-size:11px;font-weight:700;padding:3px 10px;display:inline-block;'
        f'letter-spacing:0.04em;text-transform:uppercase;{_FONT}">'
        f'{row["keyword"]}</span>'
    )
    tag_row = (
        '<table width="100%" cellspacing="0" cellpadding="0" border="0" style="margin-bottom:9px;">'
        f'<tr><td>{tag_pill}</td></tr></table>'
    )

    # Meta line: date · **source** (source bold and slightly darker)
    date_part = (
        f'<span style="color:{_MUTED};font-size:12px;{_FONT}">{row["date"]}</span>'
        "&nbsp;&middot;&nbsp;"
        if row.get("date") else ""
    )
    meta_line = (
        f'<p style="margin:5px 0 7px 0;font-size:12px;color:{_MUTED};{_FONT}">'
        f'{date_part}'
        f'<strong style="color:{_SNIP};font-weight:700;{_FONT}">{row["org"]}</strong>'
        "</p>"
    )

    snippet_part = (
        f'<p style="color:{_SNIP};font-size:14px;margin:0 0 10px 0;line-height:1.7;{_SERIF}">'
        f'{row["snippet"]}</p>'
        if row.get("snippet") else ""
    )

    # Overview block — "Overview" label replaces "AI:", Georgia serif body text.
    # If the AI summary contains an "India impact:" analysis line, split it out and
    # render it on its own line with a bold gold-navy label so it reads as distinct
    # analysis sitting beneath the factual summary.
    overview_body = ""
    if ai_summary:
        facts_text, india_text = _split_india_impact(ai_summary)
        overview_body = (
            f'<p style="margin:0;font-size:14px;color:{_TEXT};line-height:1.7;{_SERIF}">'
            f'{facts_text}'
            "</p>"
        )
        if india_text:
            overview_body += (
                f'<p style="margin:8px 0 0 0;font-size:14px;color:{_TEXT};'
                f'line-height:1.7;{_SERIF}">'
                f'<span style="font-weight:700;color:{_NAVY};{_FONT}font-size:11px;'
                f'letter-spacing:0.05em;text-transform:uppercase;">India Impact</span>'
                f'&nbsp;&nbsp;{india_text}'
                "</p>"
            )

    overview_part = (
        '<table width="100%" cellspacing="0" cellpadding="0" border="0">'
        "<tr>"
        f'<td bgcolor="{_GOLD_BG}" style="background:{_GOLD_BG};padding:9px 12px;">'
        '<table width="100%" cellspacing="0" cellpadding="0" border="0"><tr>'
        # Label cell
        '<td width="1" valign="top" style="padding-right:10px;white-space:nowrap;">'
        f'<span style="font-size:11px;font-weight:700;color:{_NAVY};'
        f'letter-spacing:0.07em;text-transform:uppercase;{_FONT}">Overview</span>'
        "</td>"
        # Body cell
        '<td valign="top">'
        f'{overview_body}'
        "</td>"
        "</tr></table>"
        "</td></tr></table>"
        if ai_summary else ""
    )

    return (
        '<table width="100%" cellspacing="0" cellpadding="0" border="0">'
        "<tr>"
        # Gold left accent bar
        f'<td width="3" bgcolor="{_GOLD}" style="background:{_GOLD};font-size:0;line-height:0;">&nbsp;</td>'
        # Card body — full width, tags + title + meta + snippet + overview all at same indent
        f'<td bgcolor="{_WHITE}" style="background:{_WHITE};padding:14px 18px 13px 16px;'
        f'border-bottom:1px solid {_DIVIDER};">'
        f'{tag_row}'
        f'<a href="{row["article_url"]}" style="color:{_NAVY};font-weight:700;'
        f'font-size:15px;text-decoration:none;line-height:1.45;display:block;{_FONT}">'
        f'{row["title"]}</a>'
        f'{meta_line}'
        f'{snippet_part}'
        f'{overview_part}'
        "</td>"
        "</tr></table>"
    )


def render_category_section(
    df: pd.DataFrame, category: str, cfg: dict,
    always_show: bool = False, uid_summaries: dict | None = None
) -> str:
    uid_summaries = uid_summaries or {}
    cat_df = df[df["category"] == category]
    n = len(cat_df)
    count_label = f'({n} item{"s" if n != 1 else ""})'

    if cat_df.empty:
        if not always_show:
            return ""
        cards = (
            '<table width="100%" cellspacing="0" cellpadding="0" border="0">'
            "<tr>"
            f'<td width="3" bgcolor="{_GOLD}" style="background:{_GOLD};font-size:0;line-height:0;">&nbsp;</td>'
            f'<td bgcolor="{_WHITE}" style="background:{_WHITE};padding:14px 18px;">'
            f'<p style="margin:0;font-size:13px;color:{_MUTED};font-style:italic;{_FONT}">'
            f"No new {category} items today."
            "</p></td></tr></table>"
        )
    else:
        cards = "".join(
            render_article_card(row, cfg, ai_summary=uid_summaries.get(row["uid"], ""))
            for _, row in cat_df.iterrows()
        )

    return (
        '<table width="100%" cellspacing="0" cellpadding="0" border="0" '
        f'style="margin-bottom:18px;border:1px solid {_DIVIDER};">'

        # Section header
        "<tr>"
        f'<td bgcolor="{cfg["header_bg"]}" style="background:{cfg["header_bg"]};padding:10px 20px;">'
        '<table width="100%" cellspacing="0" cellpadding="0" border="0"><tr>'
        # Gold left bar inside header
        f'<td width="3" bgcolor="{_GOLD}" style="background:{_GOLD};font-size:0;">&nbsp;</td>'
        '<td style="padding-left:10px;">'
        f'<span style="color:#ffffff;font-size:13px;font-weight:700;letter-spacing:0.07em;'
        f'text-transform:uppercase;{_FONT}">'
        f'{category}&nbsp;'
        f'<span style="font-weight:400;font-size:11px;color:{_GOLD};">{count_label}</span>'
        "</span>"
        "</td></tr></table>"
        "</td>"
        "</tr>"

        f'<tr><td style="padding:0;">{cards}</td></tr>'

        "</table>"
    )


def render_footer() -> str:
    return (
        '<table width="100%" cellspacing="0" cellpadding="0" border="0">'
        f'<tr><td height="3" bgcolor="{_GOLD}" style="background:{_GOLD};font-size:0;line-height:0;">&nbsp;</td></tr>'
        f'<tr><td bgcolor="{_NAVY}" style="background:{_NAVY};padding:13px 24px;text-align:center;">'
        f'<p style="font-size:11px;color:{_MUTED};margin:0;{_FONT}">'
        "All links go directly to source articles&nbsp;&middot;&nbsp;"
        "Historical duplicates auto-filtered"
        "</p></td></tr></table>"
    )


def render_ai_digest_block(digest_summary: str) -> str:
    """Daily brief block — gold left bar, Georgia serif body, no emoji."""
    if not digest_summary:
        return ""
    return (
        '<table width="100%" cellspacing="0" cellpadding="0" border="0" '
        'style="margin-bottom:6px;">'
        "<tr>"
        f'<td width="4" bgcolor="{_GOLD}" style="background:{_GOLD};font-size:0;line-height:0;">&nbsp;</td>'
        f'<td bgcolor="{_GOLD_BG}" style="background:{_GOLD_BG};padding:16px 20px;">'
        f'<p style="margin:0 0 7px 0;font-size:11px;font-weight:700;color:{_MUTED};'
        f'text-transform:uppercase;letter-spacing:0.09em;{_FONT}">'
        "Daily Brief"
        "</p>"
        f'<p style="margin:0;font-size:15px;color:{_TEXT};line-height:1.7;{_SERIF}">'
        f"Good morning. {digest_summary}"
        "</p>"
        "</td>"
        "</tr></table>"
    )


def build_email(df_new: pd.DataFrame) -> str:
    # ── AI summaries (single API call; summarization ONLY — never filters) ────
    # Relevance was already decided deterministically at scrape time
    # (news_keyword_gate). Every item in df_new is rendered.
    digest_summary, uid_summaries = generate_ai_summaries(df_new)
    df_render = df_new

    if df_render.empty:
        content = (
            '<table width="100%" cellspacing="0" cellpadding="0" border="0">'
            "<tr>"
            f'<td width="4" bgcolor="{_GOLD}" style="background:{_GOLD};font-size:0;line-height:0;">&nbsp;</td>'
            f'<td bgcolor="{_WHITE}" style="background:{_WHITE};padding:20px 18px;">'
            f'<p style="color:{_TEXT};font-size:14px;margin:0;line-height:1.7;{_SERIF}">'
            "Daily scan completed &mdash; no new matching items found today. "
            "All sources are up to date."
            "</p></td></tr></table>"
        )
        summary_html = ""
        ai_digest_html = ""
    else:
        content = "".join(
            render_category_section(
                df_render, cat, cfg,
                always_show=(cat in ("Regulatory", "Tenders")),
                uid_summaries=uid_summaries,
            )
            for cat, cfg in CATEGORY_STYLE.items()
        )
        summary_html = render_summary_bar(df_render)
        ai_digest_html = render_ai_digest_block(digest_summary)

    inner = render_header(len(df_render)) + summary_html + ai_digest_html + content + render_footer()

    # MSO conditional comment centres the email in Outlook desktop (which ignores
    # max-width + margin:auto on divs).  Non-Outlook clients use the div instead.
    return (
        "<!DOCTYPE html>"
        '<html lang="en"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="X-UA-Compatible" content="IE=edge">'
        "<title>ESG Digest</title>"
        "</head>"
        '<body style="margin:0;padding:0;background:#eef0f5;">'

        "<!--[if mso]>"
        '<table align="center" width="680" cellspacing="0" cellpadding="0" border="0">'
        '<tr><td bgcolor="#eef0f5" style="background:#eef0f5;padding:20px;">'
        "<![endif]-->"

        f'<div style="max-width:680px;margin:0 auto;padding:20px;background:#eef0f5;{_FONT}">'
        + inner +
        "</div>"

        "<!--[if mso]></td></tr></table><![endif]-->"
        "</body></html>"
    )


# ==========================================
# 9. SAVE EMAIL FILE + DELIVER
# ==========================================

email_body = build_email(df_new)

with open("Email_Summary.html", "w", encoding="utf-8") as f:
    f.write(email_body)
print("\n  Email HTML saved to Email_Summary.html")

print("  Sending to Power Automate...")
try:
    resp = requests.post(
        POWER_AUTOMATE_URL,
        data=email_body.encode("utf-8"),
        headers={"Content-Type": "text/html; charset=utf-8"},
        timeout=30,
    )
    if resp.status_code in (200, 202):
        print("  ✅ Delivered successfully.")
    else:
        print(f"  ⚠  Power Automate responded: HTTP {resp.status_code}")
        print(f"     Body: {resp.text[:200]}")
except Exception as e:
    print(f"  ✗  Delivery failed: {e}")

print(f"\n{'='*60}")
print("  Run complete.")
print(f"{'='*60}\n")
