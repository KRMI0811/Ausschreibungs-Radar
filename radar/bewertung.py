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
