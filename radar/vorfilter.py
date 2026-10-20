"""Schneller, regelbasierter Vorfilter.

Er entscheidet, welche Treffer überhaupt an das Sprachmodell gehen. Das spart
Kosten und macht die Auswahl nachvollziehbar. Die Punkte sind bewusst einfach:

* Kernbegriff im Titel           3 Punkte je Thema
* Kernbegriff in Beschreibung    1 Punkt je Thema (max. 3 Themen)
* passender CPV-Code             3 Punkte
* Leistungsart (Beratung usw.)   1 Punkt
* Ausschlussbegriff im Titel     Treffer wird verworfen
"""

from __future__ import annotations

import re
from typing import Any


def _norm(text: str) -> str:
    return " " + re.sub(r"\s+", " ", (text or "").lower()) + " "


def _enthaelt(text: str, begriff: str) -> bool:
    return begriff.lower() in text


def bewerte(eintrag: dict[str, Any], profil: dict[str, Any]) -> tuple[int, list[str], str | None]:
    """Gibt (Punkte, gefundene Kernthemen, Ausschlussgrund) zurück."""
    titel = _norm(eintrag.get("titel", "") + " " + " ".join(eintrag.get("lose") or []))
    text = _norm(eintrag.get("text", "") + " " + " ".join(eintrag.get("eignungskriterien") or []))

    for wort in profil.get("ausschluss_titel") or []:
        if _enthaelt(titel, wort):
            return 0, [], f"Ausschlussbegriff im Titel: {wort}"

    punkte = 0
    themen: list[str] = []
    nur_text = 0
    for thema, begriffe in (profil.get("kernthemen") or {}).items():
        im_titel = any(_enthaelt(titel, b) for b in begriffe)
        im_text = any(_enthaelt(text, b) for b in begriffe)
        if im_titel:
            punkte += 3
            themen.append(thema)
        elif im_text and nur_text < 3:
            punkte += 1
            nur_text += 1
            themen.append(thema)

    for code in eintrag.get("cpv_codes") or []:
        if any(str(code).startswith(p) for p in (profil.get("cpv_bonus") or {})):
            punkte += 3
            break

    alles = titel + text
    if any(_enthaelt(alles, w) for w in profil.get("leistungsarten") or []):
        punkte += 1

    return punkte, themen, None
