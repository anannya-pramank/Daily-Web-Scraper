import os
import re
import hashlib
import requests
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin
from email.utils import parsedate_to_datetime

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

# Tighter window for ESG news articles in the daily digest
NEWS_LOOKBACK_DAYS = 2
NEWS_CUTOFF = datetime.now(timezone.utc) - timedelta(days=NEWS_LOOKBACK_DAYS)


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

    The main listing page is a React SPA and cannot be scraped with a static
    GET.  This parser tries three escalating approaches:

    1. GeM advance-search page  — may expose partial SSR content.
    2. Known GeM Bid REST API patterns  — undocumented but commonly observed
       in browser network traffic; returns JSON if accessible.
    3. eprocure / CPPP keyword-search fallback  — the CPPP portal aggregates
       all central government procurement including GeM bids, so ESG tenders
       that appear on bidplus.gem.gov.in will also appear here.
    """
    org = source["org"]
    keywords = source["keywords"]
    now = datetime.now(timezone.utc)
    hits: list[dict] = []
    seen: set[str] = set()

    ESG_TERMS = ["ESG", "sustainability", "carbon", "BRSR", "net+zero", "climate", "GHG"]

    # ── Attempt 1: advance-search (may have partial SSR table) ───────────────
    for term in ESG_TERMS:
        url = (
            "https://bidplus.gem.gov.in/advance-search"
            f"?searchedKeyword={requests.utils.quote(term)}"
        )
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
                href = urljoin("https://bidplus.gem.gov.in", href)
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

    if hits:
        print(f"    ✓ GeM advance-search: {len(hits)} active tender(s)")
        return hits

    # ── Attempt 2: GeM Bid REST API (JSON) ───────────────────────────────────
    API_CANDIDATES = [
        "https://bidplus.gem.gov.in/rest/Bid/getBidSearchList",
        "https://bidplus.gem.gov.in/rest/Bid/bidListBySearchString",
    ]
    for api_url in API_CANDIDATES:
        for term in ["ESG", "sustainability", "carbon credit", "BRSR", "net zero"]:
            try:
                r = SESSION.get(
                    api_url,
                    params={"searchedKeyword": term, "page": 0, "pageSize": 25},
                    headers={
                        "Accept": "application/json, */*;q=0.8",
                        "X-Requested-With": "XMLHttpRequest",
                        "Referer": "https://bidplus.gem.gov.in/all-bids",
                    },
                    timeout=15,
                )
                if r.status_code != 200:
                    continue
                data = r.json()
                # Try common response envelope shapes
                items = None
                for key in ("data", "bids", "result", "content", "items", "bidList"):
                    candidate = data.get(key) if isinstance(data, dict) else None
                    if isinstance(candidate, list):
                        items = candidate
                        break
                if not items:
                    continue
                for item in items:
                    bid_no   = item.get("bidNumber") or item.get("bid_number") or ""
                    title    = str(item.get("bidTitle") or item.get("title") or bid_no)
                    closing  = str(item.get("bidSubmissionClosingDate")
                                   or item.get("closingDate") or "")
                    org_name = str(item.get("orgName") or item.get("ministry") or org)
                    detail_url = (
                        f"https://bidplus.gem.gov.in/bidlisting/{bid_no}"
                        if bid_no else "https://bidplus.gem.gov.in/all-bids"
                    )
                    if detail_url in seen:
                        continue
                    kw_match = first_keyword_match(title, keywords)
                    if not kw_match:
                        continue
                    # Try to get a future deadline from the closing-date field
                    deadline_dt = parse_fuzzy_date(closing)
                    if deadline_dt and deadline_dt <= now:
                        continue   # already closed
                    deadline_str = fmt_date(closing) if closing else ""
                    seen.add(detail_url)
                    hits.append({
                        "org": f"{org} — {org_name}" if org_name != org else org,
                        "category": "Tenders",
                        "keyword": kw_match,
                        "title": title[:150],
                        "article_url": detail_url,
                        "date": f"Deadline: {deadline_str}" if deadline_str else "",
                        "snippet": clean_snippet(
                            f"Bid No: {bid_no}  |  Closing: {closing}  |  {title}"
                        ),
                        "uid": make_uid(detail_url, title),
                    })
                if hits:
                    print(f"    ✓ GeM JSON API ({api_url.split('/')[-1]}): "
                          f"{len(hits)} active tender(s)")
                    return hits
            except Exception:
                continue

    # ── Attempt 3: eprocure / CPPP keyword-search (always reliable) ──────────
    print(f"    ⚠  bidplus.gem.gov.in is JS-rendered; "
          f"using CPPP/eprocure keyword search (aggregates GeM bids)")
    fallback = _eprocure_keyword_hits(org, keywords, seen, now)
    if fallback:
        print(f"    ✓ eprocure search fallback: {len(fallback)} active tender(s)")
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
# 8. EMAIL HTML BUILDER
# ==========================================

CATEGORY_STYLE = {
    "Regulatory": {
        "header_bg":  "#78350f",
        "badge_bg":   "#d97706",
        "border":     "#f59e0b",
        "light_bg":   "#fffbeb",
        "icon":       "⚖️",
    },
    "Tenders": {
        "header_bg":  "#1e40af",
        "badge_bg":   "#1e40af",
        "border":     "#3b82f6",
        "light_bg":   "#eff6ff",
        "icon":       "📋",
    },
    "ESG News": {
        "header_bg":  "#065f46",
        "badge_bg":   "#059669",
        "border":     "#10b981",
        "light_bg":   "#ecfdf5",
        "icon":       "📰",
    },
}

WRAPPER_STYLE = (
    'font-family:Arial,Helvetica,sans-serif;max-width:760px;'
    'margin:0 auto;background:#f1f5f9;padding:20px;'
)

def render_header(count: int) -> str:
    return f"""
    <div style="background:#0f172a;padding:22px 28px;border-radius:8px 8px 0 0;">
      <h1 style="color:#f8fafc;margin:0 0 4px;font-size:22px;font-weight:700;
                 letter-spacing:-0.3px;">
        ESG Intelligence Digest
      </h1>
      <p style="color:#94a3b8;margin:0;font-size:13px;">
        {TODAY_STR} &nbsp;·&nbsp; {count} new item(s) identified today
      </p>
    </div>"""


def render_summary_bar(df: pd.DataFrame) -> str:
    pills = []
    for cat, cfg in CATEGORY_STYLE.items():
        n = len(df[df["category"] == cat])
        if n:
            pills.append(
                f'<span style="background:{cfg["header_bg"]};color:#fff;'
                f'font-size:12px;padding:3px 12px;border-radius:20px;'
                f'font-weight:600;">{cfg["icon"]} {n} {cat}</span>'
            )
        else:
            pills.append(
                f'<span style="background:#e2e8f0;color:#94a3b8;'
                f'font-size:12px;padding:3px 12px;border-radius:20px;'
                f'font-weight:600;">{cfg["icon"]} 0 {cat}</span>'
            )
    pill_html = "&nbsp;&nbsp;".join(pills)
    return f"""
    <div style="background:#fff;padding:14px 20px;border:1px solid #e2e8f0;
                border-top:none;margin-bottom:20px;border-radius:0 0 6px 6px;">
      <p style="margin:0;font-size:13px;color:#475569;">{pill_html}</p>
    </div>"""


def render_article_card(row: pd.Series, cfg: dict) -> str:
    date_html = (
        f'<span style="color:#94a3b8;font-size:11px;">{row["date"]}</span>&nbsp;·&nbsp;'
        if row.get("date") else ""
    )
    snippet_html = (
        f'<p style="color:#64748b;font-size:12px;margin:6px 0 0;line-height:1.6;">'
        f'{row["snippet"]}</p>'
        if row.get("snippet") else ""
    )
    return f"""
    <div style="padding:14px 20px;border-bottom:1px solid #e8edf3;background:#fff;">
      <table width="100%" cellspacing="0" cellpadding="0" border="0">
        <tr>
          <td width="1" valign="top" style="padding-right:12px;">
            <span style="background:{cfg["badge_bg"]};color:#fff;font-size:10px;
                         font-weight:700;padding:2px 9px;border-radius:12px;
                         white-space:nowrap;display:inline-block;">
              {row["keyword"]}
            </span>
          </td>
          <td valign="top">
            <a href="{row["article_url"]}"
               style="color:#1e3a8a;font-weight:600;font-size:13px;
                      text-decoration:none;line-height:1.45;display:block;">
              {row["title"]}
            </a>
            <p style="margin:4px 0 0;font-size:11px;color:#94a3b8;">
              {date_html}{row["org"]}
            </p>
            {snippet_html}
          </td>
        </tr>
      </table>
    </div>"""


def render_category_section(df: pd.DataFrame, category: str, cfg: dict, always_show: bool = False) -> str:
    cat_df = df[df["category"] == category]
    if cat_df.empty:
        if not always_show:
            return ""
        # Show a "nothing new today" placeholder for always-visible sections
        empty_card = f"""
    <div style="padding:14px 20px;background:#fff;">
      <p style="margin:0;font-size:12px;color:#94a3b8;font-style:italic;">
        ✅ No new {category} items today.
      </p>
    </div>"""
        cards = empty_card
    else:
        cards = "".join(render_article_card(row, cfg) for _, row in cat_df.iterrows())
    return f"""
    <div style="margin-bottom:24px;border-radius:6px;overflow:hidden;
                box-shadow:0 1px 4px rgba(0,0,0,0.08);">
      <div style="background:{cfg["header_bg"]};padding:11px 20px;">
        <h2 style="color:#fff;margin:0;font-size:14px;font-weight:700;
                   letter-spacing:0.3px;">
          {cfg["icon"]}&nbsp; {category.upper()}
          &nbsp;<span style="font-weight:400;opacity:0.75;font-size:12px;">
            ({len(cat_df)} item{"s" if len(cat_df) != 1 else ""})
          </span>
        </h2>
      </div>
      {cards}
    </div>"""


def build_email(df_new: pd.DataFrame) -> str:
    if df_new.empty:
        return f"""
        <div style="{WRAPPER_STYLE}">
          {render_header(0)}
          <div style="background:#fff;padding:24px;border:1px solid #e2e8f0;
                      border-top:none;border-radius:0 0 8px 8px;margin-bottom:8px;">
            <p style="color:#475569;font-size:14px;background:#f8fafc;
                      padding:16px;border-radius:4px;border-left:4px solid #94a3b8;
                      margin:0;">
              ✅ Daily scan completed — no new matching items found today.
              All sources are up to date.
            </p>
          </div>
          {render_footer()}
        </div>"""

    sections = "".join(
        render_category_section(df_new, cat, cfg, always_show=(cat in ("Regulatory", "Tenders")))
        for cat, cfg in CATEGORY_STYLE.items()
    )
    return f"""
    <div style="{WRAPPER_STYLE}">
      {render_header(len(df_new))}
      {render_summary_bar(df_new)}
      {sections}
      {render_footer()}
    </div>"""


def render_footer() -> str:
    return (
        '<p style="font-size:11px;color:#94a3b8;text-align:center;margin-top:12px;">'
        "Automated ESG Intelligence System &nbsp;·&nbsp; "
        "All links go directly to source articles &nbsp;·&nbsp; "
        "Historical duplicates auto-filtered"
        "</p>"
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
