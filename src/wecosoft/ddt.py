"""
Generates a priced DDT (transport document) PDF from a template DDT PDF,
looking up each product's price in the "listino da usare" price list.

Reads the product table straight out of an existing DDT PDF (the kind that
already lists descrizione / CAAT riferimento / quantità / peso for each
item but has no prices), matches each description against the price list,
and produces a new PDF with price + value columns filled in.
"""

from __future__ import annotations

import os

import pandas as pd
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from wecosoft.config import DdtConfig, MatchingConfig, OrgConfig
from wecosoft.ddt_common import (
    draw_ddt_header,
    draw_signatures,
    draw_text,
    fmt_euro,
    fmt_num,
    match_referenza,
    read_ddt_header,
)
from wecosoft.textutils import clean_text, filename_text, norm_key, parse_float_it

PREZZO_COLUMN_INDEX = {"normale": 1, "basso": 2, "alto": 3}


def load_listino(listino_xlsx: str, sheet_listino: str) -> tuple[pd.DataFrame, str, str, str]:
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

    return listino, col_normale, col_basso, col_alto


def extract_product_rows(lines: list[str]) -> list[dict]:
    start_idx = None
    for i, l in enumerate(lines):
        if l == "Descrizione":
            start_idx = i
            break

    if start_idx is None:
        raise ValueError("Non riesco a trovare la tabella prodotti nel PDF.")

    table_lines = lines[start_idx:]

    headers_to_skip = {"Descrizione", "Riferimento", "Quantità", "Peso totale", "(kg)", "Prezzo", "Valore"}

    rows = []
    i = 0

    while i < len(table_lines):
        l = table_lines[i]

        if l in headers_to_skip:
            i += 1
            continue

        if l.lower().startswith("totale"):
            break

        if i + 4 < len(table_lines) and table_lines[i + 1] == "CAAT":
            descrizione = table_lines[i]
            riferimento_1 = table_lines[i + 1]
            riferimento_2 = table_lines[i + 2]
            quantita_txt = table_lines[i + 3]
            peso_txt = table_lines[i + 4]

            kg = parse_float_it(peso_txt)
            if kg is None:
                kg = parse_float_it(quantita_txt)

            rows.append(
                {
                    "descrizione": descrizione,
                    "riferimento": f"{riferimento_1}\n{riferimento_2}",
                    "quantita_txt": quantita_txt,
                    "kg": kg,
                }
            )

            i += 5
        else:
            i += 1

    if not rows:
        raise ValueError("Non ho estratto nessuna riga prodotto dal DDT.")

    return rows


def run(
    ddt_pdf: str,
    prezzo: str,
    cfg: DdtConfig,
    matching_cfg: MatchingConfig,
    org_cfg: OrgConfig,
) -> dict:
    if prezzo not in PREZZO_COLUMN_INDEX:
        raise ValueError("prezzo deve essere uno tra: normale, basso, alto")

    os.makedirs(cfg.out_dir, exist_ok=True)

    listino, col_normale, col_basso, col_alto = load_listino(cfg.listino_xlsx, cfg.sheet_listino)
    price_cols = {"normale": col_normale, "basso": col_basso, "alto": col_alto}
    colonna_prezzo = price_cols[prezzo]

    listino[colonna_prezzo] = pd.to_numeric(listino[colonna_prezzo], errors="coerce")
    listino = listino.dropna(subset=[colonna_prezzo]).copy()

    print(f"Prezzo selezionato: {colonna_prezzo}")

    header = read_ddt_header(ddt_pdf)
    rows = extract_product_rows(header["lines"])

    for r in rows:
        matched_row, score, metodo = match_referenza(
            r["descrizione"], listino, "referenza_norm", matching_cfg.manual_aliases, matching_cfg.soglia_match
        )

        r["match_score"] = score
        r["match_metodo"] = metodo

        if matched_row is None:
            r["referenza_listino"] = None
            r["prezzo"] = None
            r["valore"] = None
        else:
            r["referenza_listino"] = matched_row["referenza"]
            r["prezzo"] = float(matched_row[colonna_prezzo])
            r["valore"] = round(r["kg"] * r["prezzo"], 2)

    totale_kg = sum(r["kg"] for r in rows if r["kg"] is not None)
    totale_valore = sum(r["valore"] for r in rows if r["valore"] is not None)

    output_name = filename_text(f"ddt - {header['destinatario']} - {header['doc_line']}.pdf")
    output_pdf = os.path.join(cfg.out_dir, output_name)

    _render_pdf(output_pdf, header, rows, totale_kg, totale_valore, org_cfg)

    audit = pd.DataFrame(rows)[
        ["descrizione", "referenza_listino", "kg", "prezzo", "valore", "match_score", "match_metodo"]
    ]

    print("DDT generato:", output_pdf)
    print("\nControllo righe prezzate:")
    print(audit.to_string(index=False))
    print(f"\nTotale kg: {totale_kg:.2f}")
    print(f"Totale valore: {totale_valore:.2f} €")

    return {
        "output_pdf": output_pdf,
        "totale_kg": totale_kg,
        "totale_valore": totale_valore,
        "n_righe_non_prezzate": int(sum(1 for r in rows if r["prezzo"] is None)),
    }


def _render_pdf(output_pdf, header, rows, totale_kg, totale_valore, org: OrgConfig):
    c = canvas.Canvas(output_pdf, pagesize=A4)
    width, height = A4

    draw_ddt_header(c, width, height, org, header)

    x0 = 25
    y0 = height - 340

    header_h = 28
    row_h = 32
    total_h = 24

    col_w = [160, 165, 58, 65, 48, 49]
    xs = [x0]
    for w in col_w:
        xs.append(xs[-1] + w)

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
        y_text = top_row - 19

        draw_text(c, xs[0] + 4, y_text, r["descrizione"], 8.5)
        draw_text(c, xs[1] + 4, top_row - 13, "CAAT", 7.5)
        draw_text(c, xs[1] + 4, top_row - 25, f"D.d.T. n° Autobolla del {header['data_ddt']}", 7.5)
        draw_text(c, xs[2] + 4, y_text, r["quantita_txt"], 8)
        draw_text(c, xs[3] + col_w[3] - 5, y_text, str(r["kg"]).replace(".", ","), 8, False, "right")

        if r["prezzo"] is None:
            prezzo_txt = "n.d."
            valore_txt = "n.d."
        else:
            prezzo_txt = fmt_num(r["prezzo"])
            valore_txt = fmt_euro(r["valore"])

        draw_text(c, xs[4] + col_w[4] - 5, y_text, prezzo_txt, 8, False, "right")
        draw_text(c, xs[5] + col_w[5] - 5, y_text, valore_txt, 8, False, "right")

    base_y = y0 - (header_h + len(rows) * row_h)

    draw_text(c, xs[3] + col_w[3] - 5, base_y - 16, "Totale", 8.5, True, "right")
    draw_text(c, xs[4] + col_w[4] - 5, base_y - 16, f"{totale_kg:.1f} kg".replace(".", ","), 8.5, True, "right")
    draw_text(c, xs[5] + col_w[5] - 5, base_y - 16, fmt_euro(totale_valore), 8.5, True, "right")

    draw_signatures(c, width, height, org)

    c.save()
