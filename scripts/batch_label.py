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


def _wrap_to_width(c, text, font_name, font_size, max_width):
    """Greedy word-wrap: splits text into lines that each fit max_width at
    the given font/size. Used for the country/region/processing detail line,
    which can get long (e.g. 'Kolumbia · Huila · Umyta · Caturra')."""
    words = text.split()
    lines = []
    current = ""
    for word in words:
        candidate = (current + " " + word).strip()
        if c.stringWidth(candidate, font_name, font_size) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def build_label_pdf(bean_display_name: str, batch_date: str, weight_kg, coffee_type: str,
                     country: str = None, region: str = None, processing: str = None,
                     variety: str = None) -> bytes:
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    _register_fonts()

    width, height = 100 * mm, 150 * mm
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    margin = 6 * mm
    center_x = width / 2
    max_width = width - 2 * margin
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
    cursor -= 23
    name = bean_display_name or "—"
    font_size = 20
    while c.stringWidth(name, _FONT_BOLD, font_size) > max_width and font_size > 11:
        font_size -= 1
    c.setFont(_FONT_BOLD, font_size)
    c.drawCentredString(center_x, cursor, name)

    # Origin details — kraj pochodzenia / region / rodzaj obróbki (+ odmiana,
    # when present). Wraps onto a second line if too long to fit on one.
    details_parts = [p for p in (country, region, processing, variety) if p and str(p).strip()]
    if details_parts:
        details_text = " · ".join(str(p).strip() for p in details_parts)
        detail_font_size = 11
        detail_lines = _wrap_to_width(c, details_text, _FONT_REGULAR, detail_font_size, max_width)
        if len(detail_lines) > 2:
            # Still too much to fit nicely in two lines — shrink the font instead
            # of spilling into the rest of the label.
            detail_font_size = 9
            detail_lines = _wrap_to_width(c, details_text, _FONT_REGULAR, detail_font_size, max_width)
        cursor -= 16
        c.setFont(_FONT_REGULAR, detail_font_size)
        for line in detail_lines[:2]:
            c.drawCentredString(center_x, cursor, line)
            cursor -= (detail_font_size + 3)
        cursor += 3  # tighten gap before the date line

    # Roast date
    cursor -= 16
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
