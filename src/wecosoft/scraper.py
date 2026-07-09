"""
CAAT mercuriali scraper.

Downloads all available "mercuriale dei prezzi" PDFs published by CAAT
(Turin wholesale produce market), parses each one, and writes a price
history Excel with:

- PREV_matrix: one row per product reference, one column per listino date
- Dati_lunghi: long/database format, one row per reference per listino
- Fonti: listino date + source PDF URL
- Audit_PDF: per-PDF quality check (reference count, prices found, ...)
- Avvisi_parse: parsing anomalies to review
- Validazioni: results of any known_validations checks from config
"""

from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import copy
from datetime import date, datetime

import fitz
import pandas as pd
import requests
from bs4 import BeautifulSoup
from openpyxl.utils import get_column_letter
from requests.adapters import HTTPAdapter
from tqdm import tqdm
from urllib3.util.retry import Retry

from wecosoft.config import ScraperConfig

class ScraperCancelled(Exception):
    """Raised when a cancel_event is set while a scrape is in progress."""


PRICE_TOKEN_RE = re.compile(r"^[+-]?\d+[.,]\d{2}$")

CATEGORY_OR_NOISE = {
    "FRESCO",
    "AGRUMI",
    "FRUTTA",
    "PRODOTTI ESOTICI",
    "ORTAGGI",
    "SECCO",
    "PRODOTTI SECCHI",
    "PRODOTTO",
    "MIN",
    "PREV",
    "MAX",
    "DIFF",
}


def clean_text(s) -> str:
    return re.sub(r"\s+", " ", str(s).strip())


def is_noise(line) -> bool:
    t = clean_text(line)

    if not t:
        return True

    if t in CATEGORY_OR_NOISE:
        return True

    prefixes = (
        "ORTOFRUTTA -",
        "C.A.A.T.",
        "MERCATO ",
        "TENDENZA ",
        "I prezzi all",
        "Quando non diversamente",
        "Per ciascuna referenza",
        "Zone di produzione",
        "hanno carattere",
        "esclusivamente indicativi",
        "basata sulle normative",
    )

    if t.startswith(prefixes):
        return True

    if "Listino N." in t and "Prezzi all" in t:
        return True

    return False


def is_product_reference(line) -> bool:
    t = clean_text(line)

    if is_noise(t):
        return False

    if " - " not in t:
        return False

    if len(t) < 12:
        return False

    return True


def parse_num(x):
    if x is None:
        return None
    return float(str(x).replace(",", "."))


def parse_row_from_line(line):
    """
    Extracts REFERENCE + MIN + PREV + MAX + optional DIFF from a CAAT line.

    Expected formats:
    PRODOTTO ... 1.20 1.30 1.40
    PRODOTTO ... 2.80 3.00 3.30 0.20
    PRODOTTO ... 5.00 5.15 5.30 -0.05

    If the line is a reference with no prices, returns it with price fields
    set to None.
    """
    line = clean_text(line)

    if is_noise(line):
        return None

    tokens = line.split()
    trailing_prices = []

    i = len(tokens) - 1

    while i >= 0 and PRICE_TOKEN_RE.match(tokens[i]):
        trailing_prices.append(tokens[i])
        i -= 1

    trailing_prices = list(reversed(trailing_prices))

    if len(trailing_prices) in (3, 4):
        prodotto = clean_text(" ".join(tokens[: i + 1]))

        if not is_product_reference(prodotto):
            return None

        if len(trailing_prices) == 3:
            min_v, prev_v, max_v = trailing_prices
            diff_v = None
        else:
            min_v, prev_v, max_v, diff_v = trailing_prices

        return {
            "referenza": prodotto,
            "min": parse_num(min_v),
            "prev": parse_num(prev_v),
            "max": parse_num(max_v),
            "diff": parse_num(diff_v) if diff_v is not None else None,
            "ha_prezzi": True,
        }

    if len(trailing_prices) == 0 and is_product_reference(line):
        return {
            "referenza": line,
            "min": None,
            "prev": None,
            "max": None,
            "diff": None,
            "ha_prezzi": False,
        }

    return None


def parse_split_row(lines, i):
    """
    Fallback for cases where:
    - line i = reference
    - following lines = MIN, PREV, MAX, optional DIFF
    """
    line = clean_text(lines[i])

    if not is_product_reference(line):
        return None

    nums = []
    j = i + 1

    while j < len(lines) and len(nums) < 4:
        t = clean_text(lines[j])

        if PRICE_TOKEN_RE.match(t):
            nums.append(t)
            j += 1
        else:
            break

    if len(nums) in (3, 4):
        if len(nums) == 3:
            min_v, prev_v, max_v = nums
            diff_v = None
        else:
            min_v, prev_v, max_v, diff_v = nums

        return {
            "referenza": line,
            "min": parse_num(min_v),
            "prev": parse_num(prev_v),
            "max": parse_num(max_v),
            "diff": parse_num(diff_v) if diff_v is not None else None,
            "ha_prezzi": True,
            "skip_to": j,
        }

    return None


def extract_date_from_pdf(doc, fallback_url):
    """
    Reads the listino date from inside the PDF first (some files are named
    with one date but contain a different one). Falls back to the date in
    the URL if the PDF text doesn't have it.
    """
    text = ""

    try:
        text = doc[0].get_text("text")
    except Exception:
        pass

    m = re.search(r"(\d{2}/\d{2}/\d{4})", text)
    if m:
        return datetime.strptime(m.group(1), "%d/%m/%Y").date()

    m = re.search(r"(\d{2})-(\d{2})-(\d{4})", fallback_url)
    if m:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))

    return None


def _build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120 Safari/537.36"
            )
        }
    )

    retry = Retry(
        total=5,
        connect=5,
        read=5,
        status=5,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"],
    )

    adapter = HTTPAdapter(max_retries=retry, pool_connections=50, pool_maxsize=50)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    return session


def get_page_pdf_links(session: requests.Session, base_url: str) -> set[str]:
    """
    Reads the CAAT index page for PDF links. If the page errors out, returns
    an empty set and lets the caller fall back to predictable URL enumeration.
    """
    try:
        r = session.get(base_url, timeout=45)
        r.raise_for_status()
    except Exception as e:
        print(f"WARNING: CAAT index page not reachable right now ({e}).")
        print("Continuing with predictable PDF URL enumeration.")
        return set()

    soup = BeautifulSoup(r.text, "html.parser")
    links = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]

        if ".pdf" in href.lower() and "mercur" in href.lower():
            if href.startswith("/"):
                href = "https://caat.it" + href

            links.add(href.split("?")[0])

    return links


def candidate_pdf_urls(session: requests.Session, cfg: ScraperConfig) -> list[str]:
    """
    Combines:
    1. links found on the CAAT index page, if it responds;
    2. full enumeration of predictable URLs across the configured date range.

    Includes both "Mercuriale" and the recurring typo "Mecuriale".
    """
    urls = set()

    try:
        urls |= get_page_pdf_links(session, cfg.base_url)
    except Exception as e:
        print(f"WARNING: skipping CAAT index page due to error: {e}")

    patterns = [
        "https://caat.it/_mamawp/wp-content/uploads/{yyyy}/{mm}/Mercuriale-del-{dd}-{mm}-{yyyy}.pdf",
        "https://caat.it/_mamawp/wp-content/uploads/{yyyy}/{mm}/Mecuriale-del-{dd}-{mm}-{yyyy}.pdf",
        "https://caat.it/wp-content/uploads/{yyyy}/{mm}/Mercuriale-del-{dd}-{mm}-{yyyy}.pdf",
        "https://caat.it/wp-content/uploads/{yyyy}/{mm}/Mecuriale-del-{dd}-{mm}-{yyyy}.pdf",
        "https://caat.it/_mamawp/wp-content/uploads/{yyyy}/{mm}/caat_mercuriale_{yyyy}-{mm}-{dd}.pdf",
        "https://caat.it/wp-content/uploads/{yyyy}/{mm}/caat_mercuriale_{yyyy}-{mm}-{dd}.pdf",
        "https://caat.it/_mamawp/wp-content/uploads/{yyyy}/{mm}/CAAT_mercuriale_{yyyy}-{mm}-{dd}.pdf",
        "https://caat.it/wp-content/uploads/{yyyy}/{mm}/CAAT_mercuriale_{yyyy}-{mm}-{dd}.pdf",
    ]

    d = cfg.start_date

    while d <= cfg.end_date:
        dd = f"{d.day:02d}"
        mm = f"{d.month:02d}"
        yyyy = str(d.year)

        for p in patterns:
            urls.add(p.format(dd=dd, mm=mm, yyyy=yyyy))

        d = date.fromordinal(d.toordinal() + 1)

    return sorted(urls)


def url_exists(session: requests.Session, url: str):
    """Checks whether a PDF URL exists. Tries HEAD first, then a light GET."""
    try:
        r = session.head(url, timeout=15, allow_redirects=True)
        ct = r.headers.get("content-type", "").lower()

        if r.status_code == 200 and ("pdf" in ct or url.lower().endswith(".pdf")):
            return url

        if r.status_code in (403, 405, 500, 502, 503, 504):
            r = session.get(url, timeout=20, stream=True)
            if r.status_code == 200:
                first = next(r.iter_content(4), b"")
                if first == b"%PDF":
                    return url

    except Exception:
        return None

    return None


def find_existing_pdfs(
    session: requests.Session,
    cfg: ScraperConfig,
    cancel_event=None,
    progress_cb=None,
) -> list[str]:
    candidates = candidate_pdf_urls(session, cfg)
    total = len(candidates)
    found = set()

    print(f"Candidate URLs to check: {total}")

    with ThreadPoolExecutor(max_workers=cfg.max_workers_check) as ex:
        futures = [ex.submit(url_exists, session, u) for u in candidates]

        for i, f in enumerate(tqdm(as_completed(futures), total=total, desc="Checking PDF URLs"), start=1):
            if cancel_event is not None and cancel_event.is_set():
                ex.shutdown(cancel_futures=True)
                raise ScraperCancelled("Scraping annullato durante il controllo degli URL.")

            res = f.result()
            if res:
                found.add(res)

            if progress_cb is not None:
                progress_cb("check", i, total)

    return sorted(found)


def safe_filename(url: str) -> str:
    name = url.split("/")[-1].split("?")[0]
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)


def download_pdf(session: requests.Session, url: str, pdf_dir: str):
    local = os.path.join(pdf_dir, safe_filename(url))

    if os.path.exists(local) and os.path.getsize(local) > 1000:
        return local, url

    try:
        r = session.get(url, timeout=45)

        if r.status_code == 200 and r.content[:4] == b"%PDF":
            with open(local, "wb") as f:
                f.write(r.content)

            return local, url

    except Exception:
        pass

    return None, url


def download_all(
    session: requests.Session,
    urls: list[str],
    cfg: ScraperConfig,
    cancel_event=None,
    progress_cb=None,
):
    out = []
    total = len(urls)

    with ThreadPoolExecutor(max_workers=cfg.max_workers_download) as ex:
        futures = [ex.submit(download_pdf, session, u, cfg.pdf_dir) for u in urls]

        for i, f in enumerate(tqdm(as_completed(futures), total=total, desc="Downloading PDFs"), start=1):
            if cancel_event is not None and cancel_event.is_set():
                ex.shutdown(cancel_futures=True)
                raise ScraperCancelled("Scraping annullato durante il download dei PDF.")

            local, url = f.result()
            if local:
                out.append((local, url))

            if progress_cb is not None:
                progress_cb("download", i, total)

    return out


def page_rows_from_text(page):
    """First method: uses get_text('text'), which usually returns full lines."""
    rows = []

    lines = [clean_text(x) for x in page.get_text("text").splitlines() if clean_text(x)]

    i = 0

    while i < len(lines):
        line = lines[i]
        direct = parse_row_from_line(line)

        if direct is not None:
            rows.append(direct)
            i += 1
            continue

        split = parse_split_row(lines, i)

        if split is not None:
            skip_to = split.pop("skip_to")
            rows.append(split)
            i = skip_to
            continue

        i += 1

    return rows


def page_rows_from_words(page):
    """
    Second method: coordinate/word fallback. Reconstructs lines by sorting
    words by y then x. Used when get_text('text') returns broken lines.
    """
    words = page.get_text("words")

    if not words:
        return []

    grouped = {}

    for w in words:
        x0, y0, x1, y1, text = w[:5]

        if not clean_text(text):
            continue

        y_key = round(y0 / 3) * 3
        grouped.setdefault(y_key, []).append((x0, text))

    rows = []

    for y in sorted(grouped):
        parts = [txt for x, txt in sorted(grouped[y], key=lambda z: z[0])]
        line = clean_text(" ".join(parts))
        parsed = parse_row_from_line(line)

        if parsed is not None:
            rows.append(parsed)

    return rows


def parse_pdf(path: str, url: str):
    doc = fitz.open(path)
    listino_date = extract_date_from_pdf(doc, url)

    all_rows = []

    for page_num, page in enumerate(doc, start=1):
        rows_text = page_rows_from_text(page)
        prev_count_text = sum(1 for r in rows_text if r.get("prev") is not None)

        if len(rows_text) < 20 or prev_count_text == 0:
            rows_words = page_rows_from_words(page)
            prev_count_words = sum(1 for r in rows_words if r.get("prev") is not None)

            if prev_count_words > prev_count_text:
                rows = rows_words
                metodo = "words"
            else:
                rows = rows_text
                metodo = "text"
        else:
            rows = rows_text
            metodo = "text"

        for r in rows:
            all_rows.append(
                {
                    "data_listino": listino_date,
                    "referenza": r["referenza"],
                    "min": r["min"],
                    "prev": r["prev"],
                    "max": r["max"],
                    "diff": r["diff"],
                    "source_url": url,
                    "source_file": os.path.basename(path),
                    "pagina": page_num,
                    "metodo_parse": metodo,
                }
            )

    doc.close()

    return all_rows


def deduplicate_raw(raw: pd.DataFrame) -> pd.DataFrame:
    """
    Deduplicates: keeps rows with PREV over rows without; drops exact
    duplicates; leaves genuine conflicts (same date/reference, different
    PREV) for the caller to flag.
    """
    raw = raw.copy()
    raw["has_prev"] = raw["prev"].notna()

    raw = raw.sort_values(
        ["data_listino", "referenza", "has_prev"], ascending=[True, True, False]
    )

    raw = raw.drop_duplicates(
        subset=["data_listino", "referenza", "min", "prev", "max", "diff", "source_url"],
        keep="first",
    )

    return raw.drop(columns=["has_prev"])


def build_dataset(downloaded, cancel_event=None, progress_cb=None):
    all_rows = []
    parse_errors = []
    total = len(downloaded)

    for i, (path, url) in enumerate(tqdm(downloaded, desc="Parsing PDFs"), start=1):
        if cancel_event is not None and cancel_event.is_set():
            raise ScraperCancelled("Scraping annullato durante l'analisi dei PDF.")

        try:
            rows = parse_pdf(path, url)

            if not rows:
                parse_errors.append({"url": url, "file": path, "errore": "0 righe prodotto estratte"})

            all_rows.extend(rows)

        except Exception as e:
            parse_errors.append({"url": url, "file": path, "errore": str(e)})

        if progress_cb is not None:
            progress_cb("parse", i, total)

    raw = pd.DataFrame(all_rows)

    if raw.empty:
        raise RuntimeError("Parsing fallito: 0 righe estratte dai PDF.")

    raw = deduplicate_raw(raw)

    raw["data_listino"] = pd.to_datetime(raw["data_listino"], errors="coerce")
    raw["data_col"] = raw["data_listino"].dt.strftime("%Y-%m-%d")

    missing_date = raw[raw["data_listino"].isna()].copy()
    avvisi = pd.DataFrame(parse_errors)

    if not missing_date.empty:
        tmp = missing_date[["source_url", "source_file"]].drop_duplicates()
        tmp["errore"] = "Data listino non rilevata"
        avvisi = pd.concat([avvisi, tmp], ignore_index=True)

    raw_dated = raw[raw["data_listino"].notna()].copy()

    conflicts = (
        raw_dated.dropna(subset=["prev"])
        .groupby(["referenza", "data_col"])["prev"]
        .nunique()
        .reset_index(name="n_valori_prev_diversi")
    )

    conflicts = conflicts[conflicts["n_valori_prev_diversi"] > 1]

    if not conflicts.empty:
        conflicts["errore"] = "Stessa referenza e stessa data con PREV diversi"
        avvisi = pd.concat([avvisi, conflicts], ignore_index=True)

    matrix_source = raw_dated.copy()
    matrix_source["has_prev"] = matrix_source["prev"].notna()

    matrix_source = matrix_source.sort_values(
        ["referenza", "data_col", "has_prev", "prev"],
        ascending=[True, True, False, True],
        na_position="last",
    )

    matrix_source = matrix_source.drop_duplicates(subset=["referenza", "data_col"], keep="first")

    matrix = (
        matrix_source.pivot(index="referenza", columns="data_col", values="prev")
        .reset_index()
        .rename(columns={"referenza": "REFERENZA_COMPLETA"})
    )

    date_cols = sorted([c for c in matrix.columns if c != "REFERENZA_COMPLETA"])
    matrix = matrix[["REFERENZA_COMPLETA"] + date_cols]

    fonti = (
        raw_dated[["data_col", "source_url", "source_file"]]
        .drop_duplicates()
        .sort_values(["data_col", "source_url"])
        .rename(columns={"data_col": "DATA_LISTINO", "source_url": "URL_PDF", "source_file": "FILE"})
    )

    audit_pdf = (
        raw_dated.groupby(["data_col", "source_url", "source_file"])
        .agg(
            righe_referenza=("referenza", "count"),
            referenze_uniche=("referenza", "nunique"),
            prezzi_prev_compilati=("prev", lambda x: x.notna().sum()),
            righe_senza_prev=("prev", lambda x: x.isna().sum()),
            min_compilati=("min", lambda x: x.notna().sum()),
            max_compilati=("max", lambda x: x.notna().sum()),
            pagine_lette=("pagina", "nunique"),
        )
        .reset_index()
        .rename(columns={"data_col": "DATA_LISTINO", "source_url": "URL_PDF", "source_file": "FILE"})
    )

    audit_pdf["sospetto"] = (audit_pdf["referenze_uniche"] < 150) | (
        audit_pdf["prezzi_prev_compilati"] == 0
    )

    suspicious = audit_pdf[audit_pdf["sospetto"]].copy()

    if not suspicious.empty:
        suspicious["errore"] = "PDF con poche referenze o zero PREV: controllare manualmente"
        avvisi = pd.concat([avvisi, suspicious], ignore_index=True)

    return raw_dated, matrix, fonti, audit_pdf, avvisi


def run_known_validations(raw: pd.DataFrame, known_validations: list[dict]) -> pd.DataFrame:
    """Runs any sanity checks defined in config (scraper.known_validations)."""
    out = []

    for c in known_validations:
        data_col = c["data"]
        referenza = c["referenza"]
        prev_atteso = c["prezzo_atteso"]

        subset = raw[(raw["data_col"] == data_col) & (raw["referenza"] == referenza)]

        if subset.empty:
            out.append(
                {
                    "data_col": data_col,
                    "referenza": referenza,
                    "prev_atteso": prev_atteso,
                    "prev_trovato": None,
                    "esito": "NON_TROVATA",
                }
            )
            continue

        found_values = sorted(subset["prev"].dropna().unique().tolist())
        prev_found = found_values[0] if found_values else None

        ok = prev_found is not None and abs(prev_found - prev_atteso) < 0.00001

        out.append(
            {
                "data_col": data_col,
                "referenza": referenza,
                "prev_atteso": prev_atteso,
                "prev_trovato": prev_found,
                "esito": "OK" if ok else "ERRORE",
            }
        )

    return pd.DataFrame(
        out, columns=["data_col", "referenza", "prev_atteso", "prev_trovato", "esito"]
    )


def write_excel(output, matrix, raw, fonti, audit_pdf, avvisi, validazioni):
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        matrix.to_excel(writer, index=False, sheet_name="PREV_matrix")
        raw.to_excel(writer, index=False, sheet_name="Dati_lunghi")
        fonti.to_excel(writer, index=False, sheet_name="Fonti")
        audit_pdf.to_excel(writer, index=False, sheet_name="Audit_PDF")
        avvisi.to_excel(writer, index=False, sheet_name="Avvisi_parse")
        validazioni.to_excel(writer, index=False, sheet_name="Validazioni")

        wb = writer.book

        for ws in wb.worksheets:
            ws.freeze_panes = "B2" if ws.title == "PREV_matrix" else "A2"

            for cell in ws[1]:
                cell.font = copy(cell.font)
                cell.font = cell.font.copy(bold=True)
                cell.alignment = copy(cell.alignment)
                cell.alignment = cell.alignment.copy(horizontal="center")

            if ws.title == "PREV_matrix":
                ws.column_dimensions["A"].width = 110

                for col in range(2, ws.max_column + 1):
                    letter = get_column_letter(col)
                    ws.column_dimensions[letter].width = 12

                for row in ws.iter_rows(min_row=2, min_col=2):
                    for cell in row:
                        cell.number_format = "0.00"

            elif ws.title == "Dati_lunghi":
                widths = {
                    "A": 14,
                    "B": 110,
                    "C": 10,
                    "D": 10,
                    "E": 10,
                    "F": 10,
                    "G": 120,
                    "H": 45,
                    "I": 10,
                    "J": 14,
                    "K": 14,
                }

                for col, width in widths.items():
                    ws.column_dimensions[col].width = width

            else:
                for col in range(1, ws.max_column + 1):
                    letter = get_column_letter(col)
                    ws.column_dimensions[letter].width = 35


def run(cfg: ScraperConfig, cancel_event=None, progress_cb=None) -> dict:
    """
    cancel_event: an optional threading.Event; if set while a scrape is in
    progress, raises ScraperCancelled at the next checkpoint instead of
    continuing.
    progress_cb: an optional callable(phase: str, current: int, total: int)
    invoked as work proceeds, with phase one of "check"/"download"/"parse".
    """
    os.makedirs(cfg.pdf_dir, exist_ok=True)

    session = _build_session()

    print("Collecting all available CAAT PDFs...")
    urls = find_existing_pdfs(session, cfg, cancel_event=cancel_event, progress_cb=progress_cb)
    print(f"Candidate PDFs found: {len(urls)}")

    downloaded = download_all(session, urls, cfg, cancel_event=cancel_event, progress_cb=progress_cb)
    print(f"Downloaded and valid PDFs: {len(downloaded)}")

    raw, matrix, fonti, audit_pdf, avvisi = build_dataset(downloaded, cancel_event=cancel_event, progress_cb=progress_cb)

    validazioni = run_known_validations(raw, cfg.known_validations)

    write_excel(cfg.output_xlsx, matrix, raw, fonti, audit_pdf, avvisi, validazioni)

    summary = {
        "pdf_trovati": len(urls),
        "pdf_scaricati_validi": len(downloaded),
        "date_listino_uniche": int(raw["data_col"].nunique()),
        "referenze_uniche": int(matrix["REFERENZA_COMPLETA"].nunique()),
        "righe_long_format": int(len(raw)),
        "celle_prezzo_prev_compilate": int(
            matrix.drop(columns=["REFERENZA_COMPLETA"]).notna().sum().sum()
        ),
        "pdf_sospetti": int(audit_pdf["sospetto"].sum()),
        "avvisi_parse": int(len(avvisi)),
        "validazioni_ok": int((validazioni["esito"] == "OK").sum()) if len(validazioni) else 0,
        "validazioni_totali": int(len(validazioni)),
        "file_output": cfg.output_xlsx,
    }

    print("\nSUMMARY")
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    if summary["referenze_uniche"] == 0:
        raise RuntimeError("Errore grave: 0 referenze estratte.")

    if summary["celle_prezzo_prev_compilate"] == 0:
        raise RuntimeError("Errore grave: 0 prezzi PREV estratti.")

    if summary["validazioni_totali"] and summary["validazioni_ok"] < summary["validazioni_totali"]:
        print("\nWARNING: some known_validations checks did not pass.")
        print("Check the 'Validazioni' sheet before trusting this output.")

    if summary["pdf_sospetti"] > 0:
        print("\nWARNING: some PDFs look suspicious.")
        print("Check the 'Audit_PDF' and 'Avvisi_parse' sheets.")

    print(f"\nExcel generated: {cfg.output_xlsx}")

    return summary
