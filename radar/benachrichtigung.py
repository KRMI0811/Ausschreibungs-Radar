"""Bericht erstellen und per E-Mail und/oder Microsoft Teams verschicken.

Konfiguration über Umgebungsvariablen (als GitHub Secrets hinterlegen):

E-Mail (SMTP mit STARTTLS):
    SMTP_HOST, SMTP_PORT (Standard 587), SMTP_USER, SMTP_PASSWORD,
    MAIL_FROM (Standard = SMTP_USER), MAIL_TO (mehrere mit Komma trennen)

Microsoft Teams (Workflow "Bei Empfang einer Webhookanforderung in einem Kanal posten"):
    TEAMS_WEBHOOK_URL

Ist nichts konfiguriert, wird der Bericht nur als Datei data/letzter_bericht.html
gespeichert und in der App angezeigt.
"""

from __future__ import annotations

import html
import logging
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any

import requests

from .config import DATA_DIR, env, frist_lesbar, heute, tage_bis

log = logging.getLogger(__name__)

FARBE = {"hoch": "#1f7a3f", "mittel": "#a86b00", "niedrig": "#9b2c2c", "gering": "#1f7a3f", "unklar": "#666"}


def _e(x: Any) -> str:
    return html.escape(str(x)) if x not in (None, "") else ""


def _karte_html(e: dict[str, Any], hinweis: str = "") -> str:
    b = e.get("bewertung") or {}
    tage = tage_bis(e.get("frist"))
    frist = frist_lesbar(e.get("frist"))
    if tage is not None and tage >= 0:
        frist += f" (noch {tage} Tage)"
    if e.get("frist_quelle") and e["frist_quelle"] != "simap":
        frist += f" <i>{_e(e['frist_quelle'])}</i>"
    anforderungen = "".join(f"<li>{_e(a)}</li>" for a in (b.get("anforderungen") or [])[:6])
    themen = ", ".join(b.get("passende_kernthemen") or e.get("vorfilter_themen") or [])
    org = (
        f' · <a href="{_e(e["link_organisation"])}">Website der Organisation</a>' if e.get("link_organisation") else ""
    )
    bewertung = ""
    if b:
        bewertung = (
            f'<p style="margin:6px 0">Passung <b>{b.get("passung", "?")}/100</b> · '
            f'Erfolgschance <b style="color:{FARBE.get(b.get("erfolgschance"), "#333")}">{_e(b.get("erfolgschance"))}</b> · '
            f'Aufwand <b>{_e(b.get("aufwand"))}</b> · Empfehlung: {_e(b.get("empfehlung"))}</p>'
            f'<p style="margin:4px 0;color:#555;font-size:13px">{_e(b.get("erfolgschance_begruendung"))} '
            f'{_e(b.get("aufwand_begruendung"))}</p>'
        )
    return f"""
<div style="border:1px solid #ddd;border-radius:8px;padding:12px 14px;margin:10px 0">
  {f'<div style="color:#a86b00;font-size:12px;font-weight:bold">{_e(hinweis)}</div>' if hinweis else ''}
  <div style="font-size:16px;font-weight:bold"><a href="{_e(e.get('link_quelle'))}">{_e(e.get('titel'))}</a></div>
  <div style="color:#555;font-size:13px">{_e(e.get('auftraggeber'))}{(' · ' + _e(e.get('kanton'))) if e.get('kanton') else ''}
   · {_e(e.get('publikationsart') or e.get('quelle'))}{(' · ' + _e(e.get('verfahren'))) if e.get('verfahren') else ''}</div>
  <p style="margin:8px 0"><b>Eingabefrist:</b> {frist}</p>
  <p style="margin:8px 0">{_e(b.get('kurzfassung') or (e.get('text') or '')[:400])}</p>
  {f'<p style="margin:4px 0"><b>Anforderungen</b></p><ul style="margin:0 0 6px 18px;padding:0">{anforderungen}</ul>' if anforderungen else ''}
  {bewertung}
  <div style="font-size:13px;color:#555">Kernthemen: {_e(themen) or 'keine erkannt'}</div>
  <div style="font-size:13px;margin-top:6px"><a href="{_e(e.get('link_quelle'))}">Zur Ausschreibung</a>{org}</div>
</div>"""


def baue_bericht(neu: list[dict[str, Any]], erinnerungen: list[tuple[dict[str, Any], int]]) -> tuple[str, str, str]:
    """Gibt (Betreff, HTML, Klartext) zurück."""
    dl = [e for e in neu if e["gefaess"] == "dienstleistung"]
    fo = [e for e in neu if e["gefaess"] == "forschung"]
    sortiert = lambda xs: sorted(xs, key=lambda e: -(e.get("bewertung") or {}).get("passung", e.get("vorfilter_score", 0)))

    teile = [
        '<div style="font-family:Segoe UI,Arial,sans-serif;max-width:760px;color:#222">',
        f"<h2 style=\"margin-bottom:4px\">Findus · Ausschreibungsradar IOL · {heute().strftime('%d.%m.%Y')}</h2>",
        f'<p style="color:#555;margin-top:0">{len(dl)} neue Dienstleistungsausschreibung(en), '
        f"{len(fo)} neue Förderausschreibung(en), {len(erinnerungen)} Fristerinnerung(en).</p>",
    ]
    if erinnerungen:
        teile.append("<h3>Fristerinnerungen</h3>")
        for e, t in erinnerungen:
            teile.append(_karte_html(e, f"Erinnerung: noch {t} Tage bis zur Eingabe"))
    if dl:
        teile.append("<h3>Dienstleistung und Beratung (simap.ch)</h3>")
        teile += [_karte_html(e) for e in sortiert(dl)]
    if fo:
        teile.append("<h3>Forschung und Förderung</h3>")
        teile += [_karte_html(e) for e in sortiert(fo)]
    teile.append(
        '<p style="color:#888;font-size:12px;margin-top:24px">Automatisch erstellt. Zusammenfassungen und '
        "Einschätzungen stammen von einem Sprachmodell und ersetzen nicht die Lektüre der Ausschreibung. "
        "Massgeblich sind die Originalpublikationen (simap.ch bzw. Förderstelle).</p></div>"
    )
    html_text = "\n".join(teile)

    zeilen = [f"Findus, Ausschreibungsradar IOL, {heute().strftime('%d.%m.%Y')}", ""]
    for e, t in erinnerungen:
        zeilen.append(f"ERINNERUNG ({t} Tage): {e['titel']} | Frist {frist_lesbar(e.get('frist'))} | {e.get('link_quelle')}")
    for e in sortiert(dl) + sortiert(fo):
        b = e.get("bewertung") or {}
        zeilen.append(f"- {e['titel']} ({e.get('auftraggeber')})")
        zeilen.append(f"  Frist: {frist_lesbar(e.get('frist'))} | Passung {b.get('passung', '?')} | {e.get('link_quelle')}")
    betreff = f"Findus: {len(dl) + len(fo)} neu, {len(erinnerungen)} Erinnerung(en)"
    return betreff, html_text, "\n".join(zeilen)


def speichere_bericht(html_text: str) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "letzter_bericht.html").write_text(html_text, encoding="utf-8")


def sende_mail(betreff: str, html_text: str, klartext: str) -> bool:
    host, empfaenger = env("SMTP_HOST"), env("MAIL_TO")
    if not host or not empfaenger:
        log.info("E-Mail nicht konfiguriert (SMTP_HOST oder MAIL_TO fehlt)")
        return False
    msg = EmailMessage()
    msg["Subject"] = betreff
    msg["From"] = env("MAIL_FROM", env("SMTP_USER"))
    msg["To"] = empfaenger
    msg.set_content(klartext)
    msg.add_alternative(html_text, subtype="html")
    port = int(env("SMTP_PORT", "587") or 587)
    with smtplib.SMTP(host, port, timeout=60) as s:
        s.starttls(context=ssl.create_default_context())
        if env("SMTP_USER"):
            s.login(env("SMTP_USER"), env("SMTP_PASSWORD", ""))
        s.send_message(msg)
    log.info("E-Mail an %s versendet", empfaenger)
    return True


def sende_teams(neu: list[dict[str, Any]], erinnerungen: list[tuple[dict[str, Any], int]]) -> bool:
    url = env("TEAMS_WEBHOOK_URL")
    if not url:
        return False
    bloecke: list[dict[str, Any]] = [
        {"type": "TextBlock", "size": "Large", "weight": "Bolder", "text": f"Findus · Ausschreibungsradar · {heute().strftime('%d.%m.%Y')}"}
    ]

    def zeile(e: dict[str, Any], vor: str = "") -> dict[str, Any]:
        b = e.get("bewertung") or {}
        text = (
            f"{vor}**[{e['titel']}]({e.get('link_quelle')})**  \n"
            f"{e.get('auftraggeber')} · Frist {frist_lesbar(e.get('frist'))} · Passung {b.get('passung', '?')}/100 · "
            f"Chance {b.get('erfolgschance', '?')} · Aufwand {b.get('aufwand', '?')}  \n"
            f"{b.get('kurzfassung') or ''}"
        )
        return {"type": "TextBlock", "wrap": True, "spacing": "Medium", "text": text}

    for e, t in erinnerungen[:10]:
        bloecke.append(zeile(e, f"⏰ noch {t} Tage: "))
    for gefaess, titel in (("dienstleistung", "Dienstleistung (simap.ch)"), ("forschung", "Forschung und Förderung")):
        teil = [e for e in neu if e["gefaess"] == gefaess][:12]
        if teil:
            bloecke.append({"type": "TextBlock", "weight": "Bolder", "spacing": "Large", "text": titel})
            bloecke += [zeile(e) for e in teil]
    nutzlast = {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": bloecke,
                },
            }
        ],
    }
    r = requests.post(url, json=nutzlast, timeout=30)
    if r.status_code >= 300:
        log.warning("Teams-Webhook antwortete mit %s: %s", r.status_code, r.text[:200])
        return False
    log.info("Teams-Nachricht versendet")
    return True
