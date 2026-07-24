#!/usr/bin/env python3
"""
fetch_market_data.py — end-of-day market data updater for davidjia.ca
Runs on GitHub Actions after US market close. Uses only Python stdlib
(no pip installs). API credentials are supplied through GitHub Actions secrets:

  SPY daily history .... Tiingo EOD   https://api.tiingo.com/tiingo/daily/SPY/prices
  S&P 500 fallback ..... FRED SP500   https://api.stlouisfed.org/fred/series/observations
  VIX / 9D / 3M / 6M ... CBOE CDN     https://cdn.cboe.com/api/global/us_indices/daily_prices/
  VIX fallback ......... FRED VIXCLS
  3-mo T-bill (r) ...... FRED DTB3
  10-yr Treasury ....... FRED DGS10

Writes: site/data/market.json
Exit code is non-zero if the core price series cannot be fetched or is stale.
"""

import csv
import io
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

OUT = Path(__file__).parent / "site" / "data" / "market.json"
UA = {"User-Agent": "Mozilla/5.0 (davidjia.ca EOD data bot; contact: site owner)"}
HISTORY_DAYS = 260  # ~1 trading year kept for the chart
MARKET_TZ = ZoneInfo("America/New_York")


def get(url, timeout=30, headers=None):
    request_headers = dict(UA)
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(url, headers=request_headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def get_json(url, timeout=30, headers=None):
    return json.loads(get(url, timeout=timeout, headers=headers))


def read_csv(text):
    return list(csv.reader(io.StringIO(text)))


def expected_market_date(now=None):
    """Return the latest expected weekday in the US market timezone."""
    market_now = now.astimezone(MARKET_TZ) if now else datetime.now(MARKET_TZ)
    day = market_now.date()
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def existing_market_date():
    """Return the currently committed market date, if it can be read."""
    try:
        payload = json.loads(OUT.read_text())
        return date.fromisoformat(payload["asOf"])
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def describe_request_error(exc):
    """Describe a provider error without exposing a credential-bearing URL."""
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, (RuntimeError, ValueError)):
        return str(exc)
    return type(exc).__name__


def fred_observations(series, *, observation_start=None, limit=100000, sort_order="asc"):
    """Return non-missing (date, value) observations from the official FRED API."""
    api_key = os.environ.get("FRED_API_KEY", "").strip()
    if not api_key:
        rows = read_csv(
            get(
                "https://fred.stlouisfed.org/graph/fredgraph.csv?"
                + urllib.parse.urlencode({"id": series})
            )
        )
        observations = [
            (row[0], float(row[1]))
            for row in rows[1:]
            if len(row) >= 2
            and row[1] not in ("", ".")
            and (
                observation_start is None
                or date.fromisoformat(row[0]) >= observation_start
            )
        ]
        if sort_order == "desc":
            observations.reverse()
        print(f"  FRED {series} via no-key CSV fallback")
        return observations[:limit]

    params = {
        "series_id": series,
        "api_key": api_key,
        "file_type": "json",
        "limit": str(limit),
        "sort_order": sort_order,
    }
    if observation_start is not None:
        params["observation_start"] = observation_start.isoformat()

    url = (
        "https://api.stlouisfed.org/fred/series/observations?"
        + urllib.parse.urlencode(params)
    )
    try:
        payload = get_json(url)
    except Exception as exc:
        raise RuntimeError(
            f"FRED {series} request failed ({describe_request_error(exc)})"
        ) from None

    observations = payload.get("observations")
    if not isinstance(observations, list):
        raise ValueError(f"FRED {series} returned an invalid response")

    return [
        (row["date"], float(row["value"]))
        for row in observations
        if row.get("date") and row.get("value") not in (None, "", ".")
    ]


# ---------------------------------------------------------------- SPY
def fetch_spy_tiingo(target_date):
    """Return Tiingo SPY closes through target_date."""
    api_key = os.environ.get("TIINGO_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("TIINGO_API_KEY is not set")

    params = {
        "startDate": (target_date - timedelta(days=HISTORY_DAYS * 2)).isoformat(),
        "endDate": target_date.isoformat(),
        "format": "json",
        "resampleFreq": "daily",
    }
    url = (
        "https://api.tiingo.com/tiingo/daily/SPY/prices?"
        + urllib.parse.urlencode(params)
    )
    payload = get_json(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Token {api_key}",
        },
    )
    if not isinstance(payload, list):
        raise ValueError("Tiingo returned an invalid response")

    data = sorted(
        [
            (str(row.get("date", ""))[:10], float(row["close"]))
            for row in payload
            if row.get("date") and row.get("close") is not None
        ],
        key=lambda item: item[0],
    )[-HISTORY_DAYS:]
    if len(data) < 30:
        raise ValueError("Tiingo returned too few SPY rows")
    return data


def fetch_spy(target_date):
    """Return (price, [[date, close], ...]) — Tiingo first, FRED as sole fallback."""
    try:
        data = fetch_spy_tiingo(target_date)
        print(f"  SPY via Tiingo: {len(data)} rows, last {data[-1]}")
        return data[-1][1], [[d, c] for d, c in data], "tiingo"
    except Exception as exc:
        print(
            f"  Tiingo failed ({describe_request_error(exc)}); "
            "falling back to FRED SP500 / 10"
        )

    data = [
        (d, round(value / 10.0, 2))
        for d, value in fred_observations(
            "SP500",
            observation_start=target_date - timedelta(days=HISTORY_DAYS * 2),
        )
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
            vals = fred_observations("VIXCLS", limit=10, sort_order="desc")
            out["spot"] = round(vals[0][1], 2)
            print(f"  VIX via FRED VIXCLS: {out['spot']}")
        except Exception as e:
            print(f"  FRED VIXCLS fallback failed ({e})")
    return out, date


# ---------------------------------------------------------------- rates
def fred_latest(series):
    vals = fred_observations(series, limit=10, sort_order="desc")
    if not vals:
        raise ValueError(f"FRED {series} returned no observations")
    return round(vals[0][1], 3)


def main():
    print("Fetching end-of-day market data…")
    target_date = expected_market_date()
    current_date = existing_market_date()

    if current_date is not None and current_date >= target_date:
        print(f"Market data is already current through {current_date}; nothing to update")
        return

    price, history, spy_source = fetch_spy(target_date)  # raises on total failure
    fetched_date = date.fromisoformat(history[-1][0])
    if fetched_date < target_date:
        raise RuntimeError(
            f"Price data is stale: source returned {fetched_date}, expected {target_date}. "
            "A later scheduled retry will try again."
        )

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
