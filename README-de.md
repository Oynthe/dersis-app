<!-- Sprachauswahl -->
[![English](https://img.shields.io/badge/English-lightgrey?style=for-the-badge)](README-en.md)
[![Türkçe](https://img.shields.io/badge/T%C3%BCrk%C3%A7e-lightgrey?style=for-the-badge)](README-tr.md)
[![Deutsch](https://img.shields.io/badge/Deutsch-6e4f9e?style=for-the-badge)](README-de.md)
[![Español](https://img.shields.io/badge/Espa%C3%B1ol-lightgrey?style=for-the-badge)](README-es.md)

<p align="center">
  <img src="docs/dersis.png" alt="DERSİS-Logo" width="240">
</p>

<h1 align="center">DERSİS</h1>

<p align="center"><b>Intelligente, vollständig offline arbeitende Stundenplan-Software für Schulen und Hochschulen.</b></p>

---

## Inhaltsverzeichnis

- [Überblick](#überblick)
- [Funktionen](#funktionen)
- [Installation](#installation)
- [Aus dem Quellcode ausführen](#aus-dem-quellcode-ausführen)
- [Projektstruktur](#projektstruktur)
- [Nachbau und Alternativen](#nachbau-und-alternativen)
- [Roadmap und Ausbaumöglichkeiten](#roadmap-und-ausbaumöglichkeiten)
- [Bedienungsanleitung](#bedienungsanleitung)
- [Fehler melden](#fehler-melden)
- [Lizenz und Nutzung](#lizenz-und-nutzung)

---

## Überblick

**DERSİS** (vom türkischen *Ders Programı Hazırlama Sistemi*, „System zur Erstellung von
Stundenplänen") ist eine Desktop-Anwendung, die **wöchentliche Stundenpläne** für
Bildungseinrichtungen erstellt, optimiert und verwaltet.

Einen Stundenplan von Hand zu erstellen, ist schwierig: Sie müssen gleichzeitig
sicherstellen, dass keine Lehrkraft an zwei Orten zugleich ist, kein Raum doppelt belegt
wird, keine Lerngruppe überschneidende Kurse hat, jeder Kurs in die verfügbaren Stunden
passt und kein Raum überbelegt wird. Darüber hinaus hält ein *guter* Plan die Lücken klein,
verteilt die Last gleichmäßig über die Tage und berücksichtigt Präferenzen. DERSİS erledigt
all das automatisch für Sie – und Sie behalten dabei stets die Kontrolle.

Die Anwendung läuft **vollständig auf Ihrem eigenen Computer**. Es sind **keine Anmeldung,
kein Konto und keine Internetverbindung erforderlich** – niemals. Sie öffnen die App und
legen los.

**Für wen:** Stundenplanbüros von Hochschulen, Schulleitungen, Fachbereichskoordinatoren und
alle, die überschneidungsfreie Wochenpläne benötigen.

---

## Funktionen

> Jede der folgenden Funktionen ist tatsächlich in der Anwendung umgesetzt. Den genauen Ort
> im Quellcode finden Sie in [`docs/FEATURES.md`](docs/FEATURES.md).

### Planungs-Engine
- **Automatische Konfliktvermeidung** – schützt vor Lehrkraft-Konflikten, Raumkonflikten,
  Überschneidungen von Lerngruppen, Kursen, die zu lang für die verfügbaren Stunden sind, und
  überbelegten Räumen. Außerdem werden die verfügbaren Tage und Zeiten jeder Lehrkraft
  berücksichtigt.
- **Multi-Engine-Optimierer** – kombiniert drei Verfahren: einen schnellen heuristischen
  Platzierungsdurchlauf, eine Large Neighborhood Search (LNS) mit 7 adaptiven „Zerstören &
  Reparieren"-Strategien und den Constraint-Solver Google **OR-Tools CP-SAT** für die exakte
  Optimierung.
- **Qualitätsbewertung mit 14 Parametern** – wägt Kompaktheit der Lehrkräfte, Lücken bei
  Studierenden, Tageslastausgleich, Fragmentierung, Raumwechsel, Tageszeit-Präferenzen und
  mehr gegeneinander ab.
- **Schwierigkeitsbasierte Reihenfolge** – die am schwersten zu platzierenden Kurse werden
  zuerst eingeplant.

### Intelligente Platzierung
- **Automatisches Einplanen eines einzelnen Kurses** in das beste freie Zeitfenster.
- **Stapelplanung** vieler noch nicht platzierter Kurse auf einmal.
- **Komplette Neuplanung**, um den gesamten Plan von Grund auf zu optimieren.
- **Drag-and-drop** auf dem Raster mit **Konfliktprüfung in Echtzeit** (ein gültiges Ablegen
  wird grün, ein ungültiges rot hervorgehoben).

### Erklärbare KI
- Jede automatische Platzierung wird mit einer **verständlichen Pro/Contra-Aufschlüsselung**
  geliefert.
- Wird ein Zug abgelehnt, erklärt die App **genau, welche Regel verletzt wurde**.
- Optimierungsläufe enden mit einem **Qualitätsurteil und Vorher/Nachher-Kennzahlen**.
- **Bedingungsverhandlung:** Wenn ein Kurs partout nicht passt, schlägt die App konkrete
  Lockerungen vor (oder welchen vorhandenen Kurs man verschieben sollte), um Platz zu
  schaffen.

### Lernen von Ihnen
- DERSİS **protokolliert Ihre manuellen Verschiebungen sowie Ihre angenommenen/abgelehnten
  Vorschläge** und passt seine Bewertung nach und nach an Ihre Planungsweise an. Gelernte
  Präferenzen werden gespeichert und über Sitzungen hinweg übernommen.

### Steuerung und Schutz
- **Schutzstufen** pro Kurs: verschiebbar, weich geschützt, nur am selben Tag, nur bei
  Verbesserung, gesperrt oder vollständig fixiert.
- **Optimierungsziele:** sechs Schieberegler (Kompaktheit Lehrkräfte, Kompaktheit
  Studierende, Raumauslastung, Fairness, minimale Änderung, Bevorzugung früher Stunden) und
  sechs fertige Profile (ausgewogen, Lehrkraft-Priorität, Studierenden-Priorität, minimale
  Änderung, platzsparend, morgenfreundlich).
- **Auswirkungsanalyse für Änderungen:** Sehen Sie vorab, wie sich eine Änderung der
  Einrichtung auf den aktuellen Plan auswirken würde, bevor Sie sie übernehmen.

### Ansichten und Auswertung
- **Vier Ansichten** des Plans: nach Raum, nach Lehrkraft, nach Lerngruppe und eine
  vollständige „Alles anzeigen"-Matrix.
- **Auswertungs-Dashboard** mit einer Qualitätsbewertung von 0–100 und einer Note von A–F,
  Kennzahlen je Lehrkraft, Gruppe und Raum, Diagrammen und umsetzbaren Hinweisen.

### Import und Export
- **Excel-Import** von Lehrkräften, Räumen, Fachrichtungen und Kursen – mit Validierung,
  Dublettenerkennung und automatischer Gruppierung gekoppelter Kurse.
- **Excel-Vorlagengenerator**, der eine ausfüllfertige Arbeitsmappe mit Beispielzeilen in
  Ihrer gewählten Sprache erzeugt.
- **Export** des fertigen Plans nach **Excel** (farbcodiert, mehrere Blätter), **CSV** und
  **PDF**.

### Bedienung und Datenschutz
- **Mehrsprachige Oberfläche** – mehr als 20 Sprachen, beim ersten Start über eine
  flaggenbasierte Auswahl gewählt (22 Flaggenoptionen), einschließlich Rechts-nach-links-
  Unterstützung für Arabisch und Persisch.
- **Interaktives Tutorial** – eine geführte Einführung im Spotlight-Stil für neue
  Nutzerinnen und Nutzer.
- **Vollständig offline** – keinerlei Netzwerkaufrufe; alle Funktionen sind lokal
  freigeschaltet.
- **Verschlüsselte lokale Speicherung** – Pläne werden in einem verschlüsselten
  `.egu`-Dateiformat (AES-256-GCM) im Ordner `Documents/Dersis/` gespeichert, mit
  automatischer Speicherung.
- **Fehlermeldung in der App** – ein eingebautes Formular bereitet eine E-Mail für Sie vor
  (siehe [Fehler melden](#fehler-melden)); die App selbst sendet niemals etwas.

---

## Installation

Dieser Abschnitt ist für Anwenderinnen und Anwender gedacht, die DERSİS einfach nur
**benutzen** möchten – ganz ohne Programmierkenntnisse.

### Unter Windows (empfohlen)

1. Besorgen Sie sich die Installationsdatei. Sie heißt etwa **`Dersis_Setup_v1.0.0.exe`**
   (die Versionsnummer kann abweichen).
2. **Doppelklicken** Sie die Installationsdatei und folgen Sie dem Assistenten am Bildschirm
   (Sprache wählen, Vereinbarung annehmen, Installationsort festlegen, dann auf
   *Installieren* klicken).
3. Starten Sie nach Abschluss **DERSİS** über das Startmenü oder die Desktop-Verknüpfung.
4. Die App öffnet sich **direkt im Hauptfenster** – es gibt keine Registrierung, keine
   Anmeldung und keine Aktivierung.

> **Wo Ihre Arbeit gespeichert wird:** DERSİS legt alles in Ihrem persönlichen
> Dokumente-Ordner ab, unter `Documents\Dersis\` (Pläne, Einstellungen, Protokolle und
> Exporte). Ihre Daten verlassen Ihren Computer nie.

### Auf anderen Systemen

Die Anwendung selbst ist mit Python und dem Qt-Toolkit gebaut und läuft auch unter Linux
(siehe [Aus dem Quellcode ausführen](#aus-dem-quellcode-ausführen)). Die **fertige
Installationsdatei ist derzeit nur für Windows** verfügbar. macOS-Unterstützung ist
*noch zu bestätigen*.

---

## Aus dem Quellcode ausführen

Dieser Abschnitt richtet sich an Personen, die mit der Kommandozeile vertraut sind und DERSİS
selbst ausführen oder bauen möchten. Sie benötigen **Python 3.10 oder neuer**.

### 1. Code holen und Abhängigkeiten installieren

```bash
# Eine isolierte Umgebung erstellen
python -m venv .venv

# Aktivieren
.venv\Scripts\activate        # Windows
source .venv/bin/activate      # Linux / macOS

# Die benötigten Bibliotheken installieren
pip install -r requirements.txt
```

> Unter **Linux** benötigen Sie zusätzlich die System-Qt-Bibliotheken, von denen PyQt6
> abhängt (installieren Sie sie über die Paketverwaltung Ihrer Distribution, falls die App
> nicht startet).

### 2. App starten

```bash
python scheduler_gui.py
```

### 3. (Optional) Eine Windows-Installationsdatei bauen

Die empfohlene Paketierungsmethode bündelt eine private Python-Kopie, damit das Ergebnis auf
jedem Windows-10/11-Rechner (64-Bit) ohne weitere Einrichtung läuft:

```bat
build_embed.bat          :: erzeugt build\Dersis.dist\
iscc installer.iss       :: erzeugt Output\Dersis_Setup_v<version>.exe
```

`build_embed.bat` lädt die offizielle einbettbare Python-Laufzeit herunter, installiert alle
in `requirements-lock.txt` festgelegten Abhängigkeiten, prüft sie mit `verify_deps.py`,
kopiert die App und ihre Ressourcen und erstellt die Starter. Eine zweite Methode mit
**Nuitka** (`build_nuitka.bat`) kompiliert in nativen Code. Alle Einzelheiten, benötigte
Werkzeuge (Inno Setup) und Optionen stehen in [`BUILD.md`](BUILD.md).

---

## Projektstruktur

```
scheduler_gui.py              Einstiegspunkt – startet die App
scheduler_app/
  core/         Planungs-Engine: Datenmodelle, Konfliktregeln, der Multi-Engine-
                Optimierer (Heuristik + LNS + CP-SAT), Bewertung, Auswertung und die
                Erklär-Engine. Hier liegt kein Oberflächencode.
  ui/           Die PyQt6-Oberfläche: Hauptfenster, alle Dialoge, der Drag-and-drop-
                Stundenplan-Renderer, das Auswertungs-Dashboard, das Tutorial und die
                mehrsprachigen Übersetzungstabellen.
  data_io/      Excel-/CSV-/PDF-Import und -Export sowie der Excel-Vorlagengenerator.
  learning/     Protokolliert Ihre Interaktionen und passt die Bewertungsgewichte mit
                der Zeit an.
  storage/      Verschlüsseltes .egu-Dateiformat (AES-256-GCM) und Pfadverwaltung.
  assets/       Anwendungssymbole.
flags/          Länderflaggen für die Sprachauswahl.
docs/           Dokumentation und das Anwendungslogo.
installer/      Inno-Setup-Ressourcen (vom Installer angezeigter Lizenztext, Assistenzbilder).
VERSION         Die einzige Quelle der Wahrheit für die Versionsnummer.
build_embed.bat / build_nuitka.bat / installer.iss   Build- und Paketierungsskripte.
```

Eine vollständige, dateiweise Aufschlüsselung finden Sie in
[`docs/STRUCTURE.md`](docs/STRUCTURE.md), eine tiefe Architekturkarte im Ordner
[`dersis-mapped/`](dersis-mapped/).

---

## Nachbau und Alternativen

Wenn Sie als Entwickler oder Einrichtung etwas Ähnliches bauen – oder genau diese Konfiguration
nachbilden – möchten, finden Sie hier, woraus DERSİS besteht und wie die Teile zusammenspielen.

**Technologie-Stack**

| Aspekt | Hier verwendet | Gängige Alternativen |
|---|---|---|
| Desktop-Oberfläche | PyQt6 (Qt 6) | PySide6, Tkinter, Web-Oberfläche (Electron / Browser) |
| Exakte Optimierung | Google OR-Tools CP-SAT | Andere CP-/MILP-Solver (z. B. CP-Optimizer, Gurobi) |
| Heuristische Suche | Eigene Heuristik + Large Neighborhood Search | Simulated Annealing, genetische Algorithmen, Tabu-Suche |
| Tabellen-Ein-/Ausgabe | openpyxl + pandas | xlsxwriter, nur das csv-Modul |
| PDF-Ausgabe | reportlab | WeasyPrint, fpdf2 |
| Verschlüsselung im Ruhezustand | `cryptography` (AES-256-GCM) | SQLCipher, Schlüsselbund des Betriebssystems |
| Windows-Paketierung | Einbettbares Python + Inno Setup | PyInstaller, Nuitka, MSIX |

**Architektonischer Ansatz zum Nachbau**

1. **Halten Sie die Engine oberflächenfrei.** Das gesamte `core/`-Paket arbeitet mit
   einfachen Python-Dictionaries, was Tests, Serialisierung und parallele Ausführung
   unabhängig von der Oberfläche erleichtert.
2. **Modellieren Sie harte und weiche Bedingungen getrennt.** Harte Bedingungen (keine
   Konflikte) gelten absolut; weiche Ziele (Kompaktheit, Ausgewogenheit) werden in eine
   gewichtete Bewertung überführt.
3. **Schichten Sie die Optimierer.** Beginnen Sie mit einer schnellen Heuristik, verbessern
   Sie mit lokaler Suche und rufen Sie optional einen exakten Solver auf – das Ergebnis jeder
   Stufe fließt in die nächste.
4. **Machen Sie Entscheidungen erklärbar.** Zu jeder automatischen Wahl eine lesbare
   Begründung zu erzeugen, macht aus einem Blackbox-Solver ein Werkzeug, dem Menschen
   vertrauen.
5. **Liefern Sie mit gebündelter Laufzeit aus.** Eine private Python-Auslieferung (die
   einbettbare Methode) vermeidet „Bei mir läuft's"-Probleme für technisch nicht versierte
   Endnutzer.

Sie dürfen die Struktur zu Lernzwecken studieren. Beachten Sie vor jeder institutionellen
Wiederverwendung die [Lizenzbedingungen](#lizenz-und-nutzung).

---

## Roadmap und Ausbaumöglichkeiten

Dies sind **realistische, noch nicht zugesagte** Richtungen, aufgeführt, damit Sie die
Machbarkeit einschätzen können. Die Punkte hier sind Möglichkeiten (*noch zu bestätigen*),
keine Versprechen.

- **Native Installationsdateien für macOS und Linux.** Die Build-Skripte sind derzeit
  Windows-`.bat`-Dateien; der App-Code ist plattformübergreifend, eine plattformeigene
  Paketierung ist also machbar.
- **Eine automatisierte Testsuite.** Das Repository wird derzeit **ohne Testdateien**
  ausgeliefert; die Continuous Integration führt nur Versions-, Build-Datei- und
  Import-Smoke-Prüfungen aus. Unit-Tests rund um die `core/`-Engine wären eine
  hochwirksame, risikoarme Verbesserung.
- **Vervollständigung der Installer-Übersetzungen.** Die App-Oberfläche deckt mehr als 20
  Sprachen ab, der Windows-Installationsassistent jedoch derzeit 13. Die restlichen
  Assistentenübersetzungen könnten ergänzt werden.
- **Optionale Mehrbenutzer-/Cloud-Synchronisation.** DERSİS ist heute bewusst vollständig
  offline; ein optionaler, ausdrücklich aktivierbarer Sync- oder Datenbank-Modus wäre eine
  umfangreiche, aber machbare Ergänzung.
- **Plugin- oder Skript-Schnittstelle.** Da die Engine oberflächenfrei und Dictionary-basiert
  ist, ist eine öffentliche API oder ein Plugin-Haken für eigene Bedingungen/Ziele technisch
  unkompliziert.
- **Weitere Exportformate / Vorlagen.** Auf den vorhandenen Excel-/CSV-/PDF-Exporten
  aufbauend könnten zusätzliche Berichtslayouts hinzukommen.

---

## Bedienungsanleitung

Eine vollständige Beschreibung des Hauptablaufs. (Tastenkürzel in Klammern.)

### 1. Erster Start
Wählen Sie beim allerersten Start Ihre **Sprache** aus der flaggenbasierten Auswahl. Danach
bietet ein optionales **interaktives Tutorial** eine geführte Tour – Sie können sie absolvieren
oder überspringen und später über **Hilfe → Tutorial** erneut abspielen.

### 2. Umgebung einrichten (Bearbeiten → Einrichtung bearbeiten)
Legen Sie die Grundlagen fest, auf denen Ihr Plan aufbaut:
- **Tage** – welche Wochentage aktiv sind (z. B. Montag–Freitag).
- **Zeitfenster** – die verfügbaren Stunden pro Tag (z. B. 09:00, 10:00, …).
- **Räume** – Name und Kapazität jedes Raums.
- **Jahrgänge und Fachrichtungen** – Ihre Lerngruppen (z. B. *1. Jahrgang – Informatik*).
- **Lehrkräfte** – das Lehrpersonal, mit optionalen verfügbaren/nicht verfügbaren Tagen und
  Zeiten.

### 3. Kurse hinzufügen
- **Einzelnen Kurs hinzufügen** (`Ctrl+Shift+A`): Geben Sie einen Namen (und optional einen
  Code), eine Lehrkraft, eine Dauer (wie viele aufeinanderfolgende Zeitfenster), die
  Ziel-Lerngruppe(n), die Teilnehmerzahl und einen Ortstyp (Präsenz, online oder Büro der
  Lehrkraft) an. Optional können Sie den Kurs auf einen festen Tag/eine feste Zeit/einen
  festen Raum **fixieren** oder **Bedingungen** hinzufügen (erlaubte/ausgeschlossene Tage,
  Zeiten oder Räume).
- **Sammeleingabe** (`Ctrl+Shift+B`): Füllen Sie eine tabellenartige Maske und planen Sie
  viele Kurse auf einmal.
- **Aus Excel importieren:** Vorlage erzeugen, ausfüllen und importieren – DERSİS prüft die
  Daten und meldet etwaige Probleme, bevor die Kurse hinzugefügt werden.

### 4. Kurse platzieren
- **Ziehen und Ablegen** Sie einen beliebigen Kurs auf das Raster; die App prüft den Zug
  sofort.
- **Einzelnen Kurs automatisch einplanen** (`Ctrl+P`): Die App schlägt das beste Zeitfenster
  mit einer Erklärung vor; nehmen Sie es an oder prüfen Sie Alternativen.
- Alle nicht platzierten Kurse in einem Schritt **stapelweise planen**.
- **Komplette Neuplanung** (`Ctrl+R`): den gesamten Plan neu optimieren.

### 5. Prüfen und anpassen
Wechseln Sie zwischen den Ansichten **nach Raum**, **nach Lehrkraft**, **nach Lerngruppe** und
**Alles anzeigen**. Kurse sind nach Jahrgang farbcodiert und tragen Abzeichen für ihre
Schutzstufe. Jeder Konflikt oder jede Warnung wird deutlich angezeigt; mit Rechtsklick auf
einen Kurs erhalten Sie Schnellaktionen (platzieren, entfernen, fixieren, schützen,
bearbeiten, löschen).

### 6. Nach Ihren Prioritäten optimieren
Öffnen Sie den Neuplanungs-Dialog und stellen Sie die **Ziel-Schieberegler** ein oder wählen
Sie ein **Profil**. Führen Sie ihn aus und lesen Sie dann die Ergebnisübersicht – was
verschoben wurde, was (falls überhaupt) nicht platziert werden konnte und wie sich die
Gesamtqualität verändert hat.

### 7. Qualität auswerten
Öffnen Sie das **Dashboard** für die Qualitätsbewertung von 0–100 und die Note A–F, dazu
Reiter für Räume, Lehrkräfte, Studierende und Gesamtauslastung mit Diagrammen und
Verbesserungsvorschlägen.

### 8. Exportieren und teilen
Exportieren Sie den fertigen Plan über das Menü „Datei" oder die Export-Schaltfläche jeder
Ansicht nach **Excel**, **CSV** oder **PDF**.

### 9. Speichern und erneut laden
- **Speichern** (`Ctrl+S`) – schreibt eine automatische Sicherung sowie eine mit Zeitstempel
  versehene, verschlüsselte `.egu`-Datei unter `Documents\Dersis\saves\`.
- **Öffnen** (`Ctrl+O`), **Neu** (`Ctrl+N`), **Rückgängig** (`Ctrl+Z`),
  **Wiederholen** (`Ctrl+Y`).

---

## Fehler melden

Ein Problem gefunden oder einen Vorschlag? Es gibt zwei einfache Wege, ihn zu melden.

1. **Aus der App heraus:** Nutzen Sie die Schaltfläche **Fehler melden**. Stürzt die App
   einmal ab, erscheint zusätzlich ein sicherer Absturzdialog. Beide bereiten eine E-Mail für
   Sie vor – ausgefüllt mit App-Version, Betriebssystem, Schweregrad und Schritten – und
   öffnen sie in Ihrem Standard-E-Mail-Programm. **Die App sendet von sich aus nichts;** Sie
   behalten die Kontrolle über die Nachricht. Ist kein E-Mail-Programm eingerichtet, wird der
   Meldungstext zum Einfügen in die Zwischenablage kopiert.

2. **Direkt per E-Mail:** Schreiben Sie an
   **[emre.uygun.elt@gmail.com](mailto:emre.uygun.elt@gmail.com)**. Beschreiben Sie bitte,
   was Sie getan haben, was Sie erwartet haben und was stattdessen passiert ist – und nennen
   Sie Ihre DERSİS-Version und Ihr Betriebssystem.

---

## Lizenz und Nutzung

**DERSİS ist jetzt für alle privaten Nutzerinnen und Nutzer kostenlos.** Sie dürfen es für
Ihre persönliche Arbeit kostenlos herunterladen, installieren und verwenden.

**Einrichtungen benötigen für die institutionelle Nutzung eine Lizenz.** Einrichtungen –
darunter **Universitäten, Fakultäten, Schulen, Fachbereiche, Forschungszentren,
Verwaltungseinheiten oder jede Untergliederung einer Universität** – **dürfen DERSİS nicht
ohne Zahlung einer Lizenz- oder Integrationsgebühr in ihre eigenen institutionellen Systeme
einbetten, integrieren, bereitstellen oder offiziell aufnehmen.**

Wenn Ihre Einrichtung **institutionelle Nutzung, Integration, Einbettung, Bereitstellung,
Anpassung oder offizielle Einführung** wünscht, nehmen Sie bitte Kontakt auf, um eine Lizenz
zu vereinbaren:

> **Kontakt für institutionelle Lizenzierung:**
> [emre.uygun.elt@gmail.com](mailto:emre.uygun.elt@gmail.com)

Die vollständigen Bedingungen finden Sie in der [`LICENSE.md`](LICENSE.md) auf oberster Ebene.

---

<p align="center">
  <a href="README-en.md">English</a> ·
  <a href="README-tr.md">Türkçe</a> ·
  <a href="README-de.md">Deutsch</a> ·
  <a href="README-es.md">Español</a>
</p>
