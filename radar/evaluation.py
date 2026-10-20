"""Qualitätsprüfung mit einer Referenzliste.

Legen Sie config/referenz.csv an (Trennzeichen Semikolon, UTF-8):

    titel;beschreibung;relevant
    Organisationsanalyse Bauamt;Analyse der Strukturen und Prozesse ...;ja
    Reinigung Schulhaus;Unterhaltsreinigung ...;nein

Mindestens 30 bis 50 vergangene Ausschreibungen, bei denen das Team weiss,
ob sie für das IOL interessant gewesen wären. Dann:

    python -m radar.evaluation

Ausgabe: Trefferquote (wie viele relevante gefunden), Genauigkeit (wie viele
Meldungen tatsächlich relevant) und Tokenverbrauch. So lassen sich Modelle und
Schwellenwerte vergleichen, indem man LLM_MODEL bzw. LLM_BASE_URL wechselt.
"""

from __future__ import annotations

import csv
import sys

from . import bewertung, vorfilter
from .config import CONFIG_DIR, profil as lade_profil
from .llm import LLM


def main() -> int:
    pfad = CONFIG_DIR / "referenz.csv"
    if not pfad.exists():
        print(f"Referenzliste fehlt: {pfad}")
        return 1
    prof = lade_profil()
    s = prof.get("schwellen") or {}
    llm = LLM()
    zeilen = list(csv.DictReader(open(pfad, encoding="utf-8-sig"), delimiter=";"))
    tp = fp = fn = tn = 0
    for z in zeilen:
        wahr = z.get("relevant", "").strip().lower() in ("ja", "1", "true", "yes")
        e = {"titel": z["titel"], "text": z.get("beschreibung", ""), "cpv_codes": [], "gefaess": "dienstleistung"}
        punkte, _, _ = vorfilter.bewerte(e, prof)
        gemeldet = punkte >= int(s.get("vorfilter_min", 3))
        passung = None
        if gemeldet and llm.verfuegbar:
            passung = bewertung.bewerte_dienstleistung(llm, e, prof)["passung"]
            gemeldet = passung >= int(s.get("passung_melden", 60))
        tp += wahr and gemeldet
        fp += (not wahr) and gemeldet
        fn += wahr and not gemeldet
        tn += (not wahr) and not gemeldet
        marke = "OK " if wahr == gemeldet else "XX "
        print(f"{marke} relevant={'ja ' if wahr else 'nein'} gemeldet={'ja ' if gemeldet else 'nein'} "
              f"vorfilter={punkte:2d} passung={passung if passung is not None else '-':>3}  {z['titel'][:70]}")
    trefferquote = tp / (tp + fn) if tp + fn else 0
    genauigkeit = tp / (tp + fp) if tp + fp else 0
    print()
    print(f"Modell: {llm.modell if llm.verfuegbar else 'nur Vorfilter'}")
    print(f"Trefferquote (Recall):   {trefferquote:.0%}  ({tp} von {tp + fn} relevanten gefunden)")
    print(f"Genauigkeit (Precision): {genauigkeit:.0%}  ({tp} von {tp + fp} Meldungen relevant)")
    print(f"Sprachmodell: {llm.aufrufe} Aufrufe, {llm.tokens} Tokens")
    return 0


if __name__ == "__main__":
    sys.exit(main())
