# Audit tecnico iniziale (Fase A)

Ambiente di sviluppo usato per questa milestone: container **Linux** x86-64, Python 3.13, nessuna scheda
audio, nessun OBS, nessun Windows. Tutto ciò che dipende da Windows, OBS reale o hardware audio è
marcato **[da verificare su Windows]**.

## 1. Controllo di OBS

| Opzione | Valutazione | Decisione |
|---|---|---|
| Plugin C++ nativo | Massimo controllo, ma rischia la stabilità di OBS e lega il ciclo di rilascio a OBS. | Escluso per la v0.1 |
| obs-websocket 5.x (integrato in OBS ≥ 28) | Protocollo documentato, locale, autenticazione SHA-256 challenge/salt. | **Adottato** |
| `obsws-python` (1.8.0) | Wrapper mantenuto, ma meno controllo su timeout, close code (4009 = auth fallita), thread degli eventi e allow-list. | Valutato, non adottato |
| Client proprio su `websocket-client` | Il sottoinsieme di protocollo necessario è piccolo (Hello/Identify/Identified/Request/Response/Event). | **Adottato** (`obs/client.py`) |

Richieste usate, tutte previste da obs-websocket 5.x: `GetVersion`, `GetSceneList`, `GetStudioModeEnabled`,
`GetSceneCollectionList`, `SetCurrentProgramScene`. Il client ha una **allow-list rigida**: qualunque altra
richiesta (CreateScene, RemoveScene, StartRecord, SetInputVolume, …) viene rifiutata prima di uscire
dal processo. È verificato da un test.

- **UUID delle scene**: i campi `sceneUuid` esistono da obs-websocket 5.3 (OBS 30). Quando OBS li fornisce,
  li usiamo per `SetCurrentProgramScene`; altrimenti si usa il nome, per compatibilità con le versioni precedenti.
  La configurazione resta indicizzata per nome, perché gli UUID cambiano tra scene collection diverse.
  Le rinomine vengono seguite tramite l'evento `SceneNameChanged`.
- **Studio Mode**: `SetCurrentProgramScene` agisce sul Program. La Preview non viene mai toccata
  (`SetCurrentPreviewScene` non è nemmeno nella allow-list).
- **Audio da OBS**: obs-websocket non espone campioni audio, solo livelli (`InputVolumeMeters`, evento ad
  alto volume). Non basta per l'analisi ritmica, quindi l'audio si acquisisce direttamente dal dispositivo.
- **[da verificare su Windows]**: comportamento con OBS reale (versioni 30/31), tempi di risposta,
  eventi durante il cambio di scene collection, transizioni lunghe in Studio Mode.

## 2. Acquisizione audio su Windows

| Opzione | Note | Decisione |
|---|---|---|
| `sounddevice` 0.5.x (PortAudio) | Wheel Windows con PortAudio incluso; MME / DirectSound / WASAPI / WDM-KS; callback in thread nativo. | **Adottato** per ingressi (interfaccia, mixer USB) |
| WASAPI loopback via sounddevice | L'API di sounddevice non espone un flag di loopback utilizzabile. | Non disponibile |
| `PyAudioWPatch` | Fork di PyAudio con dispositivi loopback WASAPI. Solo Windows. | **Opzionale** (`pip install .[loopback]`); in sua assenza la funzione viene disattivata con un messaggio chiaro |
| `soundfile` (libsndfile) | WAV/FLAC/OGG/AIFF per test e simulazione. | Adottato |

Rischi individuati:
- Non tutti i mixer USB espongono il master al PC: alcuni inviano solo canali singoli o richiedono
  routing nel driver. Il wizard mostra un meter live e una selezione dei canali (1+2, 3+4, …).
- Gli indici dei dispositivi cambiano con l'hotplug, quindi la configurazione salva **nome + host API** e il
  watchdog riapre il dispositivo per nome con backoff.
- Nel callback audio fa solo la selezione dei canali, il mixdown mono, il calcolo del picco e la copia in un
  ring buffer limitato. Niente DSP, niente I/O, niente OBS.
- **[da verificare su Windows]**: latenza reale WASAPI shared, comportamento con driver ASIO-only (non
  supportati da PortAudio nella wheel standard: in quel caso va usato il driver WDM/WASAPI del dispositivo),
  PyAudioWPatch con dispositivi reali.

## 3. Beat tracking causale

| Candidato | Note | Decisione |
|---|---|---|
| BTrack | Causale e adatto al live, ma **GPL-3.0**: integrarlo renderebbe GPL l'intero prodotto. | Non integrato |
| BeatNet | Beat e downbeat online con rete neurale; dipende da PyTorch e madmom, dipendenze pesanti. | Non adottato in v0.1 |
| madmom | Molto accurato; alcuni modelli pre-addestrati hanno licenze non commerciali; build su Windows/Python recenti problematica. | Non adottato |
| librosa `beat_track` | Pensato per l'analisi offline dell'intero segnale, non causale. | Non adottato |
| Implementazione propria (ODF + autocorrelazione + comb + prior + fase) | Leggera (~2.5% di un core), senza dipendenze extra, licenza pulita, testabile. | **Adottata** (`music/beat.py`) |

L'implementazione è originale e non contiene codice di terze parti. Limiti noti, misurati in
[TESTING.md](TESTING.md): ambiguità half/double-time su pattern con basso in levare (mitigata da un prior
di tempo configurabile) e downbeat euristico, quindi sperimentale.

## 4. Computer vision (preparazione v0.2)

- Aprire direttamente una webcam già usata da OBS spesso fallisce (accesso esclusivo su molti driver).
  Candidati supportati: `GetSourceScreenshot` di obs-websocket (immagine codificata, a bassa risoluzione
  e pochi fps), oppure la Virtual Camera di OBS. Costo e latenza da misurare.
- L'engine contiene già il termine `VisualFit` e l'interfaccia `visual/interface.py`; in v0.1 vale 0.
- La RTX 3080 non è necessaria per la v0.1: tutto gira su CPU.

## 5. Packaging

- PyInstaller 6, modalità **one-folder** (avvio più affidabile del one-file, nessuna estrazione in `%TEMP%`).
  Due eseguibili condividono il runtime: `DJDirectorAI.exe` (finestra) e `dj-director-cli.exe` (console).
- `PySide6-Essentials` invece del meta-pacchetto `PySide6`, così WebEngine e gli add-on non entrano nella build.
- scipy è stato rimosso perché non necessario: le FFT di numpy bastano.
- La spec è stata **verificata su Linux** (build congelata funzionante, `selftest` superato da entrambi gli
  eseguibili). **[da verificare su Windows]**: la build `.exe` gira nella CI `windows-latest`; fino a un
  esito positivo di quella pipeline, l'eseguibile Windows va considerato non verificato.

## 6. Licenze

Vedi [THIRD_PARTY_LICENSES.md](../THIRD_PARTY_LICENSES.md). In sintesi: nessuna dipendenza GPL è inclusa
nel prodotto distribuito. Qt (PySide6) è LGPL-3.0, linkato dinamicamente nella build one-folder.
libsndfile è LGPL-2.1, anch'esso come libreria dinamica.
