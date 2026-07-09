"""
Generates a priced DDT PDF from an Ozanam-format articles Excel (a loosely
structured sheet where each row has a product name cell and a quantity cell
somewhere in it) plus the price list and a DDT template PDF for the
anagrafica (sender/recipient) details.

Pricing logic:
- "(scartati ...)" annotations are stripped and ignored
- if the product name contains "Seconda scelta" -> uses the listino's
  "basso" column (2nd-choice/lower price)
- otherwise -> uses the listino's "normale" column
"""

from __future__ import annotations

import os
import re

import pandas as pd
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from wecosoft.config import MatchingConfig, OrgConfig, OzanamConfig
from wecosoft.ddt_common import (
    draw_ddt_header,
    draw_signatures,
    draw_text,
    fmt_euro,
    fmt_num,
    match_referenza,
    read_ddt_header,
)
from wecosoft.textutils import clean_text, filename_text, norm_key


def remove_scartati(s) -> str:
    return re.sub(r"\(scartati[^)]*\)", "", str(s), flags=re.IGNORECASE).strip()


def pretty_product_name(s) -> str:
    s = remove_scartati(s)
    s = re.sub(r"(?i)(prima\s+scelta)", r" Prima scelta", s)
    s = re.sub(r"(?i)(seconda\s+scelta)", r" Seconda scelta", s)
    s = re.sub(r"(?i)(seconda\s+cella)", r" Seconda scelta", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def product_for_matching(s) -> str:
    s = pretty_product_name(s)
    s = re.sub(r"(?i)\s*prima\s+scelta", "", s)
    s = re.sub(r"(?i)\s*seconda\s+scelta", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def is_empty_cell(x) -> bool:
    if pd.isna(x):
        return True
    return clean_text(x) in ["", "-", "—", "–"]


def is_quantity_cell(x) -> bool:
    if is_empty_cell(x):
        return False
    s = str(x).lower()
    return bool(re.search(r"\d", s)) and bool(re.search(r"kg|kilo|grammi|grammo|g|mazzo|mazzi", s))


def is_probable_product_cell(x) -> bool:
    if is_empty_cell(x):
        return False
    if isinstance(x, pd.Timestamp):
        return False
    s = str(x)
    if is_quantity_cell(s):
        return False
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return False
    return bool(re.search(r"[A-Za-zÀ-ÿ]", s))


def parse_quantita(s) -> tuple[str, float | None]:
    """
    Returns (cleaned display text, numeric quantity used for value calc).
    Grams are converted to kg; kg stays kg; mazzo/mazzi are treated as
    valuable units.
    """
    s_original = remove_scartati(s)
    s = s_original.lower().replace(",", ".")

    m = re.search(r"(\d+(?:\.\d+)?)\s*(kg|kilo|chilogrammi|grammi|grammo|g|mazzo|mazzi)?", s, flags=re.IGNORECASE)

    if not m:
        return s_original, None

    value = float(m.group(1))
    unit = (m.group(2) or "kg").lower()

    if unit in ["grammi", "grammo", "g"]:
        qty_calc = value / 1000
        qty_txt = f"{value:g} grammi"
    elif unit in ["mazzo", "mazzi"]:
        qty_calc = value
        qty_txt = f"{value:g} {unit}"
    else:
        qty_calc = value
        qty_txt = f"{value:g} kg"

    return qty_txt, qty_calc


def fmt_qty(x) -> str:
    return f"{x:.3f}".rstrip("0").rstrip(".").replace(".", ",")


def load_listino(listino_xlsx: str, sheet_listino: str) -> tuple[pd.DataFrame, str, str]:
    listino = pd.read_excel(listino_xlsx, sheet_name=sheet_listino)
    listino.columns = [clean_text(c) for c in listino.columns]

    if len(listino.columns) < 4:
        raise ValueError(
            "Il listino deve avere almeno 4 colonne: referenza, prezzo normale, prezzo basso, prezzo alto."
        )

    col_referenza = listino.columns[0]
    col_normale = listino.columns[1]
    col_basso = listino.columns[2]
    col_alto = listino.columns[3]

    listino = listino.rename(columns={col_referenza: "referenza"})
    listino["referenza_norm"] = listino["referenza"].astype(str).apply(norm_key)

    for col in [col_normale, col_basso, col_alto]:
        listino[col] = pd.to_numeric(listino[col], errors="coerce")

    listino = listino.dropna(subset=[col_normale, col_basso]).copy()

    return listino, col_normale, col_basso


def extract_article_rows(articoli_xlsx: str) -> list[dict]:
    articoli_raw = pd.read_excel(articoli_xlsx, header=None, dtype=object)

    rows = []

    for _, row in articoli_raw.iterrows():
        cells = [x for x in row.tolist() if not is_empty_cell(x)]

        if not cells:
            continue

        product_cell = None
        quantity_cell = None

        for cell in cells:
            if product_cell is None and is_probable_product_cell(cell):
                product_cell = cell
            if quantity_cell is None and is_quantity_cell(cell):
                quantity_cell = cell

        if product_cell is None or quantity_cell is None:
            continue

        descrizione_originale = str(product_cell)
        descrizione_pdf = pretty_product_name(descrizione_originale)
        referenza_match = product_for_matching(descrizione_originale)

        seconda_scelta = bool(re.search(r"seconda\s+scelta", descrizione_pdf, flags=re.IGNORECASE))

        quantita_txt, quantita_calc = parse_quantita(quantity_cell)

        if quantita_calc is None:
            continue

        rows.append(
            {
                "descrizione_pdf": descrizione_pdf,
                "referenza_match": referenza_match,
                "quantita_txt": quantita_txt,
                "quantita_calc": quantita_calc,
                "seconda_scelta": seconda_scelta,
            }
        )

    if not rows:
        raise ValueError("Non ho estratto nessuna riga prodotto dall'Excel articoli.")

    return rows


def run(
    articoli_xlsx: str,
    ddt_pdf: str,
    cfg: OzanamConfig,
    matching_cfg: MatchingConfig,
    org_cfg: OrgConfig,
    listino_xlsx: str | None = None,
) -> dict:
    os.makedirs(cfg.out_dir, exist_ok=True)

    listino, col_normale, col_basso = load_listino(listino_xlsx or cfg.listino_xlsx, cfg.sheet_listino)
    rows = extract_article_rows(articoli_xlsx)

    for r in rows:
        col_prezzo = col_basso if r["seconda_scelta"] else col_normale
        r["colonna_prezzo_usata"] = col_prezzo

        matched_row, score, metodo = match_referenza(
            r["referenza_match"], listino, "referenza_norm", matching_cfg.manual_aliases, matching_cfg.soglia_match
        )

        r["match_score"] = score
        r["match_metodo"] = metodo

        if matched_row is None:
            r["referenza_listino"] = None
            r["prezzo"] = None
            r["valore"] = None
        else:
            r["referenza_listino"] = matched_row["referenza"]
            r["prezzo"] = float(matched_row[col_prezzo])
            r["valore"] = round(r["quantita_calc"] * r["prezzo"], 2)

    totale_quantita = sum(r["quantita_calc"] for r in rows if r["quantita_calc"] is not None)
    totale_valore = sum(r["valore"] for r in rows if r["valore"] is not None)

    header = read_ddt_header(ddt_pdf)

    output_name = filename_text(f"ddt - {header['destinatario']} - {header['doc_line']}.pdf")
    output_pdf = os.path.join(cfg.out_dir, output_name)

    _render_pdf(output_pdf, header, rows, totale_quantita, totale_valore, org_cfg)

    controllo = pd.DataFrame(rows)[
        [
            "descrizione_pdf",
            "referenza_listino",
            "quantita_txt",
            "quantita_calc",
            "seconda_scelta",
            "colonna_prezzo_usata",
            "prezzo",
            "valore",
            "match_score",
            "match_metodo",
        ]
    ]

    print("DDT generato:", output_pdf)
    print("\nControllo righe prezzate:")
    print(controllo.to_string(index=False))
    print(f"\nTotale quantità: {totale_quantita:.3f}")
    print(f"Totale valore: {totale_valore:.2f} €")

    return {
        "output_pdf": output_pdf,
        "totale_quantita": totale_quantita,
        "totale_valore": totale_valore,
        "n_righe_non_prezzate": int(sum(1 for r in rows if r["prezzo"] is None)),
    }


def _render_pdf(output_pdf, header, rows, totale_quantita, totale_valore, org: OrgConfig):
    c = canvas.Canvas(output_pdf, pagesize=A4)
    width, height = A4

    draw_ddt_header(c, width, height, org, header)

    x0 = 25
    y0 = height - 340

    header_h = 28
    total_h = 24

    col_w = [160, 165, 58, 65, 48, 49]
    xs = [x0]
    for w in col_w:
        xs.append(xs[-1] + w)

    max_table_bottom = height - 705 + 62
    available_h = y0 - max_table_bottom - header_h - total_h
    row_h = max(14, min(30, available_h / max(len(rows), 1)))

    font_body = 7.2 if row_h < 18 else 8.3
    font_ref = 6.7 if row_h < 18 else 7.5

    table_h = header_h + len(rows) * row_h + total_h

    c.rect(x0, y0 - table_h, xs[-1] - x0, table_h)

    for x in xs[1:-1]:
        c.line(x, y0, x, y0 - table_h)

    c.line(x0, y0 - header_h, xs[-1], y0 - header_h)

    for idx in range(len(rows)):
        y = y0 - header_h - (idx + 1) * row_h
        c.line(x0, y, xs[-1], y)

    headers = [
        ("Descrizione", 0),
        ("Riferimento", 1),
        ("Quantità", 2),
        ("Peso totale\n(kg)", 3),
        ("Prezzo", 4),
        ("Valore", 5),
    ]

    for h, idx in headers:
        parts = h.split("\n")
        draw_text(c, xs[idx] + 4, y0 - 16, parts[0], 7.5, True)
        if len(parts) > 1:
            draw_text(c, xs[idx] + 4, y0 - 26, parts[1], 7.5, True)

    for idx, r in enumerate(rows):
        top_row = y0 - header_h - idx * row_h
        y_text = top_row - (row_h * 0.65)

        draw_text(c, xs[0] + 4, y_text, r["descrizione_pdf"], font_body)
        draw_text(c, xs[1] + 4, top_row - (row_h * 0.38), "CAAT", font_ref)
        draw_text(c, xs[1] + 4, top_row - (row_h * 0.78), f"D.d.T. n° Autobolla del {header['data_ddt']}", font_ref)
        draw_text(c, xs[2] + 4, y_text, r["quantita_txt"], font_body)
        draw_text(c, xs[3] + col_w[3] - 5, y_text, fmt_qty(r["quantita_calc"]), font_body, False, "right")

        if r["prezzo"] is None:
            prezzo_txt = "n.d."
            valore_txt = "n.d."
        else:
            prezzo_txt = fmt_num(r["prezzo"])
            valore_txt = fmt_euro(r["valore"])

        draw_text(c, xs[4] + col_w[4] - 5, y_text, prezzo_txt, font_body, False, "right")
        draw_text(c, xs[5] + col_w[5] - 5, y_text, valore_txt, font_body, False, "right")

    base_y = y0 - (header_h + len(rows) * row_h)

    draw_text(c, xs[3] + col_w[3] - 5, base_y - 16, "Totale", 8.5, True, "right")
    draw_text(c, xs[4] + col_w[4] - 5, base_y - 16, fmt_qty(totale_quantita), 8.5, True, "right")
    draw_text(c, xs[5] + col_w[5] - 5, base_y - 16, fmt_euro(totale_valore), 8.5, True, "right")

    draw_signatures(c, width, height, org)

    c.save()
