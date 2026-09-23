#!/usr/bin/env python3
from playwright.sync_api import sync_playwright
import json
with sync_playwright() as p:
    b=p.chromium.launch(headless=True,args=["--disable-blink-features=AutomationControlled"])
    page=b.new_page(viewport={"width":1600,"height":1200},locale="mn-MN",timezone_id="Asia/Ulaanbaatar",
      user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")
    reqs=[]
    resps=[]
    page.on("request",lambda r:reqs.append({"method":r.method,"url":r.url,"post":(r.post_data or "")[:2000]}) if "mse.mn" in r.url else None)
    def onresp(r):
      try:
        post=r.request.post_data or ""
        if "news" in post.lower() or "article" in post.lower() or "category" in post.lower() or "news" in r.url.lower():
          resps.append({"url":r.url,"post":post[:1200],"status":r.status,"ct":r.headers.get("content-type"),"body":r.text()[:5000]})
      except: pass
    page.on("response",onresp)
    page.goto("https://new.mse.mn/live-market",wait_until="domcontentloaded",timeout=90000)
    page.wait_for_timeout(12000)
    print("[selects]",json.dumps(page.eval_on_selector_all("select","""els=>els.map((e,i)=>({i,outer:e.outerHTML}))"""),ensure_ascii=False))
    print("[body]",page.locator("body").inner_text()[-5000:].replace("\n"," | "))
    for x in reqs:
      if any(k in ((x["post"]+" "+x["url"]).lower()) for k in ["news","article","category"]):
        print("[REQ]",json.dumps(x,ensure_ascii=False))
    for x in resps:
      print("[RESP]",json.dumps(x,ensure_ascii=False))
    b.close()
