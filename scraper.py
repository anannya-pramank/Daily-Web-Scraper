import os
import requests
from bs4 import BeautifulSoup
import pandas as pd

# 1. DEFINE PATHS ON ONEDRIVE
CONFIG_PATH = "onedrive/WebScraper/Web-Scraper-Config.xlsx"
HISTORY_PATH = "onedrive/WebScraper/Historical_Matches.csv"
SUMMARY_PATH = "onedrive/WebScraper/Email_Summary.html"

# 2. READ EXCEL CONFIGURATION
print("Reading configuration from OneDrive...")
try:
    sources_df = pd.read_excel(CONFIG_PATH, sheet_name='Sources')
    keywords_df = pd.read_excel(CONFIG_PATH, sheet_name='Keywords')
except Exception as e:
    print(f"Error reading file: {e}. Ensure Web-Scraper-Config.xlsx is in your OneDrive/WebScraper folder.")
    exit(1)

# Dynamically find columns and clean text to avoid Excel spacing errors
url_col = [c for c in sources_df.columns if 'URL' in str(c).upper()][0]
org_col = [c for c in sources_df.columns if 'ORG' in str(c).upper()][0]
keyword_col = keywords_df.columns[0]

sources_df[org_col] = sources_df[org_col].astype(str).str.strip()
sources_df[url_col] = sources_df[url_col].astype(str).str.strip()
keywords = keywords_df[keyword_col].dropna().astype(str).str.strip().tolist()

# Deduplicate and lowercase for faster scanning
UNIQUE_KEYWORDS = list(set([k.lower() for k in keywords]))

# 3. RUN THE SCRAPER
results = []
headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

print(f"Scanning {len(sources_df)} websites for {len(UNIQUE_KEYWORDS)} unique keywords...")
for idx, row in sources_df.iterrows():
    org = row[org_col]
    url = row[url_col]
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            page_text = soup.get_text().lower()
            
            for kw in UNIQUE_KEYWORDS:
                if kw in page_text:
                    results.append({'Organisation': org, 'URL': url, 'Matched Keyword': kw.title()})
    except Exception:
        print(f"Skipped slow or broken site: {org}")

df_today = pd.DataFrame(results)

# 4. COMPARE WITH HISTORY (THE "MEMORY" FUNCTION)
if os.path.exists(HISTORY_PATH) and not df_today.empty:
    df_history = pd.read_csv(HISTORY_PATH)
    merged = df_today.merge(df_history, on=['Organisation', 'URL', 'Matched Keyword'], how='left', indicator=True)
    df_new = merged[merged['_merge'] == 'left_only'].drop(columns=['_merge'])
else:
    df_new = df_today.copy()
    df_history = pd.DataFrame(columns=['Organisation', 'URL', 'Matched Keyword'])

# 5. GENERATE EMAIL SUMMARY & UPDATE HISTORY
if not df_new.empty:
    pd.concat([df_history, df_new]).drop_duplicates().to_csv(HISTORY_PATH, index=False)
    
    html_table = df_new.to_html(index=False, border=1, justify='left').replace('class="dataframe"', 'style="border-collapse: collapse; width: 100%; font-family: Arial;"').replace('<th>', '<th style="background-color: #f2f2f2; padding: 8px; border: 1px solid #ddd;">').replace('<td>', '<td style="padding: 8px; border: 1px solid #ddd;">')
    
    email_body = f"""
    <div style="font-family: Arial, sans-serif;">
        <h2 style="color: #2e74b5;">Daily Web Scraper Update</h2>
        <p>The following <b>new</b> keywords were detected on your tracked websites today:</p>
        {html_table}
        <br>
        <p style="font-size: 12px; color: gray;">This is an automated alert. Previous historical matches have been filtered out.</p>
    </div>
    """
    with open(SUMMARY_PATH, "w", encoding="utf-8") as f:
        f.write(email_body)
    print(f"Found {len(df_new)} new updates. Summary created.")

else:
    email_body = """
    <div style="font-family: Arial, sans-serif;">
        <h2 style="color: #2e74b5;">Daily Web Scraper Update</h2>
        <p>No new keywords were detected today. No action required.</p>
    </div>
    """
    with open(SUMMARY_PATH, "w", encoding="utf-8") as f:
        f.write(email_body)
    print("No new updates today.")
