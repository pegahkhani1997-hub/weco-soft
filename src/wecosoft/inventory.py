"""
Weekly inventory -> client allocation.

Flow: after a priced listino ("Filter / Tweak Output" report) exists,
the user enters this week's available products/quantities, matches them
against that price list, then allocates the inventory across whichever
saved client profiles are selected for this round.

Allocation rule (by design, confirmed with the user):
- If a product is on one or more selected clients' wishlists, its full
  available quantity is split evenly across just those clients.
- If a product isn't on anyone's wishlist, its full available quantity
  is split evenly across every selected client.
This always allocates 100% of matched inventory among the selected
clients; nothing is held back as "unclaimed".
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field

import pandas as pd

from wecosoft.ddt_common import match_referenza
from wecosoft.textutils import clean_text, norm_key


@dataclass
class Client:
    id: str
    nome: str
    indirizzo: str = ""
    telefono: str = ""
    wishlist: list = field(default_factory=list)  # list[str] of referenza names
    data_consegna: str = ""
    ora_consegna: str = ""

    @staticmethod
    def new(**kwargs) -> "Client":
        return Client(id=str(uuid.uuid4()), **kwargs)


def clients_from_dicts(items: list[dict]) -> list[Client]:
    return [Client(**item) for item in items]


def clients_to_dicts(clients: list[Client]) -> list[dict]:
    return [asdict(c) for c in clients]


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


def match_inventory(
    inventory_rows: list[dict],
    listino_df: pd.DataFrame,
    price_col: str,
    manual_aliases: dict,
    soglia_match: int,
) -> list[dict]:
    """
    inventory_rows: [{"descrizione": str, "quantita_kg": float}, ...]
    Returns the same rows enriched with referenza_matched / prezzo_unitario /
    match_score / match_metodo. Rows with no usable description or quantity
    are skipped.
    """
    results = []

    for row in inventory_rows:
        descrizione = clean_text(row.get("descrizione", ""))
        quantita_kg = row.get("quantita_kg")

        if not descrizione or quantita_kg is None:
            continue

        try:
            quantita_kg = float(quantita_kg)
        except (TypeError, ValueError):
            continue

        if quantita_kg <= 0:
            continue

        matched_row, score, metodo = match_referenza(
            descrizione, listino_df, "referenza_norm", manual_aliases, soglia_match
        )

        results.append(
            {
                "descrizione": descrizione,
                "quantita_kg": quantita_kg,
                "referenza_matched": None if matched_row is None else matched_row["referenza"],
                "prezzo_unitario": None if matched_row is None else float(matched_row[price_col]),
                "match_score": score,
                "match_metodo": metodo,
            }
        )

    return results


def allocate(matched_inventory: list[dict], clients: list[Client]) -> tuple[dict, list[dict]]:
    """
    Splits each matched inventory row across the given (already-selected)
    clients. Returns (allocations, unmatched_items) where allocations maps
    client.id -> list of {referenza, quantita_kg, prezzo_unitario, valore}.
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

        share = round(item["quantita_kg"] / len(recipients), 3)
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

    if client.indirizzo:
        lines.append(client.indirizzo)
    if client.telefono:
        lines.append(f"Tel: {client.telefono}")

    if client.data_consegna or client.ora_consegna:
        lines.append(f"Consegna: {client.data_consegna} {client.ora_consegna}".strip())

    lines.append("")

    total = 0.0
    for r in rows:
        lines.append(f"- {r['referenza']}: {format_qty(r['quantita_kg'])} kg — €{r['valore']:.2f}")
        total += r["valore"]

    if not rows:
        lines.append("(nessun prodotto allocato)")

    lines.append("")
    lines.append(f"Totale: €{total:.2f}")

    return "\n".join(lines)


def format_all_summaries(clients: list[Client], allocations: dict) -> str:
    blocks = [format_client_summary(c, allocations.get(c.id, [])) for c in clients]
    return "\n\n".join(blocks)
