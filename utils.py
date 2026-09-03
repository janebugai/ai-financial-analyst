"""Yahoo Finance helpers for AI Investment Brief.

``get_stock_data`` is the only public function. It returns a JSON-friendly
dict of company metrics plus daily history, or ``None`` if the request fails.
"""

import json
import yfinance as yf


def get_stock_data(ticker):
    """Fetch about 30 days of daily bars and fundamentals for ``ticker``.

    Returns a dict with:

    - ``ticker``, ``company``, ``sector``
    - ``price`` — last close, rounded to 2 decimals, or ``"N/A"``
    - ``change_pct`` — percent change vs previous close, or ``"N/A"``
    - ``pe_ratio`` — trailing P/E, or ``"N/A"``
    - ``beta`` — or ``"N/A"``
    - ``history_json`` — list of records (ISO dates, Close, …) or ``None``

    History is serialized with ``DataFrame.to_json`` so timestamps and NaNs
    are JSON-safe for Flask. Returns ``None`` on an unexpected yfinance error.
    """
    try:
        stock = yf.Ticker(ticker)
        hist_df = stock.history(period="30d", interval="1d")

        price = "N/A"
        change_pct = "N/A"

        if not hist_df.empty:
            price_val = hist_df["Close"].iloc[-1]
            if len(hist_df) > 1:
                prev_close = hist_df["Close"].iloc[-2]
                if prev_close and prev_close != 0:
                    change_pct = round(((price_val - prev_close) / prev_close * 100), 2)
            price = round(price_val, 2)

        info = stock.info

        def safe_get_round(key, default="N/A"):
            val = info.get(key)
            return round(val, 2) if isinstance(val, (int, float)) else default

        return {
            "ticker": ticker.upper(),
            "company": info.get("longName", info.get("shortName", ticker.upper())),
            "price": price,
            "change_pct": change_pct,
            "pe_ratio": safe_get_round("trailingPE"),
            "beta": safe_get_round("beta"),
            "sector": info.get("sector", info.get("industry", "N/A")),
            "history_json": (
                json.loads(hist_df.reset_index().to_json(orient="records", date_format="iso"))
                if not hist_df.empty else None
            ),
        }

    except Exception as e:
        print(f"[get_stock_data] CRITICAL FAILURE for {ticker}: {e}")
        return None
