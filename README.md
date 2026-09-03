# AI Investment Brief

Flask app that turns a ticker into a short investment brief: live Yahoo Finance metrics, a 30-day chart, an LLM summary, and a downloadable PDF.

Not financial advice.

## DSPy

[DSPy](https://dspy.ai/) is a Stanford NLP framework for programming language models with typed signatures instead of one-off prompt strings. This app uses `dspy.Predict("input_text -> analysis_text")`: the model receives the analyst prompt and returns a named `analysis_text` field. If that call fails, it falls back to OpenAI Chat Completions, then a short placeholder. Market data still loads when no key is set.

## Setup

Python 3.11+. Copy `.env.example` to `.env`, then:

```bash
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000).

| Variable | Notes |
| --- | --- |
| `OPENAI_API_KEY` | Required for generated text. The account needs billing credits (a 429 usually means the key works but cannot be charged). |
| `OPENAI_MODEL` | Optional; defaults to `gpt-4o-mini`. |
| `SECRET_KEY` | Flask sessions (needed for PDF). Paste a hex string from `python -c "import secrets; print(secrets.token_hex(32))"`, not the command itself. |
| `PORT` | Optional; defaults to `5000`. |

The debugger is on; the **reloader is off**. DSPy/LiteLLM can touch `site-packages` mid-request, which aborts Analyze in the browser — restart the process after Python changes.
