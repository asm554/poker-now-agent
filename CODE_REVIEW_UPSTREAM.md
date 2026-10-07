# Codeprüfung poker-now-agent

Stand: 7. Oktober 2026. Geprüfter Commit: `f97a23f1b302457d5b0b6a579c8fb8f0980cc244`.
Quelle: https://github.com/clawhi-leonard/poker-now-agent/tree/f97a23f1b302457d5b0b6a579c8fb8f0980cc244

## Umfang und Urteil

README, agent.py, agent_v4.py, hand_eval.py, opponent_model.py, tracker.py, LICENSE sowie drei Testdateien über GitHub-Connector gelesen. Vollständigen Dateibaum geprüft. Einzelne reine Bewertungsfunktionen offline mit Python 3.11 ausgeführt. Kein Browserbot gestartet, keine Onlineaktionen, keine API-Aufrufe an Anthropic und keine Reparaturen. Die älteren Befunde zu scrape.py/act.py sind in CONTEXT_HANDOFF.md dokumentiert und wurden in dieser Fortsetzung nicht erneut gelesen.

Der dokumentierte Startpfad ist nicht zuverlässig lauffähig. Auch nach Behebung des Startfehlers fehlen korrekte Bewertung, belastbare Gegnerdaten und eine bestätigte Aktionsausführung. Keine belegte Aussage über Spielstärke.

## Bestätigte Befunde

1. **P1 – undefinierter tracker:** agent.py initialisiert opp_tracker, verwendet aber tracker beim Loggen und Entscheiden. Bei üblichen Zuständen mit aktiven Gegnern entsteht NameError. agent_v4.py definiert zwar seinen eigenen tracker, importiert local_decision jedoch aus agent.py. Python-Funktionen behalten den globalen Namensraum ihres Ursprungsmoduls; v4 behebt diesen Fehler daher nicht.
2. **P1 – KI-Pfad unerreichbar:** Alle regulären Pfade von local_decision liefern ein Aktionspaar. Die Abfrage decision is None erreicht Claude nicht. Ein Fehler der lokalen Entscheidung wird ebenfalls nicht als Übergabe an Claude behandelt. Die README-Angaben zur hybriden Nutzung passen nicht zum geprüften Startpfad.
3. **P1 – falsche Split-Equity:** hand_eval.py:225–298 zählt jeden ungeschlagenen Gleichstand mit 0.5, unabhängig von der Anzahl gleichstarker Spieler.
4. **P1 – falsche Omaha-Draws:** detect_draws kombiniert alle Karten und akzeptiert eine eigene Karte der Flushfarbe. Omaha verlangt zwei eigene Karten. Die Funktion meldet außerdem Draws am River und führt bereits gemachte Flushes in derselben Liste; agent.py betrachtet jede nichtleere Liste als Draw.
5. **P1 – keine Eingabevalidierung:** Doppelte Karten werden als normale Hand bewertet; leere eigene Karten werfen IndexError. Unicode-Farben werden nicht auf Deckfarben h/s/d/c normalisiert. Ob der aktuelle DOM tatsächlich Unicode liefert, ist noch nicht überprüft.
6. **P2 – unzuverlässige Gegnerprofile:** opponent_model.py zählt Aktionen statt Hände für VPIP/PFR und lässt Checks im Nenner weg. Jeder Fold gilt als Fold gegen Raise; jeder Raise/Call/Fold erhöht raise_faced. C-bet-Gelegenheiten werden pro Flopaktion des Preflopraisers gezählt. Gefoldete Spieler erhalten Stärke 0.05 und bleiben im Filter v > 0.0. Wiederholtes Einlesen alter Logs in agent.py verstärkt die Verzerrung. Logzuordnung per Namens-Substring kann ähnlich benannte Spieler verwechseln. record_showdown wird im geprüften Hauptpfad nicht aufgerufen.
7. **P2 – Handzählung kann ausfallen:** last_cards wird auch bei leerer Kartenliste überschrieben. Falls zwischen Händen ein leerer Zustand gelesen wird, scheitert die Bedingung cards != last_cards and last_cards. Dann werden Gegnerdaten nicht sauber zurückgesetzt und Sessionhände nicht erhöht. Erste Hand wird ebenfalls nicht gezählt.
8. **P2 – v4-Fehlerzähler:** consecutive_errors wird nach jedem erfolgreichen Scrape auf 0 gesetzt, bevor die Entscheidung läuft. Ein dauerhaft fehlerhafter Entscheidungspfad kann daher in jedem Durchlauf nur 1 erreichen; der Reconnect bei >15 Fehlern greift nicht.
9. **P2 – Sessionwerte:** tracker.py nimmt Buy-in 1000 und BB 10 an. Variable Rebuys überschreiben buy_in und werden später alle zum letzten Betrag gerechnet. Dashboard-BB/100 verwendet den BB der letzten Session für sämtliche Sessions. Dateiänderungen sind nicht atomisch; Lesefehler werden als leere Historie behandelt.

## Offline reproduziert

Originale hand_eval.py dieses Commits in /private/tmp/poker_review_hand_eval.py gespeichert; per runpy ohne __main__-Block geladen. Keine zusätzlichen Abhängigkeiten installiert.

| Fall | Tatsächliches Ergebnis | Erwartung |
|---|---|---|
| Eigene 2c 3d, Board Ah Kh Qh Jh Th, zwei Gegner, 20 Simulationen | 0.5 | 1/3, alle spielen Board-Royal |
| PLO Ah Ks Qc Jd, Board 2h 5h 8h | flush draw und OESD | Flush mit nur einer eigenen Herzkarte unmöglich |
| Ah Kh, River Qh Jh 2c 7d 9s | flush draw und gutshot | Keine kommende Karte, daher kein Draw |
| Eigene Ah Ah preflop | 84.93 Prozent | Ungültige doppelte Karte ablehnen |
| Eigene Karten leer | IndexError | Ungültigen/unvollständigen Zustand kontrolliert behandeln |

## Tests, Lizenz und Einrichtung

MIT-Lizenz bestätigt. Dateibaum enthält config.example.py, aber kein config.py. README beschreibt .env, während agent.py config importiert. Der dokumentierte frische Checkout benötigt daher zusätzliche Einrichtung.

Testdateien existieren: test_harness.py, test_plo_betting.py und test_plo_features.py. Die beiden PLO-Dateien geben überwiegend Werte aus, ohne Sollwerte per Assertions zu prüfen. test_harness.py startet Browser und ein Online-Spiel mit einer separaten lokalen Entscheidungsfunktion; es prüft nicht den produktiven local_decision-Pfad. Diese Dateien wurden nicht ausgeführt. Im vollständigen Dateibaum dieses Commits keine .github-Workflows gefunden.

## Reihenfolge einer Reparatur

1. Konfiguration und gemeinsames Gegnerobjekt; offline Start- und Entscheidungsprüfung.
2. Tischzustand validieren: eigener Sitz, Karten, Hand-ID, Positionen, Beträge, verfügbare Aktionen.
3. Aktionsausführung bestätigen: frischer Zug, gültige Grenzen, tatsächlicher Sliderwert, Server-/Logbestätigung und Schutz vor Wiederholung.
4. Split-Equity, Omaha-Draws und Eingabevalidierung mit deterministischen Sollfällen reparieren.
5. Gegnerereignisse deduplizieren, echte Gelegenheiten und Hände zählen, Sessionwerte aus tatsächlichen Daten übernehmen.
6. Erst anschließend einen bewusst definierten KI-Übergabepfad und messbare Strategieverbesserungen ergänzen.

Weitere Agentvarianten und große multi_bot-Dateien sind nicht vollständig geprüft. Der Bericht ist keine Freigabe für einen Onlineeinsatz.
