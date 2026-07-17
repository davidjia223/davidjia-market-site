#!/usr/bin/env python3
"""
fetch_market_data.py — end-of-day market data updater for davidjia.ca
Runs on GitHub Actions after US market close. Uses only Python stdlib
(no pip installs) and only free, no-API-key public data sources:

  SPY daily history .... Stooq        https://stooq.com/q/d/l/?s=spy.us&i=d
  S&P 500 fallback ..... FRED SP500   https://fred.stlouisfed.org/graph/fredgraph.csv?id=SP500
  VIX / 9D / 3M / 6M ... CBOE CDN     https://cdn.cboe.com/api/global/us_indices/daily_prices/
  VIX fallback ......... FRED VIXCLS
  3-mo T-bill (r) ...... FRED DTB3
  10-yr Treasury ....... FRED DGS10

Writes: site/data/market.json
Exit code is non-zero only if the core price series cannot be fetched.
"""

import csv
import io
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).parent / "site" / "data" / "market.json"
UA = {"User-Agent": "Mozilla/5.0 (davidjia.ca EOD data bot; contact: site owner)"}
HISTORY_DAYS = 260  # ~1 trading year kept for the chart


def get(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def read_csv(text):
    return list(csv.reader(io.StringIO(text)))


# ---------------------------------------------------------------- SPY
def fetch_spy():
    """Return (price, [[date, close], ...]) — Stooq first, FRED SP500/10 fallback."""
    try:
        rows = read_csv(get("https://stooq.com/q/d/l/?s=spy.us&i=d"))
        data = [(r[0], float(r[4])) for r in rows[1:] if len(r) >= 5 and r[4]]
        if len(data) < 30:
            raise ValueError("Stooq returned too few rows")
        data = data[-HISTORY_DAYS:]
        print(f"  SPY via Stooq: {len(data)} rows, last {data[-1]}")
        return data[-1][1], [[d, c] for d, c in data], "stooq"
    except Exception as e:
        print(f"  Stooq failed ({e}); falling back to FRED SP500 / 10")

    rows = read_csv(get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=SP500"))
    data = [
        (r[0], round(float(r[1]) / 10.0, 2))
        for r in rows[1:]
        if len(r) >= 2 and r[1] not in (".", "")
    ][-HISTORY_DAYS:]
    if len(data) < 30:
        raise ValueError("FRED SP500 fallback also failed")
    print(f"  SPY approximated from FRED SP500/10: last {data[-1]}")
    return data[-1][1], [[d, c] for d, c in data], "fred_sp500_div10"


# ---------------------------------------------------------------- VIX family
def cboe_last_close(symbol):
    url = f"https://cdn.cboe.com/api/global/us_indices/daily_prices/{symbol}_History.csv"
    rows = read_csv(get(url))
    header = [h.strip().upper() for h in rows[0]]
    ci = header.index("CLOSE") if "CLOSE" in header else len(rows[0]) - 1
    di = header.index("DATE") if "DATE" in header else 0
    last = [r for r in rows[1:] if len(r) > ci and r[ci]][-1]
    return float(last[ci]), last[di]


def fetch_vix_family():
    out, date = {}, None
    for key, sym in [("spot", "VIX"), ("v9d", "VIX9D"), ("v3m", "VIX3M"), ("v6m", "VIX6M")]:
        try:
            val, d = cboe_last_close(sym)
            out[key] = round(val, 2)
            date = date or d
            print(f"  {sym}: {val} ({d})")
        except Exception as e:
            out[key] = None
            print(f"  {sym} unavailable ({e})")
    if out["spot"] is None:  # FRED fallback for headline VIX
        try:
            rows = read_csv(get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS"))
            vals = [r for r in rows[1:] if len(r) >= 2 and r[1] not in (".", "")]
            out["spot"] = round(float(vals[-1][1]), 2)
            print(f"  VIX via FRED VIXCLS: {out['spot']}")
        except Exception as e:
            print(f"  FRED VIXCLS fallback failed ({e})")
    return out, date


# ---------------------------------------------------------------- rates
def fred_latest(series):
    rows = read_csv(get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"))
    vals = [r for r in rows[1:] if len(r) >= 2 and r[1] not in (".", "")]
    return round(float(vals[-1][1]), 3)


def main():
    print("Fetching end-of-day market data…")
    price, history, spy_source = fetch_spy()  # raises on total failure — that's intended
    vix, vix_date = fetch_vix_family()

    rates = {}
    for key, series in [("tbill3m", "DTB3"), ("t10y", "DGS10")]:
        try:
            rates[key] = fred_latest(series)
            print(f"  {series}: {rates[key]}%")
        except Exception as e:
            rates[key] = None
            print(f"  {series} unavailable ({e})")

    payload = {
        "generatedUtc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "asOf": history[-1][0],
        "spy": {"price": price, "source": spy_source, "history": history},
        "vix": vix,
        "rates": rates,
        "divYield": 1.0,  # SPY trailing yield; update manually if it drifts
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, separators=(",", ":")) + "\n")
    print(f"Wrote {OUT} — SPY {price}, VIX {vix.get('spot')}, as of {payload['asOf']}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        sys.exit(1)
