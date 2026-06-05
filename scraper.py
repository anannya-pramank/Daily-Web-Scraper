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

# Items older than this many days are ignored for news/tender sources
RECENCY_DAYS = 60
RECENCY_CUTOFF = datetime.now(timezone.utc) - timedelta(days=RECENCY_DAYS)

# Wider recency window for regulatory sources.
REGULATORY_RECENCY_DAYS = 90
REGULATORY_RECENCY_CUTOFF = datetime.now(timezone.utc) - timedelta(days=REGULATORY_RECENCY_DAYS)


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
    try:
        return parsedate_to_datetime(text)
    except Exception:
        pass
    candidates = [text]
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
    "Sustainability", "Sustainable",
    "Carbon Market",
    "CCTS",
    "Carbon",  # FIX: Added standalone keyword from the Tracking_List.docx blueprint
]

REALTIME_KEYWORDS = [
    # ── Carbon & Emissions ──────────────────────────────────────────────────
    "Carbon Border Adjustment", "Voluntary Carbon Market", "Joint Crediting Mechanism",
    "Carbon Sequestration", "Carbon Offsetting", "Emissions Reduction", "Carbon Removal",
    "Carbon Footprint", "Carbon Credits", "Carbon Trading", "Carbon Neutral", "Carbon Offset",
    "Carbon Credit", "Carbon Market", "Carbon Border", "Carbon Leakage", "Carbon Emissions",
    "Carbon Price", "Carbon Tax", "Net Emissions", "Carbon Standard", "Carbon Registry",
    "Global Carbon Market", "International Carbon Trading", "BRICS Carbon",
    "Multilateral Carbon", "Global Net Zero", "Net Zero",
    # ── Climate ─────────────────────────────────────────────────────────────
    "Clean Energy Transition", "Global Plastics Treaty", "Decarbonisation",
    "Climate Finance", "Climate Policy", "Climate Action", "Climate Change",
    "Climate Risk", "Climate Tech", "Paris Agreement", "Global Warming",
    "Climate Conference", "Climate Fintech",
    # ── Reporting Standards & Frameworks ─────────────────────────────────────
    "Listing Obligations and Disclosure Requirements",
    "Extended Producer Responsibility", "Biodiversity Net Gain",
    "Nature Based Solutions", "Ecosystem Services", "Integrated Reporting",
    "Double Materiality", "Greenhouse Gases", "Greenhouse Gas",
    "Sustainability Bond", "Sustainability Summit", "Sustainable Finance",
    "Green Finance Summit", "Biodiversity Credits", "ESG Disclosure",
    "ESG Reporting", "ESG Investing", "ESG Framework", "ESG Portfolio",
    "ESG Conference", "ESG Rating", "ESG Score", "ESG Fund",
    "ESG KPI", "ESG Maturity", "ESG Benchmark", "ESG Tech", "ESG SaaS",
    "Materiality Matrix", "BRSR Core", "IFRS S1", "IFRS S2", "Article 6.2", "Article 6",
    "CSRD", "ISSB", "LODR", "TCFD", "BRSR", "SASB", "TNFD", "SBTN", "GRI", "GHG", "COP",
    # ── Finance & Investment ─────────────────────────────────────────────────
    "India Sustainability", "Transition Finance", "Blended Finance",
    "Impact Investing", "Green Investment", "India Net Zero", "India ESG",
    "India Renewable", "Sustainable Investment India",
    "Carbon Summit", "Green Bond", "Taxonomy", "Greenwashing",
    "Compliance Carbon", "Gold Standard", "Verra",
    "Carbon Forum", "ESG India", "Sustainability Event", "ESG Seminar",
    "Green Fintech", "Nature Finance", "Impact Fund", "Climate Fund",
    "Climate VC", "Green PE", "CO2 Investor",
    # ── Energy ───────────────────────────────────────────────────────────────
    "Waste to Energy", "Energy Transition", "Renewable Energy", "Green Hydrogen",
    "Wind Energy", "Battery Storage", "Electric Vehicle", "Clean Tech", "Ammonia", "EV",
    # ── Nature & Water ───────────────────────────────────────────────────────
    "Water Stewardship", "Water Security", "Water Footprint", "Water Stress",
    "Water Risk", "Blue Carbon", "Ocean Carbon", "Forest Carbon",
    "Groundwater", "Water Recycling", "Water Credits", "Watershed",
    "Water Disclosure", "CDP Water", "Kunming Montreal", "Deforestation",
    "Nature Loss", "Biodiversity", "Wildlife", "Wetlands", "EUDR",
    # ── Circular / Trade ─────────────────────────────────────────────────────
    "Circular Economy", "Plastic Pollution", "Plastic Credit", "Plastic Waste",
    "Single Use Plastic", "EU Green Deal", "EU Carbon Tax", "EU ETS", "CBAM",
    "CBAM Reporting", "CBAM Certificate", "CBAM Implementation", "CBAM Transition",
    # ── Biomass ──────────────────────────────────────────────────────────────
    "BECCS", "Biochar", "Bioenergy", "Biofuel", "Biomass Energy", "Biomass Power",
    "Biomass Carbon", "Bio-CCS", "Biomass Co-firing", "Agricultural Residue",
    "Biomass Gasification", "Biomass Pellets", "Forest Biomass",
    "Biomass Sustainability Criteria", "RED III", "Biomass Carbon Neutrality", "Biomass",
    # ── Other specific ───────────────────────────────────────────────────────
    "Methane", "Scope 1", "Scope 2", "Scope 3", "Assurance", "Assessment", "JCM", "EPR",
    "Japan Carbon", "Bilateral Carbon", "Global Sustainability", "G20 Climate",
    # ── Broad catch-alls ─────────────────────────────────────────────────────
    "Sustainability", "Green Finance", "ESG", "Emissions", "Solar",
]

SEBI_KEYWORDS = [
    "BRSR", "Listing Obligations and Disclosure Requirements", "LODR", "Assurance",
    "Assessment", "BRSR Core"
]

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
        "org": "CPPP Active Tenders – Filtered",
        "url": "https://eprocure.gov.in/cppp/latestactivetendersnew/cpppdata/byYzJWc1pXTjBBMTNoMWMyVnNaV04wQTEzaDFjSFZpYkdsemFIVmtYMlJoZEdVPUExM2gxUWxKVFVnPT0=",
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
    # ── ESG News ─────────────────────────────────────────────────────────────
    {
        "org": "ESG Today",
        "url": "https://www.esgtoday.com/",
        "rss": "https://www.esgtoday.com/feed/",
        "keywords": REALTIME_KEYWORDS,
        "category": "ESG News",
        "parser": "rss_news",
    },
    {
        "org": "Trellis / GreenBiz",
        "url": "https://trellis.net/",
        "rss": "https://trellis.net/rss/everything",
        "keywords": REALTIME_KEYWORDS,
        "category": "ESG News",
        "parser": "rss_news",
    },
    {
        "org": "ESG News",
        "url": "https://esgnews.com/",
        "rss": "https://esgnews.com/feed/",
        "keywords": REALTIME_KEYWORDS,
        "category": "ESG News",
        "parser": "rss_news",
    },
    {
        "org": "Sustainability Magazine",
        "url": "https://sustainabilitymag.com/",
        "rss": "https://sustainabilitymag.com/feed/",
        "keywords": REALTIME_KEYWORDS,
        "category": "ESG News",
        "parser": "rss_news",
    },
    {
        "org": "ESG Dive",
        "url": "https://www.esgdive.com/",
        "rss": "https://www.esgdive.com/feeds/news/",
        "keywords": REALTIME_KEYWORDS,
        "category": "ESG News",
        "parser": "rss_news",
    },
    {
        "org": "ESG Clarity",
        "url": "https://esgclarity.com/",
        "rss": "https://esgclarity.com/feed/",
        "keywords": REALTIME_KEYWORDS,
        "category": "ESG News",
        "parser": "rss_news",
    },
    {
        "org": "ESG Investing",
        "url": "https://www.esginvesting.co.uk/",
        "rss": "https://www.esginvesting.co.uk/feed/",
        "keywords": REALTIME_KEYWORDS,
        "category": "ESG News",
        "parser": "rss_news",
    },
    {
        "org": "Financial Advisor Magazine",
        "url": "https://www.fa-mag.com/",
        "rss": "https://www.fa-mag.com/rss.xml",
        "keywords": REALTIME_KEYWORDS,
        "category": "ESG News",
        "parser": "rss_news",
    },
    {
        "org": "Environmental Finance",
        "url": "https://www.environmental-finance.com/",
        "rss": "https://www.environmental-finance.com/rss.xml",
        "keywords": REALTIME_KEYWORDS,
        "category": "ESG News",
        "parser": "rss_news",
    },
    {
        "org": "GreenMoney",
        "url": "https://greenmoney.com/",
        "rss": "https://greenmoney.com/feed/",
        "keywords": REALTIME_KEYWORDS,
        "category": "ESG News",
        "parser": "rss_news",
    },
    {
        "org": "Mondaq",
        "url": "https://www.mondaq.com/",
        "rss": "https://www.mondaq.com/rss.aspx",
        "keywords": REALTIME_KEYWORDS,
        "category": "ESG News",
        "parser": "rss_news",
    },
    {
        "org": "Govt of India (PIB)",
        "url": "https://www.pib.gov.in/allRel.aspx?reg=1&lang=1",
        "rss": "https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3",
        "keywords": REALTIME_KEYWORDS,
        "category": "ESG News",
        "parser": "pib",
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
    FIX: Allows for plural forms ('s') to catch terms like GHGs, EVs, or standards.
    """
    text_lower = text.lower()
    for kw in sorted(keywords, key=len, reverse=True):
        pattern = rf"(?<![a-zA-Z0-9]){re.escape(kw.lower())}s?(?![a-zA-Z0-9])"
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
    """Fetch an RSS feed using custom session configurations."""
    if not FEEDPARSER_AVAILABLE:
        return None
    try:
        r = SESSION.get(
            rss_url,
            headers={"Accept": "application/rss+xml,application/xml,text/xml,*/*;q=0.8"},
            timeout=20,
        )
        if r.status_code == 200 and len(r.text) > 100:
            return feedparser.parse(r.text)
        print(f"    ⚠  RSS HTTP {r.status_code} from {rss_url}")
    except Exception as e:
        print(f"    ✗  RSS fetch error for {rss_url}: {e}")
    return None

# ==========================================
# 4. PARSERS
# ==========================================

def parse_rss(source: dict) -> list[dict]:
    """Primary parser for news blogs; attempts RSS first, falls back to HTML parsing."""
    hits, seen = [], set()
    keywords = source["keywords"]
    org = source["org"]
    base_url = source["url"]

    rss_url = source.get("rss")
    if rss_url:
        feed = fetch_rss(rss_url)
        if feed and feed.entries:
            for entry in feed.entries:
                title = entry.get("title", "").strip()
                link = entry.get("link", "").strip()
                summary = BeautifulSoup(entry.get("summary", ""), "html.parser").get_text()
                pub_date = entry.get("published", entry.get("updated", ""))

                dt = parse_fuzzy_date(pub_date)
                if dt and dt < RECENCY_CUTOFF:
                    continue

                check = f"{title} {summary}"
                kw = first_keyword_match(check, keywords)
                if kw and link not in seen:
                    seen.add(link)
                    hits.append({
                        "org": org,
                        "category": source["category"],
                        "keyword": kw,
                        "title": title,
                        "article_url": link,
                        "date": fmt_date(pub_date),
                        "snippet": clean_snippet(summary),
                        "uid": make_uid(link, title),
                    })
            if hits:
                return hits

    soup = fetch_soup(base_url)
    if not soup:
        return []

    containers = soup.find_all("article") or []
    if not containers:
        containers = soup.select(
            'div[class*="post"], div[class*="card"], div[class*="article"], '
            'div[class*="entry"], div[class*="item"], div[class*="story"], '
            'div[class*="news"], section[class*="article"]'
        )

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
            date_el = container.find(attrs={"class": re.compile(r"date|time|publish", re.I)}) or container.find("time")
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
            date_el = (context.find(attrs={"class": re.compile(r"date|time|publish", re.I)}) or context.find("time")) if context else None
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
    FIX: Implemented up to 3 deep pagination sweeps to uncover older 90-day 
    regulatory circulars pushed back by higher volume unrelated updates.
    """
    hits, seen = [], set()
    keywords = source["keywords"]
    org = source["org"]
    base_url = source["url"]

    DATE_RE = re.compile(
        r"(\d{1,2}[-/]\w{3}[-/]\d{4}|\w{3,9}\s+\d{1,2},?\s+\d{4}"
        r"|\d{2}[-/]\d{2}[-/]\d{4}|\d{1,2}\s+\w{3,9}\s+\d{4})",
        re.IGNORECASE,
    )

    def _process(title, href, row_text, date_str):
        nonlocal hits, seen
        if href in seen or len(title) < 5:
            return
        dt = parse_fuzzy_date(date_str)
        if dt and dt < REGULATORY_RECENCY_CUTOFF:
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

    # Loop through page index boundaries 1 to 3
    for page_num in range(1, 4):
        paged_url = f"{base_url}&pageNo={page_num}" if "?" in base_url else f"{base_url}?pageNo={page_num}"
        soup = fetch_soup(paged_url, extra_headers={"Referer": "https://www.sebi.gov.in/"})
        if not soup:
            continue

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


def parse_pib(source: dict) -> list[dict]:
    """Press Information Bureau layout engine parsing logic."""
    hits, seen = [], set()
    keywords = source["keywords"]
    org = source["org"]
    base_url = source["url"]

    rss_url = source.get("rss")
    if rss_url:
        feed = fetch_rss(rss_url)
        if feed and feed.entries:
            for entry in feed.entries:
                title = entry.get("title", "").strip()
                link = entry.get("link", "").strip()
                summary = BeautifulSoup(entry.get("summary", ""), "html.parser").get_text()
                pub_date = entry.get("published", entry.get("updated", ""))

                dt = parse_fuzzy_date(pub_date)
                if dt and dt < RECENCY_CUTOFF:
                    continue

                check = f"{title} {summary}"
                kw = first_keyword_match(check, keywords)
                if kw and link not in seen:
                    seen.add(link)
                    hits.append({
                        "org": org,
                        "category": source["category"],
                        "keyword": kw,
                        "title": title,
                        "article_url": link,
                        "date": fmt_date(pub_date),
                        "snippet": clean_snippet(summary),
                        "uid": make_uid(link, title),
                    })
            if hits:
                return hits

    soup = fetch_soup(base_url)
    if not soup:
        return []

    DATE_RE = re.compile(
        r"(\d{1,2}[-/]\w{3}[-/]\d{4}|\w{3,9}\s+\d{1,2},?\s+\d{4}"
        r"|\d{2}[-/]\d{2}[-/]\d{4}|\d{1,2}\s+\w{3,9}\s+\d{4})",
        re.IGNORECASE,
    )

    def _process(title, href, row_text, date_str):
        nonlocal hits, seen
        if href in seen or len(title) < 5:
            return
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
            href = urljoin("https://www.pib.gov.in", href)
        row_text = row.get_text(separator=" ", strip=True)
        date_match = DATE_RE.search(row_text)
        _process(title, href, row_text, date_match.group(1) if date_match else "")

    for li in soup.find_all("li"):
        a = li.find("a", href=True)
        if not a:
            continue
        title = a.get_text(strip=True)
        href = a["href"]
        if not href.startswith("http"):
            href = urljoin("https://www.pib.gov.in", href)
        li_text = li.get_text(separator=" ", strip=True)
        date_match = DATE_RE.search(li_text)
        _process(title, href, li_text, date_match.group(1) if date_match else "")

    return hits


def parse_gem(source: dict) -> list[dict]:
    """
    Government e-procurement / GeM tender portal parser.
    FIX: Added dynamic execution fallback checks to alert logs if dynamic tables run empty.
    """
    hits, seen = [], set()
    keywords = source["keywords"]
    org = source["org"]
    base_url = source["url"]

    soup = fetch_soup(base_url)
    if not soup:
        return []

    rows = soup.find_all("tr")
    if not rows:
        print(f"    ⚠  No static HTML elements found for {org}. Dynamic client-side parsing needed.")

    for row in rows:
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
    "sebi":     parse_sebi,
    "pib":      parse_pib,
    "gem":      parse_gem,
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

if os.path.exists(HISTORY_PATH) and not df_today.empty:
    df_history = pd.read_csv(HISTORY_PATH)
    if "uid" in df_history.columns:
        known_uids = set(df_history["uid"].dropna())
        df_new = df_today[~df_today["uid"].isin(known_uids)].copy()
    else:
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
            <!-- FIX: Modified space formatting parameters below to force wrapping on ultra-long tracking labels -->
            <span style="background:{cfg["badge_bg"]};color:#fff;font-size:10px;
                         font-weight:700;padding:2px 9px;border-radius:12px;
                         white-space:normal;display:inline-block;max-width:180px;
                         line-height:1.2;text-align:center;">
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
        render_category_section(df_new, cat, cfg, always_show=(cat == "Regulatory"))
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
