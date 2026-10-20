"""Kleine Anbindung an die GitHub-API für die Streamlit-App.

Damit kann die App
* den Suchlauf (Workflow "Ausschreibungsradar") per Knopfdruck starten,
* den Status des letzten Laufs anzeigen,
* die aktuellen Ergebnisdateien direkt aus dem Repository lesen.

Benötigt in den Streamlit-Secrets:
    GITHUB_TOKEN  = Fine-grained Token, nur für dieses Repository,
                    Berechtigungen "Actions: Read and write" und "Contents: Read-only"
    GITHUB_REPO   = "konto/iol-ausschreibungsradar"
    GITHUB_BRANCH = "main"   (optional)
"""

from __future__ import annotations

from typing import Any

import requests

API = "https://api.github.com"
WORKFLOW = "radar.yml"


class GitHub:
    def __init__(self, token: str, repo: str, branch: str = "main"):
        self.repo = repo.strip().strip("/")
        self.branch = branch or "main"
        self.s = requests.Session()
        self.s.headers.update(
            {
                "Authorization": f"Bearer {token.strip()}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "IOL-Ausschreibungsradar",
            }
        )

    def starte_suche(self, ohne_versand: bool = False) -> None:
        r = self.s.post(
            f"{API}/repos/{self.repo}/actions/workflows/{WORKFLOW}/dispatches",
            json={"ref": self.branch, "inputs": {"ohne_versand": "true" if ohne_versand else "false", "tage": ""}},
            timeout=20,
        )
        if r.status_code != 204:
            raise RuntimeError(_fehlertext(r))

    def letzter_lauf(self) -> dict[str, Any] | None:
        r = self.s.get(
            f"{API}/repos/{self.repo}/actions/workflows/{WORKFLOW}/runs",
            params={"per_page": 1, "branch": self.branch},
            timeout=20,
        )
        if r.status_code != 200:
            raise RuntimeError(_fehlertext(r))
        laeufe = r.json().get("workflow_runs") or []
        return laeufe[0] if laeufe else None

    def lade_datei(self, pfad: str) -> str | None:
        """Liest eine Datei des Repositorys (Rohinhalt). None, wenn sie fehlt."""
        r = self.s.get(
            f"{API}/repos/{self.repo}/contents/{pfad}",
            params={"ref": self.branch},
            headers={"Accept": "application/vnd.github.raw+json"},
            timeout=30,
        )
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise RuntimeError(_fehlertext(r))
        r.encoding = "utf-8"
        return r.text


def _fehlertext(r: requests.Response) -> str:
    hinweise = {
        401: "Token ungültig oder abgelaufen.",
        403: "Token hat nicht die nötigen Rechte (Actions: Read and write, Contents: Read).",
        404: "Repository oder Workflow nicht gefunden. GITHUB_REPO und Branch prüfen.",
        422: "Workflow kann nicht gestartet werden. Branch prüfen.",
    }
    return f"GitHub {r.status_code}: {hinweise.get(r.status_code, r.text[:200])}"
