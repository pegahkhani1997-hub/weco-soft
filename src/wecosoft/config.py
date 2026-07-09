from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = "config.yaml"


def _parse_date(value, default):
    if value is None or value == "":
        return default
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d").date()


@dataclass
class ScraperConfig:
    base_url: str = "https://caat.it/il-mercuriale-dei-prezzi/"
    start_date: date = field(default_factory=lambda: date(2023, 1, 1))
    end_date: date = field(default_factory=date.today)
    out_dir: str = "./output/caat_mercuriali"
    pdf_subdir: str = "pdf"
    output_filename: str = "CAAT_tutti_mercuriali_PREV_COMPLETO.xlsx"
    max_workers_check: int = 6
    max_workers_download: int = 6
    known_validations: list = field(default_factory=list)

    @property
    def pdf_dir(self) -> str:
        return os.path.join(self.out_dir, self.pdf_subdir)

    @property
    def output_xlsx(self) -> str:
        return os.path.join(self.out_dir, self.output_filename)


@dataclass
class PricingConfig:
    input_xlsx: str = "./output/caat_mercuriali/CAAT_tutti_mercuriali_PREV_COMPLETO.xlsx"
    taxonomy_csv: str = "./data/tassonomia.csv"
    output_xlsx: str = "./output/caat_mercuriali/CAAT_tutti_mercuriali_PREV_COMPLETO_TASSONOMIA.xlsx"
    last_n_listini: int = 7
    margine_ristorante: float = 0.15
    sconto_senza_selezione_qualita: float = 0.20
    margine_con_selezione_qualita: float = 0.10
    include_taxonomy_rows_without_mercuriale_match: bool = True


@dataclass
class ExtractListinoConfig:
    input_xlsx: str = "./output/caat_mercuriali/CAAT_tutti_mercuriali_PREV_COMPLETO_TASSONOMIA.xlsx"
    output_xlsx: str = "./output/caat_mercuriali/LISTINO_DA_USARE.xlsx"


@dataclass
class MatchingConfig:
    soglia_match: int = 75
    manual_aliases: dict = field(default_factory=dict)


@dataclass
class OrgConfig:
    mittente_nome: str = "Eco dalle Città APS"
    mittente_indirizzo: str = "Via Maria Vittoria, 2 10123 Torino(TO)"
    mittente_piva: str = "P. IVA: 10255560012"
    causale: str = "Cessione gratuita ai sensi della L. n. 166 del 19/08/2016"
    footer_text: str = "Generato con BringTheFood"


@dataclass
class DdtConfig:
    listino_xlsx: str = "./output/caat_mercuriali/LISTINO_DA_USARE.xlsx"
    sheet_listino: str = "listino da usare"
    out_dir: str = "./output/ddt_con_prezzi"
    prezzo_default: str = "normale"


@dataclass
class OzanamConfig:
    listino_xlsx: str = "./output/caat_mercuriali/LISTINO_DA_USARE.xlsx"
    sheet_listino: str = "listino da usare"
    out_dir: str = "./output/ddt_con_prezzi"


@dataclass
class Config:
    scraper: ScraperConfig = field(default_factory=ScraperConfig)
    pricing: PricingConfig = field(default_factory=PricingConfig)
    extract_listino: ExtractListinoConfig = field(default_factory=ExtractListinoConfig)
    matching: MatchingConfig = field(default_factory=MatchingConfig)
    org: OrgConfig = field(default_factory=OrgConfig)
    ddt: DdtConfig = field(default_factory=DdtConfig)
    ozanam: OzanamConfig = field(default_factory=OzanamConfig)


def load_config(path: str | None = None) -> Config:
    path = path or DEFAULT_CONFIG_PATH

    if not Path(path).exists():
        raise FileNotFoundError(
            f"Config file not found: {path}\n"
            f"Copy config.example.yaml to config.yaml and edit it, "
            f"or pass --config <path>."
        )

    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    scraper_raw = dict(raw.get("scraper", {}))
    scraper_defaults = ScraperConfig()
    scraper_raw["start_date"] = _parse_date(
        scraper_raw.get("start_date"), scraper_defaults.start_date
    )
    scraper_raw["end_date"] = _parse_date(
        scraper_raw.get("end_date"), scraper_defaults.end_date
    )

    return Config(
        scraper=ScraperConfig(**scraper_raw),
        pricing=PricingConfig(**raw.get("pricing", {})),
        extract_listino=ExtractListinoConfig(**raw.get("extract_listino", {})),
        matching=MatchingConfig(**raw.get("matching", {})),
        org=OrgConfig(**raw.get("org", {})),
        ddt=DdtConfig(**raw.get("ddt", {})),
        ozanam=OzanamConfig(**raw.get("ozanam", {})),
    )
