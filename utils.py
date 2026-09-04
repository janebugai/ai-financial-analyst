"""Market-data helpers for AI Investment Brief.

``get_stock_data`` returns a JSON-friendly dict of company metrics plus daily
history, or ``None`` if every data source fails.

Yahoo Finance often rate-limits or hangs from datacenter IPs (Render). Nasdaq's
public quote API is tried first. Yahoo/yfinance run only as a short-timeout
fallback so a hung request cannot block Analyze.
"""

import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import date, timedelta
from io import StringIO

import pandas as pd
import yfinance as yf

try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    cffi_requests = None


_SESSION = None


def _http_session():
    global _SESSION
    if _SESSION is not None:
        return _SESSION
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nasdaq.com/",
        "Origin": "https://www.nasdaq.com",
    }
    if cffi_requests is not None:
        session = cffi_requests.Session(impersonate="chrome")
        session.headers.update(headers)
    else:
        import requests
        session = requests.Session()
        session.headers.update(headers)
    _SESSION = session
    return _SESSION


def _run_timeout(fn, seconds):
    """Run ``fn`` in a worker thread; return None if it exceeds ``seconds``."""
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        return executor.submit(fn).result(timeout=seconds)
    except FuturesTimeout:
        print(f"[get_stock_data] timed out after {seconds}s")
        return None
    except Exception as e:
        print(f"[get_stock_data] {e}")
        return None
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _hist_to_records(hist_df):
    if hist_df is None or hist_df.empty:
        return None
    return json.loads(hist_df.reset_index().to_json(orient="records", date_format="iso"))


def _price_from_history(hist_df):
    if hist_df is None or hist_df.empty:
        return "N/A", "N/A"
    price_val = float(hist_df["Close"].iloc[-1])
    change_pct = "N/A"
    if len(hist_df) > 1:
        prev_close = float(hist_df["Close"].iloc[-2])
        if prev_close:
            change_pct = round(((price_val - prev_close) / prev_close * 100), 2)
    return round(price_val, 2), change_pct


def _parse_nasdaq_number(value):
    if value is None:
        return None
    text = str(value).replace("$", "").replace(",", "").replace("%", "").strip()
    if not text or text in {"N/A", "NA", "--"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _nasdaq_history(ticker, session, assetclass, start, end):
    hist = session.get(
        f"https://api.nasdaq.com/api/quote/{ticker}/historical",
        params={
            "assetclass": assetclass,
            "fromdate": start.isoformat(),
            "todate": end.isoformat(),
            "limit": 40,
        },
        timeout=8,
    )
    hist.raise_for_status()
    rows = (((hist.json() or {}).get("data") or {}).get("tradesTable") or {}).get("rows") or []
    points = []
    for row in rows:
        close = _parse_nasdaq_number(row.get("close"))
        if close is None:
            continue
        points.append({"Date": pd.to_datetime(row.get("date")), "Close": close})
    if not points:
        return None
    hist_df = (
        pd.DataFrame(points)
        .dropna(subset=["Date", "Close"])
        .set_index("Date")
        .sort_index()
        .tail(30)
    )
    return hist_df if not hist_df.empty else None


def _nasdaq_live_quote(ticker, session, assetclass):
    """Last sale and company name from Nasdaq's real-time quote card."""
    quote = session.get(
        f"https://api.nasdaq.com/api/quote/{ticker}/info",
        params={"assetclass": assetclass},
        timeout=6,
    )
    quote.raise_for_status()
    data = (quote.json() or {}).get("data") or {}
    primary = data.get("primaryData") or {}
    info = {}
    company = data.get("companyName")
    if company:
        info["longName"] = company.replace(" Common Stock", "").strip()
    last_sale = _parse_nasdaq_number(primary.get("lastSalePrice"))
    if last_sale is not None:
        info["lastSale"] = last_sale
    return info


def _merge_live_close(hist_df, last_sale):
    """Put today's last sale on the chart so the last point is not yesterday's close."""
    if hist_df is None or hist_df.empty or last_sale is None:
        return hist_df
    today = pd.Timestamp(date.today()).normalize()
    last_day = pd.Timestamp(hist_df.index[-1]).normalize()
    updated = hist_df.copy()
    if last_day == today:
        updated.loc[updated.index[-1], "Close"] = last_sale
    elif last_day < today:
        updated = pd.concat([updated, pd.DataFrame({"Close": [last_sale]}, index=[today])])
        updated = updated.tail(30)
    return updated


def _nasdaq_quote(ticker, session):
    """Daily history, then live last sale + company/sector."""
    end = date.today()
    start = end - timedelta(days=50)
    info = {}

    for assetclass in ("stocks", "etf"):
        try:
            hist_df = _nasdaq_history(ticker, session, assetclass, start, end)
        except Exception as e:
            print(f"[get_stock_data] Nasdaq history failed ({assetclass}): {e}")
            continue
        if hist_df is None:
            continue

        try:
            info.update(_nasdaq_live_quote(ticker, session, assetclass))
        except Exception as e:
            print(f"[get_stock_data] Nasdaq live quote failed: {e}")

        try:
            profile = session.get(
                f"https://api.nasdaq.com/api/company/{ticker}/company-profile",
                timeout=6,
            )
            if profile.status_code == 200:
                pdata = (profile.json() or {}).get("data") or {}
                name = (pdata.get("CompanyName") or {}).get("value")
                if name and not info.get("longName"):
                    info["longName"] = name
                sector = (pdata.get("Sector") or {}).get("value")
                industry = (pdata.get("Industry") or {}).get("value")
                if sector:
                    info["sector"] = sector
                if industry:
                    info["industry"] = industry
        except Exception as e:
            print(f"[get_stock_data] Nasdaq profile failed: {e}")

        return hist_df, info

    return None, info


def _history_yfinance(ticker, session):
    stock = yf.Ticker(ticker, session=session)
    hist_df = stock.history(period="30d", interval="1d")
    if hist_df is None or hist_df.empty:
        return stock, None
    return stock, hist_df


def _history_yahoo_chart(ticker, session):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    response = session.get(url, params={"range": "1mo", "interval": "1d"}, timeout=8)
    response.raise_for_status()
    result = response.json()["chart"]["result"][0]
    meta = result.get("meta") or {}
    timestamps = result.get("timestamp") or []
    closes = (result.get("indicators") or {}).get("quote", [{}])[0].get("close") or []
    rows = [
        {"Date": pd.to_datetime(ts, unit="s"), "Close": close}
        for ts, close in zip(timestamps, closes)
        if close is not None
    ]
    if not rows:
        return None, meta
    return pd.DataFrame(rows).set_index("Date").tail(30), meta


def _history_stooq(ticker, session):
    symbol = ticker.lower()
    if "." not in symbol:
        symbol = f"{symbol}.us"
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    response = session.get(url, timeout=8)
    response.raise_for_status()
    text = response.text.strip()
    if not text or "<html" in text.lower() or text.lower().startswith("no data"):
        return None
    hist_df = pd.read_csv(StringIO(text))
    if "Close" not in hist_df.columns or hist_df.empty:
        return None
    hist_df["Date"] = pd.to_datetime(hist_df["Date"])
    hist_df = hist_df.dropna(subset=["Close"]).set_index("Date").sort_index().tail(30)
    return hist_df if not hist_df.empty else None


def _fundamentals(stock):
    if stock is None:
        return {}
    try:
        info = stock.info or {}
        if info:
            return info
    except Exception as e:
        print(f"[get_stock_data] stock.info unavailable: {e}")
    try:
        fast = stock.fast_info
        return {
            "trailingPE": getattr(fast, "trailingPE", None),
            "beta": getattr(fast, "beta", None),
            "shortName": getattr(fast, "shortName", None),
        }
    except Exception as e:
        print(f"[get_stock_data] fast_info unavailable: {e}")
        return {}


def get_stock_data(ticker):
    """Fetch about 30 days of daily bars and fundamentals for ``ticker``."""
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return None

    session = _http_session()
    stock = None
    hist_df = None
    chart_meta = {}
    nasdaq_info = {}

    try:
        hist_df, nasdaq_info = _nasdaq_quote(ticker, session)
    except Exception as e:
        print(f"[get_stock_data] Nasdaq failed for {ticker}: {e}")

    if hist_df is None:
        result = _run_timeout(lambda: _history_yfinance(ticker, session), 4)
        if result:
            stock, hist_df = result

    if hist_df is None:
        chart = _run_timeout(lambda: _history_yahoo_chart(ticker, session), 4)
        if chart:
            hist_df, chart_meta = chart

    if hist_df is None:
        hist_df = _run_timeout(lambda: _history_stooq(ticker, session), 4)

    if hist_df is None:
        print(f"[get_stock_data] no price history for {ticker}")
        return None

    info = dict(nasdaq_info or {})
    if stock is not None:
        yf_info = _run_timeout(lambda: _fundamentals(stock), 6) or {}
        for key, value in yf_info.items():
            if value not in (None, "", "N/A") and key not in info:
                info[key] = value

    def safe_get_round(key, default="N/A"):
        val = info.get(key)
        return round(val, 2) if isinstance(val, (int, float)) else default

    last_sale = info.get("lastSale")
    if last_sale is None:
        last_sale = chart_meta.get("regularMarketPrice")
        if last_sale is not None:
            try:
                last_sale = float(last_sale)
            except (TypeError, ValueError):
                last_sale = None

    if last_sale is not None:
        last_day = pd.Timestamp(hist_df.index[-1]).normalize()
        today = pd.Timestamp(date.today()).normalize()
        if last_day == today and len(hist_df) > 1:
            prev_close = float(hist_df["Close"].iloc[-2])
        else:
            prev_close = float(hist_df["Close"].iloc[-1])
        hist_df = _merge_live_close(hist_df, last_sale)
        price = round(float(last_sale), 2)
        change_pct = (
            round(((float(last_sale) - prev_close) / prev_close * 100), 2)
            if prev_close else "N/A"
        )
    else:
        price, change_pct = _price_from_history(hist_df)
    company = (
        info.get("longName")
        or info.get("shortName")
        or chart_meta.get("longName")
        or chart_meta.get("shortName")
        or ticker
    )
    return {
        "ticker": ticker,
        "company": company,
        "price": price,
        "change_pct": change_pct,
        "pe_ratio": safe_get_round("trailingPE"),
        "beta": safe_get_round("beta"),
        "sector": info.get("sector") or info.get("industry") or "N/A",
        "history_json": _hist_to_records(hist_df),
    }
