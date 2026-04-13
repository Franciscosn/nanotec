# PROJECT_STATUS

Stand: 2026-04-11

## Projektkontext (Pflicht fuer neue AI-Chats)
- Dieses Repo enthaelt die Steuer- und Monitoring-Software fuer eine Sputtering-Anlage (Vakuum, Motorik, Netzteile, Plasmaquellen).
- Es gibt zwei Codewelten:
  - Legacy/C++-Bestand im Root (`main.cpp`, `src/`, Visual-Studio-Projektdateien) als Altstand/Referenz.
  - Aktiver Python-Rewrite in `python_rewrite/` als aktuelle Arbeitsbasis fuer neue Aenderungen.
- Startpunkt fuer Nutzer ist immer `python run.py` im Repo-Root.
- Root-Launcher (`run.py`) delegiert direkt an `python_rewrite/run.py`.
- `python_rewrite/run.py` uebernimmt:
  - Laden der Runtime-Settings (standardmaessig aus `sputter_settings.json`),
  - CLI-Optionen (Simulation/Real, Port-Checks, Runtime-Dump),
  - macOS-Fallback auf Homebrew-Python bei Tk-Problemen,
  - Start der Haupt-GUI (`sputtering_app.gui.App`).
- Die Python-App kann in zwei Modi laufen:
  - Simulation: virtuelle Anlage ohne echte Hardware.
  - Realbetrieb: direkte Kommunikation mit Geraeten ueber serielle Ports.
- Zentraler Laufzeitkern ist der Controller (`python_rewrite/sputtering_app/controller.py`):
  - Tick-Loop fuer Polling/Steuerung,
  - Device-Anbindung (u. a. Nanotec, Pfeiffer, FUG, Pinnacle, Expert),
  - Interlocks, Statusverwaltung und Protokoll-Logging.
- Zielbild fuer AI-Assistenten: standardmaessig am Python-Rewrite arbeiten, Legacy nur anfassen wenn explizit verlangt.

## Wichtige Dateien
- Launcher Root: `run.py`
- Launcher Python: `python_rewrite/run.py`
- Runtime-Settings Modell: `python_rewrite/sputtering_app/runtime_settings.py`
- Haupt-GUI: `python_rewrite/sputtering_app/gui.py`
- Pumpen-GUI: `python_rewrite/sputtering_app/pump_gui.py`
- Controller: `python_rewrite/sputtering_app/controller.py`
- Default-Settings: `sputter_settings.json`

## Aktueller Fokus
- Pressure-Logger-Paritaet im Pumpenfenster weiter angleichen.
- Realdaten nur zeigen, wenn Gauge wirklich verbunden/gueltig ist.
- GUI-Verhalten unter realem Hardware-Setup testen.

## Bekannte Punkte
- Auf macOS wird fuer Tk teils auf Homebrew-Python umgeschaltet.
- Deshalb Pakete in beiden Interpretern relevant:
  - System-Python (`/usr/bin/python3`)
  - Homebrew-Python (`/opt/homebrew/bin/python3.12`)
- Bei Gauge-Fehlern werden Druckwerte jetzt als ungueltig markiert (kein "Pseudo-OK").

## Arbeitsregeln fuer AI-Assistenten
- In neuen Chats zuerst nur diese Datei lesen und auf die erste konkrete Aufgabe warten.
- Ohne klaren Auftrag keine Vollrepo-Scans und keine grossen Datei-Dumps erzeugen.
- Nur die fuer die Aufgabe benoetigten Dateien lesen/bearbeiten.
- Standardannahme: Python-Rewrite ist die aktive Codebasis; Legacy-Code nur bei expliziter Anweisung.
- Bei hardwarekritischen Aenderungen:
  - Simulations- und Realmodus-Verhalten getrennt mitdenken.
  - Verbindungs-/Gauge-Fehler nie als "OK" kaschieren.
- Nach jeder relevanten Aenderung `PROJECT_STATUS.md` aktualisieren.

## Update-Regel
Pflicht: Diese Datei muss nach jeder relevanten Aenderung aktualisiert werden.

Nach jeder groesseren Aenderung diese Datei in 5 Punkten aktualisieren:
1) Was wurde geaendert?
2) Welche Dateien?
3) Was ist offen?
4) Wie testen?
5) Naechster sinnvoller Schritt.

## Letztes Update (2026-04-11, Pumpenfenster-v9-Angleichung)
1) Was wurde geaendert?
- Das Pumpen-Detailfenster wurde deutlich an `cdt_pressure_logger_v9.py` angeglichen:
  - Neuer Rohkommando-Bereich (`!cmd` fuer write-only ACK, sonst Query).
  - Einblendbarer Steuer-/Parameterblock mit Kanalwahl und v9-aehnlichen Befehlen
    (Einheit, Sensor EIN/AUS, Degas, Filter, CAL, FSR, OFC, Diagnose, PRx-Read, Aktivieren+Pruefen, Werkreset).
  - MaxiGauge-spezifische Eingaben (Kanalname, Digits, Contrast, Screensave) sind integriert.
- Die Pfeiffer-Device-Schicht wurde um die dafuer noetigen Kommandos erweitert.
- Der Controller bietet jetzt eine zentrale High-Level-API fuer diese Pfeiffer-Kommandos,
  damit das Unterfenster weiterhin keine direkten seriellen Zugriffe ausfuehrt.

2) Welche Dateien?
- `python_rewrite/sputtering_app/pump_gui.py`
- `python_rewrite/sputtering_app/controller.py`
- `python_rewrite/sputtering_app/devices/dualg.py`

3) Was ist offen?
- Feintuning der visuellen Paritaet (Spacing/Label-Texte) gegenueber v9 im Livebetrieb.
- Hardwaretest aller neuen Kommandobuttons auf echtem TPG262/MaxiGauge.
- Optional: Hilfe-Textfenster (`texts`) wie in v9 wieder anbinden.

4) Wie testen?
- Start: `python run.py`, dann `Vakuumpumpen (Detail)` oeffnen.
- Im Simulationsmodus pruefen, dass GUI stabil bleibt und klare Fehlhinweise fuer Hardware-Kommandos zeigt.
- Im Realmodus pruefen:
  - Reconnect + Livewerte + CSV-Logging wie bisher.
  - Rohkommando (`PR1`, `!SEN,...`) funktioniert.
  - Steuerblock-Aktionen schreiben korrekt und Meldungen erscheinen im lokalen Log.
- Syntaxcheck bereits ausgefuehrt:
  - `python3 -m py_compile python_rewrite/sputtering_app/devices/dualg.py python_rewrite/sputtering_app/controller.py python_rewrite/sputtering_app/pump_gui.py`

5) Naechster sinnvoller Schritt.
- Direkter Hardware-Durchlauf mit Checkliste pro Befehl (TPG262 und MaxiGauge getrennt),
  danach kleine UX-Korrekturrunde im Pumpenfenster.

## Letztes Update (2026-04-12, Pumpenfenster auf v9-Layout umgestellt)
1) Was wurde geaendert?
- `pump_gui.py` wurde auf eine v9-nahe Komplettstruktur umgestellt:
  - linke Bedienseite + rechte Plotseite wie im `cdt_pressure_logger_v9.py`-Muster,
  - gleiche Hauptsektionen (Verbindung/Messung/Status, Kanal-Karten, Meldungen, Rohkommando, einblendbare Steuerung),
  - gleiche Kernaktionen (Logging, Diagnose, Rohbefehle, Parameterkommandos, externer Plot, CSV-Plot).
- Ganz oben wurde ein expliziter Modus-Switch (`simulation` / `real`) hinzugefuegt.
- Monitoring/Kommandos laufen weiterhin ueber den zentralen Controller (kein zweiter direkter Portzugriff aus dem Unterfenster).
- Realmodus-Regel abgesichert: wenn Gauge nicht verbunden/fehlerhaft, werden im Fenster keine Simulationswerte angezeigt.

2) Welche Dateien?
- `python_rewrite/sputtering_app/pump_gui.py`

3) Was ist offen?
- Visuelles Finetuning (pixelgenaue Paritaet) gegen den exakten v9-Standalone-Screenshot.
- Hardware-Durchlauf aller Buttons (insb. MaxiGauge-Parameter) im Realmodus.

4) Wie testen?
- Start: `python run.py`, dann `Vakuumpumpen (Detail)` oeffnen.
- Oben Modus auf `real` setzen und `Modus anwenden`.
- Ohne echte Verbindung pruefen: keine scheinbar gueltigen Simulationswerte in den Kanalanzeigen.
- Mit Verbindung pruefen: `Verbinden`, Monitoring/Logging/Kommandos.
- Checks bereits ausgefuehrt:
  - `python3 -m py_compile python_rewrite/sputtering_app/pump_gui.py`
  - `python3 python_rewrite/run.py --check`

5) Naechster sinnvoller Schritt.
- Gemeinsamer Feinschliff im UI (Label-/Abstandsdetails) direkt nach deinem visuellen Feedback im laufenden Fenster.

## Letztes Update (2026-04-12, Pinnacle-MDX Fokus: Standabgleich + Detailfenster-Klarheit)
1) Was wurde geaendert?
- Der Pinnacle-MDX-Stand wurde explizit gegen Legacy-C++ (`src/pinnacle.cpp/.h`) und Python-Rewrite geprueft.
- Die Pinnacle-Spezialseite (`pinnacle_gui.py`) wurde im Kopfbereich klarer strukturiert:
  - eigener, deutlich sichtbarer Betriebsmodus-Block ganz oben,
  - klare aktive Modus-Anzeige (`AKTIV: SIMULATION` / `AKTIV: REAL HARDWARE`),
  - Modusauswahl (`simulation`/`real`) + direkter Anwenden-Button.
- Runtime-Mode-Wechsel aus dem Pinnacle-Fenster ist jetzt an den Haupt-Runtime-Restart gekoppelt
  (kein unsicheres lokales Umschalten ohne Controller-Neustart).
- Beim Runtime-Wechsel bleibt jetzt das aufrufende Detailfenster (`pump` oder `pinnacle`) offen
  und bekommt einen frischen Controller-Handle; andere Detailfenster werden wie vorgesehen geschlossen.
- Kanalanzeigen im Pinnacle-Fenster wurden visuell klarer gemacht (Output-Badge farblich/griffiger).

2) Welche Dateien?
- `python_rewrite/sputtering_app/pinnacle_gui.py`
- `python_rewrite/sputtering_app/gui.py`

3) Was ist offen?
- Feinabstimmung der visuellen Details (Abstaende, Label-Kuerze, Button-Textlaengen) nach Live-Feedback.
- Real-Hardware-Durchlauf des Mode-Switches direkt aus dem Pinnacle-Fenster (Restart + Reconnect-Verhalten).
- Optional: identische Mode-Card-Optik fuer weitere Detailfenster (z. B. Nanotec), falls gewuenscht.

4) Wie testen?
- Start: `python run.py`, dann `Pinnacle MDX (Detail)` oeffnen.
- Oben im Pinnacle-Fenster:
  - Sichtbarkeit pruefen: Modus-Karte ist ganz oben und klar lesbar.
  - Zwischen `simulation` und `real` waehlen, `... anwenden` klicken.
  - Nach Wechsel pruefen, dass Fenster offen bleibt und Werte weiter ticken.
- Checks bereits ausgefuehrt:
  - `python3 -m py_compile python_rewrite/sputtering_app/pinnacle_gui.py python_rewrite/sputtering_app/gui.py`
  - `python3 -m py_compile python_rewrite/sputtering_app/devices/pinnacle.py python_rewrite/sputtering_app/controller.py`

5) Naechster sinnvoller Schritt.
- Gemeinsamer kurzer Live-Test am echten Pinnacle-Port (Mode-Wechsel + Reconnect + A/B ON/OFF + Setpoint-Apply),
  danach gezielter UI-Feinschliff nach deinem Eindruck im laufenden Fenster.

## Letztes Update (2026-04-12, Haupt-GUI um grosses Anlagen-Schema erweitert)
1) Was wurde geaendert?
- Die Haupt-GUI hat jetzt eine zusaetzliche breite `Anlagen-Schema`-Ansicht (Notebook-Tab) als Main-View:
  - grosse statische Schemaflaeche mit klaren Segmenten (Loadlock/Chamber/Ports + Motor/Power/Plot-Bereich),
  - farbige Pipeline-Linien und LED-Indikatoren fuer Ventile, Sensoren und Portstatus,
  - schalterartige Bedienleiste fuer zentrale Ventil-/Argon-/VAT-Aktionen,
  - integrierte Reconnect-/Detailfenster-Shortcuts direkt im Schema.
- Das Schema ist live an den zentralen Controller gekoppelt und aktualisiert sich pro Tick.
- Ein mini Pressure-Plot (Chamber/Load, logarithmisch) wurde in den Plot-Bereich integriert.

2) Welche Dateien?
- `python_rewrite/sputtering_app/gui.py`

3) Was ist offen?
- Visuelles Feintuning der Koordinaten/Abstaende gegen eure bevorzugte Anlagenzeichnung.
- Optional: echte Bitmap-Grafiken (anstelle der aktuell gezeichneten LED/Switch-Optik) einhaengen.

4) Wie testen?
- Start: `python run.py`
- Im Hauptfenster Tab `Anlagen-Schema` pruefen:
  - Linien/LEDs folgen den aktuellen Ventil-/Port-/Sensorzustaenden.
  - Schalterleiste bedient die gleichen Controller-Aktionen wie die Schnellkarten.
  - Reconnect-/Detailbuttons funktionieren.
  - Plot aktualisiert sich laufend.
- Checks ausgefuehrt:
  - `python3 -m py_compile python_rewrite/sputtering_app/gui.py`
  - `python3 python_rewrite/run.py --check`

5) Naechster sinnvoller Schritt.
- Gemeinsamer visueller Abgleich mit deinem Zielbild des Legacy-Schemas, danach gezielte Pixel-/Farbanpassung
  (inkl. optionaler Bitmap-Switch/LED-Assets).

## Letztes Update (2026-04-12, Test-Fix fuer Pinnacle-Reconnect)
1) Was wurde geaendert?
- Der fehlschlagende Test `test_reconnect_pinnacle_success` wurde an die aktuelle Controller-Logik angepasst.
- Hintergrund: `reconnect_pinnacle()` prueft inzwischen pro Adresse via `ping_address` statt dem alten `check_connection`-Stub.

2) Welche Dateien?
- `python_rewrite/tests/test_backend.py`

3) Was ist offen?
- Kein funktionales Backend-Issue aus diesem Testfall; es war ein Test-Mismatch.

4) Wie testen?
- `python3 -m unittest tests.test_backend.ControllerReconnectTests.test_reconnect_pinnacle_success -v`
- `python3 -m unittest discover -s tests -v`

5) Naechster sinnvoller Schritt.
- Optional einen zusaetzlichen Test fuer "partial success" (eine Pinnacle-Adresse antwortet, die andere nicht) ergaenzen.

## Letztes Update (2026-04-12, Pinnacle-Montag-Flexmodus: Robustheit + Umgehungen)
1) Was wurde geaendert?
- Pinnacle-Backend wurde fuer Windows-Realbetrieb robuster und flexibler gemacht:
  - Teilfehler pro Kanal A/B blockieren nicht mehr zwingend den gesamten Pinnacle-Tick.
  - Reconnect prueft jetzt die konfigurierten A/B-Adressen und kann partielle Erreichbarkeit melden.
  - Schnellere Not-Aus-Option: optional direkter `DC_OFF`-Write ohne Tick-Wartezeit.
  - Runtime-Optionen fuer Pinnacle sind nun live einstellbar:
    - strict/lenient Protokollmodus,
    - Write-Verify EIN/AUS,
    - Retry-Anzahl,
    - Command-Delay,
    - Read-Size,
    - Fast Emergency OFF.
  - Serial-Parameter fuer Pinnacle (Port/Baud/Parity/Timeout/Bytesize/Stopbits) sind live konfigurierbar.
- Pinnacle-Detailfenster bekam ein neues Service-/Flexpanel:
  - Port-Scan, Port+Modus anwenden, A/B-Adressen setzen, A/B-Ping.
  - Kompatibilitaetsoptionen direkt im Fenster.
  - Portkonflikte werden sichtbar angezeigt.
  - Output-Anzeige jetzt klar als Sollzustand plus COMM-Status (OK/ERR).
  - ON-Bestaetigung kann fuer Inbetriebnahmezwecke ein-/ausgeschaltet werden.

2) Welche Dateien?
- `python_rewrite/sputtering_app/models.py`
- `python_rewrite/sputtering_app/devices/pinnacle.py`
- `python_rewrite/sputtering_app/controller.py`
- `python_rewrite/sputtering_app/pinnacle_gui.py`
- `python_rewrite/sputtering_app/gui.py`

3) Was ist offen?
- Die flexiblen Pinnacle-Serial- und Kompatibilitaetsoptionen sind aktuell Runtime-intern:
  nach komplettem Neustart muessen sie bei Bedarf erneut gesetzt werden.
- Echthardware-Validierung steht aus (insb. wie aggressiv der leniente Protokollmodus wirklich noetig ist).
- Optional: Persistenz dieser Pinnacle-Flexoptionen in die Settings-Datei.

4) Wie testen?
- Start: `python run.py`, `Pinnacle MDX (Detail)` oeffnen.
- Im neuen Flexpanel:
  - Port scannen, Pinnacle-Port setzen, ggf. Modus+Port anwenden.
  - A/B-Adressen setzen und `A/B Ping` pruefen.
  - Bei Kommunikationsproblemen: strict deaktivieren, retries erhoehen, delay/read_size anpassen.
  - Bei kritischen Situationen: Fast Emergency OFF aktiv lassen und `NOT-AUS` testen.
- Checks bereits ausgefuehrt:
  - `python3 -m py_compile python_rewrite/sputtering_app/devices/pinnacle.py python_rewrite/sputtering_app/controller.py python_rewrite/sputtering_app/pinnacle_gui.py python_rewrite/sputtering_app/gui.py`
  - `python3 python_rewrite/run.py --check`

5) Naechster sinnvoller Schritt.
- Montag-Preflight mit echtem Geraet:
  zuerst minimaler Reconnect/Ping A/B, dann je Kanal Sollwert-Write+Readback,
  danach ON/OFF-Sequenz und dokumentierte Fallback-Werte fuer strict/retries/delay/read_size festziehen.

## Letztes Update (2026-04-12, Nanotec-Detailfenster: Modus-Switch oben + Leuchte)
1) Was wurde geaendert?
- Im Nanotec-Detailfenster wurde ganz oben ein expliziter Modus-Switch (`simulation`/`real`) mit Leuchte eingebaut, analog zum Pumpenfenster.
- Der Moduswechsel laeuft jetzt auch dort ueber Runtime-Settings + Controller-Restart (kein lokales Umschalten am Controller vorbei).
- Die Haupt-GUI unterstuetzt beim Child-getriggerten Runtime-Wechsel jetzt auch `source="nanotec"`, sodass das Nanotec-Fenster offen bleibt und nur den Controller-Handle aktualisiert.

2) Welche Dateien?
- `python_rewrite/sputtering_app/nanotec_gui.py`
- `python_rewrite/sputtering_app/gui.py`

3) Was ist offen?
- Live-Hardwaretest fuer Moduswechsel direkt aus dem Nanotec-Fenster (real -> reconnect -> Motorbefehle).
- Optionales visuelles Feintuning (Spacing/Label-Breite) nach kurzem gemeinsamen UI-Check.

4) Wie testen?
- Start: `python run.py`, dann `Schrittmotoren (Detail)` oeffnen.
- Oben im Nanotec-Fenster Modus auf `real` bzw. `simulation` stellen und anwenden.
- Pruefen: Leuchte zeigt synchronen Zustand; bei Runtime-Wechsel bleibt das Nanotec-Fenster offen und aktualisiert sich weiter.
- Check bereits ausgefuehrt:
  - `python3 -m py_compile python_rewrite/sputtering_app/nanotec_gui.py python_rewrite/sputtering_app/gui.py`

5) Naechster sinnvoller Schritt.
- Kurzer Real-Hardware-Durchlauf fuer Nanotec (Mode-Switch + Reconnect + Start/Stop/Referenz), danach ggf. kleiner UI-Feinschliff.

## Letztes Update (2026-04-12, Nanotec-Paritaet: Port/Adressen/LEDs erweitert)
1) Was wurde geaendert?
- Das Nanotec-Detailfenster wurde gezielt um die zuvor fehlenden C++-Grundelemente erweitert:
  - Nanotec-Portauswahl direkt im Fenster (Combo + Portliste aktualisieren),
  - Connect/Disconnect-Button im C++-Sinne (Runtime-Port setzen/loesen via zentralem Restart),
  - Ready-/Portstatus-Leuchte im Header,
  - editierbare Motoradressen M1/M2 inklusive Apply-Button,
  - klare Connected/Run-Status-LEDs je Motor.
- Fuer die Adressaenderung wurde eine neue sichere Controller-API eingefuehrt (`set_motor_addresses`), inkl. Validierung und optionalem Reconnect.
- Die Haupt-GUI uebergibt jetzt auch dem Nanotec-Fenster den Port-Listing-Callback.

2) Welche Dateien?
- `python_rewrite/sputtering_app/nanotec_gui.py`
- `python_rewrite/sputtering_app/controller.py`
- `python_rewrite/sputtering_app/gui.py`

3) Was ist offen?
- Feinschliff der Connect/Disconnect-Semantik gegen Real-Hardware-Bedienpraxis (ob Port-leer als "getrennt" fuer euren Betrieb exakt passt).
- Kurzer Live-Check, ob die neue Portstatus-Leuchte den erwarteten Anlagenzustand in allen Fehlerfaellen eindeutig zeigt.

4) Wie testen?
- Start: `python run.py`, dann `Schrittmotoren (Detail)` oeffnen.
- Im Nanotec-Fenster pruefen:
  - Portliste aktualisieren, Port waehlen, `Port verbinden`/`Port trennen`,
  - Ready-Leuchte + Portstatus-Text,
  - Motoradressen M1/M2 aendern und `Adressen uebernehmen`,
  - Connected/Run-LEDs pro Motor bei Start/Stop/Referenz.
- Checks bereits ausgefuehrt:
  - `python3 -m py_compile python_rewrite/sputtering_app/nanotec_gui.py python_rewrite/sputtering_app/controller.py python_rewrite/sputtering_app/gui.py`
  - `python3 python_rewrite/run.py --check`

5) Naechster sinnvoller Schritt.
- Gemeinsamer kurzer Realtest am Nanotec-Port mit absichtlichem A/B-Adresse-Wechsel, um Connect/Ready/LED-Verhalten final zu verifizieren.

## Letztes Update (2026-04-12, Nanotec Montag-Haertung: Strict-Safety + Service-Overrides)
1) Was wurde geaendert?
- Der Nanotec-Motorpfad wurde auf einen strikten Sicherheitsstandard mit kontrollierten Test-Overrides umgestellt:
  - verpflichtendes Preflight-Gating fuer `start`/`reference` im Realmodus (20s Freigabefenster, motorbezogen),
  - zentrale Controller-API fuer Preflight/Unlock und Override-Steuerung,
  - Safety-Checks (Limit/Softlimit) laufen jetzt konsistent ueber den zentralen Preflight-Pfad.
- Transaktionale Motor-Adressaenderung umgesetzt:
  - bei Reconnect-Fehler automatische Ruecknahme auf alte Adressen inkl. Reconnect-Versuch auf Altzustand.
- Nanotec-Detailfenster erweitert:
  - Preflight-Buttons pro Motor (`Start`, `Referenz`) + Live-Statuszeilen,
  - versteckter Service/Test-Block (default zugeklappt) mit Master-Schutzschalter und den vereinbarten Overrides,
  - Warn-Banner bei aktiven unsicheren Overrides.
- Tick-Overwrite-Bug in Nanotec-UI behoben:
  - Port-/Adressfelder werden im Tick nur noch synchronisiert, wenn sie nicht lokal "dirty" sind.
- Runtime-Haertung in Haupt-GUI:
  - `_tick()` crash-fest (naechster Tick wird auch bei Fehlern geplant),
  - Child-Runtime-Apply nutzt jetzt denselben Validierungspfad wie Haupt-Apply (inkl. Portduplikat-Pruefung),
  - explizite Ausnahme nur fuer gewolltes Nanotec-Disconnect (leerer Nanotec-Port aus Nanotec-Child).

2) Welche Dateien?
- `python_rewrite/sputtering_app/controller.py`
- `python_rewrite/sputtering_app/nanotec_gui.py`
- `python_rewrite/sputtering_app/gui.py`
- `python_rewrite/tests/test_nanotec_backend.py`

3) Was ist offen?
- Praxis-Finetuning der neuen Strict-Defaults gegen eure reale Verdrahtung (insb. wenn Endschalter-Bits fuer Motor 2 noch nicht final gemappt sind).
- Optional: Feineres UI-Wording/Spacing im Service-Block nach Live-Bedienfeedback.

4) Wie testen?
- Automatisiert:
  - `PYTHONPATH=python_rewrite python3 -m unittest discover -s python_rewrite/tests -v`
  - `python3 -m py_compile python_rewrite/sputtering_app/controller.py python_rewrite/sputtering_app/gui.py python_rewrite/sputtering_app/nanotec_gui.py python_rewrite/tests/test_nanotec_backend.py`
  - `python3 python_rewrite/run.py --check`
- Manuell (Montag):
  - Nanotec-Detail oeffnen, Realmodus, Port verbinden.
  - Pro Motor zuerst `Preflight ...`, dann `Start`/`Referenz`.
  - Einen Motor offline simulieren/pruefen: Online-Motor bleibt fahrbar, Offline-Motor bleibt sauber gesperrt.
  - Service-Modus nur bewusst aktivieren und danach mit `Alle Overrides zuruecksetzen` wieder auf safe-default.

5) Naechster sinnvoller Schritt.
- Vor dem ersten Real-Fahrkommando eine kurze Vor-Ort-Checkliste durchlaufen:
  Portstatus -> Motor connected -> Preflight OK -> Freigabefenster aktiv -> Start/Referenz.
