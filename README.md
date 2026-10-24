# DJ Director AI

Regista multicamera automatico e *music-aware* per performance DJ. Ascolta in tempo reale il master del
mixer, stima tempo, beat e stato musicale, e decide **se**, **quando** e **su quale camera** tagliare,
comandando OBS Studio tramite obs-websocket 5.x. Funziona interamente in locale.

> Principio guida: un bravo regista sa anche quando **non** cambiare camera.
> `HOLD` è un'azione editoriale normale, e ogni decisione ha un motivo verificabile.

Stato: **v0.1 (Music-Aware Director)**. Leggi [docs/REPORT_v0.1.md](docs/REPORT_v0.1.md) per sapere cosa
è stato verificato e cosa no, in particolare i test che richiedono Windows, OBS e hardware audio reali.

---

## Cosa fa (v0.1)

- App desktop (PySide6, tema scuro) con dashboard, wizard di primo avvio, impostazioni avanzate opzionali.
- Connessione a OBS (obs-websocket 5.x, incluso in OBS ≥ 28): legge le scene, segue Program/Preview e
  Studio Mode, rileva rinomina o eliminazione di scene e cambi di scene collection, si riconnette da solo.
- Associazione scene → ruoli editoriali: `MASTER`, `DETAIL`, `SIDE`, `WIDE`, `OVERHEAD`, `CUSTOM`
  (numero di camere arbitrario; testato fino a 5).
- Ingresso audio da interfaccia o mixer USB (PortAudio, preferendo WASAPI su Windows), WASAPI loopback
  opzionale (PyAudioWPatch) e file WAV/FLAC per i test.
- Analisi musicale causale: livello, bande di frequenza, spectral flux, densità degli onset, novelty,
  BPM con confidenza, fase del beat, downbeat (euristico, sperimentale) e stati musicali probabilistici
  con eventi (`sparse_start`, `transition`, `reentry`).
- Director Engine deterministico e testabile, con tre decisioni separate (opportunità, destinazione,
  timing), vincoli hard, isteresi e tagli sincronizzati al beat solo quando la confidenza è sufficiente.
- Preset Minimal / **Natural** (default) / Dynamic: tre insiemi di parametri dello stesso motore.
- Modalità **Shadow**: calcola e registra tutte le decisioni senza mai comandare OBS.
- **Simulazione** offline deterministica, con timeline, confronto con switcher a timer e casuale ed
  export JSON/CSV.
- Override manuale: un cambio scena fatto a mano in OBS viene riconosciuto e rispettato.
- Log delle decisioni in JSONL per ogni sessione e log diagnostico con rotazione.

Non fa (ancora): analisi video (v0.2), analisi preventiva delle tracce (v0.3). Non avvia né ferma mai
registrazione o streaming, non crea, elimina o modifica scene, sorgenti, filtri, audio o profili.

---

## Installazione (utente finale, Windows 10/11 x64)

1. Scarica `DJDirectorAI-<versione>-win64.zip` dalla build (artifact della CI *test-and-build-windows*).
2. Estrai la cartella dove vuoi, per esempio `C:\Tools\DJDirectorAI`. È una versione **portable**:
   non serve installare Python.
3. Avvia `DJDirectorAI.exe`. Al primo avvio parte il wizard.

Configurazione e log vengono salvati in `%APPDATA%\DJDirectorAI` (config) e `%LOCALAPPDATA%\DJDirectorAI\logs`,
mai nella cartella del programma. La password di OBS è cifrata con **Windows DPAPI** (legata al tuo
account) e non compare mai nel file di configurazione né nei log.

Nella stessa cartella c'è anche `dj-director-cli.exe` con gli strumenti da riga di comando:
`devices`, `obs-test`, `simulate`, `selftest`.

## Preparare OBS

1. OBS ≥ 28 → *Strumenti › Impostazioni server WebSocket* → abilita il server e annota porta e password.
2. Crea una scena per ogni camera (es. `Cam Master`, `Cam Mixer`, `Cam Side`). DJ Director cambia solo la
   scena **Program**.
3. **Studio Mode**: se attivo, DJ Director cambia comunque la scena **Program** (cioè ciò che viene
   registrato o trasmesso) e non tocca mai la Preview. È una scelta voluta: ciò che il regista decide va in onda.

## Primo utilizzo (wizard)

1. **OBS**: host, porta, password, poi *Test connection*.
2. **Cameras**: spunta le scene che sono inquadrature, assegna un ruolo e scegli una camera Master.
3. **Audio**: scegli l'ingresso che porta il **master del mixer** (non un microfono ambientale) e i canali,
   poi verifica che il meter si muova.
4. **Preset**: Natural è consigliato.
5. Consigliato: lascia attiva la **Shadow mode** per la prima prova. Vedrai le decisioni nella dashboard
   e nel log senza alcun cambio in OBS.

Dalla Dashboard: **Start Auto Director** (va LIVE), *Start Shadow*, **Pause/Resume**, **Stop**,
**Force Master**. Lo stato è sempre visibile: `OFF / ARMED / LIVE / SHADOW / PAUSED / ERROR`.

## Override manuale

Se durante la regia automatica cambi scena a mano in OBS:
- *Respect for a while, then resume* (default): il programma rispetta la tua scelta per 30 s
  (configurabile), poi riprende.
- *Respect until I press Resume*: resta fermo finché non premi Resume.
- *Adopt and continue*: adotta la nuova inquadratura e continua.
- Se passi a una scena **non gestita** (es. "Be Right Back"), la regia si sospende sempre fino a Resume:
  non verrai mai "tirato fuori" da una scena di servizio.

## Uso da riga di comando (sviluppo e diagnostica)

```bash
dj-director                      # GUI
dj-director devices              # elenca ingressi audio e loopback
dj-director obs-test --password X
dj-director simulate set.wav --preset natural --json out.json --csv out.csv
dj-director simulate --synthetic # set sintetico con ground truth
dj-director synth test.wav       # scrive il set sintetico su file
dj-director fake-obs --port 4455 # finto server OBS (richiede 'websockets')
dj-director selftest             # verifica headless di installazione/build
```

## Sviluppo

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-lock.txt
pip install -e ".[dev]"
QT_QPA_PLATFORM=offscreen pytest                   # Windows (PowerShell): $env:QT_QPA_PLATFORM="offscreen"
```

Su Linux servono `libportaudio2`, `libegl1`, `libxkbcommon0` e `libfontconfig1`.

Build Windows riproducibile (crea `dist\DJDirectorAI\` e lo zip, esegue i test e il `selftest`
sugli eseguibili congelati):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
```

La stessa procedura gira in CI su `windows-latest` (`.github/workflows/build-windows.yml`).

## Documentazione

- [docs/TECHNICAL_AUDIT.md](docs/TECHNICAL_AUDIT.md): verifica tecnica di librerie, API e rischi.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): moduli, thread, flussi dati, stati, confine tra regole,
  euristiche e AI.
- [docs/DIRECTOR.md](docs/DIRECTOR.md): come decide il regista (scoring, vincoli, timing, preset).
- [docs/TESTING.md](docs/TESTING.md): test automatici, risultati misurati, checklist dei test manuali.
- [docs/REPORT_v0.1.md](docs/REPORT_v0.1.md): report finale della milestone.
- [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md): licenze delle dipendenze.

## Privacy

Nessuna telemetria, nessun account, nessun servizio cloud. L'audio non lascia mai il computer. Il
programma comunica solo con OBS sull'host e sulla porta configurati (di default `127.0.0.1`).
