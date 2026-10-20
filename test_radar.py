"""Tests ohne Netzwerk. Die simap-Fixtures sind echte, am 29.08.2026 aufgezeichnete
API-Antworten (Quelle: github.com/malkreide/swiss-procurement-mcp, MIT-Lizenz)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from radar import bewertung, foerder, vorfilter
from radar.config import html_zu_text, profil, tage_bis
from radar.llm import extrahiere_json
from radar.simap import normalisiere

FIX = Path(__file__).parent / "fixtures"
WEB = "https://www.simap.ch/de/project-detail/{id}"


def lade(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def test_normalisiere_echte_simap_antwort():
    treffer = lade("project_search.json")["projects"][0]
    details = lade("publication_details_tender.json")
    e = normalisiere(treffer, details, WEB)
    assert e["id"] == f"simap:{treffer['id']}"
    assert e["link_quelle"].endswith(treffer["id"])
    assert e["frist"] == "2026-10-16T23:59:00+02:00"
    assert e["link_organisation"] == "https://www.sz.ch/tba"
    assert e["kontakt_email"] == "submissionen.tba@sz.ch"
    assert e["cpv_codes"] == ["45000000"]
    assert any("ISO 9001" in k for k in e["eignungskriterien"])
    assert "<p>" not in e["text"]


def test_bauauftrag_faellt_im_vorfilter_durch():
    treffer = lade("project_search.json")["projects"][0]
    e = normalisiere(treffer, lade("publication_details_tender.json"), WEB)
    punkte, themen, _ = vorfilter.bewerte(e, profil())
    assert punkte < profil()["schwellen"]["vorfilter_min"]


def test_beratungsauftrag_besteht_vorfilter():
    e = {
        "titel": "Organisationsentwicklung und Führungsschulung Stadtverwaltung",
        "text": "Begleitung eines Veränderungsprozesses inkl. Mitarbeitendenbefragung.",
        "cpv_codes": ["79411000"],
    }
    punkte, themen, ausschluss = vorfilter.bewerte(e, profil())
    assert ausschluss is None
    assert punkte >= 9
    assert "Organisationsentwicklung" in themen and "Leadership und Führung" in themen


def test_ausschlussbegriff():
    e = {"titel": "Reinigung Personalrestaurant", "text": "personal", "cpv_codes": []}
    punkte, _, grund = vorfilter.bewerte(e, profil())
    assert punkte == 0 and grund


def test_json_aus_modellantwort():
    assert extrahiere_json('```json\n{"passung": 70}\n```') == {"passung": 70}
    assert extrahiere_json('<think>hmm</think> Hier: {"a": [1, 2]} Ende') == {"a": [1, 2]}
    assert extrahiere_json('[{"titel": "x"}]') == [{"titel": "x"}]
    with pytest.raises(ValueError):
        extrahiere_json("keine Ahnung")


class FakeLLM:
    modell = "test"
    verfuegbar = True

    def __init__(self, antwort):
        self.antwort = antwort

    def chat_json(self, system, nutzer, zweck, max_tokens=1400):
        assert "INSTITUT" in nutzer or "Webseite" in nutzer
        return self.antwort


def test_bewertung_wird_bereinigt():
    llm = FakeLLM({"passung": "140", "erfolgschance": "Hoch", "aufwand": "riesig", "anforderungen": "Referenzen"})
    e = {"titel": "x", "gefaess": "dienstleistung", "frist": None, "verfahren": "offenes Verfahren"}
    b = bewertung.bewerte_dienstleistung(llm, e, profil())
    assert b["passung"] == 100
    assert b["erfolgschance"] == "hoch"
    assert b["aufwand"] == "unklar"
    assert b["anforderungen"] == ["Referenzen"]
    assert any("Offenes Verfahren" in f for f in b["rahmenfaktoren"])


def test_webseite_zu_text_mit_links():
    html = """<html><body><nav><a href="/x">Menü</a></nav><main>
    <h1>Programme</h1><p>Neuer Call: <a href="/calls/ki">Responsible AI</a>, Frist 1.2.2027</p>
    </main><footer>Impressum</footer></body></html>"""
    t = foerder.seite_als_text(html, "https://example.org/foerderung/")
    assert "[Responsible AI](https://example.org/calls/ki)" in t
    assert "Menü" not in t and "Impressum" not in t


def test_webseiten_eintrag_ungueltige_frist():
    q = {"name": "Test", "traeger": "Stiftung X", "url": "https://example.org/"}
    e = foerder.webseiten_eintrag(q, {"titel": "Call A", "link": "/a", "frist": "Ende Jahr"})
    assert e["frist"] is None and e["link_quelle"] == "https://example.org/a"


def test_html_und_fristen():
    assert html_zu_text("<p>A&amp;B</p><ul><li>eins</li></ul>") == "A&B\n• eins"
    assert tage_bis(None) is None
    assert tage_bis("2000-01-01") < 0


def test_wortanfang_und_neue_themen():
    p = profil()
    _, themen, _ = vorfilter.bewerte({"titel": "Einführung der 4-Tage-Woche", "text": "", "cpv_codes": []}, p)
    assert themen == ["Arbeitsgestaltung und Work-Life-Balance"]  # "Einführung" ist keine Führung
    _, themen, _ = vorfilter.bewerte({"titel": "Public Corporate Governance", "text": "", "cpv_codes": []}, p)
    assert "Governance" in themen
    punkte, _, _ = vorfilter.bewerte({"titel": "Kiosk Betrieb", "text": "", "cpv_codes": []}, p)
    assert punkte == 0
