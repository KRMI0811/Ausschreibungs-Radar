"""Streamlit-Ansicht des Ausschreibungsradars.

Die Suche selbst läuft als GitHub-Workflow. Die App zeigt die Ergebnisse,
gliedert die Empfehlungen nach Kompetenzzentren und kann über die GitHub-API
einen neuen Suchlauf starten.

Streamlit-Secrets (alle optional):
    APP_PASSWORT   Passwortschutz der App
    GITHUB_TOKEN   Fine-grained Token (Actions: Read and write, Contents: Read-only)
    GITHUB_REPO    "konto/repository"
    GITHUB_BRANCH  Standard "main"

Ohne GitHub-Secrets liest die App die Dateien im Ordner data/ und der
Suchknopf ist ausgeblendet.

Start lokal:  streamlit run app.py
"""

from __future__ import annotations

import hmac
import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from radar.config import DATA_DIR, ZEITZONE, frist_lesbar, profil as lade_profil, tage_bis
from radar.github_api import GitHub

st.set_page_config(page_title="Findus · Ausschreibungsradar IOL", page_icon="📡", layout="wide")

CHANCE_FARBE = {"hoch": "green", "mittel": "orange", "niedrig": "red", "unklar": "gray"}
GEFAESS = {"dienstleistung": "🏛️ Dienstleistung", "forschung": "🔬 Förderung"}


def secret(name: str, standard: str | None = None) -> str | None:
    try:
        wert = st.secrets.get(name)
    except Exception:  # keine secrets.toml vorhanden
        wert = None
    return str(wert) if wert else standard


# ---------------------------------------------------------------- Passwort

def passwort_pruefen() -> None:
    soll = secret("APP_PASSWORT")
    if not soll or st.session_state.get("angemeldet"):
        return
    st.title("📡 Findus")
    st.caption("Der Ausschreibungsradar des IOL")
    eingabe = st.text_input("Passwort", type="password")
    if eingabe:
        if hmac.compare_digest(eingabe.strip(), soll):
            st.session_state["angemeldet"] = True
            st.rerun()
        else:
            st.error("Passwort falsch.")
    st.stop()


passwort_pruefen()


# ---------------------------------------------------------------- Daten

def github() -> GitHub | None:
    token, repo = secret("GITHUB_TOKEN"), secret("GITHUB_REPO")
    if token and repo:
        return GitHub(token, repo, secret("GITHUB_BRANCH", "main"))
    return None


@st.cache_data(ttl=120, show_spinner="Lade Ergebnisse …")
def lade_daten() -> tuple[list[dict], dict, list[dict], str | None, str]:
    """Liest die Ergebnisse aus dem Repository (aktuell) oder lokal."""
    gh = github()
    roh: dict[str, str | None] = {}
    herkunft = "lokal"
    if gh:
        try:
            for name in ("ausschreibungen.json", "quellen_status.json", "nutzung.jsonl", "letzter_bericht.html"):
                roh[name] = gh.lade_datei(f"data/{name}")
            herkunft = "GitHub"
        except Exception:
            roh = {}
    if not roh:
        for name in ("ausschreibungen.json", "quellen_status.json", "nutzung.jsonl", "letzter_bericht.html"):
            p = DATA_DIR / name
            roh[name] = p.read_text(encoding="utf-8") if p.exists() else None
    daten = json.loads(roh["ausschreibungen.json"]) if roh.get("ausschreibungen.json") else []
    status = json.loads(roh["quellen_status.json"]) if roh.get("quellen_status.json") else {}
    nutzung = [json.loads(z) for z in (roh.get("nutzung.jsonl") or "").splitlines() if z.strip()]
    return daten, status, nutzung, roh.get("letzter_bericht.html"), herkunft


# ---------------------------------------------------------------- Suche starten

def _zeit(iso: str | None) -> str:
    if not iso:
        return ""
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ZEITZONE).strftime("%d.%m.%Y %H:%M")


def suchbereich() -> None:
    gh = github()
    if not gh:
        with st.popover("🔎 Suche starten"):
            st.write(
                "Der Suchknopf ist noch nicht eingerichtet. Hinterlegen Sie in den Streamlit-Secrets "
                "`GITHUB_TOKEN` und `GITHUB_REPO` (siehe README). Bis dahin läuft die Suche täglich automatisch."
            )
        return

    laeuft = st.session_state.get("suche_gestartet_um") is not None

    def anzeige() -> None:
        try:
            lauf = gh.letzter_lauf()
        except Exception as ex:
            st.error(str(ex))
            return
        gestartet = st.session_state.get("suche_gestartet_um")
        if gestartet:
            neu = lauf and datetime.fromisoformat(lauf["created_at"].replace("Z", "+00:00")) >= gestartet - timedelta(seconds=30)
            if not neu:
                st.info("⏳ Suche wird gestartet …")
                return
            if lauf["status"] != "completed":
                st.info(f"⏳ Suche läuft seit {_zeit(lauf['created_at'])}. Das dauert meist 3 bis 10 Minuten.")
                return
            # fertig
            st.session_state["suche_gestartet_um"] = None
            lade_daten.clear()
            if lauf.get("conclusion") == "success":
                st.session_state["meldung"] = "✅ Suche abgeschlossen, Ergebnisse aktualisiert."
            else:
                st.session_state["meldung"] = f"⚠️ Suche mit Fehler beendet. [Protokoll ansehen]({lauf['html_url']})"
            st.rerun(scope="app")
        elif lauf:
            symbol = {"success": "✅", "failure": "❌", "cancelled": "⏹️"}.get(lauf.get("conclusion"), "⏳")
            st.caption(f"Letzte Suche {symbol} {_zeit(lauf['created_at'])} · [Protokoll]({lauf['html_url']})")

    links, rechts = st.columns([1, 3])
    with links:
        if st.button("🔎 Suche jetzt starten", type="primary", disabled=laeuft, width="stretch"):
            try:
                gh.starte_suche(ohne_versand=False)
                st.session_state["suche_gestartet_um"] = datetime.now(timezone.utc)
                st.rerun()
            except Exception as ex:
                st.error(str(ex))
    with rechts:
        # Während einer laufenden Suche alle 15 Sekunden den Status abfragen
        st.fragment(anzeige, run_every=15 if laeuft else None)()

    if st.session_state.get("meldung"):
        st.success(st.session_state.pop("meldung"))


# ---------------------------------------------------------------- Darstellung

def ics(e: dict) -> str:
    """Kalendereintrag (ganztägig) am Tag der Eingabefrist mit Erinnerung 7 Tage vorher."""
    t = tage_bis(e.get("frist"))
    if t is None:
        return ""
    tag = datetime.now(ZEITZONE).date() + timedelta(days=t)
    titel = (e.get("titel") or "").replace("\n", " ").replace(",", "\\,").replace(";", "\\;")[:120]
    return "\r\n".join(
        [
            "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//IOL//Ausschreibungsradar//DE", "BEGIN:VEVENT",
            f"UID:{e['id'].replace(':', '-')}@ausschreibungsradar",
            f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            f"DTSTART;VALUE=DATE:{tag.strftime('%Y%m%d')}",
            f"SUMMARY:Eingabefrist: {titel}",
            f"DESCRIPTION:{e.get('link_quelle', '')}",
            "BEGIN:VALARM", "TRIGGER:-P7D", "ACTION:DISPLAY", "DESCRIPTION:Eingabefrist in 7 Tagen", "END:VALARM",
            "END:VEVENT", "END:VCALENDAR", "",
        ]
    )


def frist_text(e: dict) -> str:
    t = tage_bis(e.get("frist"))
    text = frist_lesbar(e.get("frist"))
    if t is not None:
        text += f" · {'abgelaufen' if t < 0 else f'noch {t} Tage'}"
    if e.get("frist_quelle") and e["frist_quelle"] != "simap":
        text += f" ({e['frist_quelle']})"
    return text


def details(e: dict) -> None:
    b = e.get("bewertung") or {}
    if b.get("anforderungen"):
        st.markdown("**Anforderungen an Anbietende**")
        st.markdown("\n".join(f"- {a}" for a in b["anforderungen"]))
    if b.get("leistungen"):
        st.markdown("**Gesuchte Leistungen**")
        st.markdown("\n".join(f"- {a}" for a in b["leistungen"]))
    zeilen = [
        f"**Begründung {name}:** {b[feld]}"
        for name, feld in (("Passung", "passung_begruendung"), ("Erfolgschance", "erfolgschance_begruendung"),
                           ("Aufwand", "aufwand_begruendung"))
        if b.get(feld)
    ]
    if b.get("foerderbetrag"):
        zeilen.append(f"**Förderbetrag:** {b['foerderbetrag']}")
    if b.get("fh_berechtigt"):
        zeilen.append(f"**Fachhochschulen antragsberechtigt:** {b['fh_berechtigt']}")
    if zeilen:
        st.markdown("  \n".join(zeilen))
    if b.get("rahmenfaktoren"):
        st.markdown("**Regelbasierte Faktoren**")
        st.markdown("\n".join(f"- {a}" for a in b["rahmenfaktoren"]))
    if e.get("eignungskriterien"):
        st.markdown("**Eignungskriterien laut simap**")
        st.markdown("\n".join(f"- {a[:400]}" for a in e["eignungskriterien"]))
    if e.get("zuschlagskriterien"):
        st.markdown("**Zuschlagskriterien laut simap**")
        st.markdown("\n".join(f"- {a[:300]}" for a in e["zuschlagskriterien"]))
    if e.get("fragen_bis"):
        st.markdown(f"**Fragen einreichen bis:** {', '.join(e['fragen_bis'])}")
    if e.get("kontakt_email"):
        st.markdown(f"**Kontakt Beschaffungsstelle:** {e['kontakt_email']}")
    if e.get("text"):
        st.markdown("**Originaltext (gekürzt)**")
        st.text(e["text"][:3000])
    if e.get("bewertet_mit"):
        st.caption(f"Bewertet mit {e['bewertet_mit']} am {e.get('bewertet_am', '')[:10]}. "
                   "Einschätzungen des Sprachmodells sind Hinweise, keine Fakten.")


def knoepfe(e: dict, schluessel: str, spalten: list[int]) -> None:
    t = tage_bis(e.get("frist"))
    k = st.columns(spalten)
    k[0].link_button("Zur Ausschreibung", e.get("link_quelle") or "#", width="stretch")
    if e.get("link_organisation"):
        k[1].link_button("Organisation", e["link_organisation"], width="stretch")
    kal = ics(e)
    if kal and (t or 0) >= 0:
        k[2].download_button("Frist in Kalender", kal, file_name="eingabefrist.ics", mime="text/calendar",
                             key=f"ics-{schluessel}-{e['id']}", width="stretch")


def karte(e: dict, schluessel: str = "liste") -> None:
    """Ausführliche Karte für die Listen."""
    b = e.get("bewertung") or {}
    with st.container(border=True):
        kopf, rechts = st.columns([4, 1])
        with kopf:
            st.markdown(f"#### [{e.get('titel')}]({e.get('link_quelle')})")
            meta = [e.get("auftraggeber"), e.get("kanton"), e.get("publikationsart") or e.get("quelle"), e.get("verfahren")]
            st.caption(" · ".join(str(m) for m in meta if m))
        with rechts:
            if b:
                st.metric("Passung", f"{b.get('passung', '?')}/100")
            else:
                st.metric("Vorfilter", e.get("vorfilter_score", 0))
        st.markdown(f"**Eingabefrist:** {frist_text(e)}")
        st.write(b.get("kurzfassung") or (e.get("text") or "")[:500])
        if b:
            c1, c2, c3 = st.columns(3)
            ch = b.get("erfolgschance", "unklar")
            c1.markdown(f"**Erfolgschance** :{CHANCE_FARBE.get(ch, 'gray')}[{ch}]")
            c2.markdown(f"**Aufwand** {b.get('aufwand', 'unklar')}")
            c3.markdown(f"**Empfehlung** {b.get('empfehlung', '')}")
        themen = b.get("passende_kernthemen") or e.get("vorfilter_themen") or []
        if themen:
            st.markdown(" ".join(f"`{x}`" for x in themen))
        with st.expander("Details, Anforderungen und Begründung"):
            details(e)
        knoepfe(e, schluessel, [1, 1, 1, 3])


def kompakt(e: dict, schluessel: str) -> None:
    """Kompakte Karte für die Empfehlungen."""
    b = e.get("bewertung") or {}
    ch = b.get("erfolgschance", "unklar")
    with st.container(border=True):
        st.markdown(f"**[{e.get('titel')}]({e.get('link_quelle')})**")
        st.caption(f"{GEFAESS.get(e['gefaess'], '')} · {e.get('auftraggeber') or ''}"
                   + (f" · {e['kanton']}" if e.get("kanton") else ""))
        st.markdown(
            f"Passung **{b.get('passung', '?')}** · Chance :{CHANCE_FARBE.get(ch, 'gray')}[**{ch}**] · "
            f"Aufwand **{b.get('aufwand', 'unklar')}**  \n📅 {frist_text(e)}"
        )
        st.write(b.get("kurzfassung") or (e.get("text") or "")[:300])
        with st.expander("Details"):
            details(e)
        knoepfe(e, schluessel, [1, 1, 1])


# ---------------------------------------------------------------- Logik

def themen_von(e: dict) -> set[str]:
    b = e.get("bewertung") or {}
    return set(b.get("passende_kernthemen") or e.get("vorfilter_themen") or [])


def ist_empfehlung(e: dict, schwelle: int) -> bool:
    b = e.get("bewertung") or {}
    if not b or b.get("ist_ausschreibung") is False:
        return False
    if b.get("passung", 0) < schwelle or b.get("empfehlung") == "nicht weiterverfolgen":
        return False
    t = tage_bis(e.get("frist"))
    return t is None or t >= 0


def sortiere(xs: list[dict]) -> list[dict]:
    return sorted(xs, key=lambda e: (-(e.get("bewertung") or {}).get("passung", 0), tage_bis(e.get("frist")) or 999))


# ---------------------------------------------------------------- Seite

prof = lade_profil()
schwelle = int((prof.get("schwellen") or {}).get("passung_melden", 60))

st.title("📡 Findus")
st.markdown("**Der Ausschreibungsradar des IOL**")

suchbereich()
daten, status, nutzung, bericht, herkunft = lade_daten()

letzter = status.get("letzter_lauf")
modell = (status.get("llm") or {}).get("modell") or "ohne Sprachmodell (nur Vorfilter)"
st.caption(
    f"Datenstand: {_zeit(letzter) if letzter else 'noch kein Lauf'} · Bewertung: {modell} · "
    f"{len(daten)} Einträge · Quelle: {herkunft}"
)
if status.get("letzte_fehler"):
    with st.expander(f"⚠️ {len(status['letzte_fehler'])} Hinweis(e) aus dem letzten Lauf"):
        for f in status["letzte_fehler"]:
            st.write("- " + f)

if not daten:
    st.info("Noch keine Daten vorhanden. Starten Sie oben die Suche oder in GitHub unter *Actions* den "
            "Workflow **Ausschreibungsradar**.")
    st.stop()

# Seitenleiste mit Filtern für die Listen
with st.sidebar:
    st.header("Filter für die Listen")
    suche = st.text_input("Suchbegriff")
    min_passung = st.slider("Mindestpassung", 0, 100, schwelle, 5)
    nur_offen = st.checkbox("Nur offene Fristen", value=True)
    themen_wahl = st.multiselect("Kernthemen", list((prof.get("kernthemen") or {}).keys()))
    chancen = st.multiselect("Erfolgschance", ["hoch", "mittel", "niedrig", "unklar"])
    kanton_wahl = st.multiselect("Kanton (simap)", sorted({e.get("kanton") for e in daten if e.get("kanton")}))
    st.divider()
    if st.button("Daten neu laden"):
        lade_daten.clear()
        st.rerun()


def passt(e: dict) -> bool:
    b = e.get("bewertung") or {}
    if b.get("ist_ausschreibung") is False:
        return False
    if b and b.get("passung", 0) < min_passung:
        return False
    t = tage_bis(e.get("frist"))
    if nur_offen and t is not None and t < 0:
        return False
    if suche and suche.lower() not in json.dumps(e, ensure_ascii=False).lower():
        return False
    if themen_wahl and not set(themen_wahl) & themen_von(e):
        return False
    if chancen and b.get("erfolgschance", "unklar") not in chancen:
        return False
    if kanton_wahl and e.get("kanton") not in kanton_wahl:
        return False
    return True


empfohlen = sortiere([e for e in daten if ist_empfehlung(e, schwelle)])
gefiltert = [e for e in daten if passt(e)]
dl = sortiere([e for e in gefiltert if e["gefaess"] == "dienstleistung"])
fo = sortiere([e for e in gefiltert if e["gefaess"] == "forschung"])
mit_frist = sorted([e for e in gefiltert if tage_bis(e.get("frist")) is not None], key=lambda e: tage_bis(e["frist"]))

tab_em, tab_dl, tab_fo, tab_fr, tab_nu = st.tabs(
    [f"⭐ Empfehlungen ({len(empfohlen)})", f"Dienstleistung ({len(dl)})", f"Forschung und Förderung ({len(fo)})",
     f"Fristen ({len(mit_frist)})", "Nutzung und Kosten"]
)

with tab_em:
    st.caption(f"Offene Ausschreibungen mit Passung ab {schwelle}, nach Kompetenzzentrum. "
               "Ein Treffer kann in beiden Zentren erscheinen.")
    zentren = prof.get("kompetenzzentren") or {}
    if not zentren:
        st.warning("In config/profil.yaml sind keine Kompetenzzentren definiert.")
    spalten = st.columns(max(len(zentren), 1))
    zugeordnet: set[str] = set()
    for spalte, (name, kz) in zip(spalten, zentren.items()):
        with spalte:
            treffer = [e for e in empfohlen if themen_von(e) & set(kz.get("themen") or [])]
            zugeordnet |= {e["id"] for e in treffer}
            st.subheader(f"{name} ({len(treffer)})")
            if kz.get("beschreibung"):
                st.caption(kz["beschreibung"])
            if not treffer:
                st.write("Aktuell keine passenden Ausschreibungen.")
            for e in treffer:
                kompakt(e, f"kz-{name}")
    rest = [e for e in empfohlen if e["id"] not in zugeordnet]
    if rest:
        with st.expander(f"Empfohlen, aber keinem Zentrum zugeordnet ({len(rest)})"):
            for e in rest:
                kompakt(e, "rest")

with tab_dl:
    st.caption("Öffentliche Beschaffungen von simap.ch: Dienstleistungen, Studien und Marktabklärungen.")
    if not dl:
        st.write("Keine Einträge für die gewählten Filter.")
    for e in dl:
        karte(e, "dl")

with tab_fo:
    st.caption("Ausschreibungen von SNF, Innosuisse und Stiftungen. Fristen aus Webseiten sind extrahiert und zu prüfen.")
    if not fo:
        st.write("Keine Einträge für die gewählten Filter.")
    for e in fo:
        karte(e, "fo")

with tab_fr:
    if mit_frist:
        tabelle = pd.DataFrame(
            [
                {
                    "Tage": tage_bis(e["frist"]),
                    "Frist": frist_lesbar(e["frist"]),
                    "Titel": e["titel"],
                    "Bereich": "Dienstleistung" if e["gefaess"] == "dienstleistung" else "Förderung",
                    "Auftraggeber": e.get("auftraggeber"),
                    "Passung": (e.get("bewertung") or {}).get("passung"),
                    "Link": e.get("link_quelle"),
                }
                for e in mit_frist
            ]
        )
        st.dataframe(tabelle, hide_index=True, width="stretch",
                     column_config={"Link": st.column_config.LinkColumn("Link", display_text="öffnen")})
        st.download_button("Als CSV herunterladen", tabelle.to_csv(index=False).encode("utf-8-sig"),
                           "fristen.csv", "text/csv")
    else:
        st.write("Keine Fristen in der Auswahl.")

with tab_nu:
    st.caption("Tokenverbrauch pro Lauf. Damit lassen sich die Kosten bei einem kostenpflichtigen Anbieter hochrechnen.")
    if nutzung:
        df = pd.DataFrame(nutzung)
        df["tag"] = df["zeit"].str[:10]
        pro_tag = df.groupby("tag").agg(aufrufe=("zweck", "count"), tokens=("total_tokens", "sum")).reset_index()
        st.bar_chart(pro_tag, x="tag", y="tokens")
        summe = int(df["total_tokens"].fillna(0).sum())
        tage_n = max(pro_tag.shape[0], 1)
        c1, c2, c3 = st.columns(3)
        c1.metric("Aufrufe gesamt", len(df))
        c2.metric("Tokens gesamt", f"{summe:,}".replace(",", "'"))
        c3.metric("Tokens pro Tag (Mittel)", f"{summe // tage_n:,}".replace(",", "'"))
        preis = st.number_input("Preis in CHF pro 1 Mio. Tokens (zum Hochrechnen)", 0.0, 100.0, 1.0, 0.1)
        st.write(f"Geschätzte Kosten pro Monat bei diesem Preis: **CHF {summe / tage_n * 30 / 1_000_000 * preis:.2f}**")
    else:
        st.write("Noch keine Aufrufe des Sprachmodells protokolliert.")
    stat = status.get("simap_letzte_statistik")
    if stat:
        st.write(f"Letzter simap-Lauf: {stat.get('treffer')} Projekte im Zeitraum, {stat.get('detailabrufe')} "
                 f"Details geladen, {stat.get('relevant_vorfilter')} nach Vorfilter.")

if bericht:
    with st.expander("Letzten Bericht anzeigen"):
        st.html(bericht)
