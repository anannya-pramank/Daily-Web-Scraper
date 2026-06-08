import os
import re
import json
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

# Tighter window for ESG news articles in the daily digest — hard cap at 48 hours
NEWS_CUTOFF = datetime.now(timezone.utc) - timedelta(hours=48)


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
    # ── Broad catch-alls (intentionally last) ───────────────────────────────
    "Sustainability", "Green Finance", "ESG", "Emissions", "Solar",
]

SEBI_KEYWORDS = [
    # ESG-specific SEBI reporting obligations (narrow — avoids false-positive LODR/MPS circulars)
    "BRSR", "BRSR Core",
    # Sustainability & ESG terms that appear in SEBI circular titles
    "Sustainability", "ESG",
    # Green / sustainable / social finance
    "Green Bond", "Social Bond", "Sustainability Bond", "Green Finance",
    # Climate & carbon
    "Climate", "Carbon", "Net Zero",
    # Nature & water
    "Biodiversity", "Water Stewardship",
    # International frameworks SEBI references
    "TCFD", "GRI", "ISSB", "IFRS S",
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
        "org": "ESG Clarity",
        "url": "https://esgclarity.com/",
        "rss": "https://esgclarity.com/feed",
        "rss_gnews": (
            "https://news.google.com/rss/search"
            "?q=site:esgclarity.com+ESG+OR+sustainable+OR+carbon"
            "&hl=en-US&gl=US&ceid=US:en"
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
    {
        "org": "PIB India",
        "url": "https://www.pib.gov.in/allRel.aspx?reg=1&lang=1",
        "rss": "https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3",
        "rss_gnews": (
            "https://news.google.com/rss/search"
            "?q=site:pib.gov.in+ESG+OR+sustainability+OR+climate+OR+carbon"
            "&hl=en-IN&gl=IN&ceid=IN:en"
        ),
        "keywords": REALTIME_KEYWORDS,
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

def parse_rss(source: dict) -> list[dict]:
    """
    Primary parser for all news/blog sites.
    Strategy:
      1. Try primary RSS feed (direct site feed, browser UA).
      2. If primary returns 0 entries, try rss_gnews (Google News RSS for the domain).
      3. If both RSS paths fail, fall back to HTML scraping.
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

            check = f"{title} {summary}"
            kw = first_keyword_match(check, keywords)
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
    rss_url = source.get("rss")
    if rss_url:
        feed = fetch_rss(rss_url)
        if feed and feed.entries:
            hits = _process_feed(feed)
            print(f"    ✓ primary RSS: {len(feed.entries)} entries → {len(hits)} match(es)")
            if hits:
                return hits
        else:
            status = f"HTTP error or 0 entries"
            print(f"    ⚠  primary RSS failed ({status}), trying fallback…")

    # ─ Google News RSS fallback ───────────────────────────────────────────────
    rss_gnews = source.get("rss_gnews")
    if rss_gnews and not hits:
        feed = fetch_rss(rss_gnews)
        if feed and feed.entries:
            hits = _process_feed(feed)
            print(f"    ✓ Google News RSS: {len(feed.entries)} entries → {len(hits)} match(es)")
            if hits:
                return hits
        else:
            print(f"    ⚠  Google News RSS also failed or empty")

    if hits:
        return hits

    # ─ HTML fallback ───────────────────────────────────────────────────────
    # Skip for Google News primary sources: their base_url is news.google.com,
    # which is not HTML-scrapeable in the conventional sense.
    if source.get("gnews"):
        return []

    soup = fetch_soup(base_url)
    if not soup:
        return []

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

    for container in containers:
        a = container.find("a", href=True)
        if not a:
            continue
        title = a.get_text(strip=True)
        href = a["href"]
        if not href.startswith("http"):
            href = urljoin(base_url, href)
        if href in seen or len(title) < 8:
            continue

        body_text = container.get_text(separator=" ", strip=True)
        kw = first_keyword_match(f"{title} {body_text}", keywords)
        if kw:
            seen.add(href)
            date_el = container.find(attrs={"class": re.compile(r"date|time|publish", re.I)}) \
                      or container.find("time")
            raw_date = date_el.get_text(strip=True) if date_el else ""
            # 48-hour gate: if a date is parseable and it's older than NEWS_CUTOFF, skip it.
            # Items with no extractable date are kept (age unknown).
            if raw_date:
                item_dt = parse_fuzzy_date(raw_date)
                if item_dt and item_dt < NEWS_CUTOFF:
                    continue
            hits.append({
                "org": org,
                "category": source["category"],
                "keyword": kw,
                "title": title,
                "article_url": href,
                "date": fmt_date(raw_date) if raw_date else "",
                "snippet": clean_snippet(body_text),
                "uid": make_uid(href, title),
            })

    for a, context in heading_links:
        title = a.get_text(strip=True)
        href = a["href"]
        if not href.startswith("http"):
            href = urljoin(base_url, href)
        if href in seen or len(title) < 8:
            continue

        body_text = context.get_text(separator=" ", strip=True) if context else title
        kw = first_keyword_match(f"{title} {body_text}", keywords)
        if kw:
            seen.add(href)
            date_el = (context.find(attrs={"class": re.compile(r"date|time|publish", re.I)})
                       or context.find("time")) if context else None
            raw_date = date_el.get_text(strip=True) if date_el else ""
            # 48-hour gate: same logic as container loop above
            if raw_date:
                item_dt = parse_fuzzy_date(raw_date)
                if item_dt and item_dt < NEWS_CUTOFF:
                    continue
            hits.append({
                "org": org,
                "category": source["category"],
                "keyword": kw,
                "title": title,
                "article_url": href,
                "date": fmt_date(raw_date) if raw_date else "",
                "snippet": clean_snippet(body_text),
                "uid": make_uid(href, title),
            })

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

GEMINI_API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-3.5-flash:generateContent"
)

def generate_ai_summaries(df_new: pd.DataFrame) -> tuple[str, dict]:
    """
    Returns:
        digest_summary  : str   – overall theme paragraph (HTML-safe plain text)
        uid_summaries   : dict  – {uid: one_line_summary} for every row in df_new
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
        snippet = (getattr(row, "snippet", "") or "")[:250]
        numbered.append(
            f'{i}. [{row.category}] "{row.title}" — {row.org}. {snippet}'
        )

    prompt = (
        f"You are a senior ESG legal analyst advising corporate counsel and compliance teams "
        f"at large Indian and multinational corporations. Below are {len(rows)} new ESG / "
        "sustainability intelligence items collected in today's automated digest.\n\n"
        + "\n".join(numbered)
        + "\n\n"
        "Respond ONLY with a valid JSON object — no markdown fences, no preamble:\n"
        "{\n"
        '  "digest_summary": "4-5 sentence executive briefing for a General Counsel or Chief '
        'Compliance Officer covering the most significant regulatory, compliance, and market '
        'developments today. Identify cross-cutting themes, flag any mandatory disclosure or '
        'filing obligations, and note near-term deadlines or enforcement risks.",\n'
        '  "article_summaries": {\n'
        '    "1": "6-7 line TL;DR for item 1 written for a legal/compliance audience. '
        'Structure as follows — '
        'Line 1: What happened or was announced (the core development). '
        'Line 2: Which entities, sectors, or jurisdictions are affected. '
        'Line 3: Specific regulatory or compliance obligations triggered (cite the rule, '
        'framework, or standard by name where possible). '
        'Line 4: Key deadlines, effective dates, or phase-in timelines. '
        'Line 5: Penalties, enforcement risks, or consequences of non-compliance if stated. '
        'Line 6: Immediate action points or watch items for compliance teams. '
        'Line 7: Broader strategic or precedent-setting significance, if any. '
        'Use precise regulatory language. Omit any line where the source provides no relevant '
        'information rather than padding with generic statements.",\n'
        '    "2": "6-7 line TL;DR for item 2 following the same structure"\n'
        "  }\n"
        "}"
    )

    try:
        resp = requests.post(
            GEMINI_API_URL,
            params={"key": api_key},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json"},
            },
            timeout=60,
        )
        print(f"    [GEMINI] HTTP status: {resp.status_code}")
        resp.raise_for_status()
        payload = resp.json()
        raw = payload["candidates"][0]["content"]["parts"][0]["text"].strip()
        print(f"    [GEMINI] raw response (first 300 chars): {raw[:300]}")
        data = json.loads(raw)
    except Exception as exc:
        import traceback
        print(f"  ⚠  AI summary failed: {exc}")
        traceback.print_exc()
        return "", {}

    digest_summary = data.get("digest_summary", "")
    article_summaries = data.get("article_summaries", {})

    uid_summaries: dict[str, str] = {}
    for str_idx, summary in article_summaries.items():
        try:
            idx = int(str_idx) - 1
            if 0 <= idx < len(rows):
                uid_summaries[rows[idx].uid] = summary
        except (ValueError, IndexError):
            pass

    print(f"    ✓ AI summaries generated ({len(uid_summaries)} article(s) + digest)")
    return digest_summary, uid_summaries


# ==========================================
# 8. EMAIL HTML BUILDER  (Outlook-safe table layout)
# ==========================================
#
# Outlook desktop uses Word's HTML renderer, which ignores:
#   border-radius, box-shadow, overflow:hidden, opacity, rgba(),
#   max-width + margin:auto on divs, and many CSS shorthands.
#
# Rules applied here:
#   • All layout via <table> — no <div> containers
#   • bgcolor attribute on every <td> alongside CSS background
#   • Explicit font-family on every text element
#   • No border-radius, box-shadow, overflow:hidden, opacity, rgba()
#   • MSO conditional comment wrapper for centering the outer table
#   • Full <!DOCTYPE> + <html> envelope so Outlook parses correctly

CATEGORY_STYLE = {
    "Regulatory": {
        "header_bg": "#78350f",
        "badge_bg":  "#d97706",
        "border":    "#f59e0b",
        "icon":      "⚖️",
    },
    "Tenders": {
        "header_bg": "#1e40af",
        "badge_bg":  "#1e40af",
        "border":    "#3b82f6",
        "icon":      "📋",
    },
    "ESG News": {
        "header_bg": "#065f46",
        "badge_bg":  "#059669",
        "border":    "#10b981",
        "icon":      "📰",
    },
}

_FONT = "font-family:Arial,Helvetica,sans-serif;"


def render_header(count: int) -> str:
    return (
        '<table width="100%" cellspacing="0" cellpadding="0" border="0">'
        '<tr><td bgcolor="#0f172a" style="background:#0f172a;padding:22px 28px;">'
        f'<h1 style="color:#f8fafc;margin:0 0 5px 0;font-size:22px;font-weight:700;{_FONT}">'
        "ESG Intelligence Digest"
        "</h1>"
        f'<p style="color:#94a3b8;margin:0;font-size:13px;{_FONT}">'
        f"{TODAY_STR} &nbsp;&middot;&nbsp; {count} new item(s) identified today"
        "</p>"
        "</td></tr></table>"
    )


def render_summary_bar(df: pd.DataFrame) -> str:
    cells = []
    for cat, cfg in CATEGORY_STYLE.items():
        n = len(df[df["category"] == cat])
        bg = cfg["header_bg"] if n else "#94a3b8"
        label = f'{cfg["icon"]} {n} {cat}'
        cells.append(
            f'<td style="padding:0 8px 0 0;">'
            f'<span style="background:{bg};color:#ffffff;font-size:11px;'
            f'font-weight:700;padding:3px 10px;display:inline-block;{_FONT}">'
            f'{label}</span></td>'
        )
    return (
        '<table width="100%" cellspacing="0" cellpadding="0" border="0">'
        '<tr><td bgcolor="#ffffff" style="background:#ffffff;padding:12px 20px;'
        'border-bottom:2px solid #e2e8f0;">'
        f'<table cellspacing="0" cellpadding="0" border="0"><tr>{"".join(cells)}</tr></table>'
        "</td></tr></table>"
    )


def render_article_card(row: pd.Series, cfg: dict, ai_summary: str = "") -> str:
    date_part = (
        f'<span style="color:#94a3b8;font-size:11px;{_FONT}">{row["date"]}</span>'
        " &nbsp;&middot;&nbsp; "
        if row.get("date") else ""
    )
    snippet_part = (
        f'<p style="color:#64748b;font-size:12px;margin:5px 0 0 0;line-height:1.6;{_FONT}">'
        f'{row["snippet"]}</p>'
        if row.get("snippet") else ""
    )
    ai_part = (
        f'<p style="color:#0f5132;font-size:12px;margin:5px 0 0 0;line-height:1.5;'
        f'background:#d1fae5;padding:4px 8px;{_FONT}">'
        f'&#x1F916; <strong>AI:</strong> {ai_summary}</p>'
        if ai_summary else ""
    )
    return (
        '<table width="100%" cellspacing="0" cellpadding="0" border="0">'
        '<tr><td bgcolor="#ffffff" style="background:#ffffff;padding:12px 20px;'
        'border-bottom:1px solid #e8edf3;">'
        '<table width="100%" cellspacing="0" cellpadding="0" border="0"><tr>'

        # Badge column
        '<td width="1" valign="top" style="padding-right:10px;white-space:nowrap;">'
        f'<span style="background:{cfg["badge_bg"]};color:#ffffff;font-size:10px;'
        f'font-weight:700;padding:2px 8px;display:inline-block;{_FONT}">'
        f'{row["keyword"]}</span>'
        "</td>"

        # Content column
        '<td valign="top">'
        f'<a href="{row["article_url"]}" style="color:#1e3a8a;font-weight:700;'
        f'font-size:13px;text-decoration:none;line-height:1.5;display:block;{_FONT}">'
        f'{row["title"]}</a>'
        f'<p style="margin:3px 0 0 0;font-size:11px;color:#94a3b8;{_FONT}">'
        f'{date_part}{row["org"]}</p>'
        f"{snippet_part}"
        f"{ai_part}"
        "</td>"

        "</tr></table>"
        "</td></tr></table>"
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
            '<tr><td bgcolor="#ffffff" style="background:#ffffff;padding:12px 20px;">'
            f'<p style="margin:0;font-size:12px;color:#94a3b8;font-style:italic;{_FONT}">'
            f"&#10003; No new {category} items today."
            "</p></td></tr></table>"
        )
    else:
        cards = "".join(
            render_article_card(row, cfg, ai_summary=uid_summaries.get(row["uid"], ""))
            for _, row in cat_df.iterrows()
        )

    return (
        f'<table width="100%" cellspacing="0" cellpadding="0" border="0" '
        f'style="margin-bottom:20px;border:1px solid {cfg["border"]};">'

        "<tr>"
        f'<td bgcolor="{cfg["header_bg"]}" style="background:{cfg["header_bg"]};padding:10px 20px;">'
        f'<span style="color:#ffffff;font-size:14px;font-weight:700;{_FONT}">'
        f'{cfg["icon"]}&nbsp; {category.upper()}&nbsp;'
        f'<span style="font-weight:400;font-size:12px;color:#e5e7eb;">{count_label}</span>'
        "</span>"
        "</td>"
        "</tr>"

        f'<tr><td style="padding:0;">{cards}</td></tr>'

        "</table>"
    )


def render_footer() -> str:
    return (
        '<table width="100%" cellspacing="0" cellpadding="0" border="0">'
        '<tr><td style="padding:14px 0;text-align:center;">'
        f'<p style="font-size:11px;color:#94a3b8;margin:0;{_FONT}">'
        "Automated ESG Intelligence System &nbsp;&middot;&nbsp; "
        "All links go directly to source articles &nbsp;&middot;&nbsp; "
        "Historical duplicates auto-filtered"
        "</p></td></tr></table>"
    )


def render_ai_digest_block(digest_summary: str) -> str:
    """Teal highlight block shown between the summary bar and the category sections."""
    if not digest_summary:
        return ""
    return (
        '<table width="100%" cellspacing="0" cellpadding="0" border="0" '
        'style="margin-bottom:12px;">'
        '<tr><td bgcolor="#ecfdf5" style="background:#ecfdf5;padding:14px 20px;'
        'border-left:4px solid #059669;">'
        f'<p style="margin:0 0 4px 0;font-size:11px;font-weight:700;'
        f'color:#065f46;text-transform:uppercase;letter-spacing:.05em;{_FONT}">'
        "&#x1F916;&nbsp; AI Digest — Today&#x2019;s Key Themes"
        "</p>"
        f'<p style="margin:0;font-size:13px;color:#1e3a5f;line-height:1.7;{_FONT}">'
        f"{digest_summary}"
        "</p>"
        "</td></tr></table>"
    )


def build_email(df_new: pd.DataFrame) -> str:
    # ── AI summaries (single API call for the whole digest) ───────────────────
    digest_summary, uid_summaries = generate_ai_summaries(df_new)

    if df_new.empty:
        content = (
            '<table width="100%" cellspacing="0" cellpadding="0" border="0">'
            '<tr><td bgcolor="#ffffff" style="background:#ffffff;padding:24px 20px;">'
            f'<p style="color:#475569;font-size:14px;margin:0;padding:14px 14px 14px 18px;'
            f'background:#f8fafc;border-left:4px solid #94a3b8;{_FONT}">'
            "&#10003; Daily scan completed &mdash; no new matching items found today. "
            "All sources are up to date."
            "</p></td></tr></table>"
        )
        summary_html = ""
        ai_digest_html = ""
    else:
        content = "".join(
            render_category_section(
                df_new, cat, cfg,
                always_show=(cat in ("Regulatory", "Tenders")),
                uid_summaries=uid_summaries,
            )
            for cat, cfg in CATEGORY_STYLE.items()
        )
        summary_html = render_summary_bar(df_new)
        ai_digest_html = render_ai_digest_block(digest_summary)

    inner = render_header(len(df_new)) + summary_html + ai_digest_html + content + render_footer()

    # MSO conditional comment centres the email in Outlook desktop (which ignores
    # max-width + margin:auto on divs).  Non-Outlook clients use the div instead.
    return (
        "<!DOCTYPE html>"
        '<html lang="en"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="X-UA-Compatible" content="IE=edge">'
        "<title>ESG Intelligence Digest</title>"
        "</head>"
        '<body style="margin:0;padding:0;background:#f1f5f9;">'

        "<!--[if mso]>"
        '<table align="center" width="680" cellspacing="0" cellpadding="0" border="0">'
        '<tr><td bgcolor="#f1f5f9" style="background:#f1f5f9;padding:20px;">'
        "<![endif]-->"

        f'<div style="max-width:680px;margin:0 auto;padding:20px;background:#f1f5f9;{_FONT}">'
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
