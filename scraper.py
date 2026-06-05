import os
import re
import hashlib
import requests
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime
from urllib.parse import urljoin

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

TENDER_KEYWORDS = [
    "Carbon Credit", "Carbon Offset", "Carbon Trading", "Carbon Footprint", "Carbon Neutral",
    "Net Zero", "Carbon Sequestration", "Scope 1", "Scope 2", "Scope 3", "GHG",
    "Green House Gas", "Green House Gases", "ESG", "ESG Disclosure", "Climate Change",
    "Green Finance", "Sustainable Finance", "BRSR", "Assurance", "Assessment",
    "Sustainab", "Sustainability", "Carbon Market"
]

REALTIME_KEYWORDS = [
    "Carbon Credit", "Carbon Offset", "Carbon Trading", "Carbon Footprint", "Carbon Neutral",
    "Carbon Sequestration", "Carbon Market", "Carbon Border", "Carbon Tax", "Carbon Price",
    "Net Zero", "Climate Change", "Climate Risk", "Climate Action", "Climate Finance",
    "Climate Policy", "Climate Tech", "Carbon Removal", "Decarbonisation", "Emissions Reduction",
    "Paris Agreement", "COP", "Global Warming", "Clean Energy Transition", "ESG",
    "ESG Disclosure", "ESG Reporting", "ESG Investing", "ESG Rating", "ESG Framework",
    "ESG Score", "BRSR", "BRSR Core", "Assurance", "Assessment", "Sustainab", "Sustainability",
    "Sustainable Finance", "Green Finance", "TCFD", "GRI", "CSRD", "ISSB", "SASB",
    "Integrated Reporting", "Double Materiality", "LODR", "Scope 1", "Scope 2", "Scope 3",
    "GHG", "Greenhouse Gas", "Greenhouse Gases", "Emissions", "Net Emissions",
    "Carbon Emissions", "Methane", "Renewable Energy", "Solar", "Wind Energy",
    "Green Hydrogen", "Energy Transition", "Clean Tech", "Battery Storage", "EV",
    "Electric Vehicle", "Green Bond", "Sustainability Bond", "Transition Finance",
    "Blended Finance", "Impact Investing", "ESG Fund", "Taxonomy", "Greenwashing",
    "Voluntary Carbon Market", "Compliance Carbon", "Article 6", "Carbon Credits",
    "Carbon Standard", "Verra", "Gold Standard", "Carbon Registry", "IFRS S1", "IFRS S2",
    "Carbon Offsetting", "Nature Based Solutions", "Biodiversity", "Nature Loss", "TNFD",
    "SBTN", "Ecosystem Services", "Biodiversity Credits", "Deforestation", "EUDR",
    "Biodiversity Net Gain", "Kunming Montreal", "Forest Carbon", "Water Stewardship",
    "Water Risk", "Water Stress", "Water Footprint", "Blue Carbon", "Ocean Carbon",
    "CBAM", "Carbon Border Adjustment", "EU Carbon Tax", "EU ETS", "Carbon Leakage",
    "EU Green Deal", "Circular Economy", "EPR", "Extended Producer Responsibility",
    "Green Investment", "ESG Portfolio", "India Net Zero", "India ESG", "India Sustainability",
    "ESG Conference", "Carbon Summit", "Sustainability Summit", "Green Finance Summit",
    "Bioenergy", "Biomass", "Biochar", "BECCS", "Waste to Energy", "Joint Crediting Mechanism",
    "JCM", "Article 6.2", "Plastic Pollution", "Plastic Credit", "Global Plastics Treaty",
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
        "org": "CPPP Active Tenders",
        "url": "https://eprocure.gov.in/cppp/latestactivetendersnew/cpppdata",
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
    """Return the first keyword whose whole-word form appears in text."""
    text_lower = text.lower()
    for kw in keywords:
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

# ==========================================
# 4. PARSERS
# ==========================================

def parse_rss(source: dict) -> list[dict]:
    """
    Primary parser for all news/blog sites.
    Tries the RSS feed first (cleanest data), falls back to HTML scraping.
    """
    hits, seen = [], set()
    keywords = source["keywords"]
    org = source["org"]
    base_url = source["url"]

    # ─ RSS path ────────────────────────────────────────────────────────────
    rss_url = source.get("rss")
    if rss_url and FEEDPARSER_AVAILABLE:
        try:
            feed = feedparser.parse(rss_url)
            if feed.entries:
                for entry in feed.entries:
                    title = entry.get("title", "").strip()
                    link = entry.get("link", "").strip()
                    summary = BeautifulSoup(entry.get("summary", ""), "html.parser").get_text()
                    pub_date = entry.get("published", entry.get("updated", ""))

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
                            "date": pub_date[:16] if pub_date else "",
                            "snippet": clean_snippet(summary),
                            "uid": make_uid(link, title),
                        })
                if hits:
                    return hits
        except Exception as e:
            print(f"    ⚠  RSS parse error ({rss_url}): {e}")

    # ─ HTML fallback ───────────────────────────────────────────────────────
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
            hits.append({
                "org": org,
                "category": source["category"],
                "keyword": kw,
                "title": title,
                "article_url": href,
                "date": date_el.get_text(strip=True) if date_el else "",
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
            hits.append({
                "org": org,
                "category": source["category"],
                "keyword": kw,
                "title": title,
                "article_url": href,
                "date": date_el.get_text(strip=True) if date_el else "",
                "snippet": clean_snippet(body_text),
                "uid": make_uid(href, title),
            })

    return hits


def parse_sebi(source: dict) -> list[dict]:
    """
    SEBI government portal parser.
    Pages are JSP-rendered server-side; content is in <table> rows.
    Requires a warmed session (homepage cookie) to avoid 403.
    """
    hits, seen = [], set()
    keywords = source["keywords"]
    org = source["org"]
    base_url = source["url"]

    soup = fetch_soup(base_url, extra_headers={"Referer": "https://www.sebi.gov.in/"})
    if not soup:
        return []

    for row in soup.find_all("tr"):
        a = row.find("a", href=True)
        if not a:
            continue

        title = a.get_text(strip=True)
        href = a["href"]
        if not href.startswith("http"):
            href = urljoin("https://www.sebi.gov.in", href)
        if href in seen or len(title) < 5:
            continue

        row_text = row.get_text(separator=" ", strip=True)
        kw = first_keyword_match(title, keywords) or first_keyword_match(row_text, keywords)
        if not kw:
            continue

        seen.add(href)
        # Extract date from any cell (SEBI uses formats like "Jan 10, 2024")
        date_match = re.search(
            r"(\d{1,2}[-/]\w{3}[-/]\d{4}|\w{3,9}\s+\d{1,2},?\s+\d{4}|\d{2}[-/]\d{2}[-/]\d{4})",
            row_text,
        )
        hits.append({
            "org": org,
            "category": source["category"],
            "keyword": kw,
            "title": title,
            "article_url": href,
            "date": date_match.group(1) if date_match else "",
            "snippet": clean_snippet(row_text),
            "uid": make_uid(href, title),
        })

    # Also catch list-item format pages
    for li in soup.find_all("li"):
        a = li.find("a", href=True)
        if not a:
            continue
        title = a.get_text(strip=True)
        href = a["href"]
        if not href.startswith("http"):
            href = urljoin("https://www.sebi.gov.in", href)
        if href in seen or len(title) < 5:
            continue
        kw = first_keyword_match(title, keywords)
        if kw:
            seen.add(href)
            hits.append({
                "org": org,
                "category": source["category"],
                "keyword": kw,
                "title": title,
                "article_url": href,
                "date": "",
                "snippet": clean_snippet(title),
                "uid": make_uid(href, title),
            })

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
    "Regulatory": {
        "header_bg":  "#78350f",
        "badge_bg":   "#d97706",
        "border":     "#f59e0b",
        "light_bg":   "#fffbeb",
        "icon":       "⚖️",
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


def render_category_section(df: pd.DataFrame, category: str, cfg: dict) -> str:
    cat_df = df[df["category"] == category]
    if cat_df.empty:
        return ""
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
        render_category_section(df_new, cat, cfg)
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
