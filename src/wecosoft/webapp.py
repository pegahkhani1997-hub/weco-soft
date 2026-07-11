"""
wecosoft web app: a two-button interface over the CAAT scraper and the
taxonomy-driven custom report.

Run with:
    streamlit run app.py
"""

from __future__ import annotations

import dataclasses
import io
import re
import threading
from datetime import date, datetime
from datetime import time as dtime
from datetime import timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from wecosoft import inventory, latest_report, pricing, scraper, store, tweak_report
from wecosoft.config import Config, load_config

PHASE_LABELS = {
    "check": "Controllo URL disponibili",
    "download": "Download PDF",
    "parse": "Analisi PDF",
}

UPLOADS_DIR = Path("output/uploads")
REPORTS_DIR = Path("output/reports")
STATE_DIR = Path("output/state")
CLIENTS_PATH = STATE_DIR / "clients.json"
INVENTORY_DRAFT_PATH = STATE_DIR / "inventory_draft.json"
PRICED_LISTINO_CSV = REPORTS_DIR / "listino_personalizzato.csv"

DEFAULT_INVENTORY_ROWS = 15

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
    "Ultime 2 settimane (più veloce)": 14,
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
            run_cfg.output_xlsx,
            str(REPORTS_DIR / "ultimo_listino.pdf"),
            output_csv=str(REPORTS_DIR / "ultimo_listino.csv"),
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
    generate = col_ok.button("Genera report", type="primary", width="stretch")
    cancel = col_cancel.button("Annulla", width="stretch")

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
                    output_csv=str(REPORTS_DIR / "listino_personalizzato.csv"),
                )
        except Exception as e:
            st.error(f"Errore nella generazione del report: {e}")
            return

        st.session_state["tweak_report_pdf"] = result["output_pdf"]
        st.session_state["tweak_report_meta"] = result
        st.rerun()


_PASTE_LINE_RE = re.compile(r"^(.*?\S)\s*[,;]\s*([^,;]+)\s*$")


def _parse_pasted_quantity(raw: str) -> float | None:
    """Accepts '30', '10,1', '71,6 Kg', '3 KG', etc. and returns kg as a float."""
    s = re.sub(r"(?i)kg\.?", "", str(raw)).strip()
    s = s.replace(",", ".")
    s = re.sub(r"[^0-9.\-]", "", s)

    if not s:
        return None

    try:
        return float(s)
    except ValueError:
        return None


INVENTORY_COLUMNS = ["descrizione", "ricevuto_kg", "recuperato_1a_kg", "recuperato_2a_kg", "scartato_kg", "note"]

_HEADER_OR_TOTAL_WORDS = ("prodotto", "totale", "total")


def _parse_pasted_inventory(text: str) -> list[dict]:
    """
    Parses lines pasted from the inventory tracking sheet: Prodotto,
    Ricevuto, Recuperato 1a, Recuperato 2a, Scartato, Note — tab-separated,
    as copied straight from Excel/Sheets/Numbers. Header rows and a
    trailing "TOTALE" row are skipped automatically. Trailing columns may
    be omitted (pasting just Prodotto + Ricevuto still works). "—", blank
    cells, and "kg"/comma-decimal quantities (e.g. "10,1 Kg") are all
    handled.

    Falls back to a simple "product, quantity" pair (comma/semicolon
    separated) for single lines typed by hand rather than pasted.
    """
    rows = []

    for line in text.splitlines():
        line = line.strip()

        if not line:
            continue

        if "\t" in line:
            parts = [p.strip() for p in line.split("\t")]
            descrizione = parts[0]

            if not descrizione or descrizione.lower() in _HEADER_OR_TOTAL_WORDS:
                continue

            def col(i):
                return parts[i] if len(parts) > i else ""

            row = {
                "descrizione": descrizione,
                "ricevuto_kg": _parse_pasted_quantity(col(1)),
                "recuperato_1a_kg": _parse_pasted_quantity(col(2)),
                "recuperato_2a_kg": _parse_pasted_quantity(col(3)),
                "scartato_kg": _parse_pasted_quantity(col(4)),
                "note": col(5),
            }
        else:
            m = _PASTE_LINE_RE.match(line)

            if not m:
                continue

            descrizione = m.group(1).strip()

            if descrizione.lower() in _HEADER_OR_TOTAL_WORDS:
                continue

            row = {
                "descrizione": descrizione,
                "ricevuto_kg": _parse_pasted_quantity(m.group(2)),
                "recuperato_1a_kg": None,
                "recuperato_2a_kg": None,
                "scartato_kg": None,
                "note": "",
            }

        if row["ricevuto_kg"] is None and row["recuperato_1a_kg"] is None and row["recuperato_2a_kg"] is None:
            continue

        rows.append(row)

    return rows


def _next_weekdays(n: int, start: date | None = None) -> list[date]:
    """The next n weekdays (Mon-Fri), starting today (today included if
    it's itself a weekday)."""
    start = start or date.today()
    days: list[date] = []
    d = start

    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)

    return days


def _parse_time_or_default(s: str, default: dtime = dtime(9, 0)) -> dtime:
    try:
        return datetime.strptime(s, "%H:%M").time()
    except (TypeError, ValueError):
        return default


def _load_priced_referenze() -> list[str]:
    if not PRICED_LISTINO_CSV.exists():
        return []

    df = pd.read_csv(PRICED_LISTINO_CSV)
    return sorted(df["referenza"].astype(str).tolist())


def _save_clients():
    store.save_json(str(CLIENTS_PATH), inventory.clients_to_dicts(st.session_state["clients"]))


@st.dialog("Profilo cliente")
def _client_dialog(existing: inventory.Client | None = None):
    referenze_options = _load_priced_referenze()

    nome = st.text_input("Nome ristorante", value=existing.nome if existing else "")
    indirizzo = st.text_input("Indirizzo", value=existing.indirizzo if existing else "")
    telefono = st.text_input("Telefono", value=existing.telefono if existing else "")

    wishlist_default = [w for w in (existing.wishlist if existing else []) if w in referenze_options]
    wishlist = st.multiselect("Wishlist (prodotti della tassonomia)", referenze_options, default=wishlist_default)

    weekday_options = _next_weekdays(15)
    weekday_labels = [inventory.format_weekday_date(d.isoformat()) for d in weekday_options]

    existing_date = None
    if existing and existing.data_consegna:
        try:
            existing_date = date.fromisoformat(existing.data_consegna)
        except ValueError:
            existing_date = None

    default_date_index = weekday_options.index(existing_date) if existing_date in weekday_options else 0

    data_label = st.selectbox("Data di consegna preferita (giorni feriali)", weekday_labels, index=default_date_index)
    data_consegna_iso = weekday_options[weekday_labels.index(data_label)].isoformat()

    col_t1, col_t2 = st.columns(2)

    with col_t1:
        ora_inizio = st.time_input(
            "Ora inizio consegna",
            value=_parse_time_or_default(existing.ora_inizio if existing else "", default=dtime(9, 0)),
        )

    with col_t2:
        ora_fine = st.time_input(
            "Ora fine consegna",
            value=_parse_time_or_default(existing.ora_fine if existing else "", default=dtime(11, 0)),
        )

    col_save, col_cancel = st.columns(2)
    save = col_save.button("Salva", type="primary", width="stretch")
    cancel = col_cancel.button("Annulla", width="stretch")

    if cancel:
        st.rerun()

    if save:
        if not nome.strip():
            st.error("Il nome del ristorante è obbligatorio.")
            return

        if ora_fine <= ora_inizio:
            st.error("L'ora di fine consegna deve essere successiva all'ora di inizio.")
            return

        clients = st.session_state["clients"]

        if existing:
            for i, c in enumerate(clients):
                if c.id == existing.id:
                    clients[i] = inventory.Client(
                        id=existing.id,
                        nome=nome.strip(),
                        indirizzo=indirizzo.strip(),
                        telefono=telefono.strip(),
                        wishlist=wishlist,
                        data_consegna=data_consegna_iso,
                        ora_inizio=ora_inizio.strftime("%H:%M"),
                        ora_fine=ora_fine.strftime("%H:%M"),
                    )
                    break
        else:
            clients.append(
                inventory.Client.new(
                    nome=nome.strip(),
                    indirizzo=indirizzo.strip(),
                    telefono=telefono.strip(),
                    wishlist=wishlist,
                    data_consegna=data_consegna_iso,
                    ora_inizio=ora_inizio.strftime("%H:%M"),
                    ora_fine=ora_fine.strftime("%H:%M"),
                )
            )

        st.session_state["clients"] = clients
        _save_clients()
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
            range_options = list(SCAN_RANGE_OPTIONS.keys())
            default_range = "Ultimi 90 giorni (veloce)"

            range_label = st.selectbox(
                "Periodo da scansionare",
                range_options,
                index=range_options.index(default_range),
                help=(
                    "Quanto indietro cercare i listini CAAT. Un periodo più corto vuol dire "
                    "molte meno pagine da controllare, quindi molto più veloce. I report "
                    "guardano al massimo 28 giorni lavorativi indietro: 'Ultime 2 settimane' "
                    "può bastare per un aggiornamento veloce, 'Ultimi 90 giorni' copre anche "
                    "il periodo più lungo (1 mese) con margine — usa 'Storico completo' solo "
                    "se ti serve costruire la cronologia prezzi da zero."
                ),
            )

        with button_col:
            st.write("")
            activate = st.button("🔄 Activate Scraper", type="primary", width="stretch")

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

        dl_col1, dl_col2 = st.columns(2)

        with dl_col1:
            with open(st.session_state["scraper_report_pdf"], "rb") as f:
                st.download_button(
                    "⬇️ Scarica PDF",
                    f.read(),
                    file_name="ultimo_listino_caat.pdf",
                    mime="application/pdf",
                    width="stretch",
                )

        with dl_col2:
            if meta.get("output_csv"):
                with open(meta["output_csv"], "rb") as f:
                    st.download_button(
                        "⬇️ Scarica CSV",
                        f.read(),
                        file_name="ultimo_listino_caat.csv",
                        mime="text/csv",
                        width="stretch",
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

        dl_col1, dl_col2 = st.columns(2)

        with dl_col1:
            with open(st.session_state["tweak_report_pdf"], "rb") as f:
                st.download_button(
                    "⬇️ Scarica PDF",
                    f.read(),
                    file_name="listino_personalizzato.pdf",
                    mime="application/pdf",
                    width="stretch",
                )

        with dl_col2:
            if meta.get("output_csv"):
                with open(meta["output_csv"], "rb") as f:
                    st.download_button(
                        "⬇️ Scarica CSV",
                        f.read(),
                        file_name="listino_personalizzato.csv",
                        mime="text/csv",
                        width="stretch",
                    )

    if PRICED_LISTINO_CSV.exists():
        st.divider()
        st.subheader("3. Inventario e allocazione clienti")

        if "clients" not in st.session_state:
            st.session_state["clients"] = inventory.clients_from_dicts(store.load_json(str(CLIENTS_PATH), []))

        if "inventory_rows" not in st.session_state:
            saved_rows = store.load_json(str(INVENTORY_DRAFT_PATH), None)
            st.session_state["inventory_rows"] = saved_rows or [
                {
                    "descrizione": "",
                    "ricevuto_kg": None,
                    "recuperato_1a_kg": None,
                    "recuperato_2a_kg": None,
                    "scartato_kg": None,
                    "note": "",
                }
                for _ in range(DEFAULT_INVENTORY_ROWS)
            ]

        st.write("**Inventario della settimana**")
        st.caption(
            "Inserisci i prodotti e i kg per ciascuna fase (ricevuto, recuperato 1ª/2ª "
            "selezione, scartato). Il prezzo si applica alla quantità recuperata "
            "(1ª + 2ª); se non compili le colonne di recupero si usa 'Ricevuto'. "
            "Righe vuote vengono ignorate."
        )

        with st.expander("📋 Incolla da foglio di calcolo (Excel / Sheets / Numbers)"):
            st.caption(
                "Copia le colonne del tuo foglio (Prodotto, Ricevuto, Recuperato 1ª, "
                "Recuperato 2ª, Scartato, Note — anche solo le prime due) e incollale "
                "qui sotto. Righe di intestazione e la riga 'TOTALE' vengono ignorate "
                "automaticamente. Sostituisce le righe della tabella qui sotto — più "
                "affidabile che incollare più righe direttamente nella tabella, che su "
                "alcuni browser non le importa tutte."
            )
            paste_text = st.text_area(
                "Incolla qui", height=150, key="inventory_paste_box", label_visibility="collapsed"
            )

            if st.button("Importa nella tabella"):
                parsed_rows = _parse_pasted_inventory(paste_text)

                if not parsed_rows:
                    st.warning("Non ho trovato righe valide da importare.")
                else:
                    st.session_state["inventory_rows"] = parsed_rows
                    store.save_json(str(INVENTORY_DRAFT_PATH), parsed_rows)
                    st.session_state.pop("inventory_editor", None)
                    st.success(f"{len(parsed_rows)} righe importate.")
                    st.rerun()

        inventory_df = pd.DataFrame(st.session_state["inventory_rows"])
        for col in INVENTORY_COLUMNS:
            if col not in inventory_df.columns:
                inventory_df[col] = "" if col in ("descrizione", "note") else None

        edited_inventory = st.data_editor(
            inventory_df[INVENTORY_COLUMNS],
            num_rows="dynamic",
            width="stretch",
            key="inventory_editor",
            column_config={
                "descrizione": st.column_config.TextColumn("Prodotto"),
                "ricevuto_kg": st.column_config.NumberColumn("Ricevuto (kg)", min_value=0.0, step=0.5),
                "recuperato_1a_kg": st.column_config.NumberColumn("Recuperato 1ª (kg)", min_value=0.0, step=0.5),
                "recuperato_2a_kg": st.column_config.NumberColumn("Recuperato 2ª (kg)", min_value=0.0, step=0.5),
                "scartato_kg": st.column_config.NumberColumn("Scartato (kg)", min_value=0.0, step=0.5),
                "note": st.column_config.TextColumn("Note"),
            },
        )

        if st.button("💾 Salva inventario e abbina prezzi"):
            rows = edited_inventory.to_dict("records")
            st.session_state["inventory_rows"] = rows
            store.save_json(str(INVENTORY_DRAFT_PATH), rows)

            try:
                listino_df, price_col = inventory.load_priced_listino(str(PRICED_LISTINO_CSV))
                matched = inventory.match_inventory(
                    rows, listino_df, price_col, cfg.matching.manual_aliases, cfg.matching.soglia_match
                )
                st.session_state["inventory_matched"] = matched
                st.session_state["inventory_price_col"] = price_col
            except Exception as e:
                st.error(f"Errore nell'abbinamento inventario: {e}")

        if st.session_state.get("inventory_matched"):
            matched = st.session_state["inventory_matched"]
            preview = pd.DataFrame(matched)
            n_unmatched = int(preview["referenza_matched"].isna().sum())

            st.dataframe(
                preview[
                    [
                        "descrizione",
                        "ricevuto_kg",
                        "recuperato_1a_kg",
                        "recuperato_2a_kg",
                        "scartato_kg",
                        "usable_kg",
                        "referenza_matched",
                        "prezzo_unitario",
                        "match_metodo",
                    ]
                ],
                width="stretch",
            )

            if n_unmatched:
                st.warning(
                    f"{n_unmatched} prodotto/i dell'inventario non sono stati riconosciuti nel listino "
                    "e non verranno allocati. Controlla l'ortografia o aggiungi un alias in config.yaml."
                )

        st.write("**Clienti**")

        for c in st.session_state["clients"]:
            with st.container(border=True):
                if st.session_state.get(f"confirm_delete_{c.id}"):
                    st.warning(f"Eliminare il cliente **{c.nome}**? Non si può annullare.")
                    col_yes, col_no = st.columns(2)

                    if col_yes.button("Sì, elimina", key=f"confirm_yes_{c.id}", type="primary", width="stretch"):
                        st.session_state["clients"] = [
                            x for x in st.session_state["clients"] if x.id != c.id
                        ]
                        _save_clients()
                        st.session_state.pop(f"confirm_delete_{c.id}", None)
                        st.rerun()

                    if col_no.button("Annulla", key=f"confirm_no_{c.id}", width="stretch"):
                        st.session_state.pop(f"confirm_delete_{c.id}", None)
                        st.rerun()

                    continue

                info_col, edit_col, delete_col = st.columns([4, 1, 1])

                with info_col:
                    wishlist_text = ", ".join(c.wishlist) if c.wishlist else "—"
                    data_str = inventory.format_weekday_date(c.data_consegna) if c.data_consegna else "—"
                    orario = f"{c.ora_inizio}–{c.ora_fine}" if c.ora_inizio and c.ora_fine else "—"
                    st.markdown(
                        f"**{c.nome}**  \n"
                        f"{c.indirizzo or '—'} · {c.telefono or '—'}  \n"
                        f"Wishlist: {wishlist_text}  \n"
                        f"Consegna preferita: {data_str}, {orario}"
                    )

                with edit_col:
                    if st.button("✏️ Modifica", key=f"edit_client_{c.id}", width="stretch"):
                        _client_dialog(existing=c)

                with delete_col:
                    if st.button("🗑️ Elimina", key=f"delete_client_{c.id}", width="stretch"):
                        st.session_state[f"confirm_delete_{c.id}"] = True
                        st.rerun()

        if st.button("➕ Nuovo cliente"):
            _client_dialog()

        if st.session_state["clients"]:
            st.write("**Alloca inventario ai clienti selezionati**")

            client_labels = {c.id: c.nome for c in st.session_state["clients"]}
            selected_ids = st.multiselect(
                "Clienti da includere in questa allocazione",
                options=list(client_labels.keys()),
                format_func=lambda cid: client_labels[cid],
            )

            if st.button("📋 Genera allocazione", type="primary"):
                if not st.session_state.get("inventory_matched"):
                    st.error("Salva prima l'inventario con il pulsante sopra.")
                elif not selected_ids:
                    st.error("Seleziona almeno un cliente.")
                else:
                    selected_clients = [c for c in st.session_state["clients"] if c.id in selected_ids]
                    allocations, unmatched = inventory.allocate(
                        st.session_state["inventory_matched"], selected_clients
                    )
                    st.session_state["allocation_text"] = inventory.format_all_summaries(
                        selected_clients, allocations
                    )
                    st.session_state["allocation_unmatched_count"] = len(unmatched)

            if st.session_state.get("allocation_text"):
                st.text_area(
                    "Riepilogo allocazione — pronto da copiare, inviare o stampare",
                    st.session_state["allocation_text"],
                    height=320,
                )

                st.download_button(
                    "⬇️ Scarica riepilogo (.txt)",
                    st.session_state["allocation_text"],
                    file_name="allocazione_clienti.txt",
                    mime="text/plain",
                )

                if st.session_state.get("allocation_unmatched_count"):
                    st.info(
                        f"{st.session_state['allocation_unmatched_count']} prodotto/i dell'inventario non "
                        "abbinati al listino non sono stati inclusi nell'allocazione."
                    )
