"""Streamlit-Ansicht des Ausschreibungsradars.

Die App liest nur die Dateien in data/, die der GitHub-Workflow täglich
aktualisiert. Sie startet selbst keine Suche. Dadurch ist es unerheblich, ob die
App auf Streamlit Community Cloud zwischendurch schläft.

Start lokal:  streamlit run app.py
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

from radar.config import DATA_DIR, frist_lesbar, profil as lade_profil, tage_bis

st.set_page_config(page_title="Ausschreibungsradar IOL", page_icon="📡", layout="wide")

CHANCE_FARBE = {"hoch": "green", "mittel": "orange", "niedrig": "red", "unklar": "gray"}


@st.cache_data(ttl=300)
def lade_daten() -> tuple[list[dict], dict, list[dict]]:
    def j(name, leer):
        p = DATA_DIR / name
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else leer

    nutzung = []
    p = DATA_DIR / "nutzung.jsonl"
    if p.exists():
        nutzung = [json.loads(z) for z in p.read_text(encoding="utf-8").splitlines() if z.strip()]
    return j("ausschreibungen.json", []), j("quellen_status.json", {}), nutzung


def ics(e: dict) -> str:
    """Kalendereintrag (ganztägig) am Tag der Eingabefrist."""
    t = tage_bis(e.get("frist"))
    if t is None:
        return ""
    tag = datetime.now().date() + timedelta(days=t)
    titel = (e.get("titel") or "").replace("\n", " ").replace(",", "\\,")[:120]
    return "\r\n".join(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//IOL//Ausschreibungsradar//DE",
            "BEGIN:VEVENT",
            f"UID:{e['id'].replace(':', '-')}@ausschreibungsradar",
            f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            f"DTSTART;VALUE=DATE:{tag.strftime('%Y%m%d')}",
            f"SUMMARY:Eingabefrist: {titel}",
            f"DESCRIPTION:{e.get('link_quelle', '')}",
            "BEGIN:VALARM",
            "TRIGGER:-P7D",
            "ACTION:DISPLAY",
            "DESCRIPTION:Eingabefrist in 7 Tagen",
            "END:VALARM",
            "END:VEVENT",
            "END:VCALENDAR",
            "",
        ]
    )


def karte(e: dict) -> None:
    b = e.get("bewertung") or {}
    t = tage_bis(e.get("frist"))
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

        frist = frist_lesbar(e.get("frist"))
        if t is not None:
            frist += f" · {'abgelaufen' if t < 0 else f'noch {t} Tage'}"
        hinweis = f" ({e['frist_quelle']})" if e.get("frist_quelle") and e["frist_quelle"] != "simap" else ""
        st.markdown(f"**Eingabefrist:** {frist}{hinweis}")

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
            if b.get("anforderungen"):
                st.markdown("**Anforderungen an Anbietende**")
                st.markdown("\n".join(f"- {a}" for a in b["anforderungen"]))
            if b.get("leistungen"):
                st.markdown("**Gesuchte Leistungen**")
                st.markdown("\n".join(f"- {a}" for a in b["leistungen"]))
            begruendungen = [
                f"**Begründung {name}:** {b[feld]}"
                for name, feld in (("Passung", "passung_begruendung"), ("Erfolgschance", "erfolgschance_begruendung"),
                                   ("Aufwand", "aufwand_begruendung"))
                if b.get(feld)
            ]
            if b.get("foerderbetrag"):
                begruendungen.append(f"**Förderbetrag:** {b['foerderbetrag']}")
            if b.get("fh_berechtigt"):
                begruendungen.append(f"**Fachhochschulen antragsberechtigt:** {b['fh_berechtigt']}")
            if begruendungen:
                st.markdown("  \n".join(begruendungen))
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

        links = st.columns([1, 1, 1, 3])
        links[0].link_button("Zur Ausschreibung", e.get("link_quelle") or "#", width="stretch")
        if e.get("link_organisation"):
            links[1].link_button("Organisation", e["link_organisation"], width="stretch")
        kal = ics(e)
        if kal and (t or 0) >= 0:
            links[2].download_button(
                "Frist in Kalender", kal, file_name="eingabefrist.ics", mime="text/calendar",
                key=f"ics-{e['id']}", width="stretch",
            )


# ---------------------------------------------------------------- Seite

daten, status, nutzung = lade_daten()
prof = lade_profil()

st.title("📡 Ausschreibungsradar IOL")
letzter = (status.get("letzter_lauf") or "noch kein Lauf")[:16].replace("T", " ")
modell = (status.get("llm") or {}).get("modell") or "ohne Sprachmodell (nur Vorfilter)"
st.caption(f"Letzter Suchlauf: {letzter} UTC · Bewertung: {modell} · {len(daten)} Einträge gespeichert")

if status.get("letzte_fehler"):
    with st.expander(f"⚠️ {len(status['letzte_fehler'])} Hinweis(e) aus dem letzten Lauf"):
        for f in status["letzte_fehler"]:
            st.write("- " + f)

if not daten:
    st.info(
        "Noch keine Daten vorhanden. Starten Sie den Workflow **Ausschreibungsradar** in GitHub unter "
        "*Actions*, oder lokal mit `python -m radar.run --ohne-versand`."
    )
    st.stop()

# Filter
with st.sidebar:
    st.header("Filter")
    suche = st.text_input("Suchbegriff")
    min_passung = st.slider("Mindestpassung", 0, 100, int((prof.get("schwellen") or {}).get("passung_melden", 60)), 5)
    nur_offen = st.checkbox("Nur offene Fristen", value=True)
    themen_alle = list((prof.get("kernthemen") or {}).keys())
    themen = st.multiselect("Kernthemen", themen_alle)
    chancen = st.multiselect("Erfolgschance", ["hoch", "mittel", "niedrig", "unklar"])
    kantone = sorted({e.get("kanton") for e in daten if e.get("kanton")})
    kanton_wahl = st.multiselect("Kanton (simap)", kantone)
    st.divider()
    if st.button("Daten neu laden"):
        st.cache_data.clear()
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
    if themen and not set(themen) & set(b.get("passende_kernthemen") or e.get("vorfilter_themen") or []):
        return False
    if chancen and b.get("erfolgschance", "unklar") not in chancen:
        return False
    if kanton_wahl and e.get("kanton") not in kanton_wahl:
        return False
    return True


gefiltert = [e for e in daten if passt(e)]


def sortiere(xs: list[dict]) -> list[dict]:
    return sorted(xs, key=lambda e: (-(e.get("bewertung") or {}).get("passung", 0), tage_bis(e.get("frist")) or 999))


dl = sortiere([e for e in gefiltert if e["gefaess"] == "dienstleistung"])
fo = sortiere([e for e in gefiltert if e["gefaess"] == "forschung"])
mit_frist = sorted([e for e in gefiltert if tage_bis(e.get("frist")) is not None], key=lambda e: tage_bis(e["frist"]))

tab_dl, tab_fo, tab_fr, tab_nu = st.tabs(
    [f"Dienstleistung ({len(dl)})", f"Forschung und Förderung ({len(fo)})", f"Fristen ({len(mit_frist)})", "Nutzung und Kosten"]
)

with tab_dl:
    st.caption("Öffentliche Beschaffungen von simap.ch, Auftragsart Dienstleistung, Studien und Marktabklärungen.")
    if not dl:
        st.write("Keine Einträge für die gewählten Filter.")
    for e in dl:
        karte(e)

with tab_fo:
    st.caption("Ausschreibungen von SNF, Innosuisse und Stiftungen. Fristen aus Webseiten sind extrahiert und zu prüfen.")
    if not fo:
        st.write("Keine Einträge für die gewählten Filter.")
    for e in fo:
        karte(e)

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
        st.dataframe(
            tabelle,
            hide_index=True,
            width="stretch",
            column_config={"Link": st.column_config.LinkColumn("Link", display_text="öffnen")},
        )
        st.download_button(
            "Als CSV herunterladen", tabelle.to_csv(index=False).encode("utf-8-sig"), "fristen.csv", "text/csv"
        )
    else:
        st.write("Keine Fristen in der Auswahl.")

with tab_nu:
    st.caption(
        "Tokenverbrauch pro Lauf. Damit lassen sich die Kosten bei einem kostenpflichtigen Anbieter hochrechnen."
    )
    if nutzung:
        df = pd.DataFrame(nutzung)
        df["tag"] = df["zeit"].str[:10]
        pro_tag = df.groupby("tag").agg(aufrufe=("zweck", "count"), tokens=("total_tokens", "sum")).reset_index()
        st.bar_chart(pro_tag, x="tag", y="tokens")
        c1, c2, c3 = st.columns(3)
        c1.metric("Aufrufe gesamt", len(df))
        c2.metric("Tokens gesamt", f"{int(df['total_tokens'].fillna(0).sum()):,}".replace(",", "'"))
        tage_n = max(pro_tag.shape[0], 1)
        c3.metric("Tokens pro Tag (Mittel)", f"{int(df['total_tokens'].fillna(0).sum() / tage_n):,}".replace(",", "'"))
        preis = st.number_input("Preis in CHF pro 1 Mio. Tokens (zum Hochrechnen)", 0.0, 100.0, 1.0, 0.1)
        st.write(
            f"Geschätzte Kosten pro Monat bei diesem Preis: **CHF "
            f"{df['total_tokens'].fillna(0).sum() / tage_n * 30 / 1_000_000 * preis:.2f}**"
        )
    else:
        st.write("Noch keine Aufrufe des Sprachmodells protokolliert.")
    stat = status.get("simap_letzte_statistik")
    if stat:
        st.write(
            f"Letzter simap-Lauf: {stat.get('treffer')} Projekte im Zeitraum, {stat.get('detailabrufe')} Details geladen, "
            f"{stat.get('relevant_vorfilter')} nach Vorfilter."
        )

bericht = DATA_DIR / "letzter_bericht.html"
if bericht.exists():
    with st.expander("Letzten versendeten Bericht anzeigen"):
        st.html(bericht.read_text(encoding="utf-8"))
