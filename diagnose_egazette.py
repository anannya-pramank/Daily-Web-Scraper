#!/usr/bin/env python3
"""
Diagnostic walker for the egazette.gov.in postback chain.

Mirrors parse_egazette()'s flow step-by-step, but instead of silently
returning [] the moment a form isn't found, it dumps:
  - the raw HTML of the response at each step (./egz_debug/stepN_*.html)
  - every <form> found on the page, with its action / id / name attrs
  - whether the expected form matched, and why not if it didn't

Run this from a machine that CAN reach egazette.gov.in (this sandbox
can't — its network egress is locked to package registries). Just:

    pip install requests beautifulsoup4
    python diagnose_egazette.py

Read the printed output top to bottom; the first step that says
"NO MATCH" is where the real scraper's flow is breaking.
"""
import os
import re
import sys
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

OUT_DIR = "egz_debug"
TIMEOUT = 45
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

_EGZ_SKIP_ALWAYS = {"btnStandard", "btn_Reforms", "btnHindi"}
_EGZ_SKIP_SEARCHMENU = {
    "btnGazetteID", "btnContentID", "btnMinistry", "btnBill",
    "btnNotification", "btnPublish", "btneSearch",
}


def dump(step: int, label: str, html: str) -> str:
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"step{step}_{label}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def describe_forms(soup: BeautifulSoup) -> list[dict]:
    forms = []
    for form in soup.find_all("form"):
        forms.append({
            "action": form.get("action"),
            "id": form.get("id"),
            "name": form.get("name"),
            "n_inputs": len(form.find_all("input")),
            "n_selects": len(form.find_all("select")),
        })
    return forms


def find_form(soup: BeautifulSoup, endp: str):
    endp = endp.lower()
    for form in soup.find_all("form"):
        action = (form.get("action") or "").lower()
        if action.rstrip("/").split("/")[-1] == endp or action in (f"./{endp}", endp):
            return form
    return None


def form_data(soup: BeautifulSoup, endp: str, skip: set | None = None):
    form = find_form(soup, endp)
    if form is None:
        return None
    skip = skip or set()
    postdata = []
    for tag in form.find_all(re.compile(r"^(input|select)$")):
        name = tag.get("name")
        if not name:
            continue
        if tag.name == "input":
            if tag.get("type") == "image" or name in _EGZ_SKIP_ALWAYS or name in skip:
                continue
            postdata.append((name, tag.get("value") or ""))
        else:
            if name == "ddlMinistry":
                value = "Select Ministry"
            elif name == "ddlDepartment":
                value = "Select Department"
            elif name == "ddlOffice":
                value = "Select Office"
            else:
                opt = tag.find("option", {"selected": "selected"})
                value = (opt.get("value") or "") if opt else ""
            postdata.append((name, value))
    return postdata


def set_field(postdata: list, key: str, value: str) -> list:
    out, found = [], False
    for k, v in postdata:
        if k == key:
            out.append((k, value))
            found = True
        else:
            out.append((k, v))
    if not found:
        out.append((key, value))
    return out


def report_step(step: int, label: str, soup: BeautifulSoup, url: str, expected_endp: str):
    path = dump(step, label, str(soup))
    forms = describe_forms(soup)
    match = find_form(soup, expected_endp)
    print(f"\n--- step {step}: {label} ---")
    print(f"  URL:            {url}")
    print(f"  HTML saved to:  {path}")
    print(f"  forms present:  {len(forms)}")
    for f in forms:
        print(f"    action={f['action']!r}  id={f['id']!r}  name={f['name']!r}  "
              f"inputs={f['n_inputs']}  selects={f['n_selects']}")
    if match is not None:
        print(f"  ✓ MATCH: found a form whose action basename == {expected_endp!r}")
    else:
        print(f"  ✗ NO MATCH: no form action basename == {expected_endp!r}")
        title = soup.find("title")
        print(f"  page <title>: {title.get_text(strip=True) if title else '(none)'}")
        # common failure signatures
        text_lc = soup.get_text(" ", strip=True).lower()
        for needle in ("session", "expired", "captcha", "error", "not authorized", "maintenance"):
            if needle in text_lc:
                print(f"  ⚠ page text contains {needle!r} — possible cause")
    return match, forms


def main():
    session = requests.Session()
    session.headers.update(HEADERS)

    # Step 1: homepage
    print("=== egazette.gov.in postback chain diagnostic ===")
    try:
        r = session.get("https://egazette.gov.in/", timeout=TIMEOUT, allow_redirects=True)
    except Exception as e:
        print(f"FATAL: homepage unreachable: {e}")
        sys.exit(1)
    if r.status_code != 200:
        print(f"FATAL: homepage HTTP {r.status_code}")
        sys.exit(1)
    curr_url = r.url
    soup = BeautifulSoup(r.text, "html.parser")
    default_endp = curr_url.split("/")[-1].lower() or "default.aspx"
    match, _ = report_step(1, "homepage", soup, curr_url, default_endp)
    if match is None:
        print("\n>>> Breaks at step 1: default.aspx form not found. Site structure likely changed.")
        return

    # Step 2: default.aspx -> Search menu postback ('sgzt')
    postdata = form_data(soup, default_endp)
    postdata = set_field(postdata, "__EVENTTARGET", "sgzt")
    postdata = set_field(postdata, "ddlkeyword", "Select Keyword")
    try:
        r = session.post(curr_url, data=postdata, timeout=TIMEOUT, headers={"Referer": curr_url})
    except Exception as e:
        print(f"\nFATAL: step 2 postback failed: {e}")
        return
    if r.status_code != 200 or len(r.content) <= 500:
        print(f"\nFATAL: step 2 postback HTTP {r.status_code}, {len(r.content)} bytes")
        dump(2, "searchmenu_FAILED", r.text)
        return
    curr_url = r.url
    soup = BeautifulSoup(r.text, "html.parser")
    match, _ = report_step(2, "searchmenu", soup, curr_url, "searchmenu.aspx")
    if match is None:
        print("\n>>> Breaks at step 2: SearchMenu form not found. This matches the "
              "'SearchMenu form not found (flow changed?)' failure mode.")
        return

    # Step 3: SearchMenu.aspx -> SearchCategory.aspx
    postdata = form_data(soup, "searchmenu.aspx", skip=_EGZ_SKIP_SEARCHMENU)
    search_url = urljoin(curr_url, "SearchCategory.aspx")
    try:
        r = session.post(search_url, data=postdata, timeout=TIMEOUT, headers={"Referer": curr_url})
    except Exception as e:
        print(f"\nFATAL: step 3 postback failed: {e}")
        return
    if r.status_code != 200 or len(r.content) <= 500:
        print(f"\nFATAL: step 3 postback HTTP {r.status_code}, {len(r.content)} bytes")
        dump(3, "searchcategory_FAILED", r.text)
        return
    curr_url = r.url
    soup = BeautifulSoup(r.text, "html.parser")
    endp = curr_url.split("/")[-1].lower()
    match, _ = report_step(3, "searchcategory", soup, curr_url, endp)
    if match is None:
        print("\n>>> Breaks at step 3: this is the exact failure seen in the scraper log "
              "('egazette: SearchCategory form not found'). Check the dumped HTML above "
              "for the actual form action/id — the parser's action-basename match "
              "(_egz_find_form) is likely out of sync with what the site now returns.")
        print(f">>> Landed URL was: {curr_url!r} (expected basename: {endp!r})")
        return

    print("\n>>> All three steps matched — the chain that fails in production is currently "
          "working. Re-run the real scraper; the site's ASP.NET session/state may just have "
          "been flaky at that particular run.")


if __name__ == "__main__":
    main()
