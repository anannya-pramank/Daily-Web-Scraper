import os
import requests
from bs4 import BeautifulSoup
import pandas as pd
import re

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
    print(f"Error reading file: {e}")
    exit(1)

url_col = [c for c in sources_df.columns if 'URL' in str(c).upper()][0]
org_col = [c for c in sources_df.columns if 'ORG' in str(c).upper()][0]
keyword_col = keywords_df.columns[0]

sources_df[org_col] = sources_df[org_col].astype(str).str.strip()
sources_df[url_col] = sources_df[url_col].astype(str).str.strip()
keywords = keywords_df[keyword_col].dropna().astype(str).str.strip().tolist()

UNIQUE_KEYWORDS = list(set([k.lower() for k in keywords]))

# 3. RUN THE SCRAPER (NOW WITH CONTEXT EXTRACTION)
results = []
headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

print(f"Scanning {len(sources_df)} websites...")
for idx, row in sources_df.iterrows():
    org = row[org_col]
    url = row[url_col]
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            for kw in UNIQUE_KEYWORDS:
                # Find the exact HTML elements containing the keyword
                elements = soup.find_all(string=re.compile(kw, re.IGNORECASE))
                
                if elements:
                    # Grab the text from the parent paragraph/row
                    parent_text = elements[0].parent.get_text(separator=" ", strip=True)
                    
                    # Clean up the text and limit to ~600 characters so it fits nicely in the email
                    if len(parent_text) > 600:
                        parent_text = parent_text[:600] + " ... [Read more on site]"
                        
                    results.append({
                        'Organisation': org, 
                        'URL': f'<a href="{url}">Link</a>', # Makes a clickable link
                        'Keyword': kw.title(),
                        'Extracted Context / Details': parent_text
                    })
    except Exception:
        print(f"Skipped site: {org}")

df_today = pd.DataFrame(results)

# 4. COMPARE WITH HISTORY 
if os.path.exists(HISTORY_PATH) and not df_today.empty:
    df_history = pd.read_csv(HISTORY_PATH)
    # We compare based on Org, URL, and Keyword (ignoring the exact context string so minor site changes don't trigger duplicates)
    merged = df_today.merge(df_history[['Organisation', 'Keyword']], on=['Organisation', 'Keyword'], how='left', indicator=True)
    df_new = merged[merged['_merge'] == 'left_only'].drop(columns=['_merge'])
else:
    df_new = df_today.copy()
    df_history = pd.DataFrame(columns=['Organisation', 'Keyword'])

# 5. GENERATE EMAIL SUMMARY & UPDATE HISTORY
if not df_new.empty:
    # Update history with new Org/Keyword combos
    new_history = df_new[['Organisation', 'Keyword']]
    pd.concat([df_history, new_history]).drop_duplicates().to_csv(HISTORY_PATH, index=False)
    
    # Create HTML Table with Custom Widths for readability
    html_table = df_new.to_html(index=False, border=0, justify='left', escape=False).replace(
        'class="dataframe"', 'style="border-collapse: collapse; width: 100%; font-family: Arial; font-size: 13px;"'
    ).replace(
        '<th>', '<th style="background-color: #f2f2f2; padding: 10px; border-bottom: 2px solid #ddd; text-align: left;">'
    ).replace(
        '<td>', '<td style="padding: 10px; border-bottom: 1px solid #ddd; vertical-align: top;">'
    )
    
    email_body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 900px;">
        <h2 style="color: #2e74b5; border-bottom: 1px solid #2e74b5; padding-bottom: 5px;">Daily Scraper Update & Legal Summary</h2>
        <p>The following <b>new</b> tracking terms were detected today. The relevant text excerpts have been extracted below for your review:</p>
        <br>
        {html_table}
        <br><br>
        <p style="font-size: 11px; color: gray;">*This is an automated alert. Excerpts are pulled directly from the source pages. Historical matches are filtered out.</p>
    </div>
    """
    with open(SUMMARY_PATH, "w", encoding="utf-8") as f:
        f.write(email_body)
    print(f"Found {len(df_new)} new updates.")

else:
    email_body = """
    <div style="font-family: Arial, sans-serif;">
        <h2 style="color: #2e74b5;">Daily Scraper Update</h2>
        <p>No new keywords or legal updates were detected today.</p>
    </div>
    """
    with open(SUMMARY_PATH, "w", encoding="utf-8") as f:
        f.write(email_body)
    print("No new updates today.")
