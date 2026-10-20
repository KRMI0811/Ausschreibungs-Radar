# Ausschreibungsradar IOL

Prototyp eines Agenten, der für das IOL der OST täglich passende Ausschreibungen
sucht, verständlich zusammenfasst, bewertet und per E-Mail oder Microsoft Teams
meldet. Fristerinnerungen folgen automatisch 14, 7 und 3 Tage vor der Eingabe.

Das Radar unterscheidet zwei Gefässe.

| Gefäss | Quellen | Zugriff |
|---|---|---|
| **Dienstleistung** | simap.ch (öffentliche Beschaffungen) | offizielle Lese-API, ohne Login |
| **Forschung und Förderung** | SNF, Innosuisse, Hasler Stiftung, Gebert Rüf Stiftung, Hirschmann Stiftung | RSS-Feed (SNF) bzw. Beobachtung von Webseiten |

## So funktioniert es

```
GitHub Actions (täglich 07:15 UTC)
  └─ python -m radar.run
       1. simap.ch: alle neuen Dienstleistungs-, Studien- und RFI-Publikationen der letzten Tage
       2. Vorfilter: Kernbegriffe aus config/profil.yaml, passende CPV-Codes, Ausschlussbegriffe
       3. Sprachmodell (Standard Apertus über Public AI): Kurzfassung, Anforderungen,
          Passung 0 bis 100, Erfolgschance, Aufwand, Empfehlung
       4. Förderquellen: SNF-Feed, geänderte Webseiten werden ausgewertet
       5. Bericht per E-Mail und/oder Teams, Fristerinnerungen
       6. Ergebnisse werden in data/ gespeichert und ins Repository committet
Streamlit-App (app.py)
  └─ liest data/ und zeigt Treffer, Fristen, Kalenderexport und Tokenverbrauch
```

Wichtige Gestaltungsentscheide

* **Fakten kommen aus den Quelldaten, nie vom Sprachmodell.** Frist, Auftraggeber,
  Link und Kriterien stammen direkt von simap. Das Modell fasst zusammen und
  schätzt ein. Bei Förderausschreibungen aus Webseiten ist eine extrahierte Frist
  als «bitte prüfen» markiert.
* **Modellunabhängig.** Der Wechsel zu Azure OpenAI, OpenAI, Mistral o. ä.
  erfordert nur andere Werte in `LLM_BASE_URL`, `LLM_MODEL` und `LLM_API_KEY`.
* **Kosten messbar.** Jeder Modellaufruf wird mit Tokens in `data/nutzung.jsonl`
  protokolliert. Die App rechnet die Monatskosten zu einem frei wählbaren Preis hoch.
* **Streamlit ist nur die Ansicht.** Suche und Versand laufen in GitHub Actions.
  Schläft die App auf Streamlit Community Cloud, gehen keine Meldungen verloren.

## Einrichtung (ca. 30 Minuten)

### 1. Repository anlegen
1. Auf GitHub ein neues **privates** Repository anlegen, z. B. `iol-ausschreibungsradar`.
2. Alle Dateien dieses Ordners hochladen (inklusive der versteckten Ordner
   `.github` und `.streamlit`). Am einfachsten mit GitHub Desktop oder
   `git push`. Beim Upload über die Weboberfläche werden versteckte Ordner
   teilweise nicht mitgenommen, bitte prüfen.

### 2. Schlüssel für das Sprachmodell
1. Auf [platform.publicai.co](https://platform.publicai.co) ein Konto anlegen und
   einen API-Schlüssel erzeugen.
2. Im Repository unter *Settings → Secrets and variables → Actions*
   * **Secret** `LLM_API_KEY` = Ihr Schlüssel
   * optional **Variable** `LLM_MODEL` (Standard `swiss-ai/apertus-v1.5-70b`)
   * optional **Variable** `LLM_BASE_URL` (Standard `https://api.publicai.co/v1`)

Ohne Schlüssel läuft das Radar trotzdem, dann aber nur mit dem regelbasierten
Vorfilter, ohne Zusammenfassung und Bewertung.

### 3. Versand einrichten (eine Variante genügt)

**Microsoft Teams (empfohlen, wenn die IT es zulässt)**
1. Im gewünschten Teams-Kanal *Workflows* öffnen und die Vorlage zum Posten
   in einen Kanal bei Empfang einer Webhook-Anforderung wählen (englisch
   «Post to a channel when a webhook request is received»).
2. Die erzeugte URL als Secret `TEAMS_WEBHOOK_URL` speichern.

**E-Mail über SMTP**
Secrets `SMTP_HOST`, `SMTP_PORT` (meist 587), `SMTP_USER`, `SMTP_PASSWORD`,
`MAIL_TO` (mehrere Adressen mit Komma) und optional `MAIL_FROM`.
Hinweis: Bei Microsoft 365 ist SMTP mit Benutzername und Passwort in vielen
Organisationen deaktiviert. Klären Sie mit der IT, ob ein Funktionspostfach
dafür freigegeben werden kann. Andernfalls ist Teams der einfachere Weg.

### 4. Ersten Lauf starten
1. Im Repository *Actions → Ausschreibungsradar → Run workflow* wählen.
2. Beim ersten Mal «Nur sammeln» anhaken. Der erste Lauf durchsucht die letzten
   21 Tage und dauert einige Minuten.
3. Danach in `data/ausschreibungen.json` einen Eintrag öffnen und den Link
   `link_quelle` im Browser testen. Falls simap das Linkformat geändert hat,
   in `config/quellen.yaml` den Wert `web_link` anpassen.

Ab dann läuft das Radar täglich automatisch.

### 5. Streamlit-App veröffentlichen
1. Auf [share.streamlit.io](https://share.streamlit.io) mit GitHub anmelden.
2. *Create app*, Repository wählen, Hauptdatei `app.py`.
3. Apps aus privaten Repositories sind zunächst nur für Sie sichtbar. Über
   *Share* laden Sie Kolleginnen und Kollegen per E-Mail ein. Auf Community
   Cloud ist pro Konto nur **eine** private App möglich.

Lokal geht es auch: `pip install -r requirements.txt` und `streamlit run app.py`.

## Anpassen

* **`config/profil.yaml`** Kernthemen und Suchbegriffe, Ausschlussbegriffe,
  CPV-Codes, Zielgruppen, bevorzugte Kantone und Schwellenwerte. Das ist der
  wichtigste Hebel für gute Treffer.
* **`config/quellen.yaml`** simap-Einstellungen und Förderquellen. Neue
  Stiftungen lassen sich mit `typ: webseite` und einer URL ergänzen, am besten
  die Seite, auf der die Stiftung ihre Programme oder Calls auflistet.

## Qualität prüfen und Modelle vergleichen

Legen Sie `config/referenz.csv` mit 30 bis 50 vergangenen Ausschreibungen an
(Spalten `titel;beschreibung;relevant`) und starten Sie
`python -m radar.evaluation`. Ausgegeben werden Trefferquote, Genauigkeit und
Tokenverbrauch. Wiederholen Sie das mit einem anderen `LLM_MODEL`, um Modelle
auf Basis von Zahlen statt Eindrücken zu vergleichen.

## Grenzen des Prototyps (bitte lesen)

* **Klickzahlen und Anzahl Bewerbungen sind nicht verfügbar.** simap
  veröffentlicht weder Aufrufe noch die Zahl eingegangener Angebote vor dem
  Zuschlag. Diese Funktion ist daher bewusst nicht umgesetzt.
* **Ausschreibungsunterlagen werden nicht gelesen.** Viele Eignungs- und
  Zuschlagskriterien stehen nur in den PDF-Unterlagen auf simap. Das Radar
  weist darauf hin («Kriterien stehen nur in den Unterlagen»).
* **Erfolgschance und Aufwand sind Einschätzungen.** Sie beruhen auf dem
  Ausschreibungstext, dem Institutsprofil und regelbasierten Faktoren
  (Verfahrensart, Preisgewicht, Restzeit, Region). Sie ersetzen keine
  Go/No-Go-Entscheidung im Team.
* **Stiftungen** veröffentlichen selten datierte Calls und keine Feeds. Das
  Radar erkennt Änderungen auf den beobachteten Seiten. Die Website der Stiftung
  Mercator Schweiz benötigt JavaScript und ist deshalb deaktiviert. Gemeinsame
  Programme wie «Hasler Mercator Digitalization & Democracy» werden über die
  Hasler Stiftung erfasst.
* **Robots und Nutzungsbedingungen.** simap-Daten dürfen laut AGB erst ab
  08:00 Uhr des Erscheinungstags weitergegeben werden. Der Workflow läuft
  deshalb später. Quelle der Daten ist simap.ch (Verein simap.ch); massgeblich
  sind die Originalpublikationen.
* **Public AI** stellt Rechenleistung ohne Verfügbarkeitsgarantie bereit. Fällt
  der Dienst aus, läuft der Lauf mit Vorfilter weiter und meldet den Fehler.

## Projektstruktur

```
app.py                     Streamlit-Ansicht
config/profil.yaml         Institutsprofil, Begriffe, Schwellen
config/quellen.yaml        Quellen
radar/run.py               Hauptlauf
radar/simap.py             simap-API
radar/foerder.py           RSS und Webseiten
radar/vorfilter.py         regelbasierter Vorfilter
radar/bewertung.py         Prompts und Bewertung
radar/llm.py               OpenAI-kompatible Modellanbindung mit Protokoll
radar/benachrichtigung.py  E-Mail und Teams
radar/evaluation.py        Qualitätsprüfung mit Referenzliste
data/                      Ergebnisse (wird vom Workflow gepflegt)
tests/                     Tests ohne Netzwerk (pytest)
```
