"""Bewertung von Ausschreibungen durch das Sprachmodell.

Grundsatz: Harte Fakten (Frist, Auftraggeber, Link) stammen immer aus den
Quelldaten, nie aus dem Sprachmodell. Das Modell fasst zusammen und schätzt
ein. Seine Einschätzungen sind als solche gekennzeichnet und begründet.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .config import frist_lesbar, tage_bis
from .llm import LLM

log = logging.getLogger(__name__)


def _profil_text(profil: dict[str, Any]) -> str:
    inst = profil.get("institut") or {}
    themen = ", ".join((profil.get("kernthemen") or {}).keys())
    ziel = ", ".join(profil.get("zielgruppen") or [])
    return (
        f"Institut: {inst.get('name')}\n"
        f"Profil: {inst.get('kurzbeschreibung', '').strip()}\n"
        f"Kernthemen: {themen}\n"
        f"Typische Auftraggeber: {ziel}\n"
        f"Standort: {inst.get('standort')}"
    )


def rahmenfaktoren(e: dict[str, Any], profil: dict[str, Any]) -> list[str]:
    """Regelbasierte, überprüfbare Hinweise für Erfolgschance und Aufwand."""
    f: list[str] = []
    tage = tage_bis(e.get("frist"))
    if tage is not None:
        if tage < 0:
            f.append("Frist bereits abgelaufen")
        elif tage < 10:
            f.append(f"Sehr kurze Restzeit ({tage} Tage)")
        elif tage < 21:
            f.append(f"Knappe Restzeit ({tage} Tage)")
        else:
            f.append(f"Ausreichend Zeit ({tage} Tage)")
    v = e.get("verfahren") or ""
    if "offen" in v:
        f.append("Offenes Verfahren, Wettbewerb mit vielen Anbietenden möglich")
    elif "Einladung" in v:
        f.append("Einladungsverfahren, Teilnahme nur auf Einladung")
    elif "selektiv" in v:
        f.append("Selektives Verfahren mit Präqualifikation (zweistufig)")
    if e.get("publikationsart_code") in ("advance_notice", "request_for_information"):
        f.append("Frühphase (Vorankündigung bzw. Marktabklärung), gute Gelegenheit zur Positionierung")
    pg = e.get("preisgewicht")
    if isinstance(pg, (int, float)):
        f.append(f"Preis zählt {pg} %" + (", eher preisgetriebener Wettbewerb" if pg >= 50 else ""))
    if e.get("kanton") and e["kanton"] in (profil.get("institut") or {}).get("bevorzugte_kantone", []):
        f.append(f"Auftraggeber in der Region ({e['kanton']})")
    if e.get("arge_erlaubt") == "yes":
        f.append("Bietergemeinschaften erlaubt")
    if e.get("kriterien_in_unterlagen"):
        f.append("Kriterien stehen nur in den Ausschreibungsunterlagen (Download auf simap nötig)")
    return f


SPRACHEN = {"de": "Deutsch", "fr": "Französisch", "it": "Italienisch", "en": "Englisch", "rm": "Rätoromanisch"}


def aufwand_steckbrief(e: dict[str, Any]) -> dict[str, Any]:
    """Regelbasierte Aufwandsanalyse aus den standardisierten simap-Feldern.

    Jeder Punkt ist nachvollziehbar begründet. Ergebnis:
    {"stufe": "gering|mittel|hoch", "punkte": int, "gruende": [...], "fakten": [...]}
    """
    sb = e.get("steckbrief") or {}
    kriterien_text = " ".join((e.get("eignungskriterien") or []) + (e.get("zuschlagskriterien") or [])).lower()
    alles = (kriterien_text + " " + (e.get("text") or "")).lower()
    punkte = 0
    gruende: list[str] = []
    fakten: list[str] = []

    def plus(n: int, grund: str) -> None:
        nonlocal punkte
        punkte += n
        gruende.append(grund)

    n_eig = len(e.get("eignungskriterien") or [])
    n_zus = len(e.get("zuschlagskriterien") or [])
    if n_eig:
        fakten.append(f"{n_eig} Eignungskriterien")
    if n_zus:
        fakten.append(f"{n_zus} Zuschlagskriterien")
    if n_eig >= 4:
        plus(2, f"viele Eignungsnachweise ({n_eig})")
    elif n_eig >= 2:
        plus(1, f"mehrere Eignungsnachweise ({n_eig})")
    if n_zus >= 5:
        plus(1, f"viele Zuschlagskriterien ({n_zus})")
    referenzen = alles.count("referenz")
    if referenzen:
        plus(2 if referenzen >= 3 else 1, "Referenzprojekte verlangt")
    if any(w in alles for w in ("iso 9001", "iso-", "zertifiz")):
        plus(2, "Zertifizierung verlangt")
    if any(w in alles for w in ("schlüsselperson", "projektleit", "lebenslauf", "cv ", "qualifikation der")):
        plus(2, "Schlüsselpersonen mit Nachweisen")
    if any(w in alles for w in ("konzept", "lösungsvorschlag", "vorgehensvorschlag", "methodik", "umsetzungsvorschlag")):
        plus(2, "eigenes Konzept bzw. Vorgehensvorschlag")
    if any(w in alles for w in ("präsentation", "interview", "anhörung", "offertpräsentation")):
        plus(2, "Präsentation oder Interview")
    sprachen = [SPRACHEN.get(x, x) for x in sb.get("angebotssprachen") or []]
    if sprachen:
        fakten.append("Angebotssprache: " + ", ".join(sprachen))
    if len(sprachen) == 1 and sprachen[0] != "Deutsch":
        plus(2, f"Angebot nur auf {sprachen[0]}")
    if sb.get("verfahren_code") == "selective":
        plus(2, "zweistufiges Verfahren (Präqualifikation)")
    if sb.get("dialog") == "yes":
        plus(2, "Dialogverfahren")
    if (sb.get("anzahl_lose") or 0) > 1:
        fakten.append(f"{sb['anzahl_lose']} Lose")
        plus(1, "mehrere Lose")
    if sb.get("begehung") and "keine" not in sb["begehung"].lower():
        plus(1, "Begehung bzw. Informationsveranstaltung")
    pg = e.get("preisgewicht")
    if isinstance(pg, (int, float)):
        fakten.append(f"Preis zählt {pg} %")
    zeitraum = sb.get("vertragsbeginn_ende")
    if isinstance(zeitraum, list) and len(zeitraum) == 2 and all(zeitraum):
        fakten.append(f"Vertragsdauer {zeitraum[0]} bis {zeitraum[1]}")
    if sb.get("verlaengerung") == "yes":
        fakten.append("Verlängerung möglich")
    if sb.get("subunternehmer_erlaubt") == "yes" or e.get("arge_erlaubt") == "yes":
        fakten.append("Partner bzw. Bietergemeinschaft möglich")
    if e.get("kriterien_in_unterlagen"):
        plus(1, "Kriterien nur in den Unterlagen, Download nötig")

    stufe = "gering" if punkte <= 3 else ("mittel" if punkte <= 7 else "hoch")
    if not e.get("details_geladen") and not e.get("steckbrief"):
        stufe = None  # keine strukturierten Daten (z. B. Förderausschreibung)
    return {"stufe": stufe, "punkte": punkte, "gruende": gruende, "fakten": fakten}


SYSTEM_DIENSTLEISTUNG = """Du bist eine erfahrene Akquise-Fachperson eines Hochschulinstituts in der Schweiz.
Du beurteilst öffentliche Ausschreibungen nüchtern und ehrlich. Du erfindest keine Fakten.
Wenn eine Angabe im Text fehlt, schreibst du "nicht angegeben".
Antworte ausschliesslich mit einem JSON-Objekt in Schweizer Rechtschreibung (ss statt ß)."""

AUFGABE_DIENSTLEISTUNG = """Beurteile diese Ausschreibung für das folgende Institut.

=== INSTITUT ===
{profil}

=== AUSSCHREIBUNG ===
Titel: {titel}
Auftraggeber: {auftraggeber} (Kanton {kanton})
Publikationsart: {pubart} | Verfahren: {verfahren}
Eingabefrist: {frist}
CPV: {cpv}
Lose: {lose}
Beschreibung:
{text}

Eignungskriterien:
{eignung}

Zuschlagskriterien:
{zuschlag}

Regelbasierte Hinweise: {faktoren}

=== AUFGABE ===
Gib genau dieses JSON zurück:
{{
  "kurzfassung": "2 bis 3 Sätze in einfacher Sprache: Worum geht es, was soll geliefert werden?",
  "leistungen": ["konkrete gesuchte Leistungen, max. 5"],
  "anforderungen": ["wichtigste Anforderungen an Anbietende, z. B. Referenzen, Zertifikate, Sprachen, Schlüsselpersonen; max. 6"],
  "passung": 0,
  "passende_kernthemen": ["nur Kernthemen aus dem Institutsprofil"],
  "passung_begruendung": "1 bis 2 Sätze",
  "erfolgschance": "niedrig | mittel | hoch",
  "erfolgschance_begruendung": "1 bis 2 Sätze, bezogen auf Profil, Anforderungen und Verfahren",
  "aufwand": "gering | mittel | hoch",
  "aufwand_begruendung": "1 Satz zum Aufwand für die Angebotserstellung",
  "empfehlung": "bewerben prüfen | beobachten | nicht weiterverfolgen"
}}
"passung" ist eine Zahl von 0 bis 100. 80 und mehr nur, wenn die Kernleistung klar zu einem Kernthema gehört.
Reine Bau-, Liefer-, Informatik- oder Betriebsleistungen ohne Beratungsanteil erhalten höchstens 20."""


SYSTEM_FOERDERUNG = """Du bist Forschungsförderungs-Beraterin an einer Schweizer Fachhochschule.
Du beurteilst Förderausschreibungen nüchtern. Du erfindest keine Fakten, Fristen oder Beträge.
Fehlt eine Angabe, schreibst du null bzw. "nicht angegeben".
Antworte ausschliesslich mit JSON in Schweizer Rechtschreibung (ss statt ß)."""

AUFGABE_FOERDERUNG = """Beurteile diese Förderausschreibung für das folgende Institut einer Fachhochschule.

=== INSTITUT ===
{profil}

=== AUSSCHREIBUNG ===
Förderstelle: {traeger}
Titel: {titel}
Link: {link}
Text:
{text}

=== AUFGABE ===
Gib genau dieses JSON zurück:
{{
  "ist_ausschreibung": true,
  "kurzfassung": "2 bis 3 Sätze in einfacher Sprache",
  "frist": "YYYY-MM-DD oder null, nur wenn im Text eindeutig genannt",
  "foerderbetrag": "Betrag oder Rahmen laut Text, sonst nicht angegeben",
  "anforderungen": ["wer ist antragsberechtigt, Partner, Eigenleistung usw.; max. 5"],
  "fh_berechtigt": "ja | nein | unklar",
  "passung": 0,
  "passende_kernthemen": ["nur Kernthemen aus dem Institutsprofil"],
  "passung_begruendung": "1 bis 2 Sätze",
  "erfolgschance": "niedrig | mittel | hoch",
  "erfolgschance_begruendung": "1 Satz",
  "aufwand": "gering | mittel | hoch",
  "aufwand_begruendung": "1 Satz",
  "empfehlung": "bewerben prüfen | beobachten | nicht weiterverfolgen"
}}
"ist_ausschreibung" ist false, wenn der Text keine Förderausschreibung, kein Programm und kein Call ist (z. B. Personalnachricht, Jahresbericht)."""


SYSTEM_EXTRAKTION = """Du extrahierst Förderausschreibungen und Calls aus dem Text einer Webseite.
Du erfindest nichts. Nur Einträge, die im Text tatsächlich vorkommen.
Antworte ausschliesslich mit einem JSON-Array."""

AUFGABE_EXTRAKTION = """Webseite von: {traeger}
URL: {url}

Inhalt (Links stehen in der Form [Text](URL)):
{text}

Liste alle aktuellen oder kommenden Förderausschreibungen, Calls, Programme oder
Wettbewerbe auf, bei denen man Projekte oder Gesuche einreichen kann.
Ignoriere Navigation, Personalnachrichten, Jahresberichte und abgeschlossene Programme.
Format:
[{{"titel": "...", "link": "vollständige URL aus dem Text oder null", "beschreibung": "1 bis 3 Sätze aus dem Text", "frist": "YYYY-MM-DD oder null"}}]
Gibt es keine, antworte mit []."""


def _liste(x: list[str] | None, leer: str = "nicht angegeben") -> str:
    return "\n".join(f"- {z}" for z in x) if x else leer


def bewerte_dienstleistung(llm: LLM, e: dict[str, Any], profil: dict[str, Any]) -> dict[str, Any]:
    faktoren = rahmenfaktoren(e, profil)
    aw = aufwand_steckbrief(e)
    if aw["gruende"]:
        faktoren.append("Aufwandstreiber laut simap: " + ", ".join(aw["gruende"]))
    prompt = AUFGABE_DIENSTLEISTUNG.format(
        profil=_profil_text(profil),
        titel=e.get("titel"),
        auftraggeber=e.get("auftraggeber"),
        kanton=e.get("kanton") or "?",
        pubart=e.get("publikationsart"),
        verfahren=e.get("verfahren"),
        frist=frist_lesbar(e.get("frist")),
        cpv="; ".join(e.get("cpv_labels") or []) or "nicht angegeben",
        lose="; ".join(e.get("lose") or []) or "keine",
        text=(e.get("text") or "nicht angegeben")[:5000],
        eignung=_liste(e.get("eignungskriterien"))[:2500],
        zuschlag=_liste(e.get("zuschlagskriterien"))[:1500],
        faktoren="; ".join(faktoren) or "keine",
    )
    erg = llm.chat_json(SYSTEM_DIENSTLEISTUNG, prompt, "bewertung_dienstleistung")
    return _bereinige(erg, faktoren)


def bewerte_foerderung(llm: LLM, e: dict[str, Any], profil: dict[str, Any]) -> dict[str, Any]:
    prompt = AUFGABE_FOERDERUNG.format(
        profil=_profil_text(profil),
        traeger=e.get("auftraggeber"),
        titel=e.get("titel"),
        link=e.get("link_quelle"),
        text=(e.get("text") or "")[:6000],
    )
    erg = llm.chat_json(SYSTEM_FOERDERUNG, prompt, "bewertung_foerderung")
    return _bereinige(erg, rahmenfaktoren(e, profil))


def extrahiere_calls(llm: LLM, traeger: str, url: str, text: str) -> list[dict[str, Any]]:
    prompt = AUFGABE_EXTRAKTION.format(traeger=traeger, url=url, text=text[:14000])
    erg = llm.chat_json(SYSTEM_EXTRAKTION, prompt, "extraktion_webseite", max_tokens=2000)
    if isinstance(erg, dict):
        erg = erg.get("calls") or erg.get("items") or []
    return [c for c in erg if isinstance(c, dict) and c.get("titel")]


_STUFEN = {"niedrig", "mittel", "hoch"}
_AUFWAND = {"gering", "mittel", "hoch"}


def _bereinige(erg: Any, faktoren: list[str]) -> dict[str, Any]:
    if not isinstance(erg, dict):
        raise ValueError(f"Unerwartete Antwort: {json.dumps(erg)[:200]}")
    try:
        passung = int(float(erg.get("passung", 0)))
    except (TypeError, ValueError):
        passung = 0
    erg["passung"] = max(0, min(100, passung))
    if str(erg.get("erfolgschance", "")).lower() not in _STUFEN:
        erg["erfolgschance"] = "unklar"
    else:
        erg["erfolgschance"] = str(erg["erfolgschance"]).lower()
    if str(erg.get("aufwand", "")).lower() not in _AUFWAND:
        erg["aufwand"] = "unklar"
    else:
        erg["aufwand"] = str(erg["aufwand"]).lower()
    for feld in ("leistungen", "anforderungen", "passende_kernthemen"):
        if not isinstance(erg.get(feld), list):
            erg[feld] = [erg[feld]] if erg.get(feld) else []
    erg["rahmenfaktoren"] = faktoren
    return erg
