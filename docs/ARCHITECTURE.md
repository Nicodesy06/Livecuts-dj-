# Architettura (v0.1)

```
 Audio device / file
        │  (callback PortAudio / thread file: solo mixdown + copia)
        ▼
 AudioRingBuffer (4 s max, sovrascrive i dati più vecchi e conta gli overrun)
        │
        ▼  thread "analysis" (ogni 10 ms, max 0.5 s di audio per iterazione)
 FeatureExtractor → BeatTracker → MusicStateEstimator  ⇒  MusicSnapshot + MusicEvent
        │
        ▼  thread "director" (20 Hz) — unico thread che modifica l'engine
 DirectorEngine: A) opportunità  B) destinazione  C) timing  ⇒  DirectorDecisionEvent
        │ SWITCH
        ▼  coda a slot singolo, thread "obs-commands"
 ObsController → ObsClient (obs-websocket 5.x) → SetCurrentProgramScene
        │ eventi (thread "obs-reader"): cambi manuali, rinomina, scene list, studio mode
        └──────────────► comandi in coda al director thread
 GUI (thread principale): legge AppController.snapshot() a 10 Hz, senza mai bloccarsi su I/O
```

## Moduli

| Package | Responsabilità |
|---|---|
| `core/events.py` | Contratti tipizzati e immutabili tra componenti: `AudioStatusEvent`, `MusicSnapshot`, `BeatInfo`, `MusicEvent`, `DirectorDecisionEvent`, `SceneSwitchRequested/Confirmed/Failed`, `ObsStatusEvent`, `ExternalSceneChange` |
| `core/clock.py` | Clock monotono (live) e `SimClock` (replay deterministico) |
| `core/controller.py` | Application Controller: cablaggio, thread del director, stati OFF/ARMED/LIVE/SHADOW/PAUSED/ERROR, comandi utente, log delle decisioni |
| `config/` | Schema con validazione e clamp, preset, salvataggio atomico, recupero da file corrotto, password via DPAPI, percorsi per utente |
| `audio/` | Ring buffer, backend (PortAudio, WASAPI loopback con riempimento del silenzio, file), manager con watchdog e riapertura per nome, riconoscimento dei dispositivi (UCA222 & co.), rilevamento automatico della sorgente |
| `music/` | Feature spettrali, beat tracker causale, stati musicali, pipeline pura e worker live |
| `director/` | Camera registry (ruoli, disponibilità, ancora), Director Engine, calibrazione dell'offset |
| `obs/` | Protocollo, client WebSocket con allow-list, controller (supervisione, eventi, comandi), finto server per test e demo |
| `simulation/` | Generatore sintetico con ground truth, replay offline, metriche editoriali, baseline timer e casuale |
| `visual/` | Interfaccia per l'analisi visiva (v0.2); in v0.1 è un'implementazione nulla |
| `diagnostics/` | Logging con rotazione, filtro dei segreti, log JSONL delle decisioni |
| `ui/` | Tema, widget, dashboard, pagine di configurazione, simulazione, log, wizard, traduzioni (italiano/inglese) |

## Gestione del tempo

- Tutti i tempi di analisi e decisione sono secondi sul **clock monotono**.
- `TimeAnchor` converte l'indice di campione nell'istante di acquisizione. I callback arrivano in ritardo
  variabile ma mai in anticipo, quindi si tiene il minimo di `t_callback - campioni/sr`, che può derivare
  lentamente per seguire il drift del clock della scheda. A questo si sottrae la latenza di input
  dichiarata dal driver.
- Un frame di analisi viene datato al centro della finestra FFT (`indice*hop - n_fft/2`).
- Se il ring buffer perde campioni, la pipeline sposta la mappatura frame→campione, così il beat clock
  non si desincronizza (`gaps` conteggiati).
- Il comando OBS parte con `latency_offset_ms` di anticipo (default 40 ms) rispetto al beat bersaglio. La
  latenza di presentazione video delle camere non è misurabile dal software e va calibrata con una
  registrazione di prova.

## Robustezza

- Nessuna coda illimitata: ring buffer da 4 s, coda comandi del director da 256 elementi, coda OBS a slot
  singolo (un comando più recente sostituisce quello non ancora inviato), cronologia UI limitata.
- Ogni operazione di rete ha un timeout. `ObsController.start()` non blocca mai.
- Disconnessione OBS: niente comandi, stato ARMED, riconnessione con backoff (1→15 s) e nuova validazione
  delle scene prima di tornare LIVE. Con password errata il programma smette di riprovare finché la
  configurazione non cambia.
- Il director non avvia né ferma mai registrazione o streaming. Se il programma si chiude o va in crash,
  OBS continua a registrare.
- Le eccezioni nel thread del director vengono loggate e portano allo stato ERROR, senza tagli
  incontrollati. Le eccezioni non gestite nella GUI vengono loggate e mostrate.

## Confine tra regole, euristiche e AI

| Componente | Natura |
|---|---|
| Feature (RMS, bande, flux, onset) | Misure DSP deterministiche |
| BPM e fase | Algoritmo DSP classico con confidenza esplicita |
| Downbeat | **Euristica sperimentale** (variazione armonica per posizione nella battuta) |
| Confini di frase | **Euristica**: griglia di 4/8 battute prevista dall'ultimo drop rilevato |
| Stati musicali ed eventi | **Regole esplicite** con soglie, isteresi e confidenza; ipotesi, non etichette musicologiche |
| Director | **Regole + scoring deterministici**; casualità solo da RNG con seed |
| Modelli neurali | **Nessuno** in v0.1. Nulla viene presentato come "AI" quando è una regola o un timer |
