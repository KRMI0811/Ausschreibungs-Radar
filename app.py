"""Findus, der Ausschreibungsradar des IOL (Streamlit-App).

Bedienung: links Bereich und Art der Ausschreibungen wählen, in der Mitte
erscheint die Rangliste, "Details" öffnet ein Fenster.

Streamlit-Secrets (Einstellungen der App, nur durch die Administration):
    APP_PASSWORT = "..."    optionaler Passwortschutz
    LLM_API_KEY  = "..."    derselbe Public-AI-Schlüssel wie in GitHub

Bilder auf der Hauptebene des Repositorys:
    logo_iol.png   IOL-Logo (oben links)
    findus.png     Spürhund (neben dem Namen Findus)

Datenhaltung: Der tägliche GitHub-Lauf schreibt nach data/. Die App arbeitet in
einer Kopie (Arbeitsordner), damit eine Suche aus der App das Repository nicht
verändert. Beim Start übernimmt die App jeweils den neueren Stand.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import shutil
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st

HAUPT = Path(__file__).resolve().parent
REPO_DATEN = HAUPT / "data"
ARBEIT = Path(tempfile.gettempdir()) / "findus_daten"
os.environ["RADAR_DATA_DIR"] = str(ARBEIT)

from radar import benachrichtigung, config as radar_config, llm as radar_llm, speicher  # noqa: E402
from radar.config import ZEITZONE, frist_lesbar, profil as lade_profil, tage_bis  # noqa: E402

# Alle Module des Suchprogramms auf den Arbeitsordner umlenken
for _modul in (radar_config, benachrichtigung, radar_llm, speicher):
    _modul.DATA_DIR = ARBEIT
speicher.DATEI = ARBEIT / "ausschreibungen.json"
speicher.STATUS = ARBEIT / "quellen_status.json"

st.set_page_config(page_title="Findus · Ausschreibungsradar IOL", page_icon="🐕", layout="wide")

DATEIEN = ("ausschreibungen.json", "quellen_status.json", "nutzung.jsonl")
CHANCE = {"hoch": "🟢 hoch", "mittel": "🟡 mittel", "niedrig": "🔴 niedrig"}
ART_SYMBOL = {"dienstleistung": "🏛️ Dienstleistung", "forschung": "🔬 Förderung"}

st.markdown(
    """
<style>
  section[data-testid="stSidebar"] {width: 360px !important; min-width: 360px !important;}
  .kz-marke {display:inline-block; background:#f3e8ec; color:#8a1538; border-radius:12px;
             padding:1px 10px; font-size:.8rem; margin-right:6px;}
  section[data-testid="stSidebar"] div.stButton > button {justify-content: flex-start; text-align: left;}
</style>
""",
    unsafe_allow_html=True,
)


def secret(name: str) -> str | None:
    try:
        wert = st.secrets.get(name)
    except Exception:
        wert = None
    return str(wert).strip() if wert else None


def bild(*namen: str) -> Path | None:
    """Sucht ein Bild auf der Hauptebene des Repositorys."""
    for name in namen:
        for endung in ("png", "jpg", "jpeg", "svg", "webp", "PNG", "JPG", "JPEG"):
            p = HAUPT / f"{name}.{endung}"
            if p.exists():
                return p
    return None


IOL_LOGO = bild("logo_iol", "iol_logo", "IOL_Logo", "IOL-Logo", "iol")
HUND = bild("findus", "Findus", "spuerhund", "hund")


def findus_kopf(hund_breite: int = 220) -> None:
    """Grosser Kopf mit Spürhund und Schriftzug Findus."""
    a, b = st.columns([1, 3], vertical_alignment="center")
    with a:
        if HUND:
            st.image(str(HUND), width=hund_breite)
        else:
            st.markdown(f"<div style='font-size:{hund_breite // 2}px;line-height:1'>🐕</div>", unsafe_allow_html=True)
    with b:
        st.markdown(
            "<div style='font-size:4.5rem;font-weight:800;line-height:1;margin:0'>Findus</div>"
            "<div style='font-size:1.4rem;color:#666;margin-top:.5rem'>Der Ausschreibungsradar des IOL</div>",
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------- Passwort

if secret("APP_PASSWORT") and not st.session_state.get("angemeldet"):
    _, mitte, _ = st.columns([1, 2, 1])
    with mitte:
        if IOL_LOGO:
            st.image(str(IOL_LOGO), width=300)
        st.write("")
        if HUND:
            st.image(str(HUND), width=280)
        else:
            st.markdown("<div style='font-size:140px;line-height:1'>🐕</div>", unsafe_allow_html=True)
        st.markdown(
            "<div style='font-size:5rem;font-weight:800;line-height:1;margin-top:.5rem'>Findus</div>"
            "<div style='font-size:1.5rem;color:#666;margin:.5rem 0 1.5rem'>Der Ausschreibungsradar des IOL</div>",
            unsafe_allow_html=True,
        )
        pw = st.text_input("Passwort", type="password")
        if pw:
            if hmac.compare_digest(pw.strip(), secret("APP_PASSWORT")):
                st.session_state["angemeldet"] = True
                st.rerun()
            st.error("Passwort falsch.")
    st.stop()


# ---------------------------------------------------------------- Kompetenzzentren und Zuordnung

KZ_STANDARD = {
    "Kompetenzzentrum Organisation & Nachhaltigkeit": {
        "beschreibung": "Organisationsentwicklung, digitale Transformation und KI, Nachhaltigkeitsmanagement",
        "themen": ["Organisationsentwicklung", "Digitale Transformation und KI", "Nachhaltigkeit und Kreislaufwirtschaft"],
    },
    "Kompetenzzentrum Leadership & HR": {
        "beschreibung": "Führung, Governance, Personalentwicklung und Personalauswahl, Arbeitsgestaltung und Work-Life-Balance",
        "themen": ["Leadership und Führung", "Governance", "Human Resources", "Arbeitsgestaltung und Work-Life-Balance"],
    },
}

# Ergänzende Suchbegriffe, falls im Repository noch eine ältere profil.yaml liegt
BEGRIFFE_ERGAENZUNG = {
    "Digitale Transformation und KI": ["digital", "digitalisierung", "digitale transformation", "künstliche intelligenz",
                                       " ki ", "ki-", "artificial intelligence", " ai ", "e-government", "automatisierung",
                                       "chatbot", "it-strategie", "smart city"],
    "Organisationsentwicklung": ["organisationsentwicklung", "organisationsanalyse", "reorganisation", "change",
                                 "strategieentwicklung", "kulturentwicklung", "verwaltungsentwicklung",
                                 "gemeindeorganisation", "strukturanalyse", "prozessanalyse"],
    "Nachhaltigkeit und Kreislaufwirtschaft": ["nachhaltig", "kreislauf", "circular", "klimastrategie", "esg",
                                               "umweltmanagement", "biodiversität", "netto-null", "netto null"],
    "Leadership und Führung": ["führung", "leadership", "kader", "coaching", "personalführung", "teamentwicklung"],
    "Governance": ["governance", "eigentümerstrategie", "beteiligungsmanagement", "verwaltungsrat", "stiftungsrat",
                   "compliance", "rollenklärung", "funktionendiagramm"],
    "Human Resources": ["personalentwicklung", "personalauswahl", "rekrutierung", "assessment", "human resources",
                        "mitarbeitendenbefragung", "mitarbeiterbefragung", "kompetenzmodell", "lohnsystem",
                        "employer branding", "fachkräfte"],
    "Arbeitsgestaltung und Work-Life-Balance": ["arbeitsgestaltung", "arbeitszeitmodell", "4-tage-woche",
                                                "vier-tage-woche", "work-life", "vereinbarkeit", "teilzeit",
                                                "jobsharing", "topsharing", "homeoffice", "mobiles arbeiten",
                                                "hybride arbeit", "new work", "gesundheitsmanagement", "wohlbefinden"],
}


def kompetenzzentren(prof: dict) -> dict:
    aus_profil = prof.get("kompetenzzentren") or {}
    if not aus_profil:
        return KZ_STANDARD
    return {
        (name.replace(" und ", " & ", 1) if name.startswith("Kompetenzzentrum") else name): {
            "beschreibung": kz.get("beschreibung") or "",
            "themen": list(kz.get("themen") or []),
        }
        for name, kz in aus_profil.items()
    }


def begriffe(prof: dict) -> dict[str, list[str]]:
    alle: dict[str, list[str]] = {t: list(b) for t, b in BEGRIFFE_ERGAENZUNG.items()}
    for thema, liste in (prof.get("kernthemen") or {}).items():
        alle[thema] = list(dict.fromkeys(alle.get(thema, []) + list(liste)))
    return alle


# Sehr allgemeine Begriffe zählen nur im Titel, nicht im Fliesstext. Sonst würde
# z. B. jede Ausschreibung, die "Personal" erwähnt, Leadership & HR zugeordnet.
NUR_IM_TITEL = {"personal", "digital", "zusammenarbeit", "change", "kader", "coaching", "teilzeit", "esg",
                "compliance", "assessment", "fachkräfte", "nachhaltig", "leitbild"}

_MUSTER: dict[str, re.Pattern[str]] = {}


def _treffer(text: str, begriff: str) -> int:
    m = _MUSTER.get(begriff)
    if m is None:
        ende = r"(?!\w)" if begriff.endswith(" ") else ""
        m = _MUSTER[begriff] = re.compile(r"(?<!\w)" + re.escape(begriff.strip().lower()) + ende)
    return len(m.findall(text))


def analyse(e: dict, woerter: dict[str, list[str]]) -> tuple[set[str], int, bool]:
    """Gibt (Themen, Relevanz 0 bis 100, vom Sprachmodell bewertet?) zurück.

    Themen = Einschätzung des Sprachmodells plus eindeutige Stichworttreffer
    (im Titel, oder mindestens zweimal im Text). So landet eine Ausschreibung zu
    KI auch dann beim richtigen Kompetenzzentrum, wenn das Modell das Thema
    nicht ausdrücklich nennt.
    """
    b = e.get("bewertung") or {}
    titel = " " + (e.get("titel") or "").lower() + " "
    text = " " + (b.get("kurzfassung") or "").lower() + " " + (e.get("text") or "").lower() + " "
    themen = set(b.get("passende_kernthemen") or [])
    im_titel = set()
    for thema, liste in woerter.items():
        t_titel = sum(_treffer(titel, w) for w in liste)
        t_text = sum(_treffer(text, w) for w in liste if w.strip().lower() not in NUR_IM_TITEL)
        if t_titel:
            im_titel.add(thema)
        if t_titel or t_text >= 2:
            themen.add(thema)
    if b:
        return themen, int(b.get("passung", 0)), True
    # ohne Sprachmodell: grobe Relevanz aus den Stichworten
    relevanz = 70 if im_titel else (55 if themen else 30)
    return themen, relevanz, False


# ---------------------------------------------------------------- Daten und Suche

def _stand(ordner: Path) -> str:
    try:
        return json.loads((ordner / "quellen_status.json").read_text(encoding="utf-8")).get("letzter_lauf") or ""
    except Exception:
        return ""


def synchronisiere() -> None:
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


# ---------------------------------------------------------------- Darstellung

def sterne(wert: int) -> str:
    n = max(1, min(5, round(wert / 20)))
    return "★" * n + "☆" * (5 - n)


def frist_text(e: dict) -> str | None:
    t = tage_bis(e.get("frist"))
    if t is None:
        return None
    text = frist_lesbar(e["frist"]) + (" · heute" if t == 0 else f" · noch {t} Tage")
    if e.get("frist_quelle") and e["frist_quelle"] != "simap":
        text += " (bitte prüfen)"
    return text


def bekannt(wert) -> bool:
    return bool(wert) and str(wert).lower() not in ("unklar", "nicht angegeben", "none", "null", "?")


def kz_von(themen: set[str]) -> list[str]:
    return [name for name, kz in ZENTREN.items() if themen & set(kz["themen"])]


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


def marken(themen: set[str]) -> str:
    return "".join(f"<span class='kz-marke'>{n.replace('Kompetenzzentrum ', 'KZ ')}</span>" for n in kz_von(themen))


@st.dialog("Ausschreibung im Detail", width="large")
def detailfenster(e: dict) -> None:
    b = e.get("bewertung") or {}
    themen, relevanz, bewertet = analyse(e, WOERTER)
    st.markdown(f"### {e.get('titel')}")
    st.caption(" · ".join(str(x) for x in [ART_SYMBOL.get(e["gefaess"]), e.get("auftraggeber"), e.get("kanton"),
                                           e.get("publikationsart"), e.get("verfahren")] if x))
    st.markdown(marken(themen), unsafe_allow_html=True)

    kennzahlen = []
    if bewertet:
        kennzahlen.append(("Bewertung", sterne(relevanz)))
    if bekannt(b.get("erfolgschance")):
        kennzahlen.append(("Erfolgschance", b["erfolgschance"].capitalize()))
    if bekannt(b.get("aufwand")):
        kennzahlen.append(("Aufwand", b["aufwand"].capitalize()))
    if kennzahlen:
        for spalte, (titel, wert) in zip(st.columns(len(kennzahlen)), kennzahlen):
            spalte.metric(titel, wert)
    if not bewertet:
        st.info("Noch nicht vom Sprachmodell bewertet. Die Einordnung beruht auf Stichworten.")
    if frist := frist_text(e):
        st.markdown(f"**📅 Eingabefrist:** {frist}")
    st.markdown("**Worum geht es?**")
    st.write(b.get("kurzfassung") or (e.get("text") or "")[:700])
    anforderungen = [a for a in b.get("anforderungen") or [] if bekannt(a)]
    if anforderungen:
        st.markdown("**Was wird verlangt?**")
        st.markdown("\n".join(f"- {a}" for a in anforderungen))
    if bekannt(b.get("foerderbetrag")):
        st.markdown(f"**Förderbetrag:** {b['foerderbetrag']}")
    begr = [f"**{n}:** {b[f]}" for n, f in (("Passung", "passung_begruendung"),
                                            ("Erfolgschance", "erfolgschance_begruendung"),
                                            ("Aufwand", "aufwand_begruendung")) if bekannt(b.get(f))]
    if begr:
        st.markdown("**Warum diese Einschätzung?**")
        st.markdown("  \n".join(begr))
    k1, k2, k3 = st.columns(3)
    k1.link_button("Zur Ausschreibung", e.get("link_quelle") or "#", width="stretch", type="primary")
    if e.get("link_organisation"):
        k2.link_button("Website der Organisation", e["link_organisation"], width="stretch")
    if kal := kalender(e):
        k3.download_button("Frist in Kalender", kal, "eingabefrist.ics", "text/calendar", width="stretch")
    if e.get("eignungskriterien") or e.get("zuschlagskriterien") or e.get("kontakt_email"):
        with st.expander("Kriterien und Kontakt"):
            if e.get("eignungskriterien"):
                st.markdown("**Eignungskriterien**")
                st.markdown("\n".join(f"- {a[:400]}" for a in e["eignungskriterien"]))
            if e.get("zuschlagskriterien"):
                st.markdown("**Zuschlagskriterien**")
                st.markdown("\n".join(f"- {a[:300]}" for a in e["zuschlagskriterien"]))
            if e.get("kontakt_email"):
                st.markdown(f"**Kontakt:** {e['kontakt_email']}")
    if bewertet:
        st.caption("Kurzfassung und Einschätzungen erstellt ein Sprachmodell. Massgeblich ist die Originalausschreibung.")


def zeile(rang: int, e: dict, themen: set[str], relevanz: int, bewertet: bool) -> None:
    b = e.get("bewertung") or {}
    with st.container(border=True):
        links, rechts = st.columns([6, 1], vertical_alignment="center")
        with links:
            st.markdown(f"##### {rang}. {e.get('titel')}")
            info = [ART_SYMBOL.get(e["gefaess"], ""), e.get("auftraggeber") or ""]
            if frist := frist_text(e):
                info.append(f"📅 {frist}")
            st.caption(" · ".join(i for i in info if i))
            teile = []
            if bewertet:
                teile.append(f"**{sterne(relevanz)}**")
            if bekannt(b.get("erfolgschance")):
                teile.append(f"Chance {CHANCE.get(b['erfolgschance'], b['erfolgschance'])}")
            if bekannt(b.get("aufwand")):
                teile.append(f"Aufwand {b['aufwand']}")
            st.markdown(" &nbsp;·&nbsp; ".join(teile) + ("&nbsp;&nbsp;" if teile else "") + marken(themen),
                        unsafe_allow_html=True)
            kurz = b.get("kurzfassung") or (e.get("text") or "")[:240]
            if kurz:
                st.write(kurz[:280] + ("…" if len(kurz) > 280 else ""))
        with rechts:
            if st.button("Details", key=f"d-{e['id']}", width="stretch"):
                detailfenster(e)


# ---------------------------------------------------------------- Seite

synchronisiere()
daten, status = lade_daten(_stand(ARBEIT))
prof = lade_profil()
schwelle = int((prof.get("schwellen") or {}).get("passung_melden", 60))
ZENTREN = kompetenzzentren(prof)
WOERTER = begriffe(prof)

BEREICHE = ["Gesamtes Institut"] + list(ZENTREN)
ARTEN = ["Dienstleistungsausschreibungen", "Forschung und Entwicklung Förderungen", "Weitere interessante"]
st.session_state.setdefault("bereich", BEREICHE[0])
st.session_state.setdefault("art", ARTEN[0])
if st.session_state["bereich"] not in BEREICHE:
    st.session_state["bereich"] = BEREICHE[0]

# ----- linke Seite: Logo und Schaltflächen
with st.sidebar:
    if IOL_LOGO:
        st.image(str(IOL_LOGO), width="stretch")
    else:
        st.markdown("## IOL")
    st.write("")
    st.markdown("**Bereich**")
    for name in BEREICHE:
        if st.button(name, key=f"b-{name}", width="stretch",
                     type="primary" if st.session_state["bereich"] == name else "secondary"):
            st.session_state["bereich"] = name
            st.rerun()
    st.write("")
    st.markdown("**Ausschreibungen**")
    for name in ARTEN:
        if st.button(name, key=f"a-{name}", width="stretch",
                     type="primary" if st.session_state["art"] == name else "secondary"):
            st.session_state["art"] = name
            st.rerun()
    st.divider()
    stand = status.get("letzter_lauf")
    stand_text = (datetime.fromisoformat(stand).astimezone(ZEITZONE).strftime("%d.%m.%Y, %H:%M Uhr")
                  if stand else "noch nie")
    if st.button("🔎 Jetzt neu suchen", width="stretch"):
        st.session_state["suchen"] = True
    st.caption(f"Zuletzt gesucht: {stand_text}. Findus sucht zusätzlich jeden Morgen automatisch.")
    with st.expander("Für die Administration"):
        modell = (status.get("llm") or {}).get("modell")
        st.write(f"Bewertung: {modell or 'ohne Sprachmodell (nur Stichworte)'}")
        st.write(f"{len(daten)} gespeicherte Einträge")
        if not secret("LLM_API_KEY"):
            st.warning("In den Streamlit-Secrets fehlt LLM_API_KEY. Ohne Sprachmodell fehlen Kurzfassungen, "
                       "Bewertung, Erfolgschance und Aufwand.")
        for f in status.get("letzte_fehler") or []:
            st.write("⚠️ " + f)

bereich, art = st.session_state["bereich"], st.session_state["art"]

# ----- Mitte: Kopf und Ergebnisse
findus_kopf()
st.divider()

if st.session_state.pop("suchen", False):
    suche_starten()
    st.rerun()

liste: list[tuple[dict, set[str], int, bool]] = []
for e in daten:
    b = e.get("bewertung") or {}
    if b.get("ist_ausschreibung") is False or b.get("empfehlung") == "nicht weiterverfolgen":
        continue
    t = tage_bis(e.get("frist"))
    if t is not None and t < 0:
        continue
    themen, relevanz, bewertet = analyse(e, WOERTER)
    if bereich != "Gesamtes Institut" and not themen & set(ZENTREN[bereich]["themen"]):
        continue
    passend = relevanz >= schwelle
    if art == ARTEN[0] and not (e["gefaess"] == "dienstleistung" and passend):
        continue
    if art == ARTEN[1] and not (e["gefaess"] == "forschung" and passend):
        continue
    if art == ARTEN[2] and (passend or relevanz < 30):
        continue
    liste.append((e, themen, relevanz, bewertet))
liste.sort(key=lambda x: (-x[2], tage_bis(x[0].get("frist")) or 999))

st.markdown(f"## {bereich}")
st.markdown(f"#### {art} · {len(liste)} Treffer")
if bereich != "Gesamtes Institut" and ZENTREN[bereich]["beschreibung"]:
    st.caption(ZENTREN[bereich]["beschreibung"])
if art == ARTEN[2]:
    st.caption("Ausschreibungen mit teilweiser Passung, aus Dienstleistung und Förderung. "
               "Ein Blick kann sich lohnen, etwa für Partnerschaften.")

if not daten:
    st.info("Noch keine Ausschreibungen geladen. Klicken Sie links auf **Jetzt neu suchen**. "
            "Danach aktualisiert sich Findus jeden Morgen automatisch.")
elif not liste:
    st.write("Aktuell keine passenden Ausschreibungen in dieser Auswahl.")
for i, (e, themen, relevanz, bewertet) in enumerate(liste, 1):
    zeile(i, e, themen, relevanz, bewertet)
