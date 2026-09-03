"""Generate the written financial summary for a ticker.

Pipeline (first success wins)
-----------------------------
1. DSPy ``Predict("input_text -> analysis_text")`` with a configured OpenAI LM.
2. OpenAI Chat Completions with the same prompt.
3. A short heuristic blurb (price, P/E, beta) if neither LLM path works.

Environment
-----------
OPENAI_API_KEY   Required for DSPy and the OpenAI fallback.
OPENAI_MODEL     Optional; defaults to gpt-4o-mini.
"""

import os
import traceback
from typing import Dict
import dspy
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

INSIGHT_PROMPT_TEMPLATE = """
You are a helpful financial analyst. Given the ticker {ticker} and the following data,
produce a concise investment analysis (3–6 short paragraphs) covering:
- recent price action summary
- key fundamental metrics (PE, beta)
- risk considerations
- investment thesis and recommended time horizon

Raw data:
{raw_summary}
"""

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
USE_DSPY = False
lm = None

try:
    if OPENAI_API_KEY and OPENAI_API_KEY not in ("<YOUR_OPENAI_KEY>",):
        lm = dspy.LM(model=f"openai/{OPENAI_MODEL}", api_key=OPENAI_API_KEY)
        dspy.configure(lm=lm)
        USE_DSPY = True
except Exception:
    traceback.print_exc()
    USE_DSPY = False
    lm = None

try:
    client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
except Exception:
    traceback.print_exc()
    client = None


def dsp_financial_insight(ticker: str, stock_data: Dict) -> str:
    """Return a 3–6 paragraph analysis string for ``ticker`` given ``stock_data``.

    ``stock_data`` is the dict from ``utils.get_stock_data`` (company, sector,
    price, change_pct, pe_ratio, beta). On total failure, returns an error
    string rather than raising.
    """
    last_error = None
    try:
        raw_data = {
            "company": stock_data.get("company"),
            "sector": stock_data.get("sector"),
            "price": stock_data.get("price"),
            "change_pct": stock_data.get("change_pct"),
            "pe_ratio": stock_data.get("pe_ratio"),
            "beta": stock_data.get("beta"),
        }
        raw_summary = "\n".join([f"{k}: {v}" for k, v in raw_data.items()])
        prompt = INSIGHT_PROMPT_TEMPLATE.format(ticker=ticker, raw_summary=raw_summary)

        if USE_DSPY and lm is not None:
            try:
                predictor = dspy.Predict("input_text -> analysis_text")
                result = predictor(input_text=prompt)
                return getattr(result, "analysis_text", str(result))
            except Exception as e:
                last_error = e
                traceback.print_exc()

        if client and OPENAI_API_KEY:
            try:
                response = client.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=[
                        {"role": "system", "content": "You are a helpful financial analyst."},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.2,
                    max_tokens=700,
                )
                return response.choices[0].message.content.strip()
            except Exception as e:
                last_error = e
                traceback.print_exc()

        hint = str(last_error) if last_error else "Please verify your OPENAI_API_KEY, billing credits, and DSPy setup."
        heuristic_text = (
            f"Analysis for {ticker}:\n"
            f"Price: {stock_data.get('price')}\n"
            f"P/E Ratio: {stock_data.get('pe_ratio')}\n"
            f"Beta: {stock_data.get('beta')}\n\n"
            f"(Insight generation service unavailable. {hint})"
        )
        return heuristic_text
    except Exception as e:
        traceback.print_exc()
        return f"Failed to generate insight: {e}"
