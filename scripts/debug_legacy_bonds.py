#!/usr/bin/env python3
from playwright.sync_api import sync_playwright

URL="https://www.mse.mn/todays-trade"
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,args=["--disable-blink-features=AutomationControlled"])
    page=browser.new_page(viewport={"width":1600,"height":1200},locale="mn-MN",timezone_id="Asia/Ulaanbaatar")
    page.goto(URL,wait_until="domcontentloaded",timeout=90000)
    try: page.wait_for_load_state("networkidle",timeout=30000)
    except: pass
    page.wait_for_timeout(6000)
    print("TITLE",page.title())
    print("BODY_SNIP")
    body=page.locator("body").inner_text(timeout=15000)
    for line in body.splitlines():
        if "бонд" in line.lower() or "29,519" in line or "29,200" in line:
            print(line)
    print("TABLES")
    tables=page.eval_on_selector_all("table", """els=>els.map((t,i)=>({
      i,
      headers:Array.from(t.querySelectorAll('thead th')).map(x=>x.innerText.trim()),
      rows:Array.from(t.querySelectorAll('tbody tr')).map(tr=>Array.from(tr.querySelectorAll('td')).map(td=>td.innerText.trim())).filter(r=>r.some(Boolean))
    }))""")
    for t in tables:
        print("TABLE",t["i"],"HEADERS",t["headers"])
        for r in t["rows"][:10]:
            print("ROW",r)
    browser.close()
