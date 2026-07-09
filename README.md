# wecosoft

CAAT mercuriali scraper, taxonomy-based pricing, and DDT/invoice PDF
generation for food-rescue distribution — ported from a Colab notebook into
a config-driven CLI.

## What it does

1. **`scrape`** — downloads CAAT (Turin wholesale produce market)
   "mercuriale dei prezzi" PDFs and parses them into a price-history Excel
   (`PREV_matrix`, `Dati_lunghi`, `Fonti`, `Audit_PDF`, `Avvisi_parse`,
   `Validazioni`).
2. **`price`** — maps the scraped references onto your product taxonomy
   (`data/tassonomia.csv`) and computes the sellable price list (base
   price + restaurant markup + no-quality-selection discount +
   quality-selection markup).
3. **`extract`** — exports just the `listino da usare` sheet as its own
   Excel, which `ddt` and `ozanam` read as the price list.
4. **`ddt`** — prices an existing (unpriced) DDT PDF against the price
   list, fuzzy-matching product descriptions.
5. **`ozanam`** — same as `ddt`, but the goods list comes from an
   Ozanam-format Excel instead of a PDF, with "Seconda scelta" items
   automatically priced off the lower listino column.

Every number and piece of text that used to be hardcoded in the notebook —
margins, discounts, org name/address, matching threshold, product-name
aliases, date ranges, file paths — now lives in **`config.yaml`**, which is
the file you tweak. Nothing here runs in Colab anymore, and nothing asks
for interactive input or file uploads.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .

cp config.example.yaml config.yaml
# edit config.yaml: margins, org info, matching aliases, date range, etc.

cp data/tassonomia.example.csv data/tassonomia.csv
# replace it with your real product taxonomy export. Required columns
# (matched loosely by name, so exports like Airtable's with verbose
# headers work as-is): macro category (frutta | verdura | prodotti secchi,
# case-insensitive), raggruppamento, prodotto, qualita, certificazione
# (JSON array string, e.g. ["bio"]), alias (JSON array string, e.g. ["arance"])
```

## Usage

```bash
# stage 1: scrape CAAT PDFs -> price-history Excel
wecosoft scrape

# stage 2: apply taxonomy -> priced Excel
wecosoft price

# stage 3: export the usable price list
wecosoft extract

# stages 1-3 in one go
wecosoft pipeline

# stage 4: price a DDT PDF (normale/basso/alto price column)
wecosoft ddt path/to/ddt_template.pdf --prezzo normale

# stage 5: price an Ozanam articles Excel against a DDT template
wecosoft ozanam path/to/ozanam_articoli.xlsx path/to/ddt_template.pdf
```

All commands accept `--config path/to/other-config.yaml` if you want to
keep multiple configs (e.g. one per season, or one per recipient org).

## Config reference

See `config.example.yaml` for every tweakable setting, with inline
comments. Key sections:

- `scraper` — CAAT URL, date range, output paths, worker counts
- `pricing` — taxonomy CSV path, margins/discounts, how many recent
  listini to average over
- `matching` — fuzzy-match threshold and manual product-name aliases
  (shared by pricing, `ddt`, and `ozanam`)
- `org` — sender name/address/VAT and PDF boilerplate text
- `ddt` / `ozanam` — output folders and default price column

## Notes

- `output/` and `config.yaml` are gitignored since they're
  run-specific/generated. Commit `config.example.yaml` and
  `data/tassonomia.example.csv` as the templates instead.
- The taxonomy CSV is the single most important tweak: it's what maps raw
  CAAT reference strings (e.g. `ARANCE - LANE LATE - 70-80 (6) - I - A
  PIU' STRATI - ITALIA`) onto your product catalogue.
