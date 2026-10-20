"""Hauptlauf des Ausschreibungsradars.

Aufruf:
    python -m radar.run                 # kompletter Lauf mit Versand
    python -m radar.run --ohne-versand  # nur sammeln und bewerten
    python -m radar.run --nur simap     # nur eine Quelle (simap | foerderung)
    python -m radar.run --tage 30       # Suchzeitraum für simap überschreiben
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from typing import Any

from . import benachrichtigung, bewertung, foerder, speicher, vorfilter
from .config import jetzt_iso, profil as lade_profil, quellen as lade_quellen, tage_bis
from .llm import LLM
from .simap import SimapClient, normalisiere

log = logging.getLogger("radar")
MAX_GESEHEN = 6000


def _relevant(e: dict[str, Any], prof: dict[str, Any], llm_aktiv: bool) -> bool:
    s = prof.get("schwellen") or {}
    b = e.get("bewertung")
    if b:
        if b.get("ist_ausschreibung") is False:
            return False
        return int(b.get("passung", 0)) >= int(s.get("passung_melden", 60))
    # Ohne Sprachmodell entscheidet der Vorfilter
    return not llm_aktiv and e.get("vorfilter_score", 0) >= int(s.get("vorfilter_min", 3))


def _bewerte(llm: LLM, e: dict[str, Any], prof: dict[str, Any], fehler: list[str]) -> None:
    if not llm.verfuegbar:
        return
    try:
        if e["gefaess"] == "dienstleistung":
            e["bewertung"] = bewertung.bewerte_dienstleistung(llm, e, prof)
        else:
            e["bewertung"] = bewertung.bewerte_foerderung(llm, e, prof)
            b = e["bewertung"]
            # Frist aus Text nur übernehmen, wenn keine aus den Quelldaten vorliegt
            if not e.get("frist") and isinstance(b.get("frist"), str) and len(b["frist"]) == 10:
                e["frist"] = b["frist"]
                e["frist_quelle"] = "aus Text extrahiert, bitte prüfen"
        e["bewertet_mit"] = llm.modell
        e["bewertet_am"] = jetzt_iso()
    except Exception as ex:  # ein einzelner Fehler soll den Lauf nicht stoppen
        fehler.append(f"Bewertung '{e.get('titel', '')[:60]}': {ex}")
        log.warning("Bewertung fehlgeschlagen: %s", ex)


# ---------------------------------------------------------------- simap

# Zu allgemeine Begriffe liefern in der simap-Volltextsuche zu viel Rauschen
SUCH_STOPP = {"personal", "digital", "zusammenarbeit", "change", "kader", "esg", "coaching", "teilzeit",
              "assessment", "compliance", "leitbild", "fachkräfte", "human resources"}
# Zusätzliche Suchbegriffe, die in Titeln öffentlicher Ausschreibungen häufig vorkommen
SUCH_ZUSATZ = ["HR-Dienstleistungen", "HR-Beratung", "Personaldienstleistungen", "Personalberatung",
               "Organisationsberatung", "Beratungsleistungen", "Beratungsmandat", "Führungsausbildung",
               "Kaderentwicklung", "Weiterbildung", "Evaluation", "Mitarbeitendenbefragung", "Personalgewinnung"]
SUCH_TAGE = 150        # Volltextsuche: Projekte der letzten 150 Tage, entscheidend ist danach die Frist
MIN_DETAILABRUFE = 1000
ZWISCHENSPEICHERN_ALLE = 25   # nach so vielen geprüften Projekten wird gespeichert


class Zeitbudget:
    """Sorgt dafür, dass ein Lauf geordnet endet, bevor GitHub ihn abbricht.
    Was nicht mehr geschafft wird, holt der nächste Lauf nach."""

    def __init__(self, minuten: float):
        self.ende = time.monotonic() + minuten * 60
        self.minuten = minuten

    @property
    def abgelaufen(self) -> bool:
        return time.monotonic() > self.ende


def zwischenspeichern(eintraege, status) -> None:
    speicher.speichere_eintraege(eintraege)
    speicher.speichere_status(status)


def suchbegriffe(prof: dict[str, Any]) -> list[str]:
    begriffe: list[str] = []
    for liste in (prof.get("kernthemen") or {}).values():
        for b in liste:
            t = b.strip().strip("-").strip()
            if len(t) >= 4 and t.lower() not in SUCH_STOPP:
                begriffe.append(t)
    begriffe += list(prof.get("simap_suchbegriffe") or []) + SUCH_ZUSATZ
    eindeutig: dict[str, str] = {}
    for b in begriffe:
        eindeutig.setdefault(b.lower(), b)
    return list(eindeutig.values())


def lauf_simap(eintraege, status, prof, cfg, llm, tage_override, fehler, budget: Zeitbudget | None = None) -> list[str]:
    """Findet alle noch offenen, passenden Ausschreibungen.

    Zwei Wege ergänzen sich:
    1. Volltextsuche auf simap mit allen Kernbegriffen über die letzten 150 Tage.
       Damit werden auch Ausschreibungen gefunden, die schon länger publiziert,
       aber noch offen sind.
    2. Durchsicht aller neuen Dienstleistungen der letzten Tage, damit auch
       Ausschreibungen mit unscheinbarem Titel nicht verloren gehen.
    Massgeblich ist danach die Eingabefrist: Abgelaufenes wird verworfen.
    """
    neu_ids: list[str] = []
    gesehen: dict[str, str] = status.setdefault("simap_gesehen", {})
    erstlauf = not status.get("simap_letzter_lauf")
    tage = tage_override or (cfg.get("tage_zurueck_erstlauf", 21) if erstlauf else cfg.get("tage_zurueck", 4))
    client = SimapClient(cfg["basis_url"], pause=float(cfg.get("pause_sekunden", 0.4)))
    min_score = int((prof.get("schwellen") or {}).get("vorfilter_min", 3))
    max_abrufe = max(int(cfg.get("max_detailabrufe", 0)), MIN_DETAILABRUFE)
    abrufe = verworfen_frist = 0

    # 1. Kandidaten sammeln, Stichworttreffer zuerst
    kandidaten: dict[str, dict[str, Any]] = {}
    begriffe = suchbegriffe(prof)
    fehlgeschlagen: list[str] = []
    for begriff in begriffe:
        if budget and budget.abgelaufen:
            fehler.append("simap: Zeitbudget während der Stichwortsuche erreicht, Rest folgt im nächsten Lauf")
            break
        try:
            for t in client.suche(SUCH_TAGE, cfg["auftragsarten"], cfg["publikationsarten"],
                                  max_seiten=5, suchbegriff=begriff):
                kandidaten.setdefault(t["id"], t)
        except Exception as ex:
            fehlgeschlagen.append(begriff)
            log.info("simap Suche '%s' fehlgeschlagen: %s", begriff, ex)
    if fehlgeschlagen:
        fehler.append(f"simap: Suche für {len(fehlgeschlagen)} von {len(begriffe)} Begriffen fehlgeschlagen "
                      f"(z. B. {', '.join(fehlgeschlagen[:3])})")
    stichwort = len(kandidaten)
    try:
        for t in client.suche(tage, cfg["auftragsarten"], cfg["publikationsarten"]):
            kandidaten.setdefault(t["id"], t)
    except Exception as ex:
        fehler.append(f"simap Datumssuche: {ex}")
    log.info("simap: %s Projekte gefunden (%s über %s Suchbegriffe, Rest aus den letzten %s Tagen)",
             len(kandidaten), stichwort, len(begriffe), tage)

    # 2. Details laden, nach Frist und Vorfilter entscheiden
    geprueft = 0
    for treffer in kandidaten.values():
        if budget and budget.abgelaufen:
            fehler.append(f"simap: Zeitbudget von {budget.minuten:.0f} Minuten erreicht, "
                          "die restlichen Projekte prüft der nächste Lauf")
            break
        geprueft += 1
        if geprueft % ZWISCHENSPEICHERN_ALLE == 0:
            zwischenspeichern(eintraege, status)
            log.info("simap: %s von %s Projekten geprüft, Zwischenstand gespeichert", geprueft, len(kandidaten))
        pid, pubid = treffer["id"], treffer.get("publicationId")
        eid = f"simap:{pid}"
        if gesehen.get(pid) == pubid:
            if eid in eintraege:
                eintraege[eid]["zuletzt_gesehen"] = jetzt_iso()
            continue
        if abrufe >= max_abrufe:
            fehler.append("simap: maximale Zahl an Detailabrufen erreicht, Rest folgt im nächsten Lauf")
            break
        try:
            details = client.details(pid, pubid) if pubid else None
            abrufe += 1
        except Exception as ex:
            fehler.append(f"simap Details {treffer.get('projectNumber')}: {ex}")
            continue
        e = normalisiere(treffer, details, cfg["web_link"])
        gesehen[pid] = pubid
        rest = tage_bis(e.get("frist"))
        if rest is not None and rest < 0:
            verworfen_frist += 1
            continue  # Eingabefrist abgelaufen
        if rest is None and (tage_bis(e.get("publiziert_am")) or 0) < -120:
            verworfen_frist += 1
            continue  # ohne Frist und älter als vier Monate
        punkte, themen, ausschluss = vorfilter.bewerte(e, prof)
        if punkte < min_score:
            continue
        alt = eintraege.get(eid)
        e["vorfilter_score"], e["vorfilter_themen"] = punkte, themen
        e["erstmals_gefunden"] = (alt or {}).get("erstmals_gefunden") or jetzt_iso()
        e["zuletzt_gesehen"] = jetzt_iso()
        e["gemeldet"] = (alt or {}).get("gemeldet", False)
        e["erinnert"] = (alt or {}).get("erinnert", [])
        if alt and alt.get("bewertung") and alt.get("publikationsart_code") == e.get("publikationsart_code"):
            e["bewertung"] = alt["bewertung"]
        else:
            _bewerte(llm, e, prof, fehler)
            if alt and alt.get("publikationsart_code") != e.get("publikationsart_code"):
                e["gemeldet"] = False  # z. B. aus Vorankündigung wurde Ausschreibung
        eintraege[eid] = e
        neu_ids.append(eid)

    if len(gesehen) > MAX_GESEHEN:
        for k in list(gesehen)[: len(gesehen) - MAX_GESEHEN]:
            del gesehen[k]
    status["simap_letzter_lauf"] = jetzt_iso()
    status["simap_letzte_statistik"] = {
        "treffer": len(kandidaten), "detailabrufe": abrufe,
        "relevant_vorfilter": len(neu_ids), "frist_abgelaufen": verworfen_frist,
    }
    log.info("simap: %s Details geladen, %s mit abgelaufener Frist verworfen, %s im Vorfilter",
             abrufe, verworfen_frist, len(neu_ids))
    return neu_ids


# ---------------------------------------------------------------- Förderung

def lauf_foerderung(eintraege, status, prof, quellen_liste, llm, fehler) -> list[str]:
    neu_ids: list[str] = []
    seiten: dict[str, str] = status.setdefault("seiten_hash", {})
    for q in quellen_liste:
        if q.get("aktiv") is False:
            continue
        try:
            if q["typ"] == "rss":
                kandidaten = foerder.lese_rss(q)
            elif q["typ"] == "webseite":
                text = foerder.seite_als_text(foerder.lade(q["url"]).text, q["url"])
                if len(text) < 200:
                    fehler.append(f"{q['name']}: Seite liefert kaum Text (evtl. JavaScript nötig)")
                    continue
                h = foerder.kurz_hash(text)
                if seiten.get(q["url"]) == h:
                    continue  # unverändert
                if not llm.verfuegbar:
                    fehler.append(f"{q['name']}: Seite geändert, ohne Sprachmodell keine Auswertung")
                    continue
                calls = bewertung.extrahiere_calls(llm, q["traeger"], q["url"], text)
                kandidaten = [foerder.webseiten_eintrag(q, c) for c in calls]
                seiten[q["url"]] = h
            else:
                fehler.append(f"{q['name']}: unbekannter Typ {q['typ']}")
                continue
        except Exception as ex:
            fehler.append(f"{q['name']}: {ex}")
            log.warning("Quelle %s fehlgeschlagen: %s", q["name"], ex)
            continue

        for e in kandidaten:
            if e["id"] in eintraege:
                eintraege[e["id"]]["zuletzt_gesehen"] = jetzt_iso()
                continue
            if q["typ"] == "rss":
                foerder.ergaenze_volltext(e)
            punkte, themen, _ = vorfilter.bewerte(e, prof)
            e.update(
                vorfilter_score=punkte,
                vorfilter_themen=themen,
                erstmals_gefunden=jetzt_iso(),
                zuletzt_gesehen=jetzt_iso(),
                gemeldet=False,
                erinnert=[],
            )
            _bewerte(llm, e, prof, fehler)  # Förderausschreibungen sind wenige, daher immer bewerten
            eintraege[e["id"]] = e
            neu_ids.append(e["id"])
        log.info("%s: %s Kandidaten", q["name"], len(kandidaten))
    return neu_ids


# ---------------------------------------------------------------- Ablauf

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ausschreibungsradar IOL")
    ap.add_argument("--ohne-versand", action="store_true", help="keine E-Mail und keine Teams-Nachricht senden")
    ap.add_argument("--nur", choices=["simap", "foerderung"], help="nur eine Quelle abfragen")
    ap.add_argument("--tage", type=int, help="Suchzeitraum für simap in Tagen")
    ap.add_argument("--max-minuten", type=float, default=float(os.environ.get("RADAR_MAX_MINUTEN", 90)),
                    help="Zeitbudget; danach wird geordnet gespeichert und beendet (Standard 90)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    prof, cfg = lade_profil(), lade_quellen()
    eintraege, status = speicher.lade_eintraege(), speicher.lade_status()
    llm = LLM()
    budget = Zeitbudget(args.max_minuten)
    if not llm.verfuegbar:
        log.warning("Kein LLM_API_KEY gesetzt. Es wird nur der regelbasierte Vorfilter genutzt.")
    fehler: list[str] = []

    if args.nur in (None, "simap") and (cfg.get("simap") or {}).get("aktiv", True):
        lauf_simap(eintraege, status, prof, cfg["simap"], llm, args.tage, fehler, budget)
        zwischenspeichern(eintraege, status)
    if args.nur in (None, "foerderung"):
        lauf_foerderung(eintraege, status, prof, cfg.get("foerderung") or [], llm, fehler)

    # Was wird gemeldet?
    melden = [
        e
        for e in eintraege.values()
        if not e.get("gemeldet") and _relevant(e, prof, llm.verfuegbar) and (tage_bis(e.get("frist")) or 0) >= 0
    ]
    erinnerungen: list[tuple[dict[str, Any], int]] = []
    stufen = (prof.get("schwellen") or {}).get("erinnerung_tage", [14, 7, 3])
    for e in eintraege.values():
        t = tage_bis(e.get("frist"))
        if t is None or not e.get("gemeldet") or not _relevant(e, prof, llm.verfuegbar):
            continue
        # fällige Stufe: kleinste Stufe >= Resttage, die noch nicht erinnert wurde
        offen = [s for s in stufen if t <= s and s not in e.get("erinnert", [])]
        if offen and t >= 0:
            erinnerungen.append((e, t))
            e.setdefault("erinnert", []).extend(offen)
    erinnerungen.sort(key=lambda x: x[1])

    status["letzter_lauf"] = jetzt_iso()
    status["letzte_fehler"] = fehler
    status["llm"] = {"modell": llm.modell if llm.verfuegbar else None, "aufrufe": llm.aufrufe, "tokens": llm.tokens}

    if melden or erinnerungen:
        betreff, html_text, klartext = benachrichtigung.baue_bericht(melden, erinnerungen)
        if fehler:
            html_text += "<p style='color:#888;font-size:12px'>Hinweise aus dem Lauf: " + "; ".join(fehler[:10]) + "</p>"
        benachrichtigung.speichere_bericht(html_text)
        versendet = False
        if not args.ohne_versand:
            for sender in (lambda: benachrichtigung.sende_mail(betreff, html_text, klartext),
                           lambda: benachrichtigung.sende_teams(melden, erinnerungen)):
                try:
                    versendet = sender() or versendet
                except Exception as ex:
                    fehler.append(f"Versand: {ex}")
                    log.warning("Versand fehlgeschlagen: %s", ex)
        # Als gemeldet markieren, wenn versendet oder kein Versandkanal eingerichtet ist
        if versendet or args.ohne_versand or not _versand_konfiguriert():
            for e in melden:
                e["gemeldet"] = True
                e["gemeldet_am"] = jetzt_iso()
                # Stufen, deren Zeitpunkt schon erreicht ist, deckt die Erstmeldung ab
                t = tage_bis(e.get("frist"))
                if t is not None:
                    e["erinnert"] = sorted(set(e.get("erinnert", [])) | {s for s in stufen if t <= s})
        log.info("Bericht: %s neu, %s Erinnerungen, versendet: %s", len(melden), len(erinnerungen), versendet)
    else:
        log.info("Nichts Neues zu melden")

    speicher.speichere_eintraege(eintraege)
    speicher.speichere_status(status)
    for f in fehler:
        log.warning("Hinweis: %s", f)
    return 0


def _versand_konfiguriert() -> bool:
    from .config import env

    return bool((env("SMTP_HOST") and env("MAIL_TO")) or env("TEAMS_WEBHOOK_URL"))


if __name__ == "__main__":
    sys.exit(main())
