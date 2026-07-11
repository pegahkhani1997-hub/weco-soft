"""
Builds the customizable PDF report behind the "Filter / Tweak Output"
settings window: taxonomy-matched prices over a chosen working-day window,
using a chosen price statistic, plus optional user-defined discount
columns.

Time frame: "1 week" = the previous 7 working days (Mon-Fri), "2 weeks" =
14, "3 weeks" = 21, "1 month" = 28 — counted backward from yesterday,
excluding today. (Not calendar weeks/months: each "week" unit here is a
fixed count of working days, per spec.)

Price statistic: minimo / massimo / medio / moda, computed per product
across every matched CAAT listino value inside the window. For "moda"
(most frequent value), prices are rounded to the cent first; if every
value in the window is unique (no repeats), the median is used as a
documented fallback since a strict mode is undefined in that case.

Base taxonomy records (empty qualita, e.g. "arancia" with no variety)
pool together their own direct matches plus every sibling variant's
values before the statistic is applied — same fallback behaviour as the
`price` pipeline stage, generalized to any statistic instead of always
"minimum".
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from statistics import median

import pandas as pd

from wecosoft import pricing
from wecosoft.report_pdf import cell, now_str, render_table_pdf

TIME_FRAME_DAYS = {
    "1 settimana": 7,
    "2 settimane": 14,
    "3 settimane": 21,
    "1 mese": 28,
}

STAT_KEYS = ["minimo", "massimo", "medio", "moda"]


def business_days_window(n: int, today: date | None = None) -> list[date]:
    """
    The previous n working days (Mon-Fri), counting backward from
    yesterday and excluding today. Returned oldest-first.
    """
    today = today or date.today()
    days: list[date] = []
    d = today - timedelta(days=1)

    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)

    return sorted(days)


def select_window_columns(date_cols: list[str], window_dates: list[date]) -> list[str]:
    window_set = set(window_dates)
    result = []

    for c in date_cols:
        try:
            d = datetime.strptime(c, "%Y-%m-%d").date()
        except ValueError:
            continue

        if d in window_set:
            result.append(c)

    return sorted(result)


def compute_stat(values: list[float], stat_key: str) -> float | None:
    vals = [v for v in values if v is not None and pd.notna(v)]

    if not vals:
        return None

    if stat_key == "minimo":
        return round(min(vals), 2)

    if stat_key == "massimo":
        return round(max(vals), 2)

    if stat_key == "medio":
        return round(sum(vals) / len(vals), 2)

    if stat_key == "moda":
        rounded = [round(v, 2) for v in vals]
        counts = Counter(rounded)
        max_count = max(counts.values())

        if max_count == 1:
            # No value repeats: a strict mode is undefined, fall back to
            # the median as the most representative single value.
            return round(median(vals), 2)

        candidates = sorted(v for v, c in counts.items() if c == max_count)
        return candidates[0]

    raise ValueError(f"Statistica sconosciuta: {stat_key}")


def aggregate_prices(matched_matrix: pd.DataFrame, taxonomy: pd.DataFrame, window_cols: list[str], stat_key: str) -> pd.DataFrame:
    long = matched_matrix[matched_matrix["match_status"] == "OK"].melt(
        id_vars=["TAX_ID"], value_vars=window_cols, var_name="DATA", value_name="PREV"
    )
    long = long.dropna(subset=["PREV"])

    direct_values: dict[int, list[float]] = {}
    for tax_id, group in long.groupby("TAX_ID"):
        direct_values[int(tax_id)] = group["PREV"].tolist()

    group_key_by_tax_id = {
        int(row["TAX_ID"]): (row["macro_categoria"], row["raggruppamento"], row["prodotto"])
        for _, row in taxonomy.iterrows()
    }

    family_members: dict[tuple, list[int]] = {}
    for tax_id, key in group_key_by_tax_id.items():
        family_members.setdefault(key, []).append(tax_id)

    is_base = {
        int(row["TAX_ID"]): str(row["qualita"]).strip() == "" for _, row in taxonomy.iterrows()
    }

    out_rows = []

    for tax_id, key in group_key_by_tax_id.items():
        if is_base.get(tax_id, False):
            pool = []
            for sibling_id in family_members.get(key, []):
                pool.extend(direct_values.get(sibling_id, []))
        else:
            pool = direct_values.get(tax_id, [])

        prezzo = compute_stat(pool, stat_key)

        if prezzo is None:
            continue

        out_rows.append({"TAX_ID": tax_id, "PREZZO": prezzo, "N_VALORI_USATI": len(pool)})

    return pd.DataFrame(out_rows, columns=["TAX_ID", "PREZZO", "N_VALORI_USATI"])


def build_tweak_report(
    scraper_output_xlsx: str,
    taxonomy_csv: str,
    time_frame_key: str,
    stat_key: str,
    discount_pcts: list[float],
    output_pdf: str,
) -> dict:
    if time_frame_key not in TIME_FRAME_DAYS:
        raise ValueError(f"Periodo sconosciuto: {time_frame_key}")

    if stat_key not in STAT_KEYS:
        raise ValueError(f"Statistica sconosciuta: {stat_key}")

    matrix = pd.read_excel(scraper_output_xlsx, sheet_name="PREV_matrix")

    if "REFERENZA_COMPLETA" not in matrix.columns:
        raise ValueError("Nel foglio PREV_matrix manca la colonna 'REFERENZA_COMPLETA'.")

    taxonomy = pricing.load_taxonomy(taxonomy_csv)

    match_rows = []
    for ref in matrix["REFERENZA_COMPLETA"].astype(str):
        m = pricing.match_reference(ref, taxonomy)
        m["REFERENZA_COMPLETA"] = ref
        match_rows.append(m)

    match_df = pd.DataFrame(match_rows)

    date_cols = [c for c in matrix.columns if c != "REFERENZA_COMPLETA"]
    for c in date_cols:
        matrix[c] = pd.to_numeric(matrix[c], errors="coerce")

    tax_info_cols = [
        "TAX_ID",
        "macro_categoria",
        "raggruppamento",
        "prodotto",
        "qualita",
        "certificazione",
        "alias",
        "Nome referenza ECO",
    ]
    tax_info = taxonomy[tax_info_cols].copy()

    matched_matrix = matrix.merge(match_df, on="REFERENZA_COMPLETA", how="left").merge(
        tax_info, on="TAX_ID", how="left"
    )

    n_days = TIME_FRAME_DAYS[time_frame_key]
    window_dates = business_days_window(n_days)
    window_cols = select_window_columns(date_cols, window_dates)

    if not window_cols:
        raise ValueError(
            "Nessun listino disponibile nella finestra temporale selezionata "
            f"({window_dates[0]:%d/%m/%Y} - {window_dates[-1]:%d/%m/%Y}). "
            "Esegui prima lo scraper o scegli un periodo più ampio."
        )

    agg = aggregate_prices(matched_matrix, taxonomy, window_cols, stat_key)

    report = taxonomy[tax_info_cols + ["macro_sort"]].merge(agg, on="TAX_ID", how="left")
    report = report[report["PREZZO"].notna()].copy()
    report = report.sort_values(
        ["macro_sort", "raggruppamento", "prodotto", "qualita"], kind="stable"
    ).reset_index(drop=True)

    price_col = f"prezzo ({stat_key})"
    report = report.rename(columns={"PREZZO": price_col})

    discount_cols = []
    for pct in discount_pcts:
        col_name = f"prezzo {pct:+g}%"
        report[col_name] = (report[price_col] * (1 + pct / 100)).round(2)
        discount_cols.append(col_name)

    headers = ["Referenza", price_col] + discount_cols
    rows = []

    for _, r in report.iterrows():
        referenza_label = f"{r['Nome referenza ECO']} ({r['macro_categoria']})"
        row = [cell(referenza_label), f"{r[price_col]:.2f}"]
        for c in discount_cols:
            row.append(f"{r[c]:.2f}")
        rows.append(row)

    subtitle_lines = [
        f"Periodo: {time_frame_key} ({n_days} giorni lavorativi) "
        f"— dal {window_dates[0]:%d/%m/%Y} al {window_dates[-1]:%d/%m/%Y}",
        f"Statistica prezzo: {stat_key}",
        f"Referenze incluse: {len(report)}",
        f"Report generato il {now_str()}",
    ]

    col_widths = [300, 80] + [80] * len(discount_cols)

    render_table_pdf(
        output_path=output_pdf,
        title="Listino personalizzato",
        subtitle_lines=subtitle_lines,
        headers=headers,
        rows=rows,
        col_widths=col_widths,
        landscape_page=len(discount_cols) >= 2,
    )

    return {
        "output_pdf": output_pdf,
        "n_referenze": int(len(report)),
        "finestra_da": window_dates[0].isoformat(),
        "finestra_a": window_dates[-1].isoformat(),
        "colonne_sconto": discount_cols,
    }
