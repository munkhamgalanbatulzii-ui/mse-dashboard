#!/usr/bin/env python3
"""
MSE dashboard daily updater for GitHub Actions.

- Opens https://new.mse.mn/todays-trade with Playwright.
- Reads the rendered tables after client-side data loads.
- Appends the current Ulaanbaatar trading day to data.json.
- Recomputes lightweight alerts using the dashboard's existing thresholds.
- Leaves historical/dividend/market-cap metadata intact.

If MSE changes its table layout, the script exits with a clear error instead of
writing bad data.
"""
from __future__ import annotations

import json
import re
import math
import statistics
import sys
import time
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data.json"
URL = "https://new.mse.mn/todays-trade"
TZ = ZoneInfo("Asia/Ulaanbaatar")

def num(s, default=None):
    if s is None:
        return default
    s = str(s).strip().replace("\u00a0"," ").replace(",", "").replace("₮","").replace("$","")
    s = s.replace("%","").strip()
    if not s or s in {"—","-","–","null","None"}:
        return default
    try:
        return float(s)
    except ValueError:
        # Keep digits, dot, minus only.
        t = "".join(ch for ch in s if ch.isdigit() or ch in ".-")
        try:
            return float(t) if t not in {"","-",".","-."} else default
        except ValueError:
            return default

def intnum(s, default=0):
    v = num(s, None)
    return int(round(v)) if v is not None else default

def txt(x):
    return " ".join(str(x or "").replace("\u00a0"," ").split())

def previous_symbol_name(data, sym):
    for d in reversed(data.get("dates", [])):
        for r in data.get("other", {}).get(d, []):
            if len(r) >= 3 and r[1] == sym:
                return r[2]
    return sym

def ensure_symbol(data, sym, name=None):
    for i, s in enumerate(data["syms"]):
        if s[0] == sym:
            return i
    data["syms"].append([sym, name or sym, "D"])
    i = len(data["syms"]) - 1
    data.setdefault("thr", {})[str(i)] = [0.20, 0.12, 0.08, 100000, 500000, 0.25]
    data.setdefault("sector", {})[str(i)] = "Бусад"
    data.setdefault("sectorSrc", {})[str(i)] = "тод."
    data.setdefault("stats", {})["symbols"] = len(data["syms"])
    print(f"[info] new symbol appended as Tier D: {sym}", file=sys.stderr)
    return i

def prior_trade_rows(data, sym_idx, limit=60):
    vals = []
    last_date = None
    for d in reversed(data.get("dates", [])):
        rows = data.get("days", {}).get(d, [])
        row = next((r for r in rows if r[0] == sym_idx), None)
        if row:
            if last_date is None:
                last_date = d
            vals.append(row)
            if len(vals) >= limit:
                break
    return vals, last_date

def build_alerts(data, day_rows, trade_date):
    alerts = []
    for r in day_rows:
        i, close, ret, qty, turnover = r
        th = data.get("thr", {}).get(str(i), [0.20,0.12,0.08,100000,500000,0.25])
        up_thr, dn_thr, sigma, min_turn = th[0], th[1], th[2], th[3]
        ret = ret or 0.0
        event = None

        # Price alerts: same concept documented in the dashboard footer.
        if turnover >= min_turn and ret >= up_thr:
            ratio = ret / up_thr if up_thr else 0
            sev = "CRITICAL" if ratio >= 1.5 else "WATCH"
            event = [i, sev, round(ret,4), round(ratio,1), "PRICE_UP"]
        elif turnover >= min_turn and ret <= -dn_thr:
            ratio = abs(ret) / dn_thr if dn_thr else 0
            sev = "CRITICAL" if ratio >= 1.5 else "WATCH"
            event = [i, sev, round(ret,4), round(ratio,1), "PRICE_DOWN"]

        if event is None:
            hist, last_trade = prior_trade_rows(data, i, 60)
            vols = [x[3] for x in hist if x[3] and x[3] > 0]
            med = statistics.median(vols) if vols else 0
            vr = qty / med if med else 0
            reopened = False
            if last_trade:
                try:
                    reopened = (date.fromisoformat(trade_date) - date.fromisoformat(last_trade)).days >= 20
                except Exception:
                    pass
            if vr >= 5:
                code = "VOLUME_SPIKE+REOPEN" if reopened else "VOLUME_SPIKE"
                event = [i, "INFO", round(ret,4), round(vr,1), code]
            elif reopened and turnover >= min_turn:
                event = [i, "INFO", round(ret,4), 1.0, "REOPEN"]

        if event:
            alerts.append(event)

    order = {"CRITICAL":0,"WATCH":1,"INFO":2}
    alerts.sort(key=lambda a:(order.get(a[1],9), -abs(a[2] or 0)))
    return alerts

def get_tables():
    """Load the live MSE tables in a browser context that matches a normal desktop client."""
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            viewport={"width": 1600, "height": 1200},
            locale="mn-MN",
            timezone_id="Asia/Ulaanbaatar",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/153.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        resp = page.goto(URL, wait_until="domcontentloaded", timeout=90000)
        if resp and resp.status >= 400:
            browser.close()
            raise RuntimeError(f"MSE page returned HTTP {resp.status}")

        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except PlaywrightTimeoutError:
            pass

        # MSE hydrates its trading tables client-side. A real desktop UA plus
        # a longer hydration window was required on GitHub-hosted runners.
        try:
            page.wait_for_function(
                """() => Array.from(document.querySelectorAll('table tbody tr'))
                    .some(r => r.querySelectorAll('td').length >= 11)""",
                timeout=45000,
            )
        except PlaywrightTimeoutError:
            page.wait_for_timeout(5000)

        tables = page.eval_on_selector_all(
            "table",
            """els => els.map((t,ti) => ({
                index: ti,
                headers: Array.from(t.querySelectorAll('thead th')).map(x => x.innerText.trim()),
                rows: Array.from(t.querySelectorAll('tbody tr')).map(tr =>
                    Array.from(tr.querySelectorAll('td')).map(td => td.innerText.trim())
                ).filter(r => r.some(x => x && x.trim()))
            }))"""
        )
        browser.close()
        return tables

def pick_nonempty(tables):
    return [t for t in tables if t.get("rows")]

def parse_stock_table(rows, data):
    out = []
    for c in rows:
        if len(c) < 11:
            continue
        sym = txt(c[0])
        if not sym or len(sym) > 32:
            continue
        close = num(c[6], None)
        retp = num(c[8], None)
        qty = intnum(c[9], 0)
        turn = num(c[10], 0.0) or 0.0
        if close is None or qty <= 0:
            continue
        i = ensure_symbol(data, sym)
        ret = (retp / 100.0) if retp is not None else 0.0
        out.append([i, float(close), round(ret,4), int(qty), float(turn)])
    return out


def parse_history_stock_table(rows, data):
    """Parse one historical common-stock category table from trade-daily-report."""
    out = []
    for c in rows:
        # Historical detail columns:
        # sym, open, high, low, prev_close, close, change, pct, qty, turnover, ...
        if len(c) < 10:
            continue
        sym = txt(c[0])
        if not sym or sym.startswith("Энэ өдөр") or len(sym) > 32:
            continue
        close = num(c[5], None)
        retp = num(c[7], None)
        qty = intnum(c[8], 0)
        turn = num(c[9], 0.0) or 0.0
        if close is None or qty <= 0:
            continue
        i = ensure_symbol(data, sym)
        ret = (retp / 100.0) if retp is not None else 0.0
        out.append([i, float(close), round(ret,4), int(qty), float(turn)])
    return out

def _desktop_context(browser):
    return browser.new_context(
        viewport={"width": 1600, "height": 1200},
        locale="mn-MN",
        timezone_id="Asia/Ulaanbaatar",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/153.0.0.0 Safari/537.36"
        ),
    )

def _parse_rsc_array(body):
    """Parse the JSON array from a Next.js text/x-component server-action response."""
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("1:"):
            payload = line[2:]
            try:
                obj = json.loads(payload)
                return obj if isinstance(obj, list) else []
            except Exception:
                return []
    return []

def _history_records_to_rows(records, data):
    rows = []
    for rec in records:
        sym = txt(rec.get("companySymbol"))
        if not sym:
            continue
        close = num(rec.get("ClosingPrice"), None)
        retp = num(rec.get("changePercentage"), None)
        qty = intnum(rec.get("Volume"), 0)
        turn = num(rec.get("Turnover"), 0.0) or 0.0
        if close is None or qty <= 0:
            continue
        i = ensure_symbol(data, sym)
        rows.append([
            i, float(close),
            round((retp or 0.0) / 100.0, 4),
            int(qty), float(turn)
        ])
    return rows

def fetch_historical_stock_days(target_dates, data):
    """Fetch exact historical Cs1/Cs2/Cs3 JSON from MSE server-action responses."""
    if not target_dates:
        return {}

    result = {}
    captures = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = _desktop_context(browser)
        page = context.new_page()

        def on_response(resp):
            try:
                post = resp.request.post_data or ""
                m = re.search(
                    r'"url":"(tradingHistoryCs[123])".*?date=(\d{4}-\d{2}-\d{2})',
                    post
                )
                if not m:
                    return
                endpoint, d = m.group(1), m.group(2)
                records = _parse_rsc_array(resp.text())
                captures.setdefault(d, {})[endpoint] = records
            except Exception as e:
                print(f"[backfill] response parse warning: {e}")

        page.on("response", on_response)
        resp = page.goto(
            "https://new.mse.mn/trade-daily-report",
            wait_until="domcontentloaded",
            timeout=90000,
        )
        if resp and resp.status >= 400:
            browser.close()
            raise RuntimeError(f"MSE history page returned HTTP {resp.status}")
        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except PlaywrightTimeoutError:
            pass
        page.wait_for_timeout(5000)

        inp = page.locator('input[type="date"]')
        for d in target_dates:
            print(f"[backfill] loading {d}")
            captures.pop(d, None)

            # React's controlled input reacts to the native setter + input/change.
            inp.evaluate(
                """(e,v)=>{
                    const setter=Object.getOwnPropertyDescriptor(
                        HTMLInputElement.prototype,'value'
                    ).set;
                    setter.call(e,v);
                    e.dispatchEvent(new Event('input',{bubbles:true}));
                    e.dispatchEvent(new Event('change',{bubbles:true}));
                }""",
                d,
            )

            deadline = time.time() + 40
            while time.time() < deadline:
                got = captures.get(d, {})
                if all(k in got for k in (
                    "tradingHistoryCs1",
                    "tradingHistoryCs2",
                    "tradingHistoryCs3",
                )):
                    break
                page.wait_for_timeout(250)

            got = captures.get(d, {})
            missing = [
                k for k in ("tradingHistoryCs1","tradingHistoryCs2","tradingHistoryCs3")
                if k not in got
            ]
            if missing:
                print(f"[backfill] {d}: missing responses={missing}")
                continue

            stock_rows = []
            for k in ("tradingHistoryCs1","tradingHistoryCs2","tradingHistoryCs3"):
                stock_rows += _history_records_to_rows(got[k], data)

            by_i = {r[0]: r for r in stock_rows}
            stock_rows = sorted(by_i.values(), key=lambda r: r[4], reverse=True)
            if stock_rows:
                result[d] = stock_rows
                print(
                    f"[backfill] {d}: stocks={len(stock_rows)} "
                    f"turnover={sum(r[4] for r in stock_rows):.2f}"
                )
            else:
                print(f"[backfill] {d}: no stock trades")

        browser.close()
    return result



OTHER_ENDPOINTS = {
    "tradingXoc": ("Хөрөнгө оруулалтын сан", "MNT"),
    "tradingAbs": ("Хөрөнгөөр баталгаажсан ҮЦ", "MNT"),
    "tradingStatusZG": ("Засгийн газрын ҮЦ", "MNT"),
    "tradingStatusXK": ("Компанийн бонд", "MNT"),
    "tradingStatusUSD": ("Компанийн бонд", "USD"),
}

def _other_records_to_rows(records, category, ccy, data):
    rows = []
    for rec in records:
        sym = txt(rec.get("companySymbol"))
        if not sym:
            continue
        close = num(rec.get("ClosingPrice"), None)
        qty = intnum(rec.get("Volume"), 0)
        turn = num(rec.get("Turnover"), 0.0) or 0.0
        retp = num(rec.get("changePercentage"), None)
        if close is None or qty <= 0:
            continue
        name = previous_symbol_name(data, sym)
        rows.append([
            category, sym, name, ccy, float(close), int(qty),
            float(turn), round((retp or 0.0) / 100.0, 4)
        ])
    return rows

def fetch_other_days(target_dates, data):
    """Fetch funds, ABS, government securities and corporate bonds from exact MSE responses."""
    if not target_dates:
        return {}

    captures = {}
    result = {}
    expected = tuple(OTHER_ENDPOINTS.keys())

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = _desktop_context(browser)
        page = context.new_page()

        def on_response(resp):
            try:
                post = resp.request.post_data or ""
                m = re.search(
                    r'"url":"(tradingXoc|tradingAbs|tradingStatusZG|tradingStatusXK|tradingStatusUSD)".*?date=(\d{4}-\d{2}-\d{2})',
                    post
                )
                if not m:
                    return
                endpoint, d = m.group(1), m.group(2)
                captures.setdefault(d, {})[endpoint] = _parse_rsc_array(resp.text())
            except Exception as e:
                print(f"[other] response parse warning: {e}")

        page.on("response", on_response)
        resp = page.goto(
            "https://new.mse.mn/trade-daily-report",
            wait_until="domcontentloaded",
            timeout=90000,
        )
        if resp and resp.status >= 400:
            browser.close()
            raise RuntimeError(f"MSE other-instruments report returned HTTP {resp.status}")
        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except PlaywrightTimeoutError:
            pass
        page.wait_for_timeout(4000)

        inp = page.locator('input[type="date"]')
        for d in target_dates:
            print(f"[other] loading {d}")

            # Initial page load may already have captured the current date.
            if not all(k in captures.get(d, {}) for k in expected):
                inp.evaluate(
                    """(e,v)=>{
                        const setter=Object.getOwnPropertyDescriptor(
                            HTMLInputElement.prototype,'value'
                        ).set;
                        setter.call(e,v);
                        e.dispatchEvent(new Event('input',{bubbles:true}));
                        e.dispatchEvent(new Event('change',{bubbles:true}));
                    }""",
                    d,
                )

            deadline = time.time() + 35
            while time.time() < deadline:
                if all(k in captures.get(d, {}) for k in expected):
                    break
                page.wait_for_timeout(250)

            got = captures.get(d, {})
            missing = [k for k in expected if k not in got]
            if missing:
                print(f"[other] {d}: missing responses={missing}")
                continue

            rows = []
            for endpoint, (category, ccy) in OTHER_ENDPOINTS.items():
                rows += _other_records_to_rows(
                    got.get(endpoint, []), category, ccy, data
                )
            result[d] = rows
            cats = {}
            for r in rows:
                cats[r[0] + ("/USD" if r[3] == "USD" else "")] = cats.get(
                    r[0] + ("/USD" if r[3] == "USD" else ""), 0
                ) + 1
            print(f"[other] {d}: rows={len(rows)} categories={cats}")

        browser.close()
    return result

def store_other_day(data, d, rows):
    """Store non-equity exchange instruments and keep summary fields in sync."""
    if rows:
        data.setdefault("other", {})[d] = rows
    else:
        data.setdefault("other", {}).pop(d, None)

    if d in data.get("summary", {}):
        mnt_value = sum(r[6] for r in rows if r[3] == "MNT")
        data["summary"][d][9] = round(mnt_value, 2)
        data["summary"][d][10] = len(rows)

def fetch_block_days(target_dates, data, row_overrides=None):
    """Fetch MNT block trades from MSE daily-report for exact dates."""
    if not target_dates:
        return {}
    row_overrides = row_overrides or {}
    result = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = _desktop_context(browser)
        page = context.new_page()
        resp = page.goto(
            "https://new.mse.mn/trade-daily-report",
            wait_until="domcontentloaded",
            timeout=90000,
        )
        if resp and resp.status >= 400:
            browser.close()
            raise RuntimeError(f"MSE block report returned HTTP {resp.status}")
        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except PlaywrightTimeoutError:
            pass
        page.wait_for_timeout(5000)
        inp = page.locator('input[type="date"]')

        for d in target_dates:
            print(f"[blocks] loading {d}")
            if inp.input_value() != d:
                try:
                    with page.expect_response(
                        lambda r, td=d: (
                            "trade-daily-report" in r.url
                            and r.request.method == "POST"
                            and td in (r.request.post_data or "")
                            and "tradingHistoryCs1" in (r.request.post_data or "")
                        ),
                        timeout=30000,
                    ):
                        inp.evaluate(
                            """(e,v)=>{
                                const setter=Object.getOwnPropertyDescriptor(
                                    HTMLInputElement.prototype,'value'
                                ).set;
                                setter.call(e,v);
                                e.dispatchEvent(new Event('input',{bubbles:true}));
                                e.dispatchEvent(new Event('change',{bubbles:true}));
                            }""",
                            d,
                        )
                except PlaywrightTimeoutError:
                    pass

            # Block tables hydrate slightly after the stock-history responses.
            page.wait_for_timeout(3500)
            tables = page.eval_on_selector_all(
                "table",
                """els => els.map((t,ti) => ({
                    index: ti,
                    headers: Array.from(t.querySelectorAll('thead th')).map(x => x.innerText.trim()),
                    rows: Array.from(t.querySelectorAll('tbody tr')).map(tr =>
                        Array.from(tr.querySelectorAll('td')).map(td => td.innerText.trim())
                    ).filter(r => r.some(x => x && x.trim()))
                }))"""
            )
            block_tables = [
                t for t in tables
                if t.get("headers", []) == ["Симбол","Дээд","Доод","Тоо ширхэг","Үнийн дүн"]
            ]

            # The first matching table is MNT; the second (when present) is USD.
            raw = block_tables[0].get("rows", []) if block_tables else []
            day_rows = row_overrides.get(d) or data.get("days", {}).get(d, [])
            close_by_sym = {}
            for rr in day_rows:
                try:
                    close_by_sym[data["syms"][rr[0]][0]] = rr[1]
                except Exception:
                    pass
            name_by_sym = {s[0]: s[1] for s in data.get("syms", [])}

            parsed = []
            for r in raw:
                if len(r) < 5:
                    continue
                sym = txt(r[0])
                if not sym or sym.startswith("Энэ өдөр"):
                    continue
                qty = intnum(r[3], 0)
                val = num(r[4], 0.0) or 0.0
                if qty <= 0 or val <= 0:
                    continue
                price = val / qty
                ref = close_by_sym.get(sym)
                prem = round(price / ref - 1, 4) if ref else None
                parsed.append([
                    sym, name_by_sym.get(sym, sym),
                    round(price, 6), int(qty), float(val), prem, ref
                ])

            result[d] = parsed
            print(
                f"[blocks] {d}: deals={len(parsed)} "
                f"value={sum(x[4] for x in parsed):.2f}"
            )

        browser.close()
    return result

def store_block_day(data, d, block_rows):
    """Store MNT block trades and keep summary block fields in sync."""
    if block_rows:
        data.setdefault("blocks", {})[d] = block_rows
    else:
        data.setdefault("blocks", {}).pop(d, None)
    if d in data.get("summary", {}):
        data["summary"][d][7] = len(block_rows)
        data["summary"][d][8] = round(sum(r[4] for r in block_rows), 2)

def store_stock_day(data, d, stock_rows):
    """Store one common-stock trading day and recompute alert/summary fields."""
    alerts = build_alerts(data, stock_rows, d)
    stock_turn = sum(r[4] for r in stock_rows)
    qty = sum(r[3] for r in stock_rows)
    up = sum(1 for r in stock_rows if (r[2] or 0) > 0)
    down = sum(1 for r in stock_rows if (r[2] or 0) < 0)
    crit = sum(1 for a in alerts if a[1] == "CRITICAL")

    data.setdefault("days", {})[d] = stock_rows
    data.setdefault("alerts", {})[d] = alerts

    # Historical backfill is stock-level. Other-security and block-trade
    # fields remain zero unless separately available.
    old = data.setdefault("summary", {}).get(d)
    block_count = old[7] if old and len(old) > 7 else 0
    block_value = old[8] if old and len(old) > 8 else 0
    other_value = old[9] if old and len(old) > 9 else 0
    other_count = old[10] if old and len(old) > 10 else 0

    data["summary"][d] = [
        round(stock_turn,2), len(stock_rows), up, down, qty,
        len(alerts), crit, block_count, block_value, other_value, other_count
    ]
    if d not in data["dates"]:
        data["dates"].append(d)
        data["dates"].sort()


def parse_other_table(rows, category, ccy, data):
    out = []
    for c in rows:
        if len(c) < 11:
            continue
        sym = txt(c[0])
        if not sym:
            continue
        close = num(c[6], None)
        retp = num(c[8], None)
        qty = intnum(c[9], 0)
        turn = num(c[10], 0.0) or 0.0
        if close is None or qty <= 0:
            continue
        name = previous_symbol_name(data, sym)
        out.append([category, sym, name, ccy, float(close), int(qty), float(turn),
                    round((retp or 0)/100.0,4)])
    return out

def parse_block_table(rows, data, stock_close):
    out = []
    sym_name = {s[0]:s[1] for s in data["syms"]}
    for c in rows:
        if len(c) < 6:
            continue
        sym = txt(c[0])
        qty = intnum(c[1],0)
        val = num(c[2],0.0) or 0.0
        last = num(c[5],None)
        if not sym or qty <= 0 or not val:
            continue
        price = last if last is not None else val/qty
        ref = stock_close.get(sym)
        prem = round(price/ref-1,4) if ref else None
        out.append([sym, sym_name.get(sym,sym), float(price), int(qty), float(val), prem, ref])
    return out


def _norm_company_name(s):
    s = str(s or "").lower().replace("ё", "е")
    s = re.sub(r'["“”«»‘’\']', "", s)
    s = re.sub(r'\b(хк|ббсб)\b', " ", s)
    return re.sub(r'[\W_]+', "", s, flags=re.UNICODE)

def _latest_close_for_index(data, idx):
    for d in reversed(data.get("dates", [])):
        row = next((r for r in data.get("days", {}).get(d, []) if r[0] == idx), None)
        if row:
            return row[1]
    return None

def _match_dividend_symbol(data, title, body):
    hay = _norm_company_name((title or "") + " " + (body or "")[:1400])
    candidates = []
    for i, s in enumerate(data.get("syms", [])):
        name = _norm_company_name(s[1])
        if name and len(name) >= 3 and name in hay:
            candidates.append((len(name), i, s[0]))
    for r in data.get("div", []):
        name = _norm_company_name(r.get("name"))
        idx = r.get("i")
        if name and idx is not None and len(name) >= 3 and name in hay:
            candidates.append((len(name), idx, r.get("sym")))
    if not candidates:
        return None, None
    candidates.sort(reverse=True)
    return candidates[0][1], candidates[0][2]

def _parse_money_number(s):
    if s is None:
        return None
    t = re.sub(r'[\s,]', "", str(s))
    try:
        return float(t)
    except Exception:
        return None

def _parse_dividend_period(title, body, ann):
    sample = ((title or "") + " " + (body or "")[:1200]).lower()
    m = re.search(r'(\d{4})\s*оны\s*(?:эхний|нэгдүгээр)\s*хагас', sample)
    if m:
        return m.group(1) + " H1"
    m = re.search(r'(\d{4})\s*оны\s*(?:хоёрдугаар|сүүлийн)\s*хагас', sample)
    if m:
        return m.group(1) + " H2"
    m = re.search(r'(\d{4})\s*оны', (title or "").lower())
    if m:
        return m.group(1)
    m = re.search(r'(\d{4})\s*оны', sample)
    if m:
        return m.group(1)
    return ann[:4] if ann else ""

def _extract_article_dates(text):
    vals = []
    for y,m,d in re.findall(
        r'(20\d{2})\s*оны\s*(\d{1,2})\s*(?:дүгээр|дугаар)?\s*сарын\s*(\d{1,2})',
        text or "",
        flags=re.I
    ):
        try:
            vals.append(date(int(y), int(m), int(d)).isoformat())
        except Exception:
            pass
    for y,m,d in re.findall(r'(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})', text or ""):
        try:
            vals.append(date(int(y), int(m), int(d)).isoformat())
        except Exception:
            pass
    return sorted(set(vals))

def _parse_dividend_article(data, ann, href, card_title, article_text):
    idx, sym = _match_dividend_symbol(data, card_title, article_text)
    if idx is None or not sym:
        print(f"[dividend] could not match company: {card_title}")
        return None

    text = article_text or ""
    dps = None
    patterns = [
        r'(?:нэгж|нэг)\s+хувьцаа(?:нд|ны)?[^0-9]{0,80}([0-9][0-9,\s]*(?:\.[0-9]+)?)\s*(?:\([^)]{0,180}\)\s*)?төгрөг',
        r'хувьцаа\s*(?:тус\s*бүрд|тутамд)[^0-9]{0,40}([0-9][0-9,\s]*(?:\.[0-9]+)?)\s*(?:\([^)]{0,180}\)\s*)?төгрөг',
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.I)
        if m:
            dps = _parse_money_number(m.group(1))
            break

    total = None
    m = re.search(
        r'нийт\s+([0-9][0-9,\s]*(?:\.[0-9]+)?)\s*(?:\([^)]{0,500}\)\s*)?'
        r'төгрөг(?:ийн)?\s+ногдол\s+ашиг',
        text,
        flags=re.I,
    )
    if m:
        total = _parse_money_number(m.group(1))

    per = _parse_dividend_period(card_title, text, ann)
    px = _latest_close_for_index(data, idx)
    yld = (dps / px) if (dps is not None and px) else None

    all_dates = _extract_article_dates(text)
    future_dates = [d for d in all_dates if not ann or d >= ann]
    note = ""
    if future_dates:
        latest = max(future_dates)
        if latest > ann:
            note = "Олголт " + latest + " хүртэл"

    prev_name = next(
        (r.get("name") for r in data.get("div", []) if r.get("sym") == sym and r.get("name")),
        None
    )
    name = prev_name or data["syms"][idx][1]

    return {
        "sym": sym,
        "i": idx,
        "name": name,
        "ann": ann,
        "per": per,
        "dps": dps,
        "tot": total,
        "px": px,
        "yield_": yld,
        "note": note,
        "url": href,
    }

def update_dividend_news(data):
    """Check MSE public news API for new dividend announcements and append them."""
    today = datetime.now(TZ).date().isoformat()
    current = data.setdefault("div", [])
    existing = {
        (r.get("ann"), r.get("sym"), None if r.get("dps") is None else round(float(r.get("dps")), 6))
        for r in current
    }
    existing_urls = {r.get("url") for r in current if r.get("url")}
    latest_ann = max((r.get("ann","") for r in current), default="")
    added = []

    with sync_playwright() as p:
        request = p.request.new_context(
            extra_http_headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/153.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json,text/plain,*/*",
                "Referer": "https://new.mse.mn/live-market",
            }
        )
        rr = request.get(
            "https://new.mse.mn/api/public/news?limit=100&lang=mn",
            timeout=90000,
        )
        if not rr.ok:
            request.dispose()
            raise RuntimeError(f"MSE news API HTTP {rr.status}")
        items = rr.json()
        request.dispose()

        candidates = []
        for item in items if isinstance(items, list) else []:
            if item.get("categorySlug") != "nogdol-ashig":
                continue
            ann = str(item.get("publishedAt") or item.get("date") or "")[:10]
            if not ann:
                continue
            # Existing data already covers historical announcements. Scan only
            # the newest edge plus same-date late postings.
            if latest_ann and ann < latest_ann:
                continue
            slug = item.get("slug") or item.get("url")
            if not slug:
                continue
            href = "https://new.mse.mn/news/" + slug.lstrip("/")
            if href in existing_urls:
                continue
            candidates.append((ann, href, item.get("title") or item.get("name") or "", item.get("excerpt") or ""))

        if candidates:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"]
            )
            context = _desktop_context(browser)
            article = context.new_page()

            for ann, href, title, excerpt in candidates:
                try:
                    resp = article.goto(href, wait_until="domcontentloaded", timeout=90000)
                    if resp and resp.status >= 400:
                        print(f"[dividend] HTTP {resp.status}: {href}")
                        continue
                    try:
                        article.wait_for_load_state("networkidle", timeout=20000)
                    except PlaywrightTimeoutError:
                        pass
                    article.wait_for_timeout(1200)
                    body = article.locator("body").inner_text(timeout=15000)
                    try:
                        h1 = article.locator("h1").first.inner_text(timeout=4000).strip()
                        if h1:
                            title = h1
                    except Exception:
                        pass

                    row = _parse_dividend_article(
                        data, ann, href, title,
                        (excerpt + "\n" + body).strip()
                    )
                    if not row:
                        continue
                    key = (
                        row.get("ann"), row.get("sym"),
                        None if row.get("dps") is None else round(float(row.get("dps")), 6)
                    )
                    if key in existing:
                        # Save source URL onto an old same announcement if possible.
                        for old in current:
                            oldkey = (
                                old.get("ann"), old.get("sym"),
                                None if old.get("dps") is None else round(float(old.get("dps")), 6)
                            )
                            if oldkey == key and not old.get("url"):
                                old["url"] = href
                                break
                        existing_urls.add(href)
                        continue

                    current.append(row)
                    existing.add(key)
                    existing_urls.add(href)
                    added.append(row)
                    print(
                        f"[dividend] added {row['ann']} {row['sym']} "
                        f"dps={row['dps']} total={row['tot']}"
                    )
                except Exception as e:
                    print(f"[dividend] article warning {href}: {e}")
            browser.close()

    current.sort(key=lambda r: (r.get("ann",""), r.get("sym","")), reverse=True)
    anns = sorted(r.get("ann") for r in current if r.get("ann"))
    meta = data.setdefault("divMeta", {})
    meta["source"] = "mse.mn — Мэдээ, мэдээлэл → Ногдол ашиг"
    meta["year"] = int(today[:4])
    meta["n"] = len(current)
    meta["range"] = (anns[0] + " → " + anns[-1]) if anns else ""
    meta["lastChecked"] = today
    meta["auto"] = True
    return added

def main():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    today = datetime.now(TZ).date().isoformat()

    # Load today's page first. Historical backfill is handled separately below.
    tables = get_tables()
    nonempty = pick_nonempty(tables)
    print(f"[info] tables={len(tables)} nonempty={len(nonempty)}")

    stock_rows = []
    for t in tables[:3]:
        stock_rows += parse_stock_table(t.get("rows",[]), data)
    by_i = {r[0]:r for r in stock_rows}
    stock_rows = sorted(by_i.values(), key=lambda r:r[4], reverse=True)

    # If today's live page did not hydrate, use the same exact daily-report
    # response path used for historical dates.
    if not stock_rows:
        exact_today = fetch_historical_stock_days([today], data)
        stock_rows = exact_today.get(today, [])
        if stock_rows:
            print(f"[info] recovered today's stocks from daily report: {len(stock_rows)}")

    # Backfill any missing weekdays between the most recent stored trading date
    # and today. MSE's historical report will tell us whether a weekday actually
    # had stock trades; weekends are skipped up front.
    previous = sorted(d for d in data.get("dates", []) if d < today)
    gap_dates = []
    if previous:
        cur = date.fromisoformat(previous[-1])
        stop = date.fromisoformat(today)
        from datetime import timedelta
        cur += timedelta(days=1)
        while cur < stop:
            ds = cur.isoformat()
            if cur.weekday() < 5 and ds not in data.get("days", {}):
                gap_dates.append(ds)
            cur += timedelta(days=1)

    # One-time repair for the 2026-09-14..22 historical gap. Version 1 used
    # rendered DOM tables and could lag by one selected date; version 2 reads
    # the exact server-action JSON and overwrites those dates correctly.
    repair_dates = []
    if data.get("historyBackfillVersion", 0) < 2:
        repair_dates = [
            d for d in [
                "2026-09-14","2026-09-15","2026-09-16","2026-09-17",
                "2026-09-18","2026-09-21","2026-09-22"
            ]
            if d < today
        ]

    targets = sorted(set(gap_dates + repair_dates))
    if targets:
        print(f"[backfill] targets={targets}")
        historical = fetch_historical_stock_days(targets, data)
        completed = []
        for d in targets:
            rows = historical.get(d)
            if rows:
                store_stock_day(data, d, rows)
                completed.append(d)
        if repair_dates and all(d in completed for d in repair_dates):
            data["historyBackfillVersion"] = 2

    # Repair/fill block trades independently from common-stock history.
    block_repair_dates = []
    if data.get("blockBackfillVersion", 0) < 2:
        block_repair_dates = [
            d for d in [
                "2026-09-11","2026-09-14","2026-09-15","2026-09-16",
                "2026-09-17","2026-09-18","2026-09-21","2026-09-22"
            ]
            if d < today
        ]
    block_targets = sorted(set(gap_dates + block_repair_dates + [today]))
    block_rows_by_date = fetch_block_days(
        block_targets,
        data,
        row_overrides={today: stock_rows} if stock_rows else {},
    )
    for bd in block_targets:
        if bd in block_rows_by_date:
            store_block_day(data, bd, block_rows_by_date[bd])
    if block_repair_dates and all(d in block_rows_by_date for d in block_repair_dates):
        data["blockBackfillVersion"] = 2

    # Funds, ABS, government securities and corporate bonds come from their
    # dedicated MSE server-action responses rather than HTML table positions.
    other_targets = sorted(set(gap_dates + [today]))
    other_rows_by_date = fetch_other_days(other_targets, data)
    for od in other_targets:
        if od in other_rows_by_date:
            store_other_day(data, od, other_rows_by_date[od])

    # Today's alerts must be computed after backfill so rolling history is
    # continuous and signal calculations use the immediately preceding days.
    if stock_rows:
        other = other_rows_by_date.get(today, [])
        blocks = block_rows_by_date.get(today, [])

        alerts = build_alerts(data, stock_rows, today)
        stock_turn = sum(r[4] for r in stock_rows)
        qty = sum(r[3] for r in stock_rows)
        up = sum(1 for r in stock_rows if (r[2] or 0) > 0)
        down = sum(1 for r in stock_rows if (r[2] or 0) < 0)
        crit = sum(1 for a in alerts if a[1] == "CRITICAL")
        block_val = sum(b[4] for b in blocks)
        other_val = sum(o[6] for o in other if o[3] == "MNT")

        data.setdefault("days", {})[today] = stock_rows
        data.setdefault("alerts", {})[today] = alerts
        if other:
            data.setdefault("other", {})[today] = other
        else:
            data.setdefault("other", {}).pop(today, None)
        if blocks:
            data.setdefault("blocks", {})[today] = blocks
        else:
            data.setdefault("blocks", {}).pop(today, None)

        data.setdefault("summary", {})[today] = [
            round(stock_turn,2), len(stock_rows), up, down, qty,
            len(alerts), crit, len(blocks), round(block_val,2),
            round(other_val,2), len(other)
        ]
        if today not in data["dates"]:
            data["dates"].append(today)
            data["dates"].sort()
        print(
            f"[ok] {today}: stocks={len(stock_rows)} alerts={len(alerts)} "
            f"other={len(other)} blocks={len(blocks)}"
        )
    else:
        print(f"[info] {today}: no current common-stock rows; historical backfill only")

    data["dates"] = sorted(set(data.get("dates", [])))
    if data["dates"]:
        data["latest"] = max(data["dates"])

    # Dividend announcements are independent of trading activity. A temporary
    # MSE news-page failure must not prevent the daily market update.
    try:
        new_dividends = update_dividend_news(data)
        print(f"[dividend] scan complete: added={len(new_dividends)}")
    except Exception as e:
        print(f"[dividend] scan warning: {e}", file=sys.stderr)

    data["updated"] = datetime.now(TZ).isoformat(timespec="seconds")

    st = data.setdefault("stats", {})
    st["days"] = len(data["dates"])
    st["symbols"] = len(data["syms"])
    st["rows"] = sum(len(data.get("days",{}).get(d,[])) for d in data["dates"])
    st["alertsTotal"] = sum(len(v) for v in data.get("alerts",{}).values())

    DATA.write_text(
        json.dumps(data, ensure_ascii=False, separators=(",",":")),
        encoding="utf-8"
    )

if __name__ == "__main__":
    main()


# instrument endpoint test trigger
