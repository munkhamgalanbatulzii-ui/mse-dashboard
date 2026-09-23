#!/usr/bin/env python3
from playwright.sync_api import sync_playwright
import json
with sync_playwright() as p:
    b=p.chromium.launch(headless=True,args=["--disable-blink-features=AutomationControlled"])
    page=b.new_page(viewport={"width":1600,"height":1200},locale="mn-MN",timezone_id="Asia/Ulaanbaatar",
      user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")
    req=[]
    resp=[]
    def rq(x):
        try:
            if x.method=="POST" and "new.mse.mn" in x.url:
                pd=x.post_data or ""
                if any(k in pd.lower() for k in ["news","article","dividend","category"]):
                    req.append({"url":x.url,"post":pd[:2000]})
        except: pass
    def rs(x):
        try:
            post=x.request.post_data or ""
            if any(k in post.lower() for k in ["news","article","dividend","category"]):
                resp.append({"post":post[:1000],"status":x.status,"ct":x.headers.get("content-type"),"body":x.text()[:12000]})
        except: pass
    page.on("request",rq); page.on("response",rs)
    page.goto("https://new.mse.mn/investor-hub",wait_until="domcontentloaded",timeout=90000)
    page.wait_for_timeout(15000)
    print("[BODY]", page.locator("body").inner_text()[:6000].replace("\n"," | "))
    for x in req: print("[REQ]",json.dumps(x,ensure_ascii=False))
    for x in resp: print("[RESP]",json.dumps(x,ensure_ascii=False))
    b.close()

# trigger
