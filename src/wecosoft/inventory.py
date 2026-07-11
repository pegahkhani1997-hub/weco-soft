"""
Weekly inventory -> client allocation.

Flow: after a priced listino ("Filter / Tweak Output" report) exists,
the user enters this week's inventory tracking sheet (received / recovered
in two passes / discarded, per product), matches each product against
that price list, then allocates the usable quantity across whichever
saved client profiles are selected for this round.

Usable quantity per product = Recuperato 1a + Recuperato 2a (the two
recovery passes), falling back to Ricevuto if neither recovery column was
filled in (so a simple "just log what came in" workflow still works).
Scartato (discarded) is tracked for the record but never allocated.

Allocation rule (by design, confirmed with the user):
- If a product is on one or more selected clients' wishlists, its full
  usable quantity is split evenly across just those clients.
- If a product isn't on anyone's wishlist, its full usable quantity is
  split evenly across every selected client.
This always allocates 100% of matched/usable inventory among the
selected clients; nothing is held back as "unclaimed".
"""

from __future__ import annotations

import dataclasses
import uuid
from dataclasses import asdict, dataclass, field

import pandas as pd

from wecosoft.ddt_common import match_referenza
from wecosoft.textutils import clean_text, norm_key

IT_WEEKDAYS = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]


@dataclass
class Client:
    id: str
    nome: str
    indirizzo: str = ""
    telefono: str = ""
    wishlist: list = field(default_factory=list)  # list[str] of referenza names
    data_consegna: str = ""  # ISO date, always a weekday (Mon-Fri)
    ora_inizio: str = ""  # "HH:MM"
    ora_fine: str = ""  # "HH:MM"

    @staticmethod
    def new(**kwargs) -> "Client":
        return Client(id=str(uuid.uuid4()), **kwargs)


def clients_from_dicts(items: list[dict]) -> list[Client]:
    """Tolerant loader: ignores unknown keys and fills in missing ones with
    defaults, so older saved client JSON (e.g. from before the delivery
    time became a range) still loads without crashing."""
    valid_fields = {f.name for f in dataclasses.fields(Client)}
    out = []

    for item in items:
        filtered = {k: v for k, v in item.items() if k in valid_fields}
        out.append(Client(**filtered))

    return out


def clients_to_dicts(clients: list[Client]) -> list[dict]:
    return [asdict(c) for c in clients]


def format_weekday_date(iso_date: str) -> str:
    """'2026-07-13' -> 'lunedì 13/07/2026'. Returns the input unchanged if
    it isn't a parseable ISO date."""
    try:
        from datetime import date as _date

        d = _date.fromisoformat(iso_date)
    except (TypeError, ValueError):
        return iso_date

    return f"{IT_WEEKDAYS[d.weekday()]} {d:%d/%m/%Y}"


def load_priced_listino(csv_path: str) -> tuple[pd.DataFrame, str]:
    """Returns (dataframe with a referenza_norm column, the price column name)."""
    df = pd.read_csv(csv_path)

    if "referenza" not in df.columns:
        raise ValueError(f"Il listino '{csv_path}' non ha una colonna 'referenza'.")

    price_cols = [c for c in df.columns if c.startswith("prezzo (")]
    if not price_cols:
        raise ValueError(f"Il listino '{csv_path}' non ha una colonna prezzo riconoscibile.")

    df = df.copy()
    df["referenza_norm"] = df["referenza"].astype(str).apply(norm_key)

    return df, price_cols[0]


def _as_float(x) -> float:
    if x is None:
        return 0.0

    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0

    return v if v > 0 else 0.0


def match_inventory(
    inventory_rows: list[dict],
    listino_df: pd.DataFrame,
    price_col: str,
    manual_aliases: dict,
    soglia_match: int,
) -> list[dict]:
    """
    inventory_rows: [{"descrizione": str, "ricevuto_kg": float,
    "recuperato_1a_kg": float, "recuperato_2a_kg": float,
    "scartato_kg": float, "note": str}, ...] — any of the numeric fields
    may be missing/None.

    Usable quantity (what actually gets priced/allocated) is
    recuperato_1a_kg + recuperato_2a_kg, falling back to ricevuto_kg if
    both recovery columns are empty/zero.

    Returns the same rows enriched with usable_kg / referenza_matched /
    prezzo_unitario / match_score / match_metodo. Rows with no usable
    description or zero usable quantity are skipped.
    """
    results = []

    for row in inventory_rows:
        descrizione = clean_text(row.get("descrizione", ""))

        if not descrizione:
            continue

        ricevuto = _as_float(row.get("ricevuto_kg"))
        recuperato_1a = _as_float(row.get("recuperato_1a_kg"))
        recuperato_2a = _as_float(row.get("recuperato_2a_kg"))
        scartato = _as_float(row.get("scartato_kg"))

        usable_kg = recuperato_1a + recuperato_2a
        if usable_kg <= 0:
            usable_kg = ricevuto

        if usable_kg <= 0:
            continue

        matched_row, score, metodo = match_referenza(
            descrizione, listino_df, "referenza_norm", manual_aliases, soglia_match
        )

        results.append(
            {
                "descrizione": descrizione,
                "ricevuto_kg": ricevuto,
                "recuperato_1a_kg": recuperato_1a,
                "recuperato_2a_kg": recuperato_2a,
                "scartato_kg": scartato,
                "note": clean_text(row.get("note", "")) if row.get("note") else "",
                "usable_kg": usable_kg,
                "referenza_matched": None if matched_row is None else matched_row["referenza"],
                "prezzo_unitario": None if matched_row is None else float(matched_row[price_col]),
                "match_score": score,
                "match_metodo": metodo,
            }
        )

    return results


def allocate(matched_inventory: list[dict], clients: list[Client]) -> tuple[dict, list[dict]]:
    """
    Splits each matched inventory row's usable_kg across the given
    (already-selected) clients. Returns (allocations, unmatched_items)
    where allocations maps client.id -> list of {referenza, quantita_kg,
    prezzo_unitario, valore}.
    """
    if not clients:
        return {}, list(matched_inventory)

    wishlist_norm = {c.id: {norm_key(w) for w in c.wishlist} for c in clients}
    allocations = {c.id: [] for c in clients}
    unmatched_items = []

    for item in matched_inventory:
        if item["referenza_matched"] is None or item["prezzo_unitario"] is None:
            unmatched_items.append(item)
            continue

        ref_norm = norm_key(item["referenza_matched"])
        interested = [c for c in clients if ref_norm in wishlist_norm[c.id]]
        recipients = interested if interested else clients

        share = round(item["usable_kg"] / len(recipients), 3)
        price = item["prezzo_unitario"]

        for c in recipients:
            allocations[c.id].append(
                {
                    "referenza": item["referenza_matched"],
                    "quantita_kg": share,
                    "prezzo_unitario": price,
                    "valore": round(share * price, 2),
                }
            )

    return allocations, unmatched_items


def format_qty(x: float) -> str:
    return f"{x:.3f}".rstrip("0").rstrip(".")


def format_client_summary(client: Client, rows: list[dict]) -> str:
    lines = [f"Cliente: {client.nome}"]

    if client.data_consegna or client.ora_inizio or client.ora_fine:
        data_str = format_weekday_date(client.data_consegna) if client.data_consegna else ""
        orario = ""

        if client.ora_inizio and client.ora_fine:
            orario = f"{client.ora_inizio}–{client.ora_fine}"
        elif client.ora_inizio:
            orario = client.ora_inizio

        lines.append(f"Consegna: {data_str} {orario}".strip())

    lines.append("")
    lines.append("Prodotto | Prezzo/kg | Prezzo prodotto")

    total_kg = 0.0
    total_valore = 0.0

    for r in rows:
        lines.append(
            f"{r['referenza']} | €{r['prezzo_unitario']:.2f}/kg | "
            f"{format_qty(r['quantita_kg'])} kg — €{r['valore']:.2f}"
        )
        total_kg += r["quantita_kg"]
        total_valore += r["valore"]

    if not rows:
        lines.append("(nessun prodotto allocato)")

    lines.append("")
    lines.append(f"Totale | {format_qty(total_kg)} kg | €{total_valore:.2f}")

    return "\n".join(lines)


def format_all_summaries(clients: list[Client], allocations: dict) -> str:
    blocks = [format_client_summary(c, allocations.get(c.id, [])) for c in clients]
    return "\n\n".join(blocks)
