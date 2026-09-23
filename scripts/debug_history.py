#!/usr/bin/env python3
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import json, time

DATES=["2026-09-11","2026-09-14","2026-09-15","2026-09-16","2026-09-17","2026-09-18","2026-09-21","2026-09-22","2026-09-23"]
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,args=["--disable-blink-features=AutomationControlled"])
    page=browser.new_page(viewport={"width":1600,"height":1200},locale="mn-MN",timezone_id="Asia/Ulaanbaatar",
      user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")
    page.goto("https://new.mse.mn/trade-daily-report",wait_until="domcontentloaded",timeout=90000)
    page.wait_for_timeout(7000)
    inp=page.locator('input[type="date"]')
    for d in DATES:
        try:
            with page.expect_response(lambda r,td=d: "trade-daily-report" in r.url and r.request.method=="POST" and td in (r.request.post_data or "") and "tradingHistoryCs1" in (r.request.post_data or ""), timeout=30000):
                inp.evaluate("""(e,v)=>{const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;s.call(e,v);e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));}""",d)
        except PlaywrightTimeoutError:
            pass
        page.wait_for_timeout(3500)
        tables=page.eval_on_selector_all("table","""els=>els.map((t,i)=>({i,headers:Array.from(t.querySelectorAll('thead th')).map(x=>x.innerText.trim()),rows:Array.from(t.querySelectorAll('tbody tr')).map(tr=>Array.from(tr.querySelectorAll('td')).map(td=>td.innerText.trim())).filter(r=>r.some(Boolean))}))""")
        blocks=[]
        for t in tables:
            h=t["headers"]
            if h==["Симбол","Дээд","Доод","Тоо ширхэг","Үнийн дүн"]:
                for row in t["rows"]:
                    if row and not row[0].startswith("Энэ өдөр"):
                        blocks.append(row)
        print("[DAYBLOCK]",json.dumps({"date":d,"blocks":blocks},ensure_ascii=False))
    browser.close()
