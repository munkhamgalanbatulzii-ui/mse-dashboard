#!/usr/bin/env python3
from playwright.sync_api import sync_playwright
import json
with sync_playwright() as p:
    b=p.chromium.launch(headless=True,args=["--disable-blink-features=AutomationControlled"])
    page=b.new_page(viewport={"width":1600,"height":1200},locale="mn-MN",timezone_id="Asia/Ulaanbaatar",
      user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")
    page.goto("https://new.mse.mn/investor-hub",wait_until="domcontentloaded",timeout=90000)
    page.wait_for_timeout(10000)
    links=page.eval_on_selector_all('a[href*="/news/"]',"""els=>els.map(a=>({href:a.href,text:a.innerText.trim(),outer:a.outerHTML.slice(0,1600)}))""")
    print("[LINKS]",json.dumps(links,ensure_ascii=False))
    b.close()
