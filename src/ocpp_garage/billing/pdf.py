"""
PDF statement generator. Produces a simple, clean one-page bill per household.
Uses fpdf2 (pip install fpdf2) — no external dependencies beyond the standard library.
"""

from datetime import timezone
from zoneinfo import ZoneInfo

from fpdf import FPDF

from .models import Bill

_LONDON = ZoneInfo("Europe/London")

# Layout constants
_MARGIN = 15
_COL_DATE = 32
_COL_TIME = 20
_COL_KWH = 28
_COL_RATE = 28
_COL_COST = 28
_ROW_H = 7


class StatementPDF(FPDF):
    def __init__(self, bill: Bill) -> None:
        super().__init__()
        self._bill = bill

    def header(self) -> None:
        bill = self._bill
        self.set_font("Helvetica", "B", 14)
        self.cell(0, 8, bill.landlord_name, ln=True)
        self.set_font("Helvetica", "", 9)
        self.cell(0, 5, bill.landlord_address, ln=True)
        self.ln(4)
        self.set_draw_color(180, 180, 180)
        self.line(_MARGIN, self.get_y(), self.w - _MARGIN, self.get_y())
        self.ln(4)

    def footer(self) -> None:
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(
            0, 10,
            f"Page {self.page_no()} | "
            "Electricity charges are passed through at cost (VAT-inclusive). "
            "Queries: contact your property manager.",
            align="C",
        )
        self.set_text_color(0, 0, 0)


def generate_pdf(bill: Bill) -> bytes:
    """Return the PDF as raw bytes. Write to a file or serve via HTTP."""
    pdf = StatementPDF(bill)
    pdf.set_margins(_MARGIN, 15, _MARGIN)
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    # ── Statement heading ──────────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 12, "EV Charging Statement", ln=True)

    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Billing period: {bill.period_label}", ln=True)
    pdf.cell(0, 6, f"Issued: {_today_label()}", ln=True)
    pdf.ln(4)

    # ── Tenant block ───────────────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Billed to", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, bill.household_name, ln=True)
    for line in bill.household_address.splitlines():
        pdf.cell(0, 6, line.strip(), ln=True)
    pdf.cell(0, 6, f"Charger: {bill.charger_id}", ln=True)
    pdf.ln(6)

    # ── Summary box ────────────────────────────────────────────────────────
    pdf.set_fill_color(245, 245, 245)
    pdf.set_font("Helvetica", "B", 11)
    box_y = pdf.get_y()
    pdf.rect(_MARGIN, box_y, pdf.w - 2 * _MARGIN, 22, style="F")
    pdf.set_xy(_MARGIN + 4, box_y + 4)
    pdf.cell(60, 7, "Total energy consumed:")
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 7, f"{bill.total_kwh:.3f} kWh", ln=True)
    pdf.set_x(_MARGIN + 4)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(60, 7, "Total amount due:")
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(0, 80, 0)
    pdf.cell(0, 7, f"£{bill.total_cost_pounds:.2f}", ln=True)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(8)

    # ── Usage table ────────────────────────────────────────────────────────
    if not bill.lines:
        pdf.set_font("Helvetica", "I", 10)
        pdf.cell(0, 8, "No charging activity recorded this period.", ln=True)
    else:
        _draw_table(pdf, bill)

    return bytes(pdf.output())


def _draw_table(pdf: StatementPDF, bill: Bill) -> None:
    # Header row
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(50, 50, 50)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(_COL_DATE, _ROW_H, "Date", border=0, fill=True, align="C")
    pdf.cell(_COL_TIME, _ROW_H, "Hour (local)", border=0, fill=True, align="C")
    pdf.cell(_COL_KWH,  _ROW_H, "Energy (kWh)", border=0, fill=True, align="C")
    pdf.cell(_COL_RATE, _ROW_H, "Rate (p/kWh)", border=0, fill=True, align="C")
    pdf.cell(_COL_COST, _ROW_H, "Cost (£)", border=0, fill=True, align="C")
    pdf.ln()
    pdf.set_text_color(0, 0, 0)

    # Data rows — group by date for readability
    pdf.set_font("Helvetica", "", 9)
    last_date = ""
    fill = False
    for i, line in enumerate(bill.lines):
        local_dt = line.interval_start_utc.astimezone(_LONDON)
        date_str = local_dt.strftime("%d %b %Y")
        time_str = f"{local_dt.strftime('%H:%M')}-{(local_dt.hour + 1) % 24:02d}:00"

        # Alternate row shading; extra shading on date change
        if date_str != last_date:
            pdf.set_fill_color(230, 240, 255)
            last_date = date_str
            show_date = date_str
        else:
            pdf.set_fill_color(248, 248, 248) if fill else pdf.set_fill_color(255, 255, 255)
            show_date = ""
        fill = not fill

        pdf.cell(_COL_DATE, _ROW_H, show_date, fill=True, align="L")
        pdf.cell(_COL_TIME, _ROW_H, time_str, fill=True, align="C")
        pdf.cell(_COL_KWH,  _ROW_H, f"{line.kwh:.4f}", fill=True, align="R")
        pdf.cell(_COL_RATE, _ROW_H, f"{line.pence_per_kwh:.2f}p", fill=True, align="R")
        pdf.cell(_COL_COST, _ROW_H, f"£{line.cost_pounds:.4f}", fill=True, align="R")
        pdf.ln()

    # Totals row
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(220, 220, 220)
    total_w = _COL_DATE + _COL_TIME
    pdf.cell(total_w, _ROW_H, "TOTAL", fill=True, align="R")
    pdf.cell(_COL_KWH, _ROW_H, f"{bill.total_kwh:.4f}", fill=True, align="R")
    pdf.cell(_COL_RATE, _ROW_H, "", fill=True)
    pdf.cell(_COL_COST, _ROW_H, f"£{bill.total_cost_pounds:.4f}", fill=True, align="R")
    pdf.ln()


def _today_label() -> str:
    from datetime import date
    return date.today().strftime("%d %B %Y")
