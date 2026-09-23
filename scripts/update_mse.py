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

def fetch_historical_stock_days(target_dates, data):
    """Fetch missing historical stock rows for specific dates from MSE daily report."""
    if not target_dates:
        return {}

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
            raise RuntimeError(f"MSE history page returned HTTP {resp.status}")
        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except PlaywrightTimeoutError:
            pass
        page.wait_for_timeout(5000)

        inp = page.locator('input[type="date"]')
        for d in target_dates:
            print(f"[backfill] loading {d}")
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
                # The page can occasionally finish the historical requests before
                # Playwright observes the selected response. Give hydration time
                # and validate the rendered tables below.
                pass

            page.wait_for_timeout(1800)
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

            detail = [
                t for t in tables
                if "Нээлт" in t.get("headers", [])
                and "Өмнөх өдрийн хаалт" in t.get("headers", [])
                and "Тоо ширхэг" in t.get("headers", [])
                and "Үнийн дүн" in t.get("headers", [])
            ]
            # MSE report orders common-stock categories I, II, III first.
            stock_rows = []
            for t in detail[:3]:
                stock_rows += parse_history_stock_table(t.get("rows", []), data)

            by_i = {r[0]: r for r in stock_rows}
            stock_rows = sorted(by_i.values(), key=lambda r: r[4], reverse=True)
            if stock_rows:
                result[d] = stock_rows
                print(f"[backfill] {d}: stocks={len(stock_rows)}")
            else:
                print(f"[backfill] {d}: no stock trades")

        browser.close()
    return result

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

    if gap_dates:
        print(f"[backfill] missing weekdays={gap_dates}")
        historical = fetch_historical_stock_days(gap_dates, data)
        for d in gap_dates:
            rows = historical.get(d)
            if rows:
                store_stock_day(data, d, rows)

    # Today's alerts must be computed after backfill so rolling history is
    # continuous and signal calculations use the immediately preceding days.
    if stock_rows:
        other = []
        specs = [
            (3, "Хөрөнгө оруулалтын сан", "MNT"),
            (4, "Хөрөнгөөр баталгаажсан ҮЦ", "MNT"),
            (5, "Засгийн газрын ҮЦ", "MNT"),
            (6, "Компанийн бонд", "MNT"),
            (7, "Компанийн бонд", "USD"),
        ]
        for ti, cat, ccy in specs:
            if ti < len(tables):
                other += parse_other_table(tables[ti].get("rows",[]), cat, ccy, data)

        stock_close = {data["syms"][r[0]][0]:r[1] for r in stock_rows}
        blocks = []
        if 8 < len(tables):
            blocks += parse_block_table(tables[8].get("rows",[]), data, stock_close)
        if 9 < len(tables):
            blocks += parse_block_table(tables[9].get("rows",[]), data, stock_close)

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
