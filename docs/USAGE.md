# behavioral-auth — Usage Guide / Instrukcja użycia

**Version / Wersja: v0.4.0** · Windows (installer) + Linux (AppImage)

> 🇬🇧 **English below** · 🇵🇱 **Polski poniżej** (przewiń do sekcji [Polski](#polski))

> ⚠️ **Status honesty / uczciwość statusu.** v0.4.0 was built and smoke-tested in CI,
> but **not yet verified on a real Windows desktop session**. On Linux the AppImage runs;
> the from-source install path is the tested one. Read [Known limitations](#known-limitations--what-to-verify)
> before relying on it — especially the Windows *Session 0* caveat.

---

# English

## What this is

A local-only daemon that learns **how you type and move the mouse**, freezes that pattern,
and then watches for someone whose behaviour stops matching it. Optionally it also checks
your **face** on the webcam.

**The golden rule: it only warns. It never locks the session, never logs you out.** The
strongest thing it does is write a log line, print a message, fire a desktop notification,
and (if you switch it on) forward an event to a SIEM. Everything stays on the machine
unless you explicitly enable SIEM forwarding.

**Lifecycle:** `LEARNING` → `MONITORING` → `ALARM`. It learns until it has enough of your
behaviour, freezes the pattern (promotion), then scores live behaviour against it. A
sustained mismatch raises an alarm; a return to normal clears it.

The same five commands exist on both operating systems:

| Command | What it does |
|---|---|
| `behavioral-authd` | the daemon itself (learns + monitors) |
| `behavioral-auth status` | current state, progress, deviation |
| `behavioral-auth reset` | new person at the keyboard — pattern from scratch |
| `behavioral-auth learn-more` | extend the existing pattern (explicit, not automatic) |
| `behavioral-auth pause` / `resume` | pause / resume scoring |
| `behavioral-auth db` | create / migrate the database |
| `behavioral-report` | full report: threshold, learning cycles, scoring, alarms |
| `behavioral-face verify` / `info` | one-shot camera face check / face-model state |

---

## Windows

### 1. Install

Run **`behavioral-auth-setup-0.4.0.exe`** (from the GitHub release) as an administrator.
The installer:

- installs the suite to `C:\Program Files\behavioral-auth\`;
- drops an **editable config** at `C:\ProgramData\behavioral-auth\config.yaml` (never
  overwritten on upgrade) and points the machine-wide `BEHAVIORAL_AUTH_CONFIG` env var at it;
- registers a Windows **service** named `behavioral-auth` (auto-start) and starts it.

### 2. Where things live

| | Path |
|---|---|
| Programs | `C:\Program Files\behavioral-auth\` (`behavioral-auth.exe`, `behavioral-authd.exe`, `behavioral-report.exe`, `behavioral-face.exe`, `behavioral-auth-service.exe`) |
| Config (edit this) | `C:\ProgramData\behavioral-auth\config.yaml` |
| Data + log | `C:\ProgramData\behavioral-auth\` (`behavior.duckdb`, `behavioral-auth.log`, `model.onnx`, …) |

The install folder is **not** added to `PATH`, so run the CLI with its full path or from
the folder:

```powershell
cd "C:\Program Files\behavioral-auth"
.\behavioral-auth.exe status
.\behavioral-report.exe
```

### 3. What to set

Out of the box it works with sensible defaults (learns your behaviour, warns on a
mismatch, face check on the default webcam). Edit
`C:\ProgramData\behavioral-auth\config.yaml` if you want to change:

- **`face.enabled`** — set `false` if there is no webcam, or `camera_index` to pick another.
- **`alarm.notify_cmd`** — the desktop popup. Default is a PowerShell message box; change
  the text or the command.
- **`general.mode`** — `prod` (real, strict promotion gates) or `dev` (gates lowered for a
  quick smoke test — *not* for real protection).
- **`siem.*`** — off by default. To forward events to the Windows **Event Log**, set
  `siem.enabled: true` (`sink` is already `eventlog` in the Windows config). See
  [SIEM](#5-siem--windows-event-log).

After editing, restart the service so it re-reads the config:

```powershell
cd "C:\Program Files\behavioral-auth"
.\behavioral-auth-service.exe stop
.\behavioral-auth-service.exe start
```

### 4. The service

```powershell
sc query behavioral-auth                 # is it running?
# or the GUI: services.msc  ->  "behavioral-auth (behavioural authentication)"
.\behavioral-auth-service.exe stop|start # from the install folder
```

> ⚠️ **Read this before trusting capture.** See the
> [Session 0 limitation](#known-limitations--what-to-verify): a service runs in an isolated
> session and its global input hook may not see your interactive desktop. If `status` never
> leaves `LEARNING` with the sequence counter stuck at 0 while you type, that is the
> symptom — run the daemon in your own session instead (below).

**Run in your own session (fallback / for capture):**

```powershell
cd "C:\Program Files\behavioral-auth"
.\behavioral-authd.exe            # runs in your logged-in session; Ctrl+C to stop
```

### 5. SIEM → Windows Event Log

With `siem.enabled: true` and `siem.sink: eventlog`, alarms, state changes and operations
(reset, learn-more, start/stop) are written to the **Application** log under the source
`behavioral-auth`. Check them in **Event Viewer → Windows Logs → Application** (filter by
source). What is forwarded is *verdicts and numbers only* — never your keystrokes, mouse
coordinates or face crops.

### 6. Uninstall

Use *Apps & features* (or the uninstaller). It stops and removes the service, then deletes
the program files. Your data and config in `C:\ProgramData\behavioral-auth\` are left in
place — delete that folder by hand if you want them gone.

### 7. Troubleshooting (Windows)

| Symptom | Fix |
|---|---|
| `behavioral-auth.exe` not found | run from `C:\Program Files\behavioral-auth\` or use the full path |
| `status`: *"Daemon never started"* | the service is not running — `services.msc` or `behavioral-auth-service.exe start` |
| sequence counter stuck at 0 while typing | the **Session 0** limitation — run `behavioral-authd.exe` in your own session (§4) |
| no desktop popup on alarm | check `alarm.notify_cmd`; the alarm still appears in the log and `status` |
| nothing in Event Viewer | `siem.enabled` is `false` by default — turn it on (§5) |

---

## Linux (AppImage)

The AppImage is a **download-and-run** file, not a package. It gives you the frozen app; a
few system prerequisites are on you (the from-source installers do them automatically).

### 1. Prerequisites

```bash
chmod +x behavioral-auth-x86_64.AppImage

# a) Device access — the daemon reads /dev/input. Join the input (and video for the
#    camera) group, then re-log or run `newgrp input`:
sudo usermod -aG input,video "$USER" && newgrp input

# b) Data dir — the bundled default writes to /var/lib/behavioral-auth. Create it writable,
#    or point BEHAVIORAL_AUTH_CONFIG at your own config with different paths:
sudo mkdir -p /var/lib/behavioral-auth && sudo chown "$USER" /var/lib/behavioral-auth

# c) FUSE 2 — the AppImage runtime needs libfuse.so.2. On FUSE-3-only hosts (recent Fedora):
sudo dnf install fuse-libs           # Fedora/RHEL
# or run without FUSE at all:
#   APPIMAGE_EXTRACT_AND_RUN=1 ./behavioral-auth-x86_64.AppImage authd
```

### 2. Run

The AppImage is a multi-call binary — the command is the first argument:

```bash
./behavioral-auth-x86_64.AppImage authd            # the daemon (Ctrl+C to stop)
./behavioral-auth-x86_64.AppImage auth status      # current state
./behavioral-auth-x86_64.AppImage report           # full report
./behavioral-auth-x86_64.AppImage face verify      # one-shot face check
```

### 3. Where things live (Linux)

| | Path |
|---|---|
| Config | `BEHAVIORAL_AUTH_CONFIG`, then `/etc/behavioral-auth/config.yaml`, then the bundled default (uses `/var/lib/behavioral-auth`) |
| Data + log | `/var/lib/behavioral-auth/` |

> The "config next to the executable" trick does **not** work for an AppImage (that path is
> a temporary mount). Override with `BEHAVIORAL_AUTH_CONFIG=/path/to/config.yaml` or a file
> in `/etc/behavioral-auth/`.

### 4. Autostart

The AppImage does not install itself. To run at login, either add your own systemd **user**
unit, or use the from-source installer (`make install` / `src/scripts/fedora-install.sh` /
`ubuntu-install.sh`), which drops a `behavioral-authd.service` user unit and the udev rules:

```bash
systemctl --user enable --now behavioral-authd    # only after the from-source install
```

### 5. SIEM → syslog / Wazuh (Linux)

Off by default. Set `siem.enabled: true` with `siem.sink: syslog` (writes to `/dev/log`; a
Wazuh agent on the box picks it up) or `siem.sink: wazuh` (straight to the manager). Verify:

```bash
journalctl -t behavioral-auth -f        # events land in the journal with sink: syslog
```

### 6. Troubleshooting (Linux)

| Symptom | Fix |
|---|---|
| *"No keyboard or mouse devices found"* | join the `input` group (§1a) and re-log |
| AppImage won't start / FUSE error | `dnf install fuse-libs`, or `APPIMAGE_EXTRACT_AND_RUN=1 ./…AppImage …` (§1c) |
| permission denied writing data | make `/var/lib/behavioral-auth` writable, or repoint paths in config (§1b) |
| camera not opening | set `face.camera_index`, or `face.enabled: false` if headless |

---

## The lifecycle & what to check

1. **Start the daemon and use the machine normally.** It begins in `LEARNING`.
2. **Watch progress** with `status`. You will see, roughly:
   ```
   sequences  340/1200
   activity    22/90  min
   face        18/60
   cycles 1, stable streak 0/3
   waiting for: sequences, active_minutes
   ```
   These are the promotion gates: enough sequences, enough active minutes, behaviour seen
   across distinct hours of the day, and (default) enough face samples.
3. **Promotion → `MONITORING`.** Once the gates pass *and* the model survives three stable
   learning cycles *and* the sanity gate (it must flag synthetic impostors), the pattern is
   **frozen**. Nothing retrains on its own after this — a stranger cannot teach it to accept
   them by using the machine. Only `reset` or `learn-more` changes the pattern.
4. **In `MONITORING`,** `status` shows a deviation ratio (`1.00x` = at the threshold) and a
   sparkline of recent scores. When someone whose behaviour differs uses the machine long
   enough, the ratio climbs and — after a sustained run — the state becomes `ALARM`.
5. **`ALARM`** fires the notification, writes the log/Event Log/SIEM, and keeps warning
   until behaviour returns to normal. **The session is never locked.**

**Quick checks that it's alive and correct:**

- `status` — is it `LEARNING` and is the sequence counter climbing as you type? (If not on
  Windows, see the Session 0 note.)
- `behavioral-report` — the pattern's anomaly threshold, its separation from synthetic
  impostors, per-cycle pass rates, and any alarms.
- `behavioral-face verify` — a one-shot "is this me?" against the webcam.
- **Fast smoke test:** set `general.mode: dev` in the config to shrink the gates so you can
  walk `LEARNING → MONITORING` in minutes. A pattern promoted under `dev` is a smoke test,
  **not** real protection — set it back to `prod` for real use.

---

## Known limitations & what to verify

- **The whole Windows path is unverified on real hardware.** v0.4.0 was built and
  smoke-tested in CI (the bundle freezes, `behavioral-auth.exe` runs, the installer
  compiles). Nothing past that — the service under the SCM, live capture, an alarm reaching
  the Event Log, clean uninstall — has been run on a real Windows desktop yet.
- **Windows Session 0 / service capture.** A Windows service runs as `LocalSystem` in the
  isolated *Session 0*, while you work in a separate interactive session. A global
  low-level input hook installed from Session 0 **may not receive your desktop's keyboard
  and mouse events.** If so, the service will sit in `LEARNING` forever with no sequences.
  The working setup is then to run **`behavioral-authd.exe` in your own logged-in session**
  (e.g. a per-user *Task Scheduler* task "at log on", running in the user session) and use
  the service only for lifecycle/plumbing. **This is the first thing to confirm on
  hardware.**
- **The AppImage is not a full install.** It does not set up the `input`/`video` groups, a
  writable data dir, or autostart — see the Linux prerequisites. For a turnkey Linux setup,
  use the from-source installer.
- **No FAR/FRR/EER numbers.** The system only ever sees one person, so there is no impostor
  distribution to measure against; the report deliberately does not print those metrics.

---
---

<a name="polski"></a>
# Polski

## Co to jest

Lokalny (offline) demon, który uczy się, **jak piszesz na klawiaturze i ruszasz myszą**,
zamraża ten wzorzec, a potem wykrywa, gdy zachowanie osoby przy urządzeniu przestaje do
niego pasować. Opcjonalnie sprawdza też Twoją **twarz** z kamery.

**Złota zasada: on tylko ostrzega. Nigdy nie blokuje sesji, nie wylogowuje.** Najmocniejsze,
co robi, to wpis w logu, komunikat, powiadomienie na pulpicie i (jeśli włączysz) przesłanie
zdarzenia do SIEM. Wszystko zostaje na maszynie, chyba że świadomie włączysz forwarding do
SIEM.

**Cykl życia:** `LEARNING` (NAUKA) → `MONITORING` (NADZÓR) → `ALARM`. Uczy się, aż zbierze
dość Twojego zachowania, zamraża wzorzec (promocja), potem punktuje bieżące zachowanie
względem niego. Trwała niezgodność podnosi alarm; powrót do normy go zdejmuje.

Te same pięć komend działa na obu systemach:

| Komenda | Co robi |
|---|---|
| `behavioral-authd` | sam demon (uczy się + nadzoruje) |
| `behavioral-auth status` | aktualny stan, postęp, odchylenie |
| `behavioral-auth reset` | nowa osoba przy klawiaturze — wzorzec od zera |
| `behavioral-auth learn-more` | doucz istniejący wzorzec (jawnie, nie automatycznie) |
| `behavioral-auth pause` / `resume` | wstrzymaj / wznów punktację |
| `behavioral-auth db` | utwórz / zmigruj bazę |
| `behavioral-report` | pełny raport: próg, cykle nauki, punktacja, alarmy |
| `behavioral-face verify` / `info` | jednorazowe sprawdzenie twarzy / stan modelu twarzy |

---

## Windows

### 1. Instalacja

Uruchom **`behavioral-auth-setup-0.4.0.exe`** (z release'u na GitHubie) jako administrator.
Instalator:

- instaluje aplikacje do `C:\Program Files\behavioral-auth\`;
- zostawia **edytowalny config** w `C:\ProgramData\behavioral-auth\config.yaml` (nie
  nadpisywany przy aktualizacji) i ustawia na niego zmienną `BEHAVIORAL_AUTH_CONFIG` dla
  całej maszyny;
- rejestruje **usługę** Windows o nazwie `behavioral-auth` (autostart) i ją uruchamia.

### 2. Gdzie co leży

| | Ścieżka |
|---|---|
| Programy | `C:\Program Files\behavioral-auth\` (`behavioral-auth.exe`, `behavioral-authd.exe`, `behavioral-report.exe`, `behavioral-face.exe`, `behavioral-auth-service.exe`) |
| Config (to edytuj) | `C:\ProgramData\behavioral-auth\config.yaml` |
| Dane + log | `C:\ProgramData\behavioral-auth\` (`behavior.duckdb`, `behavioral-auth.log`, `model.onnx`, …) |

Folder instalacji **nie** jest dodany do `PATH`, więc CLI uruchamiaj pełną ścieżką lub z
folderu:

```powershell
cd "C:\Program Files\behavioral-auth"
.\behavioral-auth.exe status
.\behavioral-report.exe
```

### 3. Co ustawić

Domyślnie działa od ręki z rozsądnymi ustawieniami (uczy się, ostrzega przy niezgodności,
sprawdza twarz na domyślnej kamerze). Edytuj `C:\ProgramData\behavioral-auth\config.yaml`,
jeśli chcesz zmienić:

- **`face.enabled`** — ustaw `false`, gdy nie ma kamery, albo `camera_index` na inną.
- **`alarm.notify_cmd`** — wyskakujące powiadomienie. Domyślnie to okienko PowerShell;
  zmień tekst lub komendę.
- **`general.mode`** — `prod` (prawdziwy, ostre bramki promocji) albo `dev` (bramki obniżone
  do szybkiego testu — *nie* do realnej ochrony).
- **`siem.*`** — domyślnie wyłączone. Aby słać zdarzenia do **Dziennika zdarzeń** Windows,
  ustaw `siem.enabled: true` (`sink` w configu Windows to już `eventlog`). Patrz
  [SIEM](#5-siem--dziennik-zdarzeń-windows).

Po edycji zrestartuj usługę, żeby wczytała config:

```powershell
cd "C:\Program Files\behavioral-auth"
.\behavioral-auth-service.exe stop
.\behavioral-auth-service.exe start
```

### 4. Usługa

```powershell
sc query behavioral-auth                 # czy działa?
# albo GUI: services.msc  ->  "behavioral-auth (behavioural authentication)"
.\behavioral-auth-service.exe stop|start # z folderu instalacji
```

> ⚠️ **Przeczytaj, zanim zaufasz przechwytywaniu.** Patrz
> [ograniczenie Session 0](#znane-ograniczenia--co-zweryfikować): usługa działa w
> izolowanej sesji i jej globalny hook wejścia może nie widzieć Twojego interaktywnego
> pulpitu. Jeśli `status` nie wychodzi z `LEARNING`, a licznik sekwencji stoi na 0, mimo że
> piszesz — to jest ten objaw. Uruchom demona w swojej sesji (niżej).

**Uruchomienie w swojej sesji (fallback / dla przechwytywania):**

```powershell
cd "C:\Program Files\behavioral-auth"
.\behavioral-authd.exe            # działa w Twojej zalogowanej sesji; Ctrl+C by zatrzymać
```

### 5. SIEM → Dziennik zdarzeń Windows

Przy `siem.enabled: true` i `siem.sink: eventlog` alarmy, zmiany stanu i operacje (reset,
learn-more, start/stop) trafiają do logu **Application** pod źródłem `behavioral-auth`.
Sprawdzisz je w **Podglądzie zdarzeń → Dzienniki Windows → Aplikacja** (filtruj po źródle).
Przesyłane są *tylko werdykty i liczby* — nigdy Twoje naciśnięcia klawiszy, współrzędne
myszy ani kadry twarzy.

### 6. Deinstalacja

Użyj *Aplikacje i funkcje* (lub deinstalatora). Zatrzymuje i usuwa usługę, potem kasuje
pliki programu. Twoje dane i config w `C:\ProgramData\behavioral-auth\` **zostają** — skasuj
ten folder ręcznie, jeśli chcesz się ich pozbyć.

### 7. Rozwiązywanie problemów (Windows)

| Objaw | Co zrobić |
|---|---|
| nie znaleziono `behavioral-auth.exe` | uruchom z `C:\Program Files\behavioral-auth\` lub pełną ścieżką |
| `status`: *"Demon nigdy nie był uruchomiony"* | usługa nie działa — `services.msc` lub `behavioral-auth-service.exe start` |
| licznik sekwencji stoi na 0 mimo pisania | ograniczenie **Session 0** — uruchom `behavioral-authd.exe` w swojej sesji (§4) |
| brak powiadomienia przy alarmie | sprawdź `alarm.notify_cmd`; alarm i tak jest w logu oraz w `status` |
| pusto w Podglądzie zdarzeń | `siem.enabled` domyślnie `false` — włącz (§5) |

---

## Linux (AppImage)

AppImage to plik **„pobierz i uruchom"**, nie pakiet. Daje zamrożoną aplikację; kilka
wymagań systemowych jest po Twojej stronie (instalatory ze źródeł robią je automatycznie).

### 1. Wymagania wstępne

```bash
chmod +x behavioral-auth-x86_64.AppImage

# a) Dostęp do urządzeń — demon czyta /dev/input. Dołącz do grupy input (i video dla
#    kamery), potem przeloguj się lub `newgrp input`:
sudo usermod -aG input,video "$USER" && newgrp input

# b) Katalog danych — domyślny config pisze do /var/lib/behavioral-auth. Utwórz go
#    zapisywalnym, albo wskaż BEHAVIORAL_AUTH_CONFIG na własny config z innymi ścieżkami:
sudo mkdir -p /var/lib/behavioral-auth && sudo chown "$USER" /var/lib/behavioral-auth

# c) FUSE 2 — runtime AppImage potrzebuje libfuse.so.2. Na hostach z samym FUSE 3 (nowsza Fedora):
sudo dnf install fuse-libs           # Fedora/RHEL
# albo uruchom bez FUSE:
#   APPIMAGE_EXTRACT_AND_RUN=1 ./behavioral-auth-x86_64.AppImage authd
```

### 2. Uruchomienie

AppImage to binarka multi-call — komenda jest pierwszym argumentem:

```bash
./behavioral-auth-x86_64.AppImage authd            # demon (Ctrl+C by zatrzymać)
./behavioral-auth-x86_64.AppImage auth status      # aktualny stan
./behavioral-auth-x86_64.AppImage report           # pełny raport
./behavioral-auth-x86_64.AppImage face verify      # jednorazowe sprawdzenie twarzy
```

### 3. Gdzie co leży (Linux)

| | Ścieżka |
|---|---|
| Config | `BEHAVIORAL_AUTH_CONFIG`, potem `/etc/behavioral-auth/config.yaml`, potem domyślny w bundlu (używa `/var/lib/behavioral-auth`) |
| Dane + log | `/var/lib/behavioral-auth/` |

> Sztuczka „config obok pliku wykonywalnego" **nie** działa dla AppImage (ta ścieżka to
> tymczasowy mount). Nadpisz przez `BEHAVIORAL_AUTH_CONFIG=/ścieżka/config.yaml` albo plik w
> `/etc/behavioral-auth/`.

### 4. Autostart

AppImage sam się nie instaluje. Aby startował przy logowaniu, dodaj własny unit systemd
**user**, albo użyj instalatora ze źródeł (`make install` / `src/scripts/fedora-install.sh`
/ `ubuntu-install.sh`), który wrzuca unit `behavioral-authd.service` i reguły udev:

```bash
systemctl --user enable --now behavioral-authd    # dopiero po instalacji ze źródeł
```

### 5. SIEM → syslog / Wazuh (Linux)

Domyślnie wyłączone. Ustaw `siem.enabled: true` z `siem.sink: syslog` (pisze do `/dev/log`;
agent Wazuh na maszynie to podbiera) albo `siem.sink: wazuh` (prosto do managera). Sprawdź:

```bash
journalctl -t behavioral-auth -f        # zdarzenia trafiają do journala przy sink: syslog
```

### 6. Rozwiązywanie problemów (Linux)

| Objaw | Co zrobić |
|---|---|
| *"No keyboard or mouse devices found"* | dołącz do grupy `input` (§1a) i przeloguj się |
| AppImage nie startuje / błąd FUSE | `dnf install fuse-libs`, albo `APPIMAGE_EXTRACT_AND_RUN=1 ./…AppImage …` (§1c) |
| brak uprawnień do zapisu danych | zrób `/var/lib/behavioral-auth` zapisywalnym, albo przekieruj ścieżki w configu (§1b) |
| kamera się nie otwiera | ustaw `face.camera_index`, albo `face.enabled: false` bez kamery |

---

## Cykl życia i co sprawdzić

1. **Uruchom demona i korzystaj z maszyny normalnie.** Startuje w `LEARNING`.
2. **Obserwuj postęp** przez `status`. Zobaczysz mniej więcej:
   ```
   sekwencje  340/1200
   aktywność   22/90  min
   twarz       18/60
   cykli 1, seria stabilnych 0/3
   czeka na: sequences, active_minutes
   ```
   To bramki promocji: dość sekwencji, dość aktywnych minut, zachowanie widziane w różnych
   godzinach doby oraz (domyślnie) dość próbek twarzy.
3. **Promocja → `MONITORING`.** Gdy bramki przejdą *oraz* model przetrwa trzy stabilne
   cykle nauki *oraz* bramkę sanity (musi wykrywać syntetycznych oszustów), wzorzec zostaje
   **zamrożony**. Potem nic nie douczy się samo — obcy nie nauczy systemu, żeby go
   akceptował, po prostu używając maszyny. Wzorzec zmienia tylko `reset` lub `learn-more`.
4. **W `MONITORING`** `status` pokazuje odchylenie (`1.00x` = na progu) i sparkline ostatnich
   ocen. Gdy ktoś o innym zachowaniu korzysta z maszyny dość długo, odchylenie rośnie i — po
   trwałej serii — stan zmienia się na `ALARM`.
5. **`ALARM`** odpala powiadomienie, pisze do logu/Dziennika/SIEM i ostrzega dalej, aż
   zachowanie wróci do normy. **Sesja nigdy nie jest blokowana.**

**Szybkie sprawdzenia, że żyje i działa poprawnie:**

- `status` — czy jest `LEARNING` i czy licznik sekwencji rośnie, gdy piszesz? (Jeśli nie na
  Windows — patrz uwaga o Session 0.)
- `behavioral-report` — próg anomalii wzorca, jego separacja od syntetycznych oszustów,
  pass_rate na cykl i ewentualne alarmy.
- `behavioral-face verify` — jednorazowe „czy to ja?" z kamery.
- **Szybki test:** ustaw `general.mode: dev` w configu, by zmniejszyć bramki i przejść
  `LEARNING → MONITORING` w minuty. Wzorzec promowany w `dev` to test poprawności, **nie**
  realna ochrona — na koniec wróć do `prod`.

---

## Znane ograniczenia / co zweryfikować

- **Cała ścieżka Windows jest niezweryfikowana na realnym sprzęcie.** v0.4.0 zbudowano i
  przetestowano dymnie w CI (bundle się zamraża, `behavioral-auth.exe` startuje, instalator
  się kompiluje). Nic dalej — usługa pod SCM, przechwytywanie na żywo, alarm w Dzienniku
  zdarzeń, czysta deinstalacja — nie było jeszcze uruchomione na prawdziwym desktopie.
- **Windows Session 0 / przechwytywanie z usługi.** Usługa Windows działa jako
  `LocalSystem` w izolowanej *Session 0*, a Ty pracujesz w osobnej sesji interaktywnej.
  Globalny hook wejścia założony z Session 0 **może nie odbierać zdarzeń klawiatury i myszy
  Twojego pulpitu.** Wtedy usługa zostanie w `LEARNING` bez sekwencji. Działający układ to
  uruchamianie **`behavioral-authd.exe` w Twojej zalogowanej sesji** (np. zadanie
  *Harmonogramu zadań* „przy logowaniu", w sesji użytkownika), a usługa pełni tylko rolę
  cyklu życia. **To pierwsza rzecz do potwierdzenia na sprzęcie.**
- **AppImage to nie pełna instalacja.** Nie ustawia grup `input`/`video`, zapisywalnego
  katalogu danych ani autostartu — patrz wymagania wstępne Linuksa. Do gotowego układu użyj
  instalatora ze źródeł.
- **Brak liczb FAR/FRR/EER.** System zawsze widzi tylko jedną osobę, więc nie ma rozkładu
  oszustów do porównania; raport świadomie nie drukuje tych metryk.
