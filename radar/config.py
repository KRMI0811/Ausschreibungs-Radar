"""Laden der Konfiguration und gemeinsame Hilfsfunktionen."""

from __future__ import annotations

import html
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
# Die App setzt RADAR_DATA_DIR auf einen Arbeitsordner, damit eine Suche aus der
# App die Dateien im Repository nicht verändert. Der GitHub-Workflow nutzt data/.
DATA_DIR = Path(os.environ.get("RADAR_DATA_DIR") or ROOT / "data")
ZEITZONE = ZoneInfo("Europe/Zurich")
USER_AGENT = "IOL-Ausschreibungsradar/0.1 (OST, Prototyp; Kontakt via GitHub-Repository)"


def lade_yaml(name: str) -> dict[str, Any]:
    with open(CONFIG_DIR / name, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def profil() -> dict[str, Any]:
    return lade_yaml("profil.yaml")


def quellen() -> dict[str, Any]:
    return lade_yaml("quellen.yaml")


def env(name: str, default: str | None = None) -> str | None:
    wert = os.environ.get(name)
    return wert if wert not in (None, "") else default


def heute() -> date:
    return datetime.now(ZEITZONE).date()


def jetzt_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_NL_RE = re.compile(r"\n{3,}")


def html_zu_text(roh: str | None) -> str:
    """Wandelt einfaches HTML (wie in simap-Beschreibungen) in lesbaren Text."""
    if not roh:
        return ""
    t = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>", "\n", roh)
    t = re.sub(r"(?i)<li[^>]*>", "• ", t)
    t = _TAG_RE.sub("", t)
    t = html.unescape(t)
    t = _WS_RE.sub(" ", t)
    t = "\n".join(z.strip() for z in t.split("\n"))
    return _NL_RE.sub("\n\n", t).strip()


def mehrsprachig(feld: Any, sprache: str = "de") -> str:
    """simap liefert Texte als {"de": ..., "fr": ..., ...}. Nimmt Deutsch, sonst die erste vorhandene Sprache."""
    if isinstance(feld, str):
        return feld
    if not isinstance(feld, dict):
        return ""
    for s in (sprache, "de", "fr", "it", "en"):
        if feld.get(s):
            return str(feld[s])
    return ""


def tage_bis(frist_iso: str | None) -> int | None:
    """Ganze Kalendertage von heute bis zur Frist (negativ, wenn abgelaufen)."""
    if not frist_iso:
        return None
    try:
        if len(frist_iso) == 10:
            d = date.fromisoformat(frist_iso)
        else:
            dt = datetime.fromisoformat(frist_iso.replace("Z", "+00:00"))
            if dt.tzinfo:
                dt = dt.astimezone(ZEITZONE)
            d = dt.date()
    except ValueError:
        return None
    return (d - heute()).days


def frist_lesbar(frist_iso: str | None) -> str:
    if not frist_iso:
        return "unbekannt"
    try:
        if len(frist_iso) == 10:
            return date.fromisoformat(frist_iso).strftime("%d.%m.%Y")
        dt = datetime.fromisoformat(frist_iso.replace("Z", "+00:00"))
        if dt.tzinfo:
            dt = dt.astimezone(ZEITZONE)
        return dt.strftime("%d.%m.%Y, %H:%M Uhr")
    except ValueError:
        return frist_iso
