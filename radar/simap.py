"""Zugriff auf die öffentliche Lese-API von simap.ch.

Grundlagen (Stand Oktober 2026):
* Suche:   GET /publications/v2/project/project-search   (ohne Login nutzbar)
* Details: GET /publications/v1/project/{projectId}/publication-details/{publicationId}
* Paginierung über den Wert pagination.lastItem.
* Der Server setzt beim ersten Aufruf ein Session-Cookie. Eine requests.Session
  mit Cookie-Speicher ist deshalb nötig.

Gemäss AGB von simap.ch dürfen Daten Dritten erst ab 08:00 Uhr des
Erscheinungstags bekanntgegeben werden. Der Workflow läuft deshalb nach 08:00.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any, Iterator

import requests

from .config import USER_AGENT, heute, html_zu_text, mehrsprachig

log = logging.getLogger(__name__)

# Lesbare Bezeichnungen für Felder der simap-API
PUB_ARTEN = {
    "advance_notice": "Vorankündigung",
    "request_for_information": "Marktabklärung (RFI)",
    "tender": "Ausschreibung",
    "study_contract": "Studienauftrag",
    "competition": "Wettbewerb",
    "selective_offering_phase": "Angebotsphase selektives Verfahren",
    "participant_selection": "Präqualifikation",
}
VERFAHREN = {
    "open": "offenes Verfahren",
    "selective": "selektives Verfahren",
    "invitation": "Einladungsverfahren",
    "direct": "freihändiges Verfahren",
    "no_process": "ohne Verfahren",
}


class SimapClient:
    def __init__(self, basis_url: str, pause: float = 0.4, timeout: float = 30.0):
        self.basis = basis_url.rstrip("/")
        self.pause = pause
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers.update({"Accept": "application/json", "User-Agent": USER_AGENT})
        self._cookie_ok = False

    def _get(self, pfad: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._cookie_ok:
            # Erster Aufruf setzt das Session-Cookie (cookie-check von simap)
            try:
                self.s.get(f"{self.basis}/cantons/v1", params={"lang": "de"}, timeout=self.timeout)
            except requests.RequestException as e:
                log.warning("simap Cookie-Aufruf fehlgeschlagen: %s", e)
            self._cookie_ok = True
        letzter_fehler: Exception | None = None
        for versuch in range(4):
            try:
                r = self.s.get(f"{self.basis}{pfad}", params=params, timeout=self.timeout)
                if r.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(f"HTTP {r.status_code}", response=r)
                r.raise_for_status()
                time.sleep(self.pause)
                return r.json()
            except requests.HTTPError as e:
                code = e.response.status_code if e.response is not None else None
                if code is not None and 400 <= code < 500 and code != 429:
                    raise  # Clientfehler nicht wiederholen
                letzter_fehler = e
            except (requests.ConnectionError, requests.Timeout, ValueError) as e:
                letzter_fehler = e
            time.sleep(2 ** (versuch + 1))
        raise RuntimeError(f"simap nicht erreichbar ({pfad}): {letzter_fehler}")

    def suche(
        self,
        tage_zurueck: int,
        auftragsarten: list[str],
        publikationsarten: list[str],
        max_seiten: int = 60,
        suchbegriff: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Liefert alle Projekte, deren neueste Publikation im Zeitraum liegt.
        Mit suchbegriff nutzt Findus die Volltextsuche von simap."""
        params: dict[str, Any] = {
            "lang": "de",
            "projectSubTypes": auftragsarten,
            "newestPubTypes": publikationsarten,
            "newestPublicationFrom": (heute() - timedelta(days=tage_zurueck)).isoformat(),
        }
        if suchbegriff:
            params["search"] = suchbegriff
        letztes = None
        for _ in range(max_seiten):
            if letztes:
                params["lastItem"] = letztes
            daten = self._get("/publications/v2/project/project-search", params)
            projekte = daten.get("projects") or []
            yield from projekte
            neu = (daten.get("pagination") or {}).get("lastItem")
            if not projekte or not neu or neu == letztes:
                break
            letztes = neu

    def details(self, projekt_id: str, publikation_id: str) -> dict[str, Any]:
        return self._get(f"/publications/v1/project/{projekt_id}/publication-details/{publikation_id}")


def _kriterien(liste: list[dict[str, Any]] | None, max_zeichen: int = 600) -> list[str]:
    aus = []
    for k in liste or []:
        titel = html_zu_text(mehrsprachig(k.get("title")))
        text = html_zu_text(mehrsprachig(k.get("description")))
        gewicht = k.get("weighting")
        zeile = titel or ""
        if gewicht not in (None, ""):
            zeile += f" (Gewichtung {gewicht} %)"
        if k.get("isPriceCriterion"):
            zeile += " [Preis]"
        if text:
            zeile += (": " if zeile else "") + text[:max_zeichen]
        if zeile:
            aus.append(zeile)
    return aus


def normalisiere(treffer: dict[str, Any], details: dict[str, Any] | None, web_link: str) -> dict[str, Any]:
    """Bringt Suchtreffer und Details in das gemeinsame Format des Radars."""
    d = details or {}
    info = d.get("project-info") or {}
    beschaffung = d.get("procurement") or {}
    termine = d.get("dates") or {}
    kriterien = d.get("criteria") or {}
    amt = info.get("procOfficeAddress") or {}

    cpv = beschaffung.get("cpvCode") or (d.get("base") or {}).get("cpvCode") or {}
    cpv_codes = [cpv.get("code")] if cpv.get("code") else []
    cpv_labels = [mehrsprachig(cpv.get("label"))] if cpv.get("label") else []
    for zusatz in beschaffung.get("additionalCpvCodes") or []:
        if isinstance(zusatz, dict) and zusatz.get("code"):
            cpv_codes.append(zusatz["code"])
            cpv_labels.append(mehrsprachig(zusatz.get("label")))

    lose = []
    for los in treffer.get("lots") or []:
        t = mehrsprachig(los.get("lotTitle"))
        if t:
            lose.append(f"Los {los.get('lotNumber')}: {t}")

    beschreibung = html_zu_text(mehrsprachig(beschaffung.get("orderDescription")))
    ort = (beschaffung.get("orderAddress") or {})
    kanton = ort.get("cantonId") or amt.get("cantonId")

    org_url = mehrsprachig(amt.get("url")) or None
    if org_url and not org_url.startswith("http"):
        org_url = "https://" + org_url

    return {
        "id": f"simap:{treffer['id']}",
        "gefaess": "dienstleistung",
        "quelle": "simap.ch",
        "titel": mehrsprachig(treffer.get("title")) or mehrsprachig(info.get("title")),
        "auftraggeber": mehrsprachig(treffer.get("procOfficeName")) or mehrsprachig(amt.get("name")),
        "kanton": kanton,
        "projektnummer": treffer.get("projectNumber"),
        "publikationsnummer": treffer.get("publicationNumber"),
        "publikationsart": PUB_ARTEN.get(treffer.get("pubType"), treffer.get("pubType")),
        "publikationsart_code": treffer.get("pubType"),
        "verfahren": VERFAHREN.get(treffer.get("processType"), treffer.get("processType")),
        "auftragsart": treffer.get("projectSubType"),
        "publiziert_am": treffer.get("publicationDate"),
        "frist": termine.get("offerDeadline"),
        "frist_quelle": "simap" if termine.get("offerDeadline") else None,
        "fragen_bis": [q.get("date") for q in termine.get("qnas") or [] if q.get("date")],
        "link_quelle": web_link.format(id=treffer["id"]),
        "link_organisation": org_url,
        "kontakt_email": amt.get("email"),
        "cpv_codes": cpv_codes,
        "cpv_labels": cpv_labels,
        "lose": lose,
        "text": beschreibung[:6000],
        "eignungskriterien": _kriterien(kriterien.get("qualificationCriteria")),
        "zuschlagskriterien": _kriterien(kriterien.get("awardCriteria")),
        "preisgewicht": next(
            (k.get("weighting") for k in kriterien.get("awardCriteria") or [] if k.get("isPriceCriterion")),
            None,
        ),
        "kriterien_in_unterlagen": kriterien.get("awardCriteriaSelection") == "criteria_in_documents"
        or kriterien.get("qualificationCriteriaInDocuments") == "yes",
        "arge_erlaubt": (d.get("terms") or {}).get("consortiumAllowed"),
        "details_geladen": bool(details),
        # Steckbrief für die Aufwandsanalyse (standardisierte simap-Felder)
        "steckbrief": {
            "angebotssprachen": info.get("offerLanguages") or [],
            "eingabeart": info.get("offerTypes") or [],
            "dialog": info.get("dialog"),
            "vertragsbeginn_ende": ((beschaffung.get("contractPeriod") or {}).get("dateRange")
                                    or (beschaffung.get("executionPeriod") or {}).get("dateRange")),
            "verlaengerung": beschaffung.get("canContractBeExtended"),
            "optionen": beschaffung.get("options"),
            "varianten": beschaffung.get("variants"),
            "teilangebote": beschaffung.get("partialOffers"),
            "begehung": html_zu_text(mehrsprachig((d.get("terms") or {}).get("walkThroughNotes")))[:300],
            "angebotsgueltigkeit_tage": termine.get("offerValidityDeadlineDays"),
            "unterlagen_kostenpflichtig": info.get("documentsWithCosts"),
            "anzahl_lose": len(treffer.get("lots") or []),
            "subunternehmer_erlaubt": (d.get("terms") or {}).get("subContractorAllowed"),
            "verfahren_code": treffer.get("processType"),
        },
    }
