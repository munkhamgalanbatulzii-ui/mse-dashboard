#!/usr/bin/env python3
from playwright.sync_api import sync_playwright
import json
with sync_playwright() as p:
    b=p.chromium.launch(headless=True,args=["--disable-blink-features=AutomationControlled"])
    page=b.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/153.0.0.0 Safari/537.36")
    page.goto("https://new.mse.mn",wait_until="domcontentloaded",timeout=90000)
    for url in [
      "https://new.mse.mn/api/public/news/categories?lang=mn",
      "https://new.mse.mn/api/public/news?limit=100&lang=mn"
    ]:
      r=page.request.get(url,timeout=90000)
      print("[API]",url,r.status,r.text()[:30000])
    b.close()
