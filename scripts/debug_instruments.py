#!/usr/bin/env python3
from playwright.sync_api import sync_playwright
import re, json
with sync_playwright() as p:
    b=p.chromium.launch(headless=True,args=["--disable-blink-features=AutomationControlled"])
    page=b.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/153.0.0.0 Safari/537.36")
    page.goto("https://new.mse.mn/trade-daily-report",wait_until="domcontentloaded",timeout=90000)
    page.wait_for_timeout(5000)
    srcs=page.eval_on_selector_all("script[src]","els=>els.map(x=>x.src)")
    names=set()
    for url in srcs:
        try:
            r=page.request.get(url,timeout=30000)
            txt=r.text()
            for m in re.findall(r'trading(?:History|Status)[A-Za-z0-9_]+',txt):
                names.add(m)
        except Exception as e:
            pass
    print("[NAMES]",json.dumps(sorted(names)))
    b.close()
