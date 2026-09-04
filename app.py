"""Flask application for AI Investment Brief.

Routes
------
GET  / , /analyze , /analyze/<ticker>
    Home page. An optional ticker prefills the form and auto-submits Analyze.
POST /api/analyze
    Fetch Yahoo Finance data, generate an LLM insight, store the brief in the
    session, and return JSON for the front end (metrics, HTML summary, history).
GET  /report/brief.pdf
    Download a ReportLab PDF of the session brief (metrics, price chart, summary).

The Werkzeug reloader is disabled in ``__main__`` because DSPy/LiteLLM can
touch ``site-packages`` during a request and abort Analyze in the browser.
"""

import os
import traceback
import markdown

from io import BytesIO
from datetime import datetime, UTC
from flask import Flask, render_template, request, jsonify, send_file, session, flash, redirect, url_for
from dotenv import load_dotenv
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.colors import HexColor
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Flowable
from xml.sax.saxutils import escape
from utils import get_stock_data
from ai_module import dsp_financial_insight


load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "CHANGE_THIS_TO_A_RANDOM_VALUE")
PORT = int(os.getenv("PORT", 5000))

app = Flask(__name__)
app.config.update(SECRET_KEY=SECRET_KEY)


def _to_native(value):
    """Convert numpy/pandas scalars to plain Python values for JSON and sessions."""
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return value


def _history_points(rows):
    """Turn history dicts into ``(label, close)`` pairs for the PDF chart."""
    points = []
    for row in rows or []:
        raw_date = row.get("Date") or row.get("Datetime") or row.get("date") or ""
        close = _to_native(row.get("Close") if row.get("Close") is not None else row.get("close"))
        try:
            close = float(close)
        except (TypeError, ValueError):
            continue
        label = str(raw_date)[:10]
        try:
            label = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00")).strftime("%b %d")
        except Exception:
            pass
        points.append((label, close))
    return points


def _json_safe_history(rows):
    """Keep the last 30 closes as JSON-serializable date/close records for the session."""
    points = []
    for row in (rows or [])[-30:]:
        points.append({
            "Date": str(row.get("Date") or row.get("Datetime") or row.get("date") or ""),
            "Close": _to_native(row.get("Close") if row.get("Close") is not None else row.get("close")),
        })
    return points


class PriceHistoryChart(Flowable):
    """ReportLab flowable: 30-day close line with dollar axis labels."""

    def __init__(self, points, width=500, height=190):
        super().__init__()
        self.points = points
        self.chart_width = width
        self.chart_height = height

    def wrap(self, availWidth, availHeight):
        self.width = min(self.chart_width, availWidth)
        self.height = self.chart_height
        return self.width, self.height

    def draw(self):
        if len(self.points) < 2:
            self.canv.setFont("Helvetica", 9)
            self.canv.setFillColor(HexColor("#6c757d"))
            self.canv.drawString(0, self.height / 2, "No price history available.")
            return

        left, right, bottom, top = 42, 8, 22, 8
        plot_w = self.width - left - right
        plot_h = self.height - bottom - top
        closes = [p[1] for p in self.points]
        lo, hi = min(closes), max(closes)
        if hi == lo:
            hi = lo + 1
        pad = (hi - lo) * 0.08
        lo -= pad
        hi += pad
        n = len(self.points)
        color = HexColor("#198754") if closes[-1] >= closes[0] else HexColor("#dc3545")
        c = self.canv

        c.setStrokeColor(HexColor("#dee2e6"))
        c.setLineWidth(0.6)
        for i in range(5):
            y = bottom + plot_h * i / 4
            c.line(left, y, left + plot_w, y)
            price = lo + (hi - lo) * i / 4
            c.setFillColor(HexColor("#6c757d"))
            c.setFont("Helvetica", 7)
            c.drawRightString(left - 4, y - 2, f"${price:.2f}")

        c.setStrokeColor(HexColor("#adb5bd"))
        c.rect(left, bottom, plot_w, plot_h, stroke=1, fill=0)

        path = c.beginPath()
        for i, (_, price) in enumerate(self.points):
            x = left + plot_w * i / (n - 1)
            y = bottom + plot_h * (price - lo) / (hi - lo)
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        c.setStrokeColor(color)
        c.setLineWidth(1.8)
        c.drawPath(path, stroke=1, fill=0)

        c.setFillColor(HexColor("#6c757d"))
        c.setFont("Helvetica", 7)
        for idx in (0, n // 2, n - 1):
            x = left + plot_w * idx / (n - 1)
            c.drawCentredString(x, 6, self.points[idx][0])


@app.route("/")
@app.route("/analyze")
@app.route("/analyze/<ticker>")
def index(ticker=None):
    """Render the AI Investment Brief page; optional ticker triggers auto-analyze in JS."""
    ticker = (ticker or request.args.get("ticker") or "").strip().upper()
    return render_template("index.html", ticker=ticker)


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    """Generate a brief for ``{"ticker": "..."}`` and cache it on the session for PDF export."""
    try:
        data = request.get_json(force=True)
        ticker = data.get("ticker", "").strip().upper()

        if not ticker:
            return jsonify({"error": "Ticker required"}), 400

        stock_data = get_stock_data(ticker)
        if not stock_data:
            return jsonify({"error": f"No data found for {ticker}"}), 404

        insight = dsp_financial_insight(ticker, stock_data)
        history = stock_data.get("history_json") or []
        history_preview = history[-30:] if isinstance(history, list) else []

        session["latest_insight"] = {
            "ticker": ticker,
            "company": str(stock_data.get("company", "N/A")),
            "price": _to_native(stock_data.get("price", "N/A")),
            "change_pct": _to_native(stock_data.get("change_pct", "N/A")),
            "pe_ratio": _to_native(stock_data.get("pe_ratio", "N/A")),
            "beta": _to_native(stock_data.get("beta", "N/A")),
            "insight": insight,
            "history": _json_safe_history(history_preview),
        }
        stock_payload = {k: v for k, v in stock_data.items() if k != "history_json"}
        return jsonify({
            "stock": stock_payload,
            "insight": insight,
            "insight_html": markdown.markdown(insight or ""),
            "history_preview": history_preview,
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/report/brief.pdf", endpoint="brief_report")
@app.route("/report/portfolio.pdf", endpoint="portfolio_report")
def brief_report():
    """Send a PDF of the current session brief, or redirect home if none exists."""
    insight = session.get("latest_insight")
    if not insight:
        flash("Analyze a ticker first to download the AI Investment Brief PDF.")
        return redirect(url_for("index"))

    try:
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            leftMargin=50,
            rightMargin=50,
            topMargin=48,
            bottomMargin=48,
        )
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "BriefTitle",
            parent=styles["Heading1"],
            textColor=HexColor("#0d6efd"),
            fontSize=16,
            spaceAfter=10,
        )
        heading_style = ParagraphStyle(
            "BriefHeading",
            parent=styles["Heading2"],
            textColor=HexColor("#36454F"),
            fontSize=12,
            spaceBefore=14,
            spaceAfter=8,
        )
        meta_style = ParagraphStyle(
            "BriefMeta",
            parent=styles["Normal"],
            fontSize=10,
            leading=14,
            spaceAfter=3,
        )
        body_style = ParagraphStyle(
            "BriefBody",
            parent=styles["BodyText"],
            alignment=TA_JUSTIFY,
            fontSize=10,
            leading=15,
            spaceAfter=10,
        )

        company = escape(str(insight.get("company") or "N/A"))
        ticker = escape(str(insight.get("ticker") or ""))
        price = escape(str(insight.get("price", "N/A")))
        change = escape(str(insight.get("change_pct", "N/A")))
        pe = escape(str(insight.get("pe_ratio", "N/A")))
        beta = escape(str(insight.get("beta", "N/A")))

        story = [
            Paragraph("AI Investment Brief", heading_style),
            Paragraph(f"{company} ({ticker})", title_style),
            Paragraph(f"Generated: {datetime.now(UTC):%Y-%m-%d %H:%M:%S UTC}", meta_style),
            Spacer(1, 8),
            Paragraph(f"<b>Price:</b> ${price}", meta_style),
            Paragraph(f"<b>Change:</b> {change}%", meta_style),
            Paragraph(f"<b>P/E Ratio:</b> {pe}", meta_style),
            Paragraph(f"<b>Beta:</b> {beta}", meta_style),
            Paragraph("Price history (30 days)", heading_style),
            PriceHistoryChart(_history_points(insight.get("history"))),
            Paragraph("Financial Summary", heading_style),
        ]

        insight_text = insight.get("insight") or "No insight available."
        for para in str(insight_text).split("\n\n"):
            para = para.strip()
            if para:
                story.append(Paragraph(escape(para).replace("\n", "<br/>"), body_style))

        doc.build(story)
        buffer.seek(0)
        filename = f"{insight.get('ticker') or 'brief'}_investment_brief.pdf"
        return send_file(buffer, mimetype="application/pdf", as_attachment=True, download_name=filename)

    except Exception as e:
        traceback.print_exc()
        return f"Failed to generate report: {e}", 500


if __name__ == "__main__":
    # Reloader is off: DSPy/LiteLLM touch files under site-packages and would
    # restart the server mid-request, which shows up as a network error in the UI.
    app.run(host="0.0.0.0", port=PORT, debug=True, use_reloader=False)
