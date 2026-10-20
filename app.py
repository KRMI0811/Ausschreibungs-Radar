"""Findus, der Ausschreibungsradar des IOL (Streamlit-App).

Bedienung für alle Nutzenden ohne GitHub-Konto:
  1. Bereich wählen (gesamtes Institut oder ein Kompetenzzentrum)
  2. Art wählen (Dienstleistung, Forschung und Förderung oder beides)
  3. Rangliste ansehen, Details öffnen sich als Fenster

Streamlit-Secrets (Einstellungen der App, nur durch die Administration):
    APP_PASSWORT = "..."    optionaler Passwortschutz
    LLM_API_KEY  = "..."    derselbe Public-AI-Schlüssel wie in GitHub;
                            nötig, damit "Jetzt neu suchen" auch bewertet

Datenhaltung: Der tägliche GitHub-Lauf schreibt nach data/. Die App arbeitet in
einer Kopie (Arbeitsordner), damit eine Suche aus der App das Repository nicht
verändert. Beim Start übernimmt die App jeweils den neueren Stand.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import shutil
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st

# Arbeitsordner festlegen, bevor das Paket radar geladen wird
REPO_DATEN = Path(__file__).resolve().parent / "data"
ARBEIT = Path(tempfile.gettempdir()) / "findus_daten"
os.environ["RADAR_DATA_DIR"] = str(ARBEIT)

from radar.config import ZEITZONE, frist_lesbar, profil as lade_profil, tage_bis  # noqa: E402

st.set_page_config(page_title="Findus · Ausschreibungsradar IOL", page_icon="📡", layout="centered")

DATEIEN = ("ausschreibungen.json", "quellen_status.json", "nutzung.jsonl")
CHANCE = {"hoch": "🟢 hoch", "mittel": "🟡 mittel", "niedrig": "🔴 niedrig", "unklar": "⚪ unklar"}
ART = {"dienstleistung": "🏛️ Dienstleistung", "forschung": "🔬 Forschung"}


def secret(name: str) -> str | None:
    try:
        wert = st.secrets.get(name)
    except Exception:
        wert = None
    return str(wert).strip() if wert else None


# ---------------------------------------------------------------- Passwort

if secret("APP_PASSWORT") and not st.session_state.get("angemeldet"):
    st.title("📡 Findus")
    st.caption("Der Ausschreibungsradar des IOL")
    pw = st.text_input("Passwort", type="password")
    if pw:
        if hmac.compare_digest(pw.strip(), secret("APP_PASSWORT")):
            st.session_state["angemeldet"] = True
            st.rerun()
        st.error("Passwort falsch.")
    st.stop()


# ---------------------------------------------------------------- Daten

def _stand(ordner: Path) -> str:
    p = ordner / "quellen_status.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("letzter_lauf") or ""
    except Exception:
        return ""


def synchronisiere() -> None:
    """Übernimmt den Stand aus dem Repository, wenn er neuer ist als die Arbeitskopie."""
    ARBEIT.mkdir(parents=True, exist_ok=True)
    if _stand(REPO_DATEN) > _stand(ARBEIT):
        for name in DATEIEN:
            if (REPO_DATEN / name).exists():
                shutil.copy2(REPO_DATEN / name, ARBEIT / name)


@st.cache_data(ttl=60)
def lade_daten(_marke: str) -> tuple[list[dict], dict]:
    def lies(name, leer):
        p = ARBEIT / name
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else leer

    return lies("ausschreibungen.json", []), lies("quellen_status.json", {})


@st.cache_resource
def suchsperre() -> threading.Lock:
    return threading.Lock()


class _AnzeigeHandler(logging.Handler):
    """Zeigt Fortschrittsmeldungen des Suchlaufs im Statusfenster."""

    def __init__(self, ziel):
        super().__init__(logging.INFO)
        self.ziel = ziel

    def emit(self, record):
        text = record.getMessage()
        if any(w in text for w in ("simap:", "Kandidaten", "Bericht", "Nichts Neues")):
            self.ziel.write("· " + text)


def suche_starten() -> None:
    sperre = suchsperre()
    if not sperre.acquire(blocking=False):
        st.warning("Es läuft bereits eine Suche. Bitte in einigen Minuten die Seite neu laden.")
        return
    try:
        if secret("LLM_API_KEY"):
            os.environ["LLM_API_KEY"] = secret("LLM_API_KEY")
        from radar import run

        with st.status("Findus sucht … das dauert meist 2 bis 8 Minuten.", expanded=True) as box:
            handler = _AnzeigeHandler(box)
            logging.getLogger().addHandler(handler)
            try:
                run.main(["--ohne-versand", "--tage", "7"])
                box.update(label="Suche abgeschlossen", state="complete", expanded=False)
            except Exception as ex:
                box.update(label=f"Suche mit Fehler beendet: {ex}", state="error")
            finally:
                logging.getLogger().removeHandler(handler)
        lade_daten.clear()
    finally:
        sperre.release()


# ---------------------------------------------------------------- Bewertung und Darstellung

def sterne(wert: int | None) -> str:
    if wert is None:
        return "☆☆☆☆☆"
    n = max(1, min(5, round(wert / 20)))
    return "★" * n + "☆" * (5 - n)


def frist_text(e: dict) -> str:
    t = tage_bis(e.get("frist"))
    if t is None:
        return "Frist nicht angegeben"
    text = frist_lesbar(e["frist"])
    text += " · heute" if t == 0 else f" · noch {t} Tage"
    if e.get("frist_quelle") and e["frist_quelle"] != "simap":
        text += " (bitte prüfen)"
    return text


def themen_von(e: dict) -> set[str]:
    b = e.get("bewertung") or {}
    return set(b.get("passende_kernthemen") or e.get("vorfilter_themen") or [])


def kalender(e: dict) -> str | None:
    t = tage_bis(e.get("frist"))
    if t is None or t < 0:
        return None
    tag = datetime.now(ZEITZONE).date() + timedelta(days=t)
    titel = (e.get("titel") or "").replace("\n", " ").replace(",", "\\,").replace(";", "\\;")[:120]
    return "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//IOL//Findus//DE", "BEGIN:VEVENT",
        f"UID:{e['id'].replace(':', '-')}@findus",
        f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        f"DTSTART;VALUE=DATE:{tag.strftime('%Y%m%d')}",
        f"SUMMARY:Eingabefrist: {titel}", f"DESCRIPTION:{e.get('link_quelle', '')}",
        "BEGIN:VALARM", "TRIGGER:-P7D", "ACTION:DISPLAY", "DESCRIPTION:Eingabefrist in 7 Tagen", "END:VALARM",
        "END:VEVENT", "END:VCALENDAR", "",
    ])


@st.dialog("Ausschreibung im Detail", width="large")
def detailfenster(e: dict) -> None:
    b = e.get("bewertung") or {}
    st.markdown(f"### {e.get('titel')}")
    st.caption(" · ".join(str(x) for x in [ART.get(e["gefaess"]), e.get("auftraggeber"), e.get("kanton"),
                                           e.get("publikationsart"), e.get("verfahren")] if x))
    c1, c2, c3 = st.columns(3)
    c1.metric("Bewertung", sterne(passung(e)), help=f"Passung {passung(e)} von 100" + ("" if b else ", vorläufig ohne Sprachmodell"))
    c2.metric("Erfolgschance", (b.get("erfolgschance") or "unklar").capitalize())
    c3.metric("Aufwand", (b.get("aufwand") or "unklar").capitalize())
    st.markdown(f"**📅 Eingabefrist:** {frist_text(e)}")
    st.markdown("**Worum geht es?**")
    st.write(b.get("kurzfassung") or (e.get("text") or "")[:600])
    if b.get("anforderungen"):
        st.markdown("**Was wird verlangt?**")
        st.markdown("\n".join(f"- {a}" for a in b["anforderungen"]))
    if b.get("foerderbetrag") and b["foerderbetrag"] != "nicht angegeben":
        st.markdown(f"**Förderbetrag:** {b['foerderbetrag']}")
    begr = [f"**{n}:** {b[f]}" for n, f in (("Passung", "passung_begruendung"),
                                            ("Erfolgschance", "erfolgschance_begruendung"),
                                            ("Aufwand", "aufwand_begruendung")) if b.get(f)]
    if begr:
        st.markdown("**Warum diese Einschätzung?**")
        st.markdown("  \n".join(begr))
    k1, k2, k3 = st.columns(3)
    k1.link_button("Zur Ausschreibung", e.get("link_quelle") or "#", width="stretch", type="primary")
    if e.get("link_organisation"):
        k2.link_button("Website der Organisation", e["link_organisation"], width="stretch")
    if kal := kalender(e):
        k3.download_button("Frist in Kalender", kal, "eingabefrist.ics", "text/calendar", width="stretch")
    with st.expander("Weitere Angaben"):
        if e.get("eignungskriterien"):
            st.markdown("**Eignungskriterien**")
            st.markdown("\n".join(f"- {a[:400]}" for a in e["eignungskriterien"]))
        if e.get("zuschlagskriterien"):
            st.markdown("**Zuschlagskriterien**")
            st.markdown("\n".join(f"- {a[:300]}" for a in e["zuschlagskriterien"]))
        if e.get("kontakt_email"):
            st.markdown(f"**Kontakt:** {e['kontakt_email']}")
        if e.get("text"):
            st.text(e["text"][:2500])
    st.caption("Kurzfassung und Einschätzungen erstellt ein Sprachmodell. Massgeblich ist die Originalausschreibung.")


def zeile(rang: int, e: dict) -> None:
    b = e.get("bewertung") or {}
    with st.container(border=True):
        links, rechts = st.columns([5, 1], vertical_alignment="center")
        with links:
            st.markdown(f"**{rang}. {e.get('titel')}**")
            st.caption(f"{ART.get(e['gefaess'], '')} · {e.get('auftraggeber') or ''} · 📅 {frist_text(e)}")
            st.markdown(f"{sterne(passung(e))} &nbsp; Chance {CHANCE.get(b.get('erfolgschance'), '⚪ unklar')}"
                        f" &nbsp; Aufwand {b.get('aufwand') or 'unklar'}")
            kurz = b.get("kurzfassung") or (e.get("text") or "")[:220]
            st.write(kurz[:260] + ("…" if len(kurz) > 260 else ""))
        with rechts:
            if st.button("Details", key=f"d-{e['id']}", width="stretch"):
                detailfenster(e)


# ---------------------------------------------------------------- Seite

synchronisiere()
daten, status = lade_daten(_stand(ARBEIT))
prof = lade_profil()
schwelle = int((prof.get("schwellen") or {}).get("passung_melden", 60))
zentren = prof.get("kompetenzzentren") or {}

st.title("📡 Findus")
st.caption("Der Ausschreibungsradar des IOL")

bereiche = ["Gesamtes Institut"] + list(zentren)
bereich = st.segmented_control("Bereich", bereiche, default=bereiche[0], label_visibility="collapsed") or bereiche[0]
art = st.segmented_control("Art", ["Alle", "Dienstleistung", "Forschung und Förderung"], default="Alle",
                           label_visibility="collapsed") or "Alle"


VORFILTER_MIN = int((prof.get("schwellen") or {}).get("vorfilter_min", 3))


def passung(e: dict) -> int:
    """Passung laut Sprachmodell. Ohne Bewertung eine grobe Schätzung aus dem Vorfilter."""
    b = e.get("bewertung") or {}
    if b:
        return int(b.get("passung", 0))
    return min(70, e.get("vorfilter_score", 0) * 10)


def gehoert_dazu(e: dict) -> bool:
    b = e.get("bewertung") or {}
    if b.get("ist_ausschreibung") is False or b.get("empfehlung") == "nicht weiterverfolgen":
        return False
    if not b and e.get("vorfilter_score", 0) < VORFILTER_MIN:
        return False
    t = tage_bis(e.get("frist"))
    if t is not None and t < 0:
        return False
    if art == "Dienstleistung" and e["gefaess"] != "dienstleistung":
        return False
    if art == "Forschung und Förderung" and e["gefaess"] != "forschung":
        return False
    if bereich != "Gesamtes Institut" and not themen_von(e) & set(zentren[bereich].get("themen") or []):
        return False
    return True


kandidaten = sorted([e for e in daten if gehoert_dazu(e)],
                    key=lambda e: (-passung(e), tage_bis(e.get("frist")) or 999))
passend = [e for e in kandidaten if passung(e) >= schwelle]
weitere = [e for e in kandidaten if e not in passend]

st.divider()
if not daten:
    st.info("Noch keine Ausschreibungen geladen. Klicken Sie unten auf **Jetzt neu suchen**. "
            "Danach aktualisiert sich Findus jeden Morgen automatisch.")
else:
    st.markdown(f"#### {len(passend)} passende Ausschreibung{'en' if len(passend) != 1 else ''}")
    if bereich != "Gesamtes Institut" and zentren[bereich].get("beschreibung"):
        st.caption(zentren[bereich]["beschreibung"])
    if not passend:
        st.write("Aktuell keine Ausschreibung mit hoher Passung in dieser Auswahl.")
    for i, e in enumerate(passend, 1):
        zeile(i, e)
    if weitere:
        with st.expander(f"Weniger passende anzeigen ({len(weitere)})"):
            for i, e in enumerate(weitere, len(passend) + 1):
                zeile(i, e)

st.divider()
stand = status.get("letzter_lauf")
stand_text = datetime.fromisoformat(stand).astimezone(ZEITZONE).strftime("%d.%m.%Y, %H:%M Uhr") if stand else "noch nie"
c1, c2 = st.columns([3, 2], vertical_alignment="center")
c1.caption(f"Zuletzt gesucht: {stand_text}. Findus sucht zusätzlich jeden Morgen automatisch.")
if c2.button("🔎 Jetzt neu suchen", width="stretch"):
    suche_starten()
    st.rerun()

with st.expander("Für die Administration"):
    modell = (status.get("llm") or {}).get("modell")
    st.write(f"Bewertung: {modell or 'ohne Sprachmodell (nur Vorfilter)'} · {len(daten)} gespeicherte Einträge")
    if not secret("LLM_API_KEY"):
        st.warning("In den Streamlit-Secrets fehlt LLM_API_KEY. Eine Suche aus der App bewertet dann nicht.")
    for f in status.get("letzte_fehler") or []:
        st.write("⚠️ " + f)
