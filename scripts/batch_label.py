#!/usr/bin/env python3
"""Builds a 100x150mm roasted-batch label PDF: bean name, roast date,
finished weight, and a przelew/espresso stamp.

Used from the dashboard's "koniec palenia partii" + "wydrukuj etykietę"
controls (one label per bean per roasting day).
"""
import os
from io import BytesIO

_FONTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")
_FONT_REGULAR = "Helvetica"
_FONT_BOLD = "Helvetica-Bold"
_fonts_registered = False


def _register_fonts():
    """Register DejaVu Sans so Polish diacritics (ą ę ś ł ó ż ź ć ń) actually
    render — reportlab's built-in Helvetica (base14) has no diacritic glyphs
    and silently draws blank boxes for them instead."""
    global _FONT_REGULAR, _FONT_BOLD, _fonts_registered
    if _fonts_registered:
        return
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        regular_path = os.path.join(_FONTS_DIR, "DejaVuSans.ttf")
        bold_path = os.path.join(_FONTS_DIR, "DejaVuSans-Bold.ttf")
        if os.path.exists(regular_path) and os.path.exists(bold_path):
            pdfmetrics.registerFont(TTFont("DejaVuSans", regular_path))
            pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", bold_path))
            _FONT_REGULAR = "DejaVuSans"
            _FONT_BOLD = "DejaVuSans-Bold"
    except Exception:
        pass  # fall back to Helvetica (ASCII-only) rather than crash label printing
    _fonts_registered = True


def build_label_pdf(bean_display_name: str, batch_date: str, weight_kg, coffee_type: str) -> bytes:
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    _register_fonts()

    width, height = 100 * mm, 150 * mm
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    margin = 6 * mm
    center_x = width / 2
    cursor = height - margin

    # Header
    cursor -= 11
    c.setFont(_FONT_BOLD, 13)
    c.drawCentredString(center_x, cursor, "cukru.coffee")
    cursor -= 13
    c.setFont(_FONT_REGULAR, 9)
    c.drawCentredString(center_x, cursor, "palarnia — etykieta partii")

    cursor -= 10
    c.setLineWidth(1)
    c.line(margin, cursor, width - margin, cursor)

    # Bean name — large, auto-shrink to fit the label width
    cursor -= 26
    name = bean_display_name or "—"
    max_width = width - 2 * margin
    font_size = 24
    while c.stringWidth(name, _FONT_BOLD, font_size) > max_width and font_size > 12:
        font_size -= 1
    c.setFont(_FONT_BOLD, font_size)
    c.drawCentredString(center_x, cursor, name)

    # Roast date
    cursor -= 22
    c.setFont(_FONT_REGULAR, 13)
    c.drawCentredString(center_x, cursor, f"Data palenia: {batch_date}")

    # Finished weight
    cursor -= 22
    c.setFont(_FONT_REGULAR, 11)
    c.drawCentredString(center_x, cursor, "Ilość wypalonej kawy:")
    cursor -= 26
    c.setFont(_FONT_BOLD, 26)
    weight_text = f"{weight_kg:.3f} kg" if weight_kg is not None else "— kg"
    c.drawCentredString(center_x, cursor, weight_text)

    # Coffee-type stamp box, anchored near the bottom
    box_w, box_h = width - 2 * margin, 22 * mm
    box_y = margin + 6
    label = "ESPRESSO" if coffee_type == "espresso" else "PRZELEW / FILTER"
    c.setLineWidth(1.6)
    c.rect(margin, box_y, box_w, box_h, stroke=1, fill=0)
    c.setFont(_FONT_BOLD, 19)
    c.drawCentredString(center_x, box_y + box_h / 2 - 7, label)

    c.showPage()
    c.save()
    return buf.getvalue()
