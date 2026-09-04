"""Yahoo Finance helpers for AI Investment Brief.

``get_stock_data`` returns a JSON-friendly dict of company metrics plus daily
history, or ``None`` if every data source fails.

Yahoo often rate-limits datacenter IPs (Render, etc.). History and fundamentals
are fetched separately so one failure does not discard the other, and Stooq is
used as a price fallback.
"""

import json
from io import StringIO

import pandas as pd
import yfinance as yf

try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    cffi_requests = None


def _http_session():
    if cffi_requests is not None:
        return cffi_requests.Session(impersonate="chrome")
    import requests
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
    })
    return session


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


def _history_yfinance(ticker, session):
    stock = yf.Ticker(ticker, session=session)
    hist_df = stock.history(period="30d", interval="1d")
    return stock, hist_df if hist_df is not None and not hist_df.empty else None


def _history_yahoo_chart(ticker, session):
    """Yahoo chart endpoint without yfinance's cookie/crumb flow.

    Returns ``(hist_df, meta)``. ``meta`` may include shortName / longName.
    """
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    response = session.get(url, params={"range": "1mo", "interval": "1d"}, timeout=20)
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
    """Daily closes from Stooq; more tolerant of cloud IPs than Yahoo."""
    symbol = ticker.lower()
    if "." not in symbol:
        symbol = f"{symbol}.us"
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    response = session.get(url, timeout=20)
    response.raise_for_status()
    text = response.text.strip()
    if not text or text.lower().startswith("no data") or "not found" in text.lower():
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
    """Fetch about 30 days of daily bars and fundamentals for ``ticker``.

    Returns a dict with ticker, company, sector, price, change_pct, pe_ratio,
    beta, and history_json. Returns ``None`` only when no price history is
    available from Yahoo or Stooq.
    """
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return None

    session = _http_session()
    stock = None
    hist_df = None

    try:
        stock, hist_df = _history_yfinance(ticker, session)
    except Exception as e:
        print(f"[get_stock_data] yfinance history failed for {ticker}: {e}")
        stock = None

    chart_meta = {}
    if hist_df is None:
        try:
            hist_df, chart_meta = _history_yahoo_chart(ticker, session)
        except Exception as e:
            print(f"[get_stock_data] Yahoo chart failed for {ticker}: {e}")
            chart_meta = {}

    if hist_df is None:
        try:
            hist_df = _history_stooq(ticker, session)
        except Exception as e:
            print(f"[get_stock_data] Stooq failed for {ticker}: {e}")

    if hist_df is None:
        print(f"[get_stock_data] no price history for {ticker}")
        return None

    info = _fundamentals(stock)

    def safe_get_round(key, default="N/A"):
        val = info.get(key)
        return round(val, 2) if isinstance(val, (int, float)) else default

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
