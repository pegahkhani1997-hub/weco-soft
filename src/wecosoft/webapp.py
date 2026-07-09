"""
wecosoft web app: a two-button interface over the CAAT scraper and the
taxonomy-driven custom report.

Run with:
    streamlit run app.py
"""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import streamlit as st

from wecosoft import latest_report, pricing, scraper, tweak_report
from wecosoft.config import Config, load_config

UPLOADS_DIR = Path("output/uploads")
REPORTS_DIR = Path("output/reports")

DISCOUNT_HELP = (
    "Percentuali da applicare al prezzo scelto, separate da virgola. "
    "Usa il segno per indicare sconto o maggiorazione, es. -20, 10, 15 "
    "genera tre colonne aggiuntive."
)


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

    if st.button("🔄 Activate Scraper", type="primary"):
        try:
            with st.spinner("Scraping in corso — può richiedere qualche minuto..."):
                scraper.run(cfg.scraper)

                REPORTS_DIR.mkdir(parents=True, exist_ok=True)
                result = latest_report.build_latest_report(
                    cfg.scraper.output_xlsx, str(REPORTS_DIR / "ultimo_listino.pdf")
                )

            st.session_state["scraper_report_pdf"] = result["output_pdf"]
            st.session_state["scraper_report_meta"] = result
        except Exception as e:
            st.error(f"Errore durante lo scraping: {e}")

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
