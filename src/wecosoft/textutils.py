from __future__ import annotations

import re
import unicodedata


def clean_text(s) -> str:
    return re.sub(r"\s+", " ", str(s).strip())


def strip_accents(s) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", str(s)) if not unicodedata.combining(c)
    )


def norm_key(s) -> str:
    s = strip_accents(str(s)).lower()
    s = s.replace("’", "'")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def filename_text(s) -> str:
    s = str(s)
    s = s.replace("/", " - ")
    s = re.sub(r'[\\:*?"<>|]', "-", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s+-\s+", " - ", s)
    return s[:180]


def parse_float_it(s) -> float | None:
    s = str(s).strip()
    s = s.replace("Kg", "").replace("kg", "")
    s = s.replace(",", ".")
    s = re.sub(r"[^0-9.\-]", "", s)

    if not s:
        return None

    return float(s)
