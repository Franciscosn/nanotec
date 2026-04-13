# Python Rewrite (Tkinter) - Sputtering Facility

Dieser Ordner enthaelt den Python-Stand der Anlage.

## Schnellstart

Empfohlen aus dem Projekt-Root:

```bash
python run.py
```

Oder direkt in diesem Ordner:

```bash
cd python_rewrite
python3 run.py
```

Windows alternativ (im Root):

```powershell
py -3 run.py
```

Windows alternativ (direkt in `python_rewrite`):

```powershell
py -3 run.py
```

Per Doppelklick:

- macOS: `Start_Sputtering.command` (im Projekt-Root)
- Windows: `Start_Sputtering.bat` (im Projekt-Root)

## Runtime-Settings (neu)

Simulation und Realbetrieb sind jetzt dieselbe App.
In der Haupt-GUI gibt es einen eigenen Bereich fuer:

- Moduswahl (`Simulation`/`Real hardware`)
- Pfeiffer-Backend und Kanal-Mapping
- Portzuordnung pro Geraet
- Laden/Speichern einer JSON-Settings-Datei
- Neustart des Controllers mit den aktuellen Settings

Beim Start wird automatisch `sputter_settings.json` geladen (falls vorhanden).

Explizit eine Datei laden:

```bash
python3 run.py --settings /pfad/zu/sputter_settings.json
```

Settings-Template schreiben:

```bash
python3 run.py --save-settings-template sputter_settings.json
```

## Wichtige Optionen

```bash
python3 run.py --check
python3 run.py --list-ports
python3 run.py --show-runtime
python3 run.py --simulation
python3 run.py --real
python3 run.py --settings ./sputter_settings.json
python3 run.py --save-settings-template ./sputter_settings.json
```

## Plattformen

- Windows und macOS werden unterstuetzt.
- Standard ist Simulation, dadurch laeuft die App ohne Hardware.
- Fuer echte Geraetekommunikation `pyserial` installieren:

```bash
python3 -m pip install -r requirements.txt
```

`matplotlib` ist in `requirements.txt` enthalten. Falls die Installation auf einem
Rechner absichtlich minimal bleiben soll, laeuft die App auch ohne matplotlib; in
diesem Fall bleiben Plotbereiche deaktiviert.

## Simulation oder echte Hardware?

Primar wird der Modus jetzt in der GUI eingestellt (Runtime-Konfiguration).
Optional kannst du den Startmodus weiter per CLI/ENV vorgeben:

- `python3 run.py --simulation`
- `python3 run.py --real`
- `SPUTTER_SIMULATION=true|false` (Legacy/automations-kompatibel)

macOS/Linux:

```bash
python3 run.py --simulation
```

```bash
python3 run.py --real
```

Windows PowerShell:

```powershell
py -3 run.py --simulation
```

```powershell
py -3 run.py --real
```

Der aktive Modus wird in der Haupt-GUI oben angezeigt.

## Neue GUI-Navigation: Vakuumpumpen

In der Haupt-GUI gibt es jetzt den Button `Vakuumpumpen`.

Beim Klick oeffnet sich eine pressure_logger-aehnliche Unteransicht mit:

- Live-Anzeige fuer Chamber- und Loaddruck (inkl. Pfeiffer-Statuscodes)
- farbigen Statusindikatoren (OK / OFF / ERR)
- CSV-Logging direkt aus der Unter-GUI
- optionalem Live-Plot (wenn `matplotlib` installiert ist)
- Buttons fuer `Chamber Gauge EIN/AUS` und `Load Gauge EIN/AUS`

Wichtig:
- Diese Unter-GUI nutzt denselben zentralen Controller-State wie die Haupt-GUI.
- Es wird keine zweite serielle Verbindung geoeffnet; dadurch werden Portkonflikte vermieden.

## Neue Hauptseite: Gesamtuebersicht

Die Haupt-GUI wurde auf eine echte Ein-Seiten-Gesamtansicht erweitert.

Jetzt sichtbar und bedienbar auf einer Seite:

- Portstatus aller Kernbackends (`dualg`, `pinnacle`, `nanotec`, `fug`, `expert`)
- explizite Reconnect-Buttons fuer diese Backends
- Vakuum-/Gauge-Schnellbedienung inkl. Sensor-Toggle und Argon-Setpoint
- Ventil-/Gate-Schnellsteuerung (Bypass/VAT/Back-Valve/Gate) mit zentralen Interlocks
- Pinnacle-Schnellbedienung fuer Kanal A/B (Mode, Setpoint, Pulse, ON/OFF)
- Nanotec-Schnellbedienung fuer Motor 1/2 (Sollwerte, Start/Stop/Referenz)
- FUG-Schnellbedienung (Setpoints/Ramps/HV)

Die bisherigen Detailfenster bleiben erhalten und sind ueber die Buttons im Kopfbereich
weiterhin direkt erreichbar.

## Neue GUI-Navigation: Pinnacle MDX

In der Haupt-GUI gibt es jetzt den Button `Pinnacle MDX`.

Beim Klick oeffnet sich eine eigene Unteransicht mit:

- Kanal A/B Bedienfeldern (Regelmodus, Setpoint, Pulsfrequenz, Puls-Umkehrzeit)
- Output `EIN/AUS` pro Kanal
- Live-Istwerten (Spannung, Strom, Leistung, aktiver Regelmodus)
- Spannungsplot fuer Kanal A und B (optional mit `matplotlib`)
- globalem `NOT-AUS Pinnacle (A+B OFF)`-Button

Sicherheits-/Robustheitsstand im Backend:

- Pinnacle-Antwortframes werden strikt geprueft (Laenge, Adresse, Kommando, CRC).
- Nach Sollwertschreiben erfolgt Readback-Verifikation der Regelparameter.
- Wenn ein Schreibpfad bei angefordertem `Output EIN` fehlschlaegt, wird fail-safe
  automatisch ein `DC_OFF`-Versuch ausgefuehrt.

## Neue GUI-Navigation: Schrittmotoren (Nanotec)

In der Haupt-GUI gibt es jetzt den Button `Schrittmotoren`.

Beim Klick oeffnet sich eine eigene Unteransicht fuer beide Nanotec-Motoren mit:

- Sollwerten: `Target Speed`, `Target Position`, `Step Mode`, `Direction`,
  `Reference Direction`, `Loops`
- Befehlen pro Motor: `Start`, `Stop`, `Referenz`
- globaler Sicherheitsaktion: `STOPP ALLE MOTOREN`
- Live-Rueckmeldung: `connected`, `running`, `status code`, `status text`,
  `active step mode`, Position/Encoder, Laufzeit/Restzeit/Fortschritt
- Taster-/Endschalteranzeige auf Basis der E9053-Ruecklesebits
- `Nanotec neu verbinden` fuer erneuten Verbindungscheck ohne App-Neustart

Wichtige Sicherheitsdetails:

- Sollwerte werden zentral im Controller validiert (Speed/Loops/Step-Mode usw.).
- Bei Hardware-Apply-Fehlern wird im Controller auf den letzten gueltigen
  Motor-Sollzustand zurueckgerollt.
- Schrittmodus-Aenderungen werden vor Start aktiv auf das Geraet geschrieben und
  per `Zg` rueckgelesen.
- Start/Referenz werden fuer Sicherheit blockiert, wenn der Endschalter in der
  angeforderten Fahrtrichtung bereits aktiv ist.
- Trifft ein laufender Motor den Endschalter in Fahrtrichtung, versucht der
  Controller automatisch einen Safety-Stop.

Optionales Endschalter-Feintuning per ENV (invertierte Verdrahtung):

```bash
export SPUTTER_MOTOR1_LEFT_TASTER_ACTIVE_LEVEL=1
export SPUTTER_MOTOR1_RIGHT_TASTER_ACTIVE_LEVEL=1
export SPUTTER_MOTOR2_LEFT_TASTER_ACTIVE_LEVEL=1
export SPUTTER_MOTOR2_RIGHT_TASTER_ACTIVE_LEVEL=1
```

`1` bedeutet: Bitwert `1` ist "aktiv".  
`0` bedeutet: Bitwert `0` ist "aktiv" (invertierte Logik, z. B. NC-Verkabelung).

Optionale zweite Safety-Ebene: Software-Fahrgrenzen pro Motor

```bash
export SPUTTER_MOTOR1_SOFT_MIN_MM=-10
export SPUTTER_MOTOR1_SOFT_MAX_MM=620
export SPUTTER_MOTOR2_SOFT_MIN_MM=0
export SPUTTER_MOTOR2_SOFT_MAX_MM=220
```

Wichtige Hinweise:
- Diese Grenzen sind zusaetzlich zu Endschaltern gedacht, nicht als Ersatz.
- Wenn eine Grenze nicht gesetzt ist, ist sie auf dieser Seite deaktiviert.
- Im Startfall blockiert der Controller Fahrten, die mit aktuellem Sollweg
  ausserhalb der konfigurierten Grenzen enden wuerden.
- Bei Referenzfahrt blockiert der Controller, wenn der Motor bereits am/ausserhalb
  der Soft-Grenze steht und die Referenzrichtung weiter nach aussen zeigen wuerde.
- Die aktiven Soft-Limits werden im Schrittmotor-Unterfenster sichtbar angezeigt.

## Backend-Status (dieser Rewrite)

Der Backend-Teil wurde auf Geraeteklassen umgestellt und orientiert sich am C++-Original:

- `sputtering_app/devices/fug.py`
- `sputtering_app/devices/dualg.py`
- `sputtering_app/devices/pinnacle.py`
- `sputtering_app/devices/nanotec.py`
- `sputtering_app/devices/expert.py`
- `sputtering_app/devices/interlocks.py`
- `sputtering_app/devices/simulation.py`
- `sputtering_app/devices/transport.py`

Steuerung und Logging:

- `sputtering_app/controller.py`
- `sputtering_app/models.py`
- `sputtering_app/protocols.py`
- `sputtering_app/logging_utils.py`

## Simulation / Ports per ENV

```bash
export SPUTTER_SIMULATION=false
export SPUTTER_PORT_NANOTEC=/dev/cu.usbserial-XXXX
export SPUTTER_PORT_DUALG=/dev/cu.usbserial-YYYY
export SPUTTER_PORT_FUG=/dev/cu.usbserial-ZZZZ
export SPUTTER_PORT_PINNACLE=/dev/cu.usbserial-AAAA
export SPUTTER_PORT_EXPERT=/dev/cu.usbserial-BBBB
python3 run.py
```

Windows (PowerShell), komplettes Real-Setup als Beispiel:

```powershell
$env:SPUTTER_SIMULATION="false"
$env:SPUTTER_PORT_NANOTEC="COM4"
$env:SPUTTER_PORT_DUALG="COM6"
$env:SPUTTER_PORT_PINNACLE="COM3"
$env:SPUTTER_PORT_EXPERT="COM3"
$env:SPUTTER_PORT_FUG="COM3"
py -3 run.py
```

Hinweis:
- Im historischen C++-Projekt teilen mehrere Geraete den Default `COM3`.
- Fuer den echten Betrieb muessen die Ports an die reale Verdrahtung angepasst
  werden, damit nicht zwei Treiber dieselbe Schnittstelle gleichzeitig oeffnen.
- Der Python-Controller warnt im Realmodus explizit, wenn mehrere Backends auf
  denselben Port konfiguriert sind.

## Pfeiffer-Konfiguration (TPG262 oder MaxiGauge)

Ab diesem Stand kann das Backend beide Pfeiffer-Controllerfamilien:

- `TPG262` (Dual Gauge, 2 Kanaele)
- `MaxiGauge` (TPG 256 A, 6 Kanaele)

Wichtige Umgebungsvariablen:

- `SPUTTER_PFEIFFER_CONTROLLER=maxigauge` oder `tpg262`
- `SPUTTER_PFEIFFER_SINGLE_GAUGE=true|false` (nur fuer `tpg262`)
- `SPUTTER_MAXI_CHAMBER_CHANNEL=1..6`
- `SPUTTER_MAXI_LOAD_CHANNEL=1..6`
- `SPUTTER_PRESSURE_MAX_AGE_SEC` (optional, Standard `3.0`)

Beispiel (macOS/Linux, MaxiGauge mit Kanal 1/2):

```bash
export SPUTTER_SIMULATION=false
export SPUTTER_PFEIFFER_CONTROLLER=maxigauge
export SPUTTER_MAXI_CHAMBER_CHANNEL=1
export SPUTTER_MAXI_LOAD_CHANNEL=2
export SPUTTER_PORT_DUALG=/dev/cu.usbserial-YYYY
python3 run.py
```

Windows (PowerShell):

```powershell
$env:SPUTTER_SIMULATION="false"
$env:SPUTTER_PFEIFFER_CONTROLLER="maxigauge"
$env:SPUTTER_MAXI_CHAMBER_CHANNEL="1"
$env:SPUTTER_MAXI_LOAD_CHANNEL="2"
$env:SPUTTER_PORT_NANOTEC="COM4"
$env:SPUTTER_PORT_DUALG="COM6"
py -3 run.py
```

## Tests

```bash
cd python_rewrite
python3 -m unittest discover -s tests -v
```
