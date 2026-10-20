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


_MUSTER: dict[str, re.Pattern[str]] = {}


def _enthaelt(text: str, begriff: str) -> bool:
    """Begriff muss am Wortanfang stehen, damit "führung" zwar "Führungsentwicklung",
    aber nicht "Einführung" oder "Durchführung" trifft. Endet der Begriff in der
    Konfiguration mit einem Leerzeichen (z. B. " ki "), muss er auch am Wortende stehen."""
    muster = _MUSTER.get(begriff)
    if muster is None:
        kern = re.escape(begriff.strip().lower())
        ende = r"(?!\w)" if begriff.endswith(" ") else ""
        muster = _MUSTER[begriff] = re.compile(r"(?<!\w)" + kern + ende)
    return bool(muster.search(text))


# Wird erhöht, wenn sich die Filterlogik ändert. Die App prüft dann bereits
# gesehene simap-Projekte erneut.
VERSION = 2


def bewerte(eintrag: dict[str, Any], profil: dict[str, Any]) -> tuple[int, list[str], str | None]:
    """Gibt (Punkte, gefundene Kernthemen, Ausschlussgrund) zurück."""
    haupttitel = _norm(eintrag.get("titel", ""))
    titel = _norm(eintrag.get("titel", "") + " " + " ".join(eintrag.get("lose") or []))
    text = _norm(eintrag.get("text", "") + " " + " ".join(eintrag.get("eignungskriterien") or []))
    kernthemen = profil.get("kernthemen") or {}

    # Ausschluss nur über den Haupttitel und nur, wenn dort kein Kernbegriff steht.
    # Früher wurden auch Lostitel geprüft: "HR-Dienstleistungen" mit einem Los
    # "Personalverleih" fiel dadurch komplett heraus.
    ausschluss = [w for w in profil.get("ausschluss_titel") or [] if _enthaelt(haupttitel, w)]
    if ausschluss:
        # Wörter mit Ausschlussbegriff entfernen ("Personalverleih" zählt nicht als "Personal")
        rest = haupttitel
        for w in ausschluss:
            rest = re.sub(r"(?<!\w)" + re.escape(w.strip().lower()) + r"\w*", " ", rest)
        if not any(_enthaelt(rest, b) for liste in kernthemen.values() for b in liste):
            return 0, [], f"Ausschlussbegriff im Titel: {ausschluss[0]}"

    punkte = 0
    text_punkte = 0
    themen: list[str] = []
    for thema, begriffe in kernthemen.items():
        if any(_enthaelt(titel, b) for b in begriffe):
            punkte += 3
            themen.append(thema)
        elif any(_enthaelt(text, b) for b in begriffe):
            themen.append(thema)  # alle Themen merken, auch wenn die Punkte gedeckelt sind
            text_punkte += 1
    punkte += min(text_punkte, 3)

    for code in eintrag.get("cpv_codes") or []:
        if any(str(code).startswith(p) for p in (profil.get("cpv_bonus") or {})):
            punkte += 3
            break

    alles = titel + text
    if any(_enthaelt(alles, w) for w in profil.get("leistungsarten") or []):
        punkte += 1

    return punkte, themen, None
