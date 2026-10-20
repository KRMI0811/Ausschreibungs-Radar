"""Modellunabhängige Anbindung an ein Sprachmodell.

Genutzt wird die OpenAI-kompatible Schnittstelle /v1/chat/completions. Sie wird
von Public AI (Apertus), Azure OpenAI, OpenAI, Mistral und vielen anderen
angeboten. Ein Wechsel erfordert nur andere Umgebungsvariablen:

    LLM_BASE_URL   z. B. https://api.publicai.co/v1
    LLM_API_KEY    geheimer Schlüssel (als GitHub Secret hinterlegen)
    LLM_MODEL      z. B. swiss-ai/apertus-v1.5-70b

Jeder Aufruf wird mit Tokenverbrauch in data/nutzung.jsonl protokolliert, damit
das Institut die Kosten anderer Anbieter realistisch hochrechnen kann.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import requests

from .config import DATA_DIR, USER_AGENT, env, jetzt_iso

log = logging.getLogger(__name__)

STANDARD_BASE_URL = "https://api.publicai.co/v1"
STANDARD_MODELL = "swiss-ai/apertus-v1.5-70b"


class LLMNichtKonfiguriert(RuntimeError):
    pass


class LLM:
    def __init__(self) -> None:
        self.base_url = (env("LLM_BASE_URL", STANDARD_BASE_URL) or "").rstrip("/")
        self.api_key = env("LLM_API_KEY")
        self.modell = env("LLM_MODEL", STANDARD_MODELL)
        self.timeout = float(env("LLM_TIMEOUT", "120") or 120)
        self.aufrufe = 0
        self.tokens = 0

    @property
    def verfuegbar(self) -> bool:
        return bool(self.api_key)

    def chat(self, system: str, nutzer: str, zweck: str, max_tokens: int = 1400) -> str:
        if not self.api_key:
            raise LLMNichtKonfiguriert("LLM_API_KEY fehlt")
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,  # Public AI verlangt einen User-Agent
        }
        nutzlast = {
            "model": self.modell,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": nutzer}],
            "temperature": 0.1,
            "max_tokens": max_tokens,
        }
        fehler: Exception | None = None
        for versuch in range(3):
            try:
                t0 = time.time()
                r = requests.post(url, headers=headers, json=nutzlast, timeout=self.timeout)
                if r.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(f"HTTP {r.status_code}: {r.text[:200]}", response=r)
                r.raise_for_status()
                daten = r.json()
                antwort = daten["choices"][0]["message"]["content"] or ""
                self._protokolliere(daten.get("usage") or {}, zweck, time.time() - t0)
                return antwort
            except (requests.RequestException, KeyError, ValueError) as e:
                fehler = e
                time.sleep(5 * (versuch + 1))
        raise RuntimeError(f"Sprachmodell nicht erreichbar: {fehler}")

    def chat_json(self, system: str, nutzer: str, zweck: str, max_tokens: int = 1400) -> Any:
        """Wie chat(), erwartet aber JSON. Ein zweiter Versuch korrigiert ungültiges JSON."""
        antwort = self.chat(system, nutzer, zweck, max_tokens)
        try:
            return extrahiere_json(antwort)
        except ValueError:
            log.info("Antwort war kein gültiges JSON, zweiter Versuch")
            antwort = self.chat(
                system,
                nutzer + "\n\nWICHTIG: Antworte ausschliesslich mit gültigem JSON, ohne Erklärtext.",
                zweck + "_wiederholung",
                max_tokens,
            )
            return extrahiere_json(antwort)

    def _protokolliere(self, usage: dict[str, Any], zweck: str, sekunden: float) -> None:
        self.aufrufe += 1
        gesamt = int(usage.get("total_tokens") or 0)
        self.tokens += gesamt
        DATA_DIR.mkdir(exist_ok=True)
        with open(DATA_DIR / "nutzung.jsonl", "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "zeit": jetzt_iso(),
                        "modell": self.modell,
                        "zweck": zweck,
                        "prompt_tokens": usage.get("prompt_tokens"),
                        "completion_tokens": usage.get("completion_tokens"),
                        "total_tokens": gesamt,
                        "sekunden": round(sekunden, 1),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


_DENK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)


def extrahiere_json(text: str) -> Any:
    """Findet das erste JSON-Objekt oder -Array in einer Modellantwort."""
    t = _DENK_RE.sub("", text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t.strip(), flags=re.I)
    try:
        return json.loads(t)
    except ValueError:
        pass
    for start_zeichen, end_zeichen in (("{", "}"), ("[", "]")):
        a = t.find(start_zeichen)
        e = t.rfind(end_zeichen)
        if a != -1 and e > a:
            try:
                return json.loads(t[a : e + 1])
            except ValueError:
                continue
    raise ValueError("Kein gültiges JSON in der Antwort gefunden")
