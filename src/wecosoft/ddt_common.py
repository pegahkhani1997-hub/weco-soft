"""Shared helpers for generating DDT (transport document) PDFs."""

from __future__ import annotations

import re

import fitz
import pandas as pd
from rapidfuzz import fuzz, process
from reportlab.pdfbase.pdfmetrics import stringWidth

from wecosoft.config import OrgConfig
from wecosoft.textutils import clean_text, norm_key


def draw_text(c, x, y, text, size=9, bold=False, align="left"):
    font = "Helvetica-Bold" if bold else "Helvetica"
    c.setFont(font, size)
    text = str(text)

    if align == "center":
        x -= stringWidth(text, font, size) / 2
    elif align == "right":
        x -= stringWidth(text, font, size)

    c.drawString(x, y, text)


def draw_wrapped_text(c, x, y, text, max_width, size=9, bold=False, leading=10):
    font = "Helvetica-Bold" if bold else "Helvetica"
    words = str(text).split()
    lines = []
    current = ""

    for w in words:
        test = (current + " " + w).strip()
        if stringWidth(test, font, size) <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = w

    if current:
        lines.append(current)

    for i, line in enumerate(lines):
        draw_text(c, x, y - i * leading, line, size=size, bold=bold)

    return len(lines)


def read_ddt_header(pdf_path: str) -> dict:
    """
    Reads the DDT PDF template's first page and extracts the document
    number/date line and the destinatario (recipient) block.
    """
    doc = fitz.open(pdf_path)
    page = doc[0]
    text = page.get_text("text")
    lines = [clean_text(x) for x in text.splitlines() if clean_text(x)]

    doc_line = None
    for line in lines:
        if line.lower().startswith("documento n."):
            doc_line = line
            break

    if not doc_line:
        raise ValueError("Non riesco a trovare la riga 'Documento n. ... del ...' nel DDT PDF.")

    m_date = re.search(r"del\s+(\d{2}/\d{2}/\d{4})", doc_line)
    data_ddt = m_date.group(1) if m_date else ""

    blocks = page.get_text("blocks")

    destinatario = None
    dest_address = ""
    dest_piva = ""

    for b in blocks:
        block_text = b[4]

        if "DESTINATARIO" in block_text:
            block_lines = [clean_text(x) for x in block_text.splitlines() if clean_text(x)]

            after = []
            seen = False

            for l in block_lines:
                if l == "DESTINATARIO":
                    seen = True
                    continue
                if seen:
                    after.append(l)

            name_lines = []

            for l in after:
                low = l.lower()

                if low.startswith(("via ", "viale ", "corso ", "piazza ")):
                    dest_address = l
                    continue

                if low.startswith("p. iva"):
                    dest_piva = l
                    continue

                if low.startswith("documento n."):
                    continue

                name_lines.append(l)

            destinatario = clean_text(" ".join(name_lines))
            break

    if not destinatario:
        destinatario = "Destinatario"

    doc.close()

    return {
        "doc_line": doc_line,
        "data_ddt": data_ddt,
        "destinatario": destinatario,
        "dest_address": dest_address,
        "dest_piva": dest_piva,
        "lines": lines,
    }


def draw_ddt_header(c, width, height, org: OrgConfig, header: dict):
    """Draws title, mittente/destinatario blocks and causale onto the canvas."""
    draw_text(c, width / 2, height - 35, "Documento di trasporto", 22, True, "center")
    draw_text(
        c,
        width / 2,
        height - 58,
        "(D.P.R. 472 del 14 agosto 1996 - D.P.R. 441 del 10 novembre 1997)",
        10,
        False,
        "center",
    )

    left_x = 25
    right_x = width - 25
    top = height - 120

    draw_text(c, left_x, top, "MITTENTE", 8)
    draw_text(c, left_x, top - 16, org.mittente_nome, 14, True)
    draw_text(c, left_x, top - 36, org.mittente_indirizzo, 10)
    draw_text(c, left_x, top - 56, org.mittente_piva, 10)

    draw_text(c, right_x, top, "DESTINATARIO", 8, False, "right")

    destinatario = header["destinatario"]

    if len(destinatario) > 34:
        draw_wrapped_text(c, right_x - 230, top - 16, destinatario, 230, size=12, bold=True, leading=13)
    else:
        draw_text(c, right_x, top - 16, destinatario, 13, True, "right")

    if header["dest_address"]:
        draw_text(c, right_x, top - 52, header["dest_address"], 10, False, "right")

    draw_text(c, right_x, top - 70, header["dest_piva"] or "P. IVA:", 10, False, "right")
    draw_text(c, right_x, height - 210, header["doc_line"], 10, False, "right")

    draw_text(c, 25, height - 255, "CAUSALE:", 11, True)
    draw_text(c, 93, height - 255, org.causale, 11)


def draw_signatures(c, width, height, org: OrgConfig):
    v_y = height - 665
    draw_text(c, 25, v_y, "VETTORE", 9, True)
    c.line(25, v_y - 8, 150, v_y - 8)

    sig_top = height - 705
    sig_h = 54
    sig_w = (width - 50) / 3

    for i, title in enumerate(["FIRMA CONDUCENTE", "FIRMA VETTORE", "FIRMA DESTINATARIO"]):
        x = 25 + i * sig_w
        c.rect(x, sig_top - sig_h, sig_w, sig_h)
        c.line(x, sig_top - 16, x + sig_w, sig_top - 16)
        draw_text(c, x + 6, sig_top - 12, title, 9, True)

    draw_text(c, width / 2, 30, org.footer_text, 9, False, "center")


def match_referenza(
    query_text: str,
    listino: pd.DataFrame,
    referenza_norm_col: str,
    manual_aliases: dict,
    soglia_match: int,
):
    """
    Matches a free-text product description to a listino row:
    1. manual alias override, 2. exact normalized match, 3. fuzzy match.

    Returns (row_or_None, score, metodo).
    """
    referenze_listino = listino[referenza_norm_col].tolist()
    query = norm_key(query_text)

    if query in manual_aliases:
        target = norm_key(manual_aliases[query])
        exact = listino[listino[referenza_norm_col] == target]

        if not exact.empty:
            return exact.iloc[0], 100, "manuale"

    exact = listino[listino[referenza_norm_col] == query]

    if not exact.empty:
        return exact.iloc[0], 100, "esatto"

    match = process.extractOne(query, referenze_listino, scorer=fuzz.WRatio)

    if not match:
        return None, 0, "non trovato"

    matched_norm, score, _ = match

    if score < soglia_match:
        return None, score, "match debole"

    return listino[listino[referenza_norm_col] == matched_norm].iloc[0], score, "fuzzy"


def fmt_num(x) -> str:
    return f"{x:.2f}".replace(".", ",")


def fmt_euro(x) -> str:
    return f"{x:.2f} €".replace(".", ",")
