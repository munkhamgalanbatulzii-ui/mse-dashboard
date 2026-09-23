#!/usr/bin/env python3
from playwright.sync_api import sync_playwright
import json
TARGET="2026-09-23"
with sync_playwright() as p:
    b=p.chromium.launch(headless=True,args=["--disable-blink-features=AutomationControlled"])
    page=b.new_page(viewport={"width":1600,"height":1200},locale="mn-MN",timezone_id="Asia/Ulaanbaatar",
      user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")
    seen=[]
    def onresp(r):
      try:
        post=r.request.post_data or ""
        if r.request.method=="POST" and "trade-daily-report" in r.url and TARGET in post:
          body=r.text()
          seen.append({"post":post,"status":r.status,"ct":r.headers.get("content-type"),"body":body[:12000]})
      except Exception as e:
        seen.append({"error":str(e)})
    page.on("response",onresp)
    page.goto("https://new.mse.mn/trade-daily-report",wait_until="domcontentloaded",timeout=90000)
    page.wait_for_timeout(7000)
    inp=page.locator('input[type="date"]')
    if inp.input_value()!=TARGET:
      inp.evaluate("""(e,v)=>{const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;s.call(e,v);e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));}""",TARGET)
    page.wait_for_timeout(15000)
    for x in seen:
      print("[POSTRESP]",json.dumps(x,ensure_ascii=False))
    b.close()
