"""
Builds the report for the "Activate Scraper" button: a simple table of
every product reference and its most recent CAAT listino price, as both a
PDF and a CSV. No taxonomy needed — this runs straight off the raw
scraper output.
"""

from __future__ import annotations

import pandas as pd

from wecosoft.report_pdf import cell, now_str, render_table_pdf


def build_latest_report(scraper_output_xlsx: str, output_pdf: str, output_csv: str | None = None) -> dict:
    matrix = pd.read_excel(scraper_output_xlsx, sheet_name="PREV_matrix")

    date_cols = sorted([c for c in matrix.columns if c != "REFERENZA_COMPLETA"])

    if not date_cols:
        raise ValueError("Nessuna data listino trovata nel file scraper.")

    latest_date = date_cols[-1]

    latest = matrix[["REFERENZA_COMPLETA", latest_date]].copy()
    latest = latest.rename(columns={"REFERENZA_COMPLETA": "referenza", latest_date: "prezzo"})
    latest = latest.dropna(subset=["prezzo"])
    latest = latest.sort_values("referenza").reset_index(drop=True)

    rows = [[cell(r["referenza"]), f"{r['prezzo']:.2f}"] for _, r in latest.iterrows()]

    render_table_pdf(
        output_path=output_pdf,
        title="CAAT — Ultimo listino disponibile",
        subtitle_lines=[
            f"Data listino: {latest_date}",
            f"Referenze con prezzo: {len(latest)}",
            f"Report generato il {now_str()}",
        ],
        headers=["Referenza", "Prezzo (€/kg)"],
        rows=rows,
        col_widths=[420, 90],
    )

    if output_csv is not None:
        latest.to_csv(output_csv, index=False)

    return {
        "output_pdf": output_pdf,
        "output_csv": output_csv,
        "data_listino": latest_date,
        "n_referenze": int(len(latest)),
    }
