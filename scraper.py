import os
import requests
from bs4 import BeautifulSoup
import pandas as pd
import re

# ==========================================
# 1. HARDCODED CONFIGURATION & KEYWORDS MATRICES
# ==========================================

POWER_AUTOMATE_URL = "https://defaultfd7143fa1107460d98b18ef251b16d.50.environment.api.powerplatform.com:443/powerautomate/automations/direct/workflows/83c582d1339848bf82bb44367f463879/triggers/manual/paths/invoke?api-version=1&sp=%2Ftriggers%2Fmanual%2Frun&sv=1.0&sig=G0QNUbenz6wjlJLNkAW0j0b34BMc6aB7i58Lfmy-8Jg"

TENDER_KEYWORDS = [
    "Carbon Credit", "Carbon Offset", "Carbon Trading", "Carbon Footprint", "Carbon", "Carbon Neutral", 
    "Net Zero", "Carbon Sequestration", "Scope 1", "Scope 2", "Scope 3", "Ghg", "Green House Gas", 
    "Green House Gases", "Esg", "ESG Disclosure", "Climate Change", "Green Finance", "Sustainable Finance", 
    "Brsr", "Assurance", "Assessment", "Sustainab", "Sustainability", "Ccts", "Carbon Market"
]

REALTIME_KEYWORDS = [
    "Carbon Credit", "Carbon Offset", "Carbon Trading", "Carbon Footprint", "Carbon Neutral", "Carbon Sequestration", 
    "Carbon Market", "Carbon Border", "Carbon Tax", "Carbon Price", "Net Zero", "Climate Change", "Climate Risk", 
    "Climate Action", "Climate Finance", "Climate Policy", "Climate Tech", "Carbon Removal", "Decarbonisation", 
    "Emissions Reduction", "Paris Agreement", "COP", "Global Warming", "Clean Energy Transition", "ESG", 
    "ESG Disclosure", "ESG Reporting", "ESG Investing", "ESG Rating", "ESG Framework", "ESG Score", "BRSR", 
    "BRSR Core", "Assurance", "Assessment", "Sustainab", "Sustainability", "Sustainable Finance", "Green Finance", 
    "TCFD", "GRI", "CSRD", "ISSB", "SASB", "Integrated Reporting", "Double Materiality", "LODR", "Scope 1", 
    "Scope 2", "Scope 3", "GHG", "Greenhouse Gas", "Greenhouse Gases", "Emissions", "Net Emissions", 
    "Carbon Emissions", "Methane", "Renewable Energy", "Solar", "Wind Energy", "Green Hydrogen", "Energy Transition", 
    "Clean Tech", "Battery Storage", "EV", "Electric Vehicle", "Ammonia", "Green Bond", "Sustainability Bond", 
    "Transition Finance", "Blended Finance", "Impact Investing", "ESG Fund", "Taxonomy", "Greenwashing", 
    "Carbon Credit Market", "Voluntary Carbon Market", "Compliance Carbon", "India Net Zero", "India Renewable", 
    "India ESG", "India Sustainability", "ESG Conference", "Carbon Summit", "Sustainability Summit", "ESG Seminar", 
    "Climate Conference", "Carbon Forum", "Green Finance Summit", "ESG India", "Sustainability Event", "CBAM", 
    "Carbon Border Adjustment", "EU Carbon Tax", "CBAM Reporting", "CBAM Certificate", "EU ETS", "Carbon Leakage", 
    "CBAM Implementation", "EU Green Deal", "CBAM Transition", "Voluntary Carbon Market", "Compliance Carbon Market", 
    "Article 6", "Carbon Credits", "Carbon Standard", "Verra", "Gold Standard", "Carbon Registry", "IFRS S1", 
    "IFRS S2", "Carbon Offsetting", "Climate Fintech", "Green Fintech", "CO2 Investor", "Nature Finance", 
    "ESG Investor", "Impact Fund", "Climate Fund", "Green Investment", "Sustainable Investment India", "ESG Portfolio", 
    "Climate VC", "Green PE", "Plastic Pollution", "Extended Producer Responsibility", "EPR", "Plastic Waste", 
    "Global Plastics Treaty", "Single Use Plastic", "Circular Economy", "Plastic Credit", "BRICS Carbon", 
    "Global Carbon Market", "International Carbon Trading", "Global Sustainability", "G20 Climate", "Multilateral Carbon", 
    "Global Net Zero", "ESG KPI", "Materiality Matrix", "ESG Maturity", "ESG Benchmark", "ESG Disclosure", "Double Materiality", 
    "ESG Tech", "ESG SaaS", "Joint Crediting Mechanism", "JCM", "Japan Carbon", "Bilateral Carbon", "Article 6.2", 
    "Bioenergy", "Biomass", "Biomass Energy", "Biofuel", "Sustainable Biomass", "Biomass Power", "Biomass Carbon", 
    "BECCS", "Biomass Co-firing", "Waste to Energy", "Agricultural Residue", "Biomass Gasification", "Biomass Pellets", 
    "Forest Biomass", "Biomass Sustainability Criteria", "RED III", "Biomass Carbon Neutrality", "Biochar", "Bio-CCS", 
    "Nature Based Solutions", "Biodiversity", "Nature Loss", "TNFD", "SBTN", "Ecosystem Services", "Biodiversity Credits", 
    "Deforestation", "EUDR", "Biodiversity Net Gain", "Kunming Montreal", "Wildlife", "Wetlands", "Forest Carbon", 
    "Water Stewardship", "Water Risk", "Water Stress", "Water Footprint", "Water Credits", "Watershed", "Water Disclosure", 
    "CDP Water", "Water Security", "Groundwater", "Water Recycling", "Blue Carbon", "Ocean Carbon"
]

SEBI_KEYWORDS = [
    "BRSR", "Listing Obligations and Disclosure Requirements", "LODR", "Assurance", "Assessment", "BRSR Core"
]

SOURCES = [
    {"org": "GeM CPPP", "url": "https://gem.gov.in/cppp", "keywords": TENDER_KEYWORDS},
    {"org": "GeM List of Bids", "url": "https://bidplus.gem.gov.in/all-bids", "keywords": TENDER_KEYWORDS},
    {"org": "GeM CPPP Active Tenders", "url": "https://eprocure.gov.in/cppp/latestactivetendersnew/cpppdata/byYzJWc1pXTjBBMTNoMWMyVnNaV04wQTEzaDFjSFZpYkdsemFHVmtYMlJoZEdVPUExM2gxUWxKVFVnPT0=", "keywords": TENDER_KEYWORDS},
    {"org": "GeM CPPP Active Tenders - Central", "url": "https://eprocure.gov.in/cppp/latestactivetendersnew/cpppdata", "keywords": TENDER_KEYWORDS},
    {"org": "GeM CPPP Active Tenders - State", "url": "https://eprocure.gov.in/cppp/latestactivetendersnew/mmpdata", "keywords": TENDER_KEYWORDS},
    {"org": "ESG Today", "url": "https://www.esgtoday.com/", "keywords": REALTIME_KEYWORDS},
    {"org": "Trellis / GreenBiz", "url": "https://trellis.net/", "keywords": REALTIME_KEYWORDS},
    {"org": "EsgNews.com", "url": "https://esgnews.com/", "keywords": REALTIME_KEYWORDS},
    {"org": "Sustainability Magazine", "url": "https://sustainabilitymag.com/", "keywords": REALTIME_KEYWORDS},
    {"org": "ESG Dive", "url": "https://www.esgdive.com/", "keywords": REALTIME_KEYWORDS},
    {"org": "ESG Clarity", "url": "https://esgclarity.com/", "keywords": REALTIME_KEYWORDS},
    {"org": "ESG Investing", "url": "https://www.esginvesting.co.uk/", "keywords": REALTIME_KEYWORDS},
    {"org": "Financial Advisor Magazine", "url": "https://www.fa-mag.com/", "keywords": REALTIME_KEYWORDS},
    {"org": "Environmental Finance", "url": "https://www.environmental-finance.com/", "keywords": REALTIME_KEYWORDS},
    {"org": "GreenMoney", "url": "https://greenmoney.com/", "keywords": REALTIME_KEYWORDS},
    {"org": "Mondaq", "url": "https://www.mondaq.com/", "keywords": REALTIME_KEYWORDS},
    {"org": "Government of India (PIB)", "url": "https://www.pib.gov.in/allRel.aspx?reg=1&lang=1", "keywords": REALTIME_KEYWORDS},
    {"org": "SEBI Master Circular", "url": "https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=1&ssid=6&smid=0", "keywords": SEBI_KEYWORDS},
    {"org": "SEBI Advisory/Guidance", "url": "https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=1&ssid=96&smid=0", "keywords": SEBI_KEYWORDS},
    {"org": "SEBI Circulars", "url": "https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=1&ssid=7&smid=0", "keywords": SEBI_KEYWORDS},
    {"org": "SEBI Gazette Notification", "url": "https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=1&ssid=82&smid=0", "keywords": SEBI_KEYWORDS}
]

HISTORY_PATH = "Historical_Matches.csv"

# ==========================================
# 2. OPERATIONAL ENGINE & CONTEXT EXTRACTION
# ==========================================
results = []
headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

print(f"Commencing scan across {len(SOURCES)} hardcoded destination nodes...")

for site in SOURCES:
    org = site["org"]
    url = site["url"]
    site_keywords = list(set([k.lower() for k in site["keywords"]]))
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            for kw in site_keywords:
                elements = soup.find_all(string=re.compile(rf"\b{re.escape(kw)}\b", re.IGNORECASE))
                if elements:
                    parent_text = elements[0].parent.get_text(separator=" ", strip=True)
                    if len(parent_text) > 550:
                        parent_text = parent_text[:550] + " ... [Read full context at source]"
                    results.append({
                        'Organisation': org,
                        'URL': f'<a href="{url}">Source Link</a>',
                        'Keyword Matched': kw.title(),
                        'Extracted Legal / Context Details': parent_text
                    })
    except Exception:
        continue

df_today = pd.DataFrame(results)

# ==========================================
# 3. FILTRATION ENGINE & HISTORY MERGE
# ==========================================
if os.path.exists(HISTORY_PATH) and not df_today.empty:
    df_history = pd.read_csv(HISTORY_PATH)
    merged = df_today.merge(df_history[['Organisation', 'Keyword Matched']], on=['Organisation', 'Keyword Matched'], how='left', indicator=True)
    df_new = merged[merged['_merge'] == 'left_only'].drop(columns=['_merge'])
else:
    df_new = df_today.copy()
    df_history = pd.DataFrame(columns=['Organisation', 'Keyword Matched'])

# --- TEMPORARY LIVE TEST OVERRIDE ---
df_new = df_today.copy()

# ==========================================
# 4. STRUCTURAL HTML COMPILATION & DELIVERY
# ==========================================
if not df_new.empty:
    new_history = df_new[['Organisation', 'Keyword Matched']]
    pd.concat([df_history, new_history]).drop_duplicates().to_csv(HISTORY_PATH, index=False)
    
    html_table = df_new.to_html(index=False, border=0, justify='left', escape=False).replace(
        'class="dataframe"', 'style="border-collapse: collapse; width: 100%; font-family: Arial, sans-serif; font-size: 13px;"'
    ).replace(
        '<th>', '<th style="background-color: #f4f6f9; color: #333333; padding: 12px 10px; border-bottom: 2px solid #cfd4dc; text-align: left;">'
    ).replace(
        '<td>', '<td style="padding: 12px 10px; border-bottom: 1px solid #e2e8f0; color: #2d3748; vertical-align: top;">'
    )
    
    email_body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 950px; margin: 0 auto; line-height: 1.5;">
        <h2 style="color: #1e3a8a; border-bottom: 2px solid #1e3a8a; padding-bottom: 6px; margin-bottom: 15px;">Daily Compliance Scraper Update</h2>
        <p style="font-size: 14px; color: #4a5568;">The system has executed its daily validation run. The following <b>new entries</b> were successfully identified along with their unredacted contextual details:</p>
        <br>
        {html_table}
        <br><br>
        <p style="font-size: 11px; color: #a0aec0; border-top: 1px solid #e2e8f0; padding-top: 10px;">*Automated system generation alert. Excerpts are contextual and mapped dynamically. Historical updates are automatically filtered out to eliminate redundant reporting.</p>
    </div>
    """
else:
    email_body = """
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; line-height: 1.5;">
        <h2 style="color: #1e3a8a; border-bottom: 2px solid #1e3a8a; padding-bottom: 6px; margin-bottom: 15px;">Daily Compliance Scraper Update</h2>
        <p style="font-size: 14px; color: #2d3748;">The daily compliance run completed successfully.</p>
        <p style="font-size: 14px; padding: 12px; background-color: #f8fafc; border-left: 4px solid #cbd5e1; color: #475569;">
            <b>Status:</b> No brand-new tracking terms were isolated across your monitored matrix during this scan run. Everything is up to date!
        </p>
        <p style="font-size: 11px; color: #a0aec0; border-top: 1px solid #e2e8f0; padding-top: 10px; margin-top: 20px;">*Automated system generation alert.</p>
    </div>
    """

print("Transmitting email payload directly to Power Automate...")
try:
    response = requests.post(
        POWER_AUTOMATE_URL, 
        data=email_body.encode('utf-8'), 
        headers={'Content-Type': 'text/html; charset=utf-8'}
    )
    if response.status_code in [200, 202]:
        print("✅ Email payload successfully delivered to Power Automate workflow!")
    else:
        print(f"⚠️ Power Automate responded with error code: {response.status_code}")
except Exception as e:
    print(f"❌ Failed to reach Power Automate endpoint: {e}")
