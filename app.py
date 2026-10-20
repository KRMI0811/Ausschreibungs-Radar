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


def _json(pfad: Path, leer):
    try:
        return json.loads(pfad.read_text(encoding="utf-8"))
    except Exception:
        return leer


def synchronisiere() -> None:
    """Übernimmt einen neueren Stand aus dem Repository. Treffer, die nur eine
    Suche aus der App gefunden hat, bleiben dabei erhalten."""
    ARBEIT.mkdir(parents=True, exist_ok=True)
    if _stand(REPO_DATEN) <= _stand(ARBEIT):
        return
    eigene = {e["id"]: e for e in _json(ARBEIT / "ausschreibungen.json", [])}
    for name in DATEIEN:
        if (REPO_DATEN / name).exists():
            shutil.copy2(REPO_DATEN / name, ARBEIT / name)
    repo = {e["id"]: e for e in _json(ARBEIT / "ausschreibungen.json", [])}
    for eid, e in eigene.items():
        repo.setdefault(eid, e)
    (ARBEIT / "ausschreibungen.json").write_text(json.dumps(list(repo.values()), ensure_ascii=False), encoding="utf-8")


def suchzeitraum() -> int:
    """Tage, die eine Suche aus der App zurückschaut. Beim ersten Mal und nach
    einer Änderung der Filterlogik 30 Tage, sonst seit der letzten Suche."""
    from radar import vorfilter

    pfad = ARBEIT / "quellen_status.json"
    status = _json(pfad, {})
    version = getattr(vorfilter, "VERSION", 1)
    letzte = status.get("simap_letzter_lauf")
    if status.get("findus_filterversion") != version:
        # neue Filterlogik: bereits verworfene Projekte nochmals prüfen
        status.pop("simap_gesehen", None)
        status["findus_filterversion"] = version
        pfad.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
        return 30
    if not letzte:
        return 30
    seit = (datetime.now(timezone.utc) - datetime.fromisoformat(letzte)).days
    return max(7, min(30, seit + 2))


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

        with st.status("Findus sucht … das kann einige Minuten dauern, beim ersten Mal bis zu 15 Minuten.", expanded=True) as box:
            handler = _AnzeigeHandler(box)
            logging.getLogger().addHandler(handler)
            try:
                run.main(["--ohne-versand", "--tage", str(suchzeitraum())])
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


PRIO_SYMBOL = {"hoch": "🔴 hoch", "mittel": "🟠 mittel", "niedrig": "⚪ niedrig"}


def tage_seit(datum: str | None) -> int | None:
    t = tage_bis(datum)
    return -t if t is not None else None


def prioritaet(e: dict, relevanz: int) -> tuple[str, int, list[str]]:
    """Priorität aus Passung, Eingabefrist und Erscheinungsdatum. Gibt (Stufe, Punkte, Gründe) zurück."""
    punkte = relevanz
    gruende = [f"Passung {relevanz}/100"]
    t = tage_bis(e.get("frist"))
    if t is not None:
        if t < 5:
            punkte -= 15
            gruende.append(f"nur noch {t} Tage, kaum mehr machbar")
        elif t <= 21:
            punkte += 10
            gruende.append(f"Frist in {t} Tagen, jetzt entscheiden")
        else:
            gruende.append(f"genügend Zeit ({t} Tage)")
    seit = tage_seit(e.get("publiziert_am"))
    if seit is not None and seit <= 7:
        punkte += 5
        gruende.append("neu erschienen")
    if e.get("publikationsart_code") in ("advance_notice", "request_for_information"):
        punkte += 5
        gruende.append("Frühphase, Positionierung möglich")
    stufe = "hoch" if punkte >= 80 else ("mittel" if punkte >= 60 else "niedrig")
    return stufe, punkte, gruende


def aufwand(e: dict) -> tuple[str | None, dict]:
    """Aufwand aus den simap-Feldern, sonst Einschätzung des Sprachmodells."""
    try:
        from radar.bewertung import aufwand_steckbrief
        aw = aufwand_steckbrief(e)
    except Exception:
        aw = {"stufe": None, "gruende": [], "fakten": []}
    stufe = aw.get("stufe")
    if not stufe and bekannt((e.get("bewertung") or {}).get("aufwand")):
        stufe = e["bewertung"]["aufwand"]
    return stufe, aw


@st.dialog("Ausschreibung im Detail", width="large")
def detailfenster(e: dict) -> None:
    b = e.get("bewertung") or {}
    themen, relevanz, bewertet = analyse(e, WOERTER)
    st.markdown(f"### {e.get('titel')}")
    st.caption(" · ".join(str(x) for x in [ART_SYMBOL.get(e["gefaess"]), e.get("auftraggeber"), e.get("kanton"),
                                           e.get("publikationsart"), e.get("verfahren")] if x))
    st.markdown(marken(themen), unsafe_allow_html=True)

    prio, _, prio_gruende = prioritaet(e, relevanz)
    aw_stufe, aw = aufwand(e)
    kennzahlen = [("Priorität", PRIO_SYMBOL[prio])]
    if bewertet:
        kennzahlen.append(("Passung", sterne(relevanz)))
    if bekannt(b.get("erfolgschance")):
        kennzahlen.append(("Erfolgschance", b["erfolgschance"].capitalize()))
    if aw_stufe:
        kennzahlen.append(("Aufwand", aw_stufe.capitalize()))
    for spalte, (titel, wert) in zip(st.columns(len(kennzahlen)), kennzahlen):
        spalte.metric(titel, wert)
    st.caption("Priorität: " + ", ".join(prio_gruende))
    if not bewertet:
        st.info("Noch nicht vom Sprachmodell bewertet. Die Einordnung beruht auf Stichworten.")
    termine = []
    if e.get("publiziert_am"):
        termine.append(f"**Erschienen:** {frist_lesbar(e['publiziert_am'])}")
    if frist := frist_text(e):
        termine.append(f"**📅 Eingabefrist:** {frist}")
    if e.get("fragen_bis"):
        termine.append(f"**Fragen bis:** {', '.join(frist_lesbar(x) for x in e['fragen_bis'])}")
    if termine:
        st.markdown(" &nbsp;·&nbsp; ".join(termine))
    st.markdown("**Worum geht es?**")
    st.write(b.get("kurzfassung") or (e.get("text") or "")[:700])
    anforderungen = [a for a in b.get("anforderungen") or [] if bekannt(a)]
    if anforderungen:
        st.markdown("**Was wird verlangt?**")
        st.markdown("\n".join(f"- {a}" for a in anforderungen))
    if bekannt(b.get("foerderbetrag")):
        st.markdown(f"**Förderbetrag:** {b['foerderbetrag']}")
    if aw.get("gruende") or aw.get("fakten"):
        st.markdown("**Steckbrief und Aufwand**")
        zeilen_ = [f"- {f}" for f in aw.get("fakten") or []]
        zeilen_ += [f"- Aufwandstreiber: {', '.join(aw['gruende'])}"] if aw.get("gruende") else []
        st.markdown("\n".join(zeilen_))
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
            if e.get("publiziert_am"):
                info.append(f"erschienen {frist_lesbar(e['publiziert_am'])}")
            if frist := frist_text(e):
                info.append(f"📅 Frist {frist}")
            st.caption(" · ".join(i for i in info if i))
            prio, _, _ = prioritaet(e, relevanz)
            teile = [f"Priorität **{PRIO_SYMBOL[prio]}**"]
            if bewertet:
                teile.append(f"**{sterne(relevanz)}**")
            if bekannt(b.get("erfolgschance")):
                teile.append(f"Chance {CHANCE.get(b['erfolgschance'], b['erfolgschance'])}")
            aw_stufe, _ = aufwand(e)
            if aw_stufe:
                teile.append(f"Aufwand {aw_stufe}")
            st.markdown(" &nbsp;·&nbsp; ".join(teile) + ("&nbsp;&nbsp;" if teile else "") + marken(themen),
                        unsafe_allow_html=True)
            kurz = b.get("kurzfassung") or (e.get("text") or "")[:240]
            if kurz:
                st.write(kurz[:280] + ("…" if len(kurz) > 280 else ""))
        with rechts:
            if st.button("Details", key=f"d-{e['id']}", width="stretch"):
                detailfenster(e)


# ---------------------------------------------------------------- Einzelne Ausschreibung prüfen

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
PUB_LESBAR = {
    "tender": "Ausschreibung", "advance_notice": "Vorankündigung", "request_for_information": "Marktabklärung",
    "study_contract": "Studienauftrag", "competition": "Wettbewerb", "award": "Zuschlag (bereits vergeben)",
    "direct_award": "freihändiger Zuschlag (bereits vergeben)", "revocation": "Widerruf", "abandonment": "Abbruch",
    "participant_selection": "Präqualifikation abgeschlossen", "selective_offering_phase": "Angebotsphase selektives Verfahren",
}


def pruefe_simap(link: str) -> tuple[dict | None, list[tuple[str, str]]]:
    """Lädt ein simap-Projekt und erklärt Schritt für Schritt, ob Findus es zeigt."""
    from radar import vorfilter
    from radar.simap import SimapClient, normalisiere

    m = UUID_RE.search(link or "")
    if not m:
        return None, [("❌", "Im Link wurde keine simap-Projektnummer gefunden. Bitte den ganzen Link einfügen.")]
    pid = m.group(0).lower()
    cfg = radar_config.quellen()["simap"]
    c = SimapClient(cfg["basis_url"], pause=0)
    try:
        kopf_ = c._get(f"/publications/v2/project/{pid}/project-header")
        lp = kopf_.get("latestPublication") or {}
        details = c.details(pid, lp["id"])
    except Exception as ex:
        return None, [("❌", f"simap.ch hat das Projekt nicht geliefert: {ex}")]

    termine = details.get("dates") or {}
    treffer = {
        "id": pid, "title": lp.get("title"), "projectNumber": kopf_.get("projectNumber"),
        "projectSubType": kopf_.get("projectSubType"), "processType": lp.get("processType") or kopf_.get("processType"),
        "pubType": lp.get("pubType"), "publicationId": lp.get("id"), "publicationNumber": lp.get("publicationNumber"),
        "publicationDate": termine.get("publicationDate") or (details.get("base") or {}).get("publicationDate"),
        "procOfficeName": ((details.get("project-info") or {}).get("procOfficeAddress") or {}).get("name"),
        "lots": [{"lotNumber": l.get("lotNumber"), "lotTitle": l.get("title")} for l in kopf_.get("lots") or []],
    }
    e = normalisiere(treffer, details, cfg["web_link"])
    b: list[tuple[str, str]] = []

    art_ = treffer["projectSubType"]
    b.append(("✅" if art_ in cfg["auftragsarten"] else "❌",
              f"Auftragsart: {art_}" + ("" if art_ in cfg["auftragsarten"] else " wird von Findus nicht durchsucht")))
    pt = treffer["pubType"]
    ok_pt = pt in cfg["publikationsarten"]
    b.append(("✅" if ok_pt else "❌", f"Neueste Publikation: {PUB_LESBAR.get(pt, pt)}"
              + ("" if ok_pt else ". Findus zeigt nur Ausschreibungen, bei denen man sich noch bewerben kann.")))
    datum = treffer["publicationDate"]
    if datum:
        alter = (datetime.now(ZEITZONE).date() - datetime.fromisoformat(datum[:10]).date()).days
        b.append(("✅" if alter <= 150 else "⚠️", f"Publiziert am {datum[:10]}, vor {alter} Tagen"
                  + ("" if alter <= 150 else ". Das liegt vor dem Suchzeitraum von Findus (150 Tage).")))
    t = tage_bis(e.get("frist"))
    if t is None:
        b.append(("⚠️", "Keine Eingabefrist in den simap-Daten"))
    else:
        b.append(("✅" if t >= 0 else "❌", f"Eingabefrist {frist_lesbar(e['frist'])}" + ("" if t >= 0 else ", bereits abgelaufen")))
    punkte, themen, grund = vorfilter.bewerte(e, lade_profil())
    minimum = int((lade_profil().get("schwellen") or {}).get("vorfilter_min", 3))
    if grund:
        b.append(("❌", f"Vorfilter: aussortiert ({grund})"))
    else:
        b.append(("✅" if punkte >= minimum else "❌", f"Vorfilter: {punkte} Punkte (nötig {minimum}), "
                  f"Themen: {', '.join(themen) or 'keine'}"))
    vorhanden = next((x for x in daten if x["id"] == e["id"]), None)
    if vorhanden:
        th, rel, bew = analyse(vorhanden, WOERTER)
        wo = ", ".join(kz_von(th)) or "keinem Kompetenzzentrum"
        b.append(("✅", f"In Findus gespeichert. Passung {rel}/100"
                  + ("" if bew else " (vorläufig, ohne Sprachmodell)") + f", zugeordnet zu {wo}"))
    else:
        b.append(("ℹ️", "Noch nicht in Findus gespeichert"))
    e.update(vorfilter_score=punkte, vorfilter_themen=themen)
    return e, b


def aufnehmen(e: dict) -> None:
    """Nimmt eine geprüfte Ausschreibung in Findus auf und lässt sie bewerten."""
    from radar import bewertung
    from radar.llm import LLM

    if secret("LLM_API_KEY"):
        os.environ["LLM_API_KEY"] = secret("LLM_API_KEY")
    jetzt = datetime.now(timezone.utc).isoformat(timespec="seconds")
    e.update(erstmals_gefunden=jetzt, zuletzt_gesehen=jetzt, gemeldet=False, erinnert=[], manuell=True)
    llm = LLM()
    if llm.verfuegbar:
        with st.spinner("Sprachmodell bewertet die Ausschreibung …"):
            try:
                e["bewertung"] = bewertung.bewerte_dienstleistung(llm, e, lade_profil())
                e["bewertet_mit"], e["bewertet_am"] = llm.modell, jetzt
            except Exception as ex:
                st.warning(f"Bewertung nicht möglich: {ex}")
    alle = speicher.lade_eintraege()
    alle[e["id"]] = e
    speicher.speichere_eintraege(alle)
    lade_daten.clear()


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
    if art == ARTEN[2] and (passend or relevanz < 15):
        continue
    liste.append((e, themen, relevanz, bewertet))
liste.sort(key=lambda x: (-prioritaet(x[0], x[2])[1], tage_bis(x[0].get("frist")) or 999))

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

st.write("")
with st.expander("🔍 Fehlt eine Ausschreibung? Link von simap.ch prüfen"):
    st.caption("Fügen Sie den Link einer Ausschreibung von simap.ch ein. Findus zeigt, ob und warum sie "
               "erscheint, und kann sie aufnehmen.")
    link = st.text_input("Link", placeholder="https://www.simap.ch/de/project-detail/…", label_visibility="collapsed")
    if st.button("Prüfen") and link:
        with st.spinner("Frage simap.ch ab …"):
            st.session_state["pruefung"] = pruefe_simap(link)
    if st.session_state.get("pruefung"):
        geprueft, befunde = st.session_state["pruefung"]
        if geprueft:
            st.markdown(f"**{geprueft.get('titel')}** · {geprueft.get('auftraggeber') or ''}")
        for zeichen, text in befunde:
            st.write(f"{zeichen} {text}")
        if geprueft and not any(geprueft["id"] == x["id"] for x in daten):
            if st.button("In Findus aufnehmen und bewerten", type="primary"):
                aufnehmen(geprueft)
                st.session_state.pop("pruefung", None)
                st.success("Aufgenommen. Die Ausschreibung erscheint jetzt in der Liste.")
                st.rerun()
