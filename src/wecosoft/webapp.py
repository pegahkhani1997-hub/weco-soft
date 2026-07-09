"""
wecosoft web app: a two-button interface over the CAAT scraper and the
taxonomy-driven custom report.

Run with:
    streamlit run app.py
"""

from __future__ import annotations

import dataclasses
import io
import threading
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from wecosoft import latest_report, pricing, scraper, tweak_report
from wecosoft.config import Config, load_config

PHASE_LABELS = {
    "check": "Controllo URL disponibili",
    "download": "Download PDF",
    "parse": "Analisi PDF",
}

UPLOADS_DIR = Path("output/uploads")
REPORTS_DIR = Path("output/reports")

DISCOUNT_HELP = (
    "Percentuali da applicare al prezzo scelto, separate da virgola. "
    "Usa il segno per indicare sconto o maggiorazione, es. -20, 10, 15 "
    "genera tre colonne aggiuntive."
)

# Reports never look back further than 28 working days (~6 weeks), so
# scanning years of history on every run is almost always wasted work.
# These are calendar-day lookbacks; "Storico completo" keeps whatever
# start_date is set in config.yaml (default: 2023-01-01).
SCAN_RANGE_OPTIONS = {
    "Ultimi 90 giorni (veloce)": 90,
    "Ultimi 6 mesi": 182,
    "Ultimo anno": 365,
    "Storico completo (lento)": None,
}


def _load_cfg() -> Config:
    try:
        return load_config("config.yaml")
    except FileNotFoundError:
        st.info(
            "Nessun config.yaml trovato: uso le impostazioni di default. "
            "Copia config.example.yaml in config.yaml per personalizzarle.",
            icon="ℹ️",
        )
        return Config()


def _save_taxonomy_upload(uploaded_file) -> str:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    dest = UPLOADS_DIR / "tassonomia_uploaded.csv"
    raw = uploaded_file.getvalue()

    is_binary_excel = raw[:4] == b"PK\x03\x04" or raw[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

    if is_binary_excel:
        df = pd.read_excel(io.BytesIO(raw))
        df.to_csv(dest, index=False)
    else:
        dest.write_bytes(raw)

    return str(dest)


class ScraperJob:
    """
    Shared state between the background scraping thread and the UI.
    Only plain attributes are mutated from the background thread — no
    Streamlit calls happen there, so this is safe without extra locking
    for our purposes (each field is set independently and the UI only
    ever reads a possibly-one-tick-stale value).
    """

    def __init__(self):
        self.cancel_event = threading.Event()
        self.status = "running"  # running | done | cancelled | error
        self.phase = None
        self.current = 0
        self.total = 0
        self.result = None
        self.error = None


def _run_scraper_job(run_cfg, job: ScraperJob):
    def progress_cb(phase, current, total):
        job.phase = phase
        job.current = current
        job.total = total

    try:
        scraper.run(run_cfg, cancel_event=job.cancel_event, progress_cb=progress_cb)

        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        job.result = latest_report.build_latest_report(
            run_cfg.output_xlsx, str(REPORTS_DIR / "ultimo_listino.pdf")
        )
        job.status = "done"
    except scraper.ScraperCancelled:
        job.status = "cancelled"
    except Exception as e:
        job.error = str(e)
        job.status = "error"


@st.fragment(run_every=1)
def _scraper_progress_fragment():
    job = st.session_state.get("scraper_job")

    if job is None:
        return

    if job.status == "running":
        label = PHASE_LABELS.get(job.phase, "Avvio")

        if job.total:
            st.progress(min(job.current / job.total, 1.0), text=f"{label}: {job.current}/{job.total}")
        else:
            st.write(f"{label}...")

        if st.button("⏹ Stop", key="stop_scraper"):
            job.cancel_event.set()
            st.info("Interruzione richiesta, attendere...")

        return

    # Terminal state: hand results off to session_state and do a full
    # app rerun so the range picker/button and any success message
    # outside this fragment reappear.
    if job.status == "done":
        st.session_state["scraper_report_pdf"] = job.result["output_pdf"]
        st.session_state["scraper_report_meta"] = job.result
    elif job.status == "cancelled":
        st.session_state["scraper_last_message"] = ("warning", "Scraping interrotto.")
    elif job.status == "error":
        st.session_state["scraper_last_message"] = ("error", f"Errore durante lo scraping: {job.error}")

    st.session_state["scraper_job"] = None
    st.rerun()


def _parse_discounts(text: str) -> list[float]:
    values = []

    for token in text.split(","):
        token = token.strip()
        if not token:
            continue
        values.append(float(token.replace("%", "")))

    return values


@st.dialog("Filter / Tweak Output")
def _tweak_settings_dialog(cfg: Config):
    st.write("Personalizza il listino prima di generarlo.")

    time_frame = st.selectbox("Periodo (giorni lavorativi)", list(tweak_report.TIME_FRAME_DAYS.keys()))

    stat_key = st.selectbox(
        "Prezzo da usare",
        tweak_report.STAT_KEYS,
        format_func=lambda k: {"minimo": "Minimo", "massimo": "Massimo", "medio": "Medio", "moda": "Moda"}[k],
    )

    discount_text = st.text_input("Colonne prezzo scontato (%, separate da virgola)", value="", help=DISCOUNT_HELP)

    col_ok, col_cancel = st.columns(2)
    generate = col_ok.button("Genera report", type="primary", use_container_width=True)
    cancel = col_cancel.button("Annulla", use_container_width=True)

    if cancel:
        st.rerun()

    if generate:
        if not Path(cfg.scraper.output_xlsx).exists():
            st.error("Devi prima eseguire 'Activate Scraper' per avere i dati dei prezzi.")
            return

        try:
            discounts = _parse_discounts(discount_text)
        except ValueError:
            st.error("Sconti non validi: usa numeri separati da virgola, es. -20, 10, 15")
            return

        REPORTS_DIR.mkdir(parents=True, exist_ok=True)

        try:
            with st.spinner("Genero il report..."):
                result = tweak_report.build_tweak_report(
                    scraper_output_xlsx=cfg.scraper.output_xlsx,
                    taxonomy_csv=st.session_state["taxonomy_path"],
                    time_frame_key=time_frame,
                    stat_key=stat_key,
                    discount_pcts=discounts,
                    output_pdf=str(REPORTS_DIR / "listino_personalizzato.pdf"),
                )
        except Exception as e:
            st.error(f"Errore nella generazione del report: {e}")
            return

        st.session_state["tweak_report_pdf"] = result["output_pdf"]
        st.session_state["tweak_report_meta"] = result
        st.rerun()


def main():
    st.set_page_config(page_title="wecosoft — Listino CAAT", page_icon="🥕", layout="centered")
    st.title("wecosoft")
    st.caption("Scraper mercuriali CAAT e generazione listino personalizzato")

    cfg = _load_cfg()

    st.divider()
    st.subheader("1. Scraper")
    st.write("Raccoglie gli ultimi listini CAAT e genera un report PDF con i prezzi più recenti.")

    st.session_state.setdefault("scraper_job", None)

    if st.session_state["scraper_job"] is not None:
        _scraper_progress_fragment()
    else:
        last_message = st.session_state.pop("scraper_last_message", None)
        if last_message:
            kind, text = last_message
            getattr(st, kind)(text)

        range_col, button_col = st.columns([2, 1])

        with range_col:
            range_label = st.selectbox(
                "Periodo da scansionare",
                list(SCAN_RANGE_OPTIONS.keys()),
                index=0,
                help=(
                    "Quanto indietro cercare i listini CAAT. Un periodo più corto vuol dire "
                    "molte meno pagine da controllare, quindi molto più veloce. I report "
                    "guardano al massimo 28 giorni lavorativi indietro, quindi 'Ultimi 90 "
                    "giorni' basta per l'uso normale — usa 'Storico completo' solo se ti "
                    "serve costruire la cronologia prezzi da zero."
                ),
            )

        with button_col:
            st.write("")
            activate = st.button("🔄 Activate Scraper", type="primary", use_container_width=True)

        if activate:
            days_back = SCAN_RANGE_OPTIONS[range_label]

            if days_back is None:
                run_start_date = cfg.scraper.start_date
            else:
                run_start_date = max(cfg.scraper.start_date, date.today() - timedelta(days=days_back))

            run_cfg = dataclasses.replace(cfg.scraper, start_date=run_start_date)

            job = ScraperJob()
            st.session_state["scraper_job"] = job

            thread = threading.Thread(target=_run_scraper_job, args=(run_cfg, job), daemon=True)
            thread.start()
            st.rerun()

    if st.session_state.get("scraper_report_pdf"):
        meta = st.session_state["scraper_report_meta"]
        st.success(f"Report generato — listino del {meta['data_listino']}, {meta['n_referenze']} referenze.")

        with open(st.session_state["scraper_report_pdf"], "rb") as f:
            st.download_button(
                "⬇️ Scarica report PDF",
                f.read(),
                file_name="ultimo_listino_caat.pdf",
                mime="application/pdf",
            )

    st.divider()
    st.subheader("2. Tassonomia e listino personalizzato")

    uploaded = st.file_uploader("Carica il file tassonomia", type=["csv", "xls", "xlsx"])

    if uploaded is not None and uploaded.name != st.session_state.get("taxonomy_uploaded_name"):
        try:
            path = _save_taxonomy_upload(uploaded)
            tax = pricing.load_taxonomy(path)
            st.session_state["taxonomy_path"] = path
            st.session_state["taxonomy_n_rows"] = len(tax)
            st.session_state["taxonomy_uploaded_name"] = uploaded.name
        except Exception as e:
            st.error(f"Errore nel file tassonomia: {e}")
            st.session_state.pop("taxonomy_path", None)

    if st.session_state.get("taxonomy_path"):
        st.success(f"Tassonomia caricata — {st.session_state['taxonomy_n_rows']} referenze.")

        if st.button("⚙️ Filter / Tweak Output"):
            _tweak_settings_dialog(cfg)

    if st.session_state.get("tweak_report_pdf"):
        meta = st.session_state["tweak_report_meta"]
        st.success(
            f"Listino personalizzato generato — {meta['n_referenze']} referenze, "
            f"periodo {meta['finestra_da']} → {meta['finestra_a']}."
        )

        with open(st.session_state["tweak_report_pdf"], "rb") as f:
            st.download_button(
                "⬇️ Scarica listino personalizzato PDF",
                f.read(),
                file_name="listino_personalizzato.pdf",
                mime="application/pdf",
            )
