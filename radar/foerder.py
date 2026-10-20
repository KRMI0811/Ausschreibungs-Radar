"""Förderquellen: RSS-Feeds und beobachtete Webseiten."""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any
from urllib.parse import urljoin, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup

from .config import USER_AGENT, html_zu_text

log = logging.getLogger(__name__)
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "de-CH,de;q=0.9,en;q=0.7"}


def kurz_hash(*teile: str) -> str:
    return hashlib.sha1("|".join(t.strip().lower() for t in teile).encode("utf-8")).hexdigest()[:16]


def lade(url: str, timeout: float = 30.0) -> requests.Response:
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r


# ---------------------------------------------------------------- RSS

def lese_rss(quelle: dict[str, Any]) -> list[dict[str, Any]]:
    """Liest einen Feed. Fällt auf ersatz_url zurück, wenn der erste leer ist."""
    for url in [quelle["url"], quelle.get("ersatz_url")]:
        if not url:
            continue
        try:
            feed = feedparser.parse(lade(url).content)
        except requests.RequestException as e:
            log.warning("Feed %s nicht lesbar: %s", url, e)
            continue
        if feed.entries:
            return [_rss_eintrag(quelle, x) for x in feed.entries]
        log.info("Feed %s ohne Einträge", url)
    return []


def _rss_eintrag(quelle: dict[str, Any], x: Any) -> dict[str, Any]:
    link = x.get("link") or quelle.get("info_url") or quelle["url"]
    datum = None
    if x.get("published_parsed"):
        p = x.published_parsed
        datum = f"{p.tm_year:04d}-{p.tm_mon:02d}-{p.tm_mday:02d}"
    text = html_zu_text(x.get("summary") or x.get("description") or "")
    return {
        "id": f"foerder:{kurz_hash(quelle['traeger'], link)}",
        "gefaess": "forschung",
        "quelle": quelle["name"],
        "titel": html_zu_text(x.get("title") or "(ohne Titel)"),
        "auftraggeber": quelle["traeger"],
        "publiziert_am": datum,
        "frist": None,
        "frist_quelle": None,
        "link_quelle": link,
        "link_organisation": quelle.get("info_url") or _startseite(link),
        "text": text[:6000],
    }


def ergaenze_volltext(eintrag: dict[str, Any], max_zeichen: int = 6000) -> None:
    """RSS-Einträge sind oft knapp. Lädt bei Bedarf die verlinkte Seite nach."""
    if len(eintrag.get("text") or "") > 600:
        return
    try:
        text = seite_als_text(lade(eintrag["link_quelle"]).text, eintrag["link_quelle"])
        if text:
            eintrag["text"] = ((eintrag.get("text") or "") + "\n\n" + text)[:max_zeichen]
    except requests.RequestException as e:
        log.info("Volltext für %s nicht ladbar: %s", eintrag["link_quelle"], e)


# ---------------------------------------------------------------- Webseiten

_UNNOETIG = ["script", "style", "noscript", "nav", "header", "footer", "form", "svg", "iframe"]


def seite_als_text(html: str, basis_url: str) -> str:
    """Hauptinhalt einer Seite als Text, Links als [Text](absolute URL)."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(_UNNOETIG):
        tag.decompose()
    for sel in ['[role="navigation"]', '[aria-label*="breadcrumb" i]', ".cookie", "#cookie", ".skip-link"]:
        for tag in soup.select(sel):
            tag.decompose()
    haupt = soup.find("main") or soup.find(attrs={"role": "main"}) or soup.body or soup
    for a in haupt.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            a.unwrap()
            continue
        text = a.get_text(" ", strip=True)
        if text:
            a.replace_with(f"[{text}]({urljoin(basis_url, href)})")
    text = haupt.get_text("\n", strip=True)
    text = re.sub(r"\n{2,}", "\n", text)
    return text


def _startseite(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}/"


def webseiten_eintrag(quelle: dict[str, Any], call: dict[str, Any]) -> dict[str, Any]:
    link = call.get("link") or quelle["url"]
    if link and not str(link).startswith("http"):
        link = urljoin(quelle["url"], link)
    frist = call.get("frist")
    if frist and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(frist)):
        frist = None
    return {
        "id": f"foerder:{kurz_hash(quelle['traeger'], call['titel'])}",
        "gefaess": "forschung",
        "quelle": quelle["name"],
        "titel": call["titel"],
        "auftraggeber": quelle["traeger"],
        "publiziert_am": None,
        "frist": frist,
        "frist_quelle": "aus Webseite extrahiert, bitte prüfen" if frist else None,
        "link_quelle": link,
        "link_organisation": quelle["url"],
        "text": call.get("beschreibung") or "",
    }
