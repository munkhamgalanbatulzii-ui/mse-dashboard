#!/usr/bin/env python3
from playwright.sync_api import sync_playwright
import json,re
with sync_playwright() as p:
    b=p.chromium.launch(headless=True,args=["--disable-blink-features=AutomationControlled"])
    page=b.new_page(viewport={"width":1600,"height":1200},locale="mn-MN",timezone_id="Asia/Ulaanbaatar",
      user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")
    req=[]
    def rq(x):
        try:
            if x.method=="POST" and "new.mse.mn" in x.url:
                pd=x.post_data or ""
                m=re.search(r'"url":"([^"]+)"',pd)
                req.append({"endpoint":m.group(1) if m else None,"post":pd[:1000]})
        except: pass
    page.on("request",rq)
    page.goto("https://new.mse.mn/investor-hub",wait_until="domcontentloaded",timeout=90000)
    page.wait_for_timeout(15000)
    for x in req: print("[REQ]",json.dumps(x,ensure_ascii=False))
    b.close()
