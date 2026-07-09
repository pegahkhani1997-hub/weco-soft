# wecosoft

CAAT mercuriali scraper, taxonomy-based pricing, and DDT/invoice PDF
generation for food-rescue distribution — ported from a Colab notebook into
a config-driven CLI, plus a simple two-button web app for day-to-day use.

## Web app

The easiest way to use this day-to-day. Two buttons:

- **Activate Scraper** — downloads the latest CAAT listini and gives you a
  PDF report of the most recent prices to download. No taxonomy needed.
- **Upload Taxonomy** — upload your taxonomy CSV, then **Filter / Tweak
  Output** opens a settings window to customize the exported price-list
  PDF before generating it:
  - **Time frame**: 1/2/3 weeks or 1 month, where each "week" is 7 working
    days (Mon-Fri), 2 weeks is 14, 3 weeks is 21, and 1 month is 28 —
    counted backward from yesterday (today is always excluded).
  - **Price statistic**: minimum, maximum, average, or mode (most
    frequent) price across the window's listini for each product.
  - **Discounted price columns**: type one or more percentages (e.g.
    `-20, 10, 15`) to add that many extra columns, each showing the
    chosen price statistic adjusted by that percentage.

Run it with:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
streamlit run app.py
```

Then open the URL Streamlit prints (defaults to http://localhost:8501).
Uploaded taxonomy files and generated reports are written under
`output/uploads/` and `output/reports/`.

## CLI (scripting / automation)

For unattended or scripted use, the same pipeline is available as a CLI
driven by `config.yaml`.

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
- The web app's "mode" price statistic rounds prices to the cent and picks
  the most frequently occurring value in the window; if every value in
  the window is unique (no repeats), it falls back to the median.
