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
    "BRSR", "Listing Obligations and Disclosure Requirements", "LODR", "Assurance",
    "Assessment", "BRSR Core"
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
        "parser": "gem",
    },
    {
        "org": "GeM List of Bids",
        "url": "https://bidplus.gem.gov.in/all-bids",
        "rss": None,
        "keywords": TENDER_KEYWORDS,
        "category": "Tenders",
        "parser": "gem",
    },
    {
        "org": "CPPP Active Tenders – Central",
        "url": "https://eprocure.gov.in/cppp/latestactivetendersnew/cpppdata",
        "rss": None,
        "keywords": TENDER_KEYWORDS,
        "category": "Tenders",
        "parser": "gem",
    },
    {
        "org": "CPPP Active Tenders – State",
        "url": "https://eprocure.gov.in/cppp/latestactivetendersnew/mmpdata",
        "rss": None,
        "keywords": TENDER_KEYWORDS,
        "category": "Tenders",
        "parser": "gem",
    },
    # ── ESG News — Google News hybrid (4 broad queries replace 12 individual scrapers) ──
    # Each entry uses gnews=True so _process_feed pulls the real publisher
    # from entry.source.title (e.g. "ESG Today", "Reuters") per article.
    # URL-based uid dedup handles overlap between the 4 queries automatically.
    {
        # Standards & Disclosure: ESG, CSRD, ISSB, BRSR, greenwashing, TCFD
        "org": "ESG News",   # fallback label; overridden per-article via entry.source.title
        "url": "https://news.google.com/",
        "rss": (
            "https://news.google.com/rss/search"
            "?q=ESG+OR+CSRD+OR+ISSB+OR+BRSR+OR+greenwashing+OR+TCFD"
            "+OR+%22sustainability+reporting%22+OR+%22ESG+disclosure%22"
            "&hl=en-US&gl=US&ceid=US:en"
        ),
        "keywords": REALTIME_KEYWORDS,
        "category": "ESG News",
        "parser": "rss_news",
        "gnews": True,
    },
    {
        # Carbon & Climate: credits, net zero, removal, offsets, climate risk/finance
        "org": "ESG News",
        "url": "https://news.google.com/",
        "rss": (
            "https://news.google.com/rss/search"
            "?q=%22carbon+credit%22+OR+%22net+zero%22+OR+%22carbon+removal%22"
            "+OR+%22climate+risk%22+OR+%22carbon+offset%22+OR+%22carbon+market%22"
            "+OR+decarbonisation+OR+%22climate+finance%22"
            "&hl=en-US&gl=US&ceid=US:en"
        ),
        "keywords": REALTIME_KEYWORDS,
        "category": "ESG News",
        "parser": "rss_news",
        "gnews": True,
    },
    {
        # Green Finance & Energy: bonds, renewables, EVs, biodiversity, water
        "org": "ESG News",
        "url": "https://news.google.com/",
        "rss": (
            "https://news.google.com/rss/search"
            "?q=%22green+bond%22+OR+%22sustainable+finance%22"
            "+OR+%22renewable+energy%22+OR+%22energy+transition%22"
            "+OR+biodiversity+OR+%22water+stewardship%22"
            "+OR+%22electric+vehicle%22+OR+%22forest+carbon%22"
            "&hl=en-US&gl=US&ceid=US:en"
        ),
        "keywords": REALTIME_KEYWORDS,
        "category": "ESG News",
        "parser": "rss_news",
        "gnews": True,
    },
    {
        # India-focused ESG: SEBI, BRSR, India net-zero, India green bonds (IN locale)
        "org": "ESG News",
        "url": "https://news.google.com/",
        "rss": (
            "https://news.google.com/rss/search"
            "?q=India+ESG+OR+India+BRSR+OR+%22India+sustainability%22"
            "+OR+%22India+net+zero%22+OR+%22India+carbon%22"
            "+OR+%22SEBI+ESG%22+OR+%22India+green+bond%22"
            "&hl=en-IN&gl=IN&ceid=IN:en"
        ),
        "keywords": REALTIME_KEYWORDS,
        "category": "ESG News",
        "parser": "rss_news",
        "gnews": True,
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

            # Skip items older than RECENCY_DAYS
            dt = parse_fuzzy_date(pub_date)
            if dt and dt < RECENCY_CUTOFF:
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


def parse_gem(source: dict) -> list[dict]:
    """
    Government e-procurement / GeM tender portal parser.
    Tender data is typically in HTML tables or structured list rows.
    """
    hits, seen = [], set()
    keywords = source["keywords"]
    org = source["org"]
    base_url = source["url"]

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

        a = row.find("a", href=True)
        href = urljoin(base_url, a["href"]) if a else base_url
        title = a.get_text(strip=True) if a else row_text[:150]

        if href in seen:
            continue
        seen.add(href)

        date_match = re.search(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", row_text)
        hits.append({
            "org": org,
            "category": source["category"],
            "keyword": kw,
            "title": title[:150],
            "article_url": href,
            "date": date_match.group(0) if date_match else "",
            "snippet": clean_snippet(row_text),
            "uid": make_uid(href, title),
        })

    return hits


PARSER_MAP = {
    "rss_news": parse_rss,
    "sebi": parse_sebi,
    "gem": parse_gem,
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
    "ESG News": {
        "header_bg":  "#065f46",
        "badge_bg":   "#059669",
        "border":     "#10b981",
        "light_bg":   "#ecfdf5",
        "icon":       "📰",
    },
    "Tenders": {
        "header_bg":  "#1e40af",
        "badge_bg":   "#1e40af",
        "border":     "#3b82f6",
        "light_bg":   "#eff6ff",
        "icon":       "📋",
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
