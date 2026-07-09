"""
wecosoft command-line interface.

    wecosoft scrape                          # stage 1
    wecosoft price                           # stage 2
    wecosoft extract                         # stage 3
    wecosoft ddt <ddt_pdf> [--prezzo normale|basso|alto]     # stage 4
    wecosoft ozanam <articoli_xlsx> <ddt_pdf> [--listino ...]  # stage 5
    wecosoft pipeline                        # stages 1 -> 2 -> 3

Every stage reads its settings from config.yaml (or --config <path>).
"""

from __future__ import annotations

import argparse
import sys

from wecosoft.config import load_config


def _add_config_arg(parser: argparse.ArgumentParser):
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml (default: ./config.yaml)")


def cmd_scrape(args):
    from wecosoft import scraper

    cfg = load_config(args.config)
    scraper.run(cfg.scraper)


def cmd_price(args):
    from wecosoft import pricing

    cfg = load_config(args.config)
    pricing.run(cfg.pricing)


def cmd_extract(args):
    from wecosoft import extract_listino

    cfg = load_config(args.config)
    extract_listino.run(cfg.extract_listino)


def cmd_ddt(args):
    from wecosoft import ddt

    cfg = load_config(args.config)
    prezzo = args.prezzo or cfg.ddt.prezzo_default
    ddt.run(args.ddt_pdf, prezzo, cfg.ddt, cfg.matching, cfg.org)


def cmd_ozanam(args):
    from wecosoft import ozanam

    cfg = load_config(args.config)
    ozanam.run(args.articoli_xlsx, args.ddt_pdf, cfg.ozanam, cfg.matching, cfg.org, listino_xlsx=args.listino)


def cmd_pipeline(args):
    from wecosoft import extract_listino, pricing, scraper

    cfg = load_config(args.config)
    scraper.run(cfg.scraper)
    pricing.run(cfg.pricing)
    extract_listino.run(cfg.extract_listino)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wecosoft", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scrape", help="Download + parse CAAT mercuriali PDFs into a price-history Excel")
    _add_config_arg(p)
    p.set_defaults(func=cmd_scrape)

    p = sub.add_parser("price", help="Apply the taxonomy CSV and compute the sellable price list")
    _add_config_arg(p)
    p.set_defaults(func=cmd_price)

    p = sub.add_parser("extract", help="Export the 'listino da usare' sheet as its own Excel file")
    _add_config_arg(p)
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("ddt", help="Generate a priced DDT PDF from a DDT template PDF")
    _add_config_arg(p)
    p.add_argument("ddt_pdf", help="Path to the template DDT PDF (unpriced)")
    p.add_argument("--prezzo", choices=["normale", "basso", "alto"], help="Which listino price column to use")
    p.set_defaults(func=cmd_ddt)

    p = sub.add_parser("ozanam", help="Generate a priced DDT PDF from an Ozanam-format articles Excel")
    _add_config_arg(p)
    p.add_argument("articoli_xlsx", help="Path to the Ozanam articles Excel")
    p.add_argument("ddt_pdf", help="Path to the template DDT PDF (for anagrafica/header)")
    p.add_argument("--listino", default=None, help="Override the listino Excel path from config")
    p.set_defaults(func=cmd_ozanam)

    p = sub.add_parser("pipeline", help="Run scrape -> price -> extract in sequence")
    _add_config_arg(p)
    p.set_defaults(func=cmd_pipeline)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        args.func(args)
    except Exception as e:
        print(f"Errore: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
