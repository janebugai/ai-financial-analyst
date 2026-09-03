# AI Investment Brief

A Flask web app that turns a stock ticker into a short investment brief. Enter a symbol such as `AAPL`, and the app pulls live market data, asks an LLM for a written analysis, and shows price, daily change, P/E, beta, a 30-day price chart, and a financial summary. You can download that same brief as a PDF.

This is a research and demonstration tool. It is **not** financial advice.

## What it does

1. Fetches roughly 30 days of daily prices plus company fundamentals from [Yahoo Finance](https://finance.yahoo.com/) via `yfinance`.
2. Sends those metrics to an LLM (DSPy first, then the OpenAI API if DSPy fails).
3. Renders the brief on the same page: metrics on the left, Chart.js price history on the right, justified summary below.
4. Stores the latest brief in the Flask session so **Download PDF** can export it with ReportLab, including a line chart of the same price history.

If no API key is set, or the model call fails (for example missing billing credits), the app still shows market data and a short heuristic placeholder instead of a generated summary.

## Project layout

| Path | Role |
| --- | --- |
| `app.py` | Flask app: home page, analyze API, PDF download |
| `ai_module.py` | Prompt template and insight generation (DSPy → OpenAI → fallback) |
| `utils.py` | Yahoo Finance fetch for price, change, P/E, beta, sector, and history |
| `templates/base.html` | Shared layout, nav, and flash messages |
| `templates/index.html` | Brief UI: ticker form, metrics, chart, summary |
| `static/css/custom.css` | Summary justification and chart height |
| `requirements.txt` | Python dependencies |
| `.env` | Local secrets and port (not committed) |
| `.gitignore` | Ignores `.env`, caches, virtualenvs, and databases |

## Setup

Requires Python 3.11+ (3.11 is what this project has been run with).

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in values:

| Variable | Required | Purpose |
| --- | --- | --- |
| `OPENAI_API_KEY` | Yes, for AI text | OpenAI key used by DSPy and the Chat Completions fallback |
| `OPENAI_MODEL` | No | Defaults to `gpt-4o-mini` |
| `SECRET_KEY` | Yes, for sessions / PDF | Flask session signing key. Generate with `python -c "import secrets; print(secrets.token_hex(32))"` and paste the hex string — do not leave the generator command as the value |
| `PORT` | No | HTTP port; defaults to `5000` |
| `FLASK_APP` | No | Set to `app.py` if you use the Flask CLI |

Keep `.env` out of git. It is listed in `.gitignore`.

## Run

```bash
python app.py
```

Then open [http://127.0.0.1:5000](http://127.0.0.1:5000).

The built-in debugger is on, but the **reloader is off**. DSPy/LiteLLM can touch files under `site-packages` during a request; a reloader restart in the middle of Analyze shows up in the browser as a network error. After you change Python files, restart the process yourself.

## How to use it

1. Type a ticker (for example `AAPL` or `MSFT`) and click **Analyze**.
2. Wait for metrics, the 30-day chart, and the financial summary.
3. Hover the chart for a vertical crosshair and date/price label.
4. Click **Download PDF** to save the current brief. You must analyze a ticker in this browser session first.

Deep links also work: `/analyze/AAPL` or `/?ticker=AAPL` prefills the box and runs Analyze automatically.

## HTTP routes

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/`, `/analyze`, `/analyze/<ticker>` | Home page. Optional ticker prefills and auto-submits the form |
| `POST` | `/api/analyze` | JSON body `{"ticker": "AAPL"}`. Returns stock metrics, markdown HTML insight, and history for the chart. Also writes `session["latest_insight"]` for PDF export |
| `GET` | `/report/brief.pdf` | PDF of the session brief. Redirects home with a flash message if nothing has been analyzed yet |

`POST /api/analyze` JSON shape:

```json
{
  "stock": {
    "ticker": "AAPL",
    "company": "Apple Inc.",
    "price": 190.12,
    "change_pct": 0.45,
    "pe_ratio": 32.1,
    "beta": 1.2,
    "sector": "Technology"
  },
  "insight": "plain-text / markdown analysis...",
  "insight_html": "<p>...</p>",
  "history_preview": [{ "Date": "2026-08-04T00:00:00", "Close": 189.5 }]
}
```

Errors return `{ "error": "..." }` with HTTP 400 (missing ticker), 404 (no market data), or 500 (unexpected failure).

## Insight pipeline (`ai_module.py`)

The model is asked for 3–6 short paragraphs covering recent price action, P/E and beta, risks, thesis, and time horizon.

1. **DSPy** — `dspy.Predict("input_text -> analysis_text")` with a configured OpenAI LM (`openai/<OPENAI_MODEL>`).
2. **OpenAI Chat Completions** — same prompt if DSPy raises.
3. **Heuristic** — price, P/E, and beta plus a short unavailable-service note.

The OpenAI account must have billing credits. A 429 from the API usually means the key is valid but the account cannot be charged.

## PDF export (`app.py`)

The PDF is built with ReportLab from session data, not by printing the HTML page. It includes company name, ticker, UTC timestamp, price / change / P/E / beta, a 30-day line chart (`PriceHistoryChart`), and the financial summary. Chart color is green if the last close is at or above the first close in the window, otherwise red.

## Templates and front end

- **`base.html`** — page title, Bootstrap 5 CSS, custom CSS, nav (AI Investment Brief, Download PDF), flash alerts, `{% block content %}`.
- **`index.html`** — ticker form and result card. Chart.js 4 draws the line chart. A custom plugin draws a hover-only `#838383` vertical crosshair. Animation is off. Daily change is colored green / red / black. Insight HTML from the API is preferred; a client-side formatter is the fallback.

## Dependencies (`requirements.txt`)

| Package | Used for |
| --- | --- |
| Flask | Web app and sessions |
| python-dotenv | Load `.env` |
| yfinance | Market data (pulls pandas transitively) |
| dspy | Structured LLM calls |
| openai | Chat Completions fallback |
| Markdown | Convert insight text to HTML for the page |
| reportlab | PDF generation |

## Notes

- Price, change, P/E, and beta depend on Yahoo Finance. Invalid tickers or empty history can yield `N/A` or a 404.
- Session cookies are signed with `SECRET_KEY`. A placeholder or the unevaluated `python -c ...` string will still run locally but is not suitable beyond a private machine.
- Do not commit `.env`.
