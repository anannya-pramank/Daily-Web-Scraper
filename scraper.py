import os
import requests
from bs4 import BeautifulSoup
import pandas as pd

# Define paths (Assuming your config is in a folder named 'WebScraper' on OneDrive)
CONFIG_PATH = "onedrive/WebScraper/Web-Scraper-Config.xlsx"
OUTPUT_PATH = "onedrive/WebScraper/ScraperResults.csv"

print("Reading configuration from OneDrive...")
try:
    sources_df = pd.read_excel(CONFIG_PATH, sheet_name='Sources')
    keywords_df = pd.read_excel(CONFIG_PATH, sheet_name='Keywords')
except Exception as e:
    print(f"Error reading file: {e}. Ensure Web-Scraper-Config.xlsx is in your OneDrive/WebScraper folder.")
    exit(1)

# Fix invisible character trailing spaces from Excel headers dynamically
url_col = [c for c in sources_df.columns if 'URL' in str(c).upper()][0]
org_col = [c for c in sources_df.columns if 'ORG' in str(c).upper()][0]
keyword_col = keywords_df.columns[0]

sources_df[org_col] = sources_df[org_col].astype(str).str.strip()
sources_df[url_col] = sources_df[url_col].astype(str).str.strip()
keywords = keywords_df[keyword_col].dropna().astype(str).str.strip().tolist()

results = []
headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

print(f"Scanning {len(sources_df)} websites for {len(keywords)} keywords...")
for idx, row in sources_df.iterrows():
    org = row[org_col]
    url = row[url_col]
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            page_text = soup.get_text()
            
            for kw in keywords:
                if kw.lower() in page_text.lower():
                    results.append({'Organisation': org, 'URL': url, 'Matched Keyword': kw})
    except Exception:
        print(f"Skipped slow or broken site: {org}")

# Save the output file back to OneDrive structure
output_df = pd.DataFrame(results)
if not output_df.empty:
    output_df.to_csv(OUTPUT_PATH, index=False)
    print(f"Success! Found {len(output_df)} matches.")
else:
    pd.DataFrame(columns=['Organisation', 'URL', 'Matched Keyword']).to_csv(OUTPUT_PATH, index=False)
    print("Scan finished. No matches found today.")
