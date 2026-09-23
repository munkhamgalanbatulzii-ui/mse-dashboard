#!/usr/bin/env python3
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import json

URL="https://new.mse.mn/trade-daily-report"
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,args=["--disable-blink-features=AutomationControlled"])
    page=browser.new_page(
        viewport={"width":1600,"height":1200},
        locale="mn-MN",
        timezone_id="Asia/Ulaanbaatar",
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36",
    )
    hits=[]
    page.on("request", lambda req: hits.append({"method":req.method,"url":req.url,"post":(req.post_data or "")[:1000]}) if ("mse.mn" in req.url) else None)
    r=page.goto(URL,wait_until="domcontentloaded",timeout=90000)
    try: page.wait_for_load_state("networkidle",timeout=30000)
    except: pass
    page.wait_for_timeout(10000)
    print("[status]", r.status if r else None, page.url, page.title())
    print("[inputs]", json.dumps(page.eval_on_selector_all("input", """els=>els.map((e,i)=>({i,type:e.type,name:e.name,id:e.id,value:e.value,placeholder:e.placeholder,outer:e.outerHTML.slice(0,600)}))"""),ensure_ascii=False))
    print("[buttons]", json.dumps(page.eval_on_selector_all("button", """els=>els.map((e,i)=>({i,text:e.innerText.trim(),aria:e.getAttribute('aria-label'),title:e.getAttribute('title'),outer:e.outerHTML.slice(0,500)})).filter(x=>x.text||x.aria||x.title)"""),ensure_ascii=False))
    print("[selects]", json.dumps(page.eval_on_selector_all("select", """els=>els.map((e,i)=>({i,id:e.id,name:e.name,value:e.value,outer:e.outerHTML.slice(0,700)}))"""),ensure_ascii=False))
    print("[body]", page.locator("body").inner_text()[:5000].replace("\n"," | "))
    for x in hits:
        u=x["url"].lower()
        if any(k in u for k in ["report","trade","history","daily","date","market"]):
            print("[REQ]",json.dumps(x,ensure_ascii=False))
    browser.close()
