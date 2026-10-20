"""Einfache Ablage als JSON-Dateien im Ordner data/.

Für einen Prototyp genügt das: Die Dateien werden vom GitHub-Workflow ins
Repository zurückgeschrieben und von der Streamlit-App gelesen. Damit ist jede
Änderung über die Git-Historie nachvollziehbar.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import DATA_DIR

DATEI = DATA_DIR / "ausschreibungen.json"
STATUS = DATA_DIR / "quellen_status.json"


def _lade(pfad: Path, leer: Any) -> Any:
    if not pfad.exists():
        return leer
    with open(pfad, encoding="utf-8") as f:
        return json.load(f)


def _speichere(pfad: Path, daten: Any) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    tmp = pfad.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(daten, f, ensure_ascii=False, indent=1, sort_keys=False)
        f.write("\n")
    tmp.replace(pfad)


def lade_eintraege() -> dict[str, dict[str, Any]]:
    return {e["id"]: e for e in _lade(DATEI, [])}


def speichere_eintraege(eintraege: dict[str, dict[str, Any]]) -> None:
    liste = sorted(eintraege.values(), key=lambda e: e.get("erstmals_gefunden") or "", reverse=True)
    _speichere(DATEI, liste)


def lade_status() -> dict[str, Any]:
    return _lade(STATUS, {})


def speichere_status(status: dict[str, Any]) -> None:
    _speichere(STATUS, status)
