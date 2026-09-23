#!/usr/bin/env python3
from playwright.sync_api import sync_playwright
import json

TARGET="2026-09-22"
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,args=["--disable-blink-features=AutomationControlled"])
    page=browser.new_page(
        viewport={"width":1600,"height":1200}, locale="mn-MN", timezone_id="Asia/Ulaanbaatar",
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")
    reqs=[]
    page.on("request",lambda req:reqs.append({"method":req.method,"url":req.url,"post":(req.post_data or "")[:500]}) if req.method=="POST" and "trade-daily-report" in req.url else None)
    page.goto("https://new.mse.mn/trade-daily-report",wait_until="domcontentloaded",timeout=90000)
    page.wait_for_timeout(8000)
    inp=page.locator('input[type="date"]')
    print("[before]",inp.input_value())
    inp.evaluate("""(e,v)=>{const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;s.call(e,v);e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));}""",TARGET)
    page.wait_for_timeout(10000)
    print("[after]",inp.input_value())
    print("[body-date]", "2026-09-22" in page.locator("body").inner_text())
    tables=page.eval_on_selector_all("table","""els=>els.map((t,i)=>({i,headers:Array.from(t.querySelectorAll('thead th')).map(x=>x.innerText.trim()),rows:Array.from(t.querySelectorAll('tbody tr')).map(tr=>Array.from(tr.querySelectorAll('td')).map(td=>td.innerText.trim())).filter(r=>r.some(Boolean))}))""")
    for t in tables:
        if t["rows"]:
            print("[TABLE]",json.dumps({"i":t["i"],"headers":t["headers"],"n":len(t["rows"]),"first":t["rows"][0]},ensure_ascii=False))
    for q in reqs[-50:]:
        if TARGET in q["post"]:
            print("[REQ]",json.dumps(q,ensure_ascii=False))
    browser.close()
