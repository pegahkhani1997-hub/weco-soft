"""Small JSON-file persistence for app state that must survive restarts
(client profiles, the current draft inventory) — no database needed for
a single-user tool like this."""

from __future__ import annotations

import json
from pathlib import Path


def load_json(path: str, default):
    p = Path(path)

    if not p.exists():
        return default

    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
