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
import sys
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

def lauf_simap(eintraege, status, prof, cfg, llm, tage_override, fehler) -> list[str]:
    neu_ids: list[str] = []
    gesehen: dict[str, str] = status.setdefault("simap_gesehen", {})
    erstlauf = not status.get("simap_letzter_lauf")
    tage = tage_override or (cfg.get("tage_zurueck_erstlauf", 21) if erstlauf else cfg.get("tage_zurueck", 4))
    client = SimapClient(cfg["basis_url"], pause=float(cfg.get("pause_sekunden", 0.4)))
    min_score = int((prof.get("schwellen") or {}).get("vorfilter_min", 3))
    abrufe = 0
    gefunden = 0
    try:
        for treffer in client.suche(tage, cfg["auftragsarten"], cfg["publikationsarten"]):
            gefunden += 1
            pid, pubid = treffer["id"], treffer.get("publicationId")
            eid = f"simap:{pid}"
            if gesehen.get(pid) == pubid:
                if eid in eintraege:
                    eintraege[eid]["zuletzt_gesehen"] = jetzt_iso()
                continue
            if abrufe >= int(cfg.get("max_detailabrufe", 400)):
                fehler.append("simap: maximale Zahl an Detailabrufen erreicht, Rest folgt im nächsten Lauf")
                break
            try:
                details = client.details(pid, pubid) if pubid else None
                abrufe += 1
            except Exception as ex:
                fehler.append(f"simap Details {treffer.get('projectNumber')}: {ex}")
                continue
            e = normalisiere(treffer, details, cfg["web_link"])
            punkte, themen, ausschluss = vorfilter.bewerte(e, prof)
            gesehen[pid] = pubid
            if punkte < min_score:
                continue  # irrelevant, nur als gesehen merken
            alt = eintraege.get(eid)
            e["vorfilter_score"], e["vorfilter_themen"] = punkte, themen
            e["erstmals_gefunden"] = (alt or {}).get("erstmals_gefunden") or jetzt_iso()
            e["zuletzt_gesehen"] = jetzt_iso()
            e["gemeldet"] = (alt or {}).get("gemeldet", False)
            e["erinnert"] = (alt or {}).get("erinnert", [])
            if alt and alt.get("bewertung") and alt.get("publikationsart_code") == e.get("publikationsart_code"):
                e["bewertung"] = alt["bewertung"]  # nur Korrektur, keine neue Bewertung nötig
            else:
                _bewerte(llm, e, prof, fehler)
                if alt and alt.get("publikationsart_code") != e.get("publikationsart_code"):
                    e["gemeldet"] = False  # z. B. aus Vorankündigung wurde Ausschreibung
            eintraege[eid] = e
            neu_ids.append(eid)
    except Exception as ex:
        fehler.append(f"simap: {ex}")
        log.exception("simap-Lauf abgebrochen")
    # Gesehen-Liste begrenzen
    if len(gesehen) > MAX_GESEHEN:
        for k in list(gesehen)[: len(gesehen) - MAX_GESEHEN]:
            del gesehen[k]
    status["simap_letzter_lauf"] = jetzt_iso()
    status["simap_letzte_statistik"] = {"treffer": gefunden, "detailabrufe": abrufe, "relevant_vorfilter": len(neu_ids)}
    log.info("simap: %s Projekte im Zeitraum, %s Details geladen, %s im Vorfilter", gefunden, abrufe, len(neu_ids))
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
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    prof, cfg = lade_profil(), lade_quellen()
    eintraege, status = speicher.lade_eintraege(), speicher.lade_status()
    llm = LLM()
    if not llm.verfuegbar:
        log.warning("Kein LLM_API_KEY gesetzt. Es wird nur der regelbasierte Vorfilter genutzt.")
    fehler: list[str] = []

    if args.nur in (None, "simap") and (cfg.get("simap") or {}).get("aktiv", True):
        lauf_simap(eintraege, status, prof, cfg["simap"], llm, args.tage, fehler)
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
