# Unser Browser-Pokerbot

Erste Version für lokales Üben mit Spielgeld. Ein echter Chromium-Browser liest den sichtbaren Tisch, entscheidet für deinen Platz und klickt Check, Call, Fold oder Raise. Der eigene Tisch spielt Heads-up No-Limit Texas Hold’em gegen einen einfachen Übungsgegner.

## Start auf diesem Mac

`Start.command` doppelt anklicken. Im geöffneten Browser **Übernahme starten** klicken. **Bot stoppen** unterbricht die automatische Übernahme; die aktuelle Hand kann man anschließend selbst weiterspielen. Der Bot beginnt neue Hände automatisch. Mit Strg+C im Terminal beendet man Tisch und Browser.

Alternativ:

```sh
export PLAYWRIGHT_BROWSERS_PATH="$PWD/.browser-cache"
.venv/bin/python -m practice.app
```

## Frischer Checkout

Python 3.11 oder neuer:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
PLAYWRIGHT_BROWSERS_PATH=.browser-cache .venv/bin/python -m playwright install chromium
PLAYWRIGHT_BROWSERS_PATH=.browser-cache .venv/bin/python -m practice.app
```

Diese Version benötigt keinen API-Schlüssel. Die Entscheidung nutzt Kartenbewertung und eine einfache Value-/Pot-Odds-Strategie. Die Equity bezieht sich auf unbekannte zufällige Gegnerkarten, nicht auf eine aus dem Wettverhalten abgeleitete Range. Keine belegte überlegene Spielstärke; keine selbstlernende KI.

## Was geprüft wird

- Eigene Karten und Board sind vollständig, gültig und ohne Duplikate.
- Raise auf einen Gesamtbetrag innerhalb der vom Tisch vorgegebenen Grenzen.
- Vor einem Klick erneut derselbe Zug; der Server verwirft veraltete Aktionen.
- Einsatzfeld nach Eingabe zurücklesen und Aktion anhand einer Tischbestätigung nachweisen.
- Stoppschalter auch während der Berechnung; keine versteckten Calls als Fehlerfallback.
- Gegnerkarten bleiben bis zum Showdown verborgen, auch im DOM/API-Zustand.

Desktop- und Mobilansicht sowie automatische Browseraktionen sind getestet. Die lokalen Tests prüfen außerdem All-ins, Chip-Erhaltung, Heads-up-Positionen, Split-Pots und Omaha-Draws.

```sh
PLAYWRIGHT_BROWSERS_PATH=.browser-cache .venv/bin/python -m unittest discover -s tests -v
```

## Stand und nächste Entwicklung

Aktiver Einstieg ist `practice.app`. Die übernommenen `agent.py`, `agent_v4.py` und weiteren Browseradapter bleiben experimentelle Altpfade. Ihr Konfigurationsimport und das gemeinsame Gegnerobjekt sind korrigiert; weitere im Prüfbericht beschriebene DOM-/Aktionsfehler sind noch offen. Der lokale Übungspfad nutzt diese Adapter nicht.

Die aktuelle Version steuert ausschließlich unseren lokalen Tisch. Poker Now und Replay Poker werden nicht automatisch geöffnet oder bespielt. Ein externer Anbieter benötigt einen eigenen getesteten Adapter und einen für Automatisierung freigegebenen Übungstisch. Der bisherige Replay-Tisch ist keine Zielseite dieser Version.

Weitere Arbeiten: versionierter Anbieteradapter, Gegner-Ranges anhand deduplizierter Handereignisse, größere Strategiebewertung gegen feste Vergleichsgegner und gespeicherte Handhistorien. Erst nach diesen Tests lässt sich eine stärkere Strategie seriös bewerten.

Fork: https://github.com/asm554/poker-now-agent. MIT-Lizenz des Ursprungsprojekts bleibt erhalten.
