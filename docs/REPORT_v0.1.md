# Report finale — milestone v0.1 "Music-Aware Director"

Ambiente di sviluppo: container Linux x86-64, Python 3.13, **senza Windows, senza OBS reale e senza
hardware audio**. Questa distinzione è riportata in ogni sezione.

## 1. Cosa funziona realmente (verificato da test automatici)

| Requisito 0.1 | Stato | Come è stato verificato |
|---|---|---|
| GUI desktop reale | ✅ | test offscreen di tutte le pagine, wizard, avvio dell'eseguibile congelato (Linux) |
| Connessione a OBS, lettura scene | ✅ con server finto | 12 test di integrazione contro un server obs-websocket 5 finto ma fedele al protocollo |
| Ruoli delle camere, ≥ 2 camere, estendibile | ✅ | test dell'engine con 1, 3, 4 camere e simulazioni con 5 |
| Selezione dell'ingresso audio | ✅ backend file / ⚠️ dispositivi reali | backend file testato in real-time; PortAudio caricato, ma nessun dispositivo nel container |
| WASAPI loopback | ⚠️ implementato, non testato | richiede Windows + PyAudioWPatch |
| Attività audio, livello, clipping, dropout | ✅ | test su silenzio, segnale debole, rumore, clipping, gap |
| BPM e beat con confidenza | ✅ (sintetico) | errore < 1% a 124/128/140/172 BPM, fase ~8 ms |
| Variazioni musicali relative | ✅ (sintetico) | breakdown e drop rilevati 2/2; un aumento di volume non viene scambiato per un drop |
| Director Engine (A/B/C, vincoli, isteresi, timing) | ✅ | 27 test unitari + harness editoriale |
| Preset Minimal / Natural / Dynamic | ✅ | test di ritmo crescente, stesso engine |
| Cambio scene OBS | ✅ con server finto | test end-to-end real-time: audio → analisi → director → `SetCurrentProgramScene` |
| Shadow mode (nessun comando) | ✅ | test end-to-end: 0 richieste di cambio scena |
| Stop immediato | ✅ | nessun cambio dopo Stop |
| Override manuale rispettato | ✅ | test engine (4 policy) + end-to-end |
| Niente tagli compulsivi, Master come riferimento | ✅ | harness: 0 A-B-A-B, 0 violazioni, 73% sul Master (Natural) |
| Condizioni non ideali (OBS giù, scene sparite, audio perso) | ✅ | test di riconnessione, rimozione e rinomina, perdita del segnale |
| Log delle decisioni e motivazioni | ✅ | JSONL per sessione; motivi generati dai fattori effettivi |
| Configurazione persistente, corruzione gestita | ✅ | test di roundtrip e di recupero con backup |
| Procedura di packaging riproducibile | ✅ spec + script + CI | spec verificata con una build congelata **su Linux** (selftest OK su entrambi gli eseguibili) |
| `.exe` costruito e verificato | ❌ **non ancora** | serve Windows: la CI `windows-latest` costruisce, testa ed esegue il `selftest` dell'`.exe`, ma qui non ho potuto eseguirla né vederne l'esito |

**Totale test automatici: 95, tutti superati** (unit 64, integrazione 25, harness 6), durata ~3 min.

## 2. Cosa rimane sperimentale

- **Downbeat**: euristica sulla variazione armonica per posizione nella battuta. Su materiale sintetico
  a 124 BPM è corretta circa l'83% delle volte quando si dichiara confidente; a 90 BPM non è affidabile.
  Il director la usa solo sopra la soglia `downbeat_confidence`, altrimenti sincronizza sul beat.
- **Stati musicali** (sparse, re-entry, transition): regole con soglie tarate su materiale sintetico.
  Su DJ set reali vanno validate e probabilmente ritarate (parametro *Musical sensitivity*).
- **Half/double-time**: a 90 BPM con basso in levare viene stimato 180. Si corregge impostando il
  *Typical tempo* del genere (es. 95 per l'hip-hop, 174 per la drum & bass).

## 3. Problemi noti e limiti

1. Nessun test con OBS reale, interfacce audio reali o Windows (vedi la checklist in `docs/TESTING.md`).
2. La regia è agganciata al beat ma **poco ai confini strutturali**: sul set sintetico solo 1 taglio su 9
   (Natural) cade vicino a un cambio di sezione. Gli eventi musicali influenzano l'opportunità, ma
   gran parte dei tagli nasce dalla durata pianificata.
3. La latenza di presentazione video non è misurabile dal software: `latency_offset_ms` (default 40 ms)
   va calibrato con una registrazione di prova.
4. Le percentuali di tempo sul Master variano molto su clip brevi (pochi tagli); servono sessioni lunghe
   per valutarle.
5. Senza PyAudioWPatch il loopback è disattivato (messaggio esplicito in UI).
6. La GUI è in inglese; la documentazione è in italiano.

## 4. Prossimo incremento prioritario (v0.1.1, prima della v0.2)

1. **Validazione su hardware reale**: build CI Windows verde, poi un DJ set di 60+ minuti in Shadow,
   revisione umana della timeline e ritaratura delle soglie degli stati musicali con registrazioni vere
   (il replay deterministico lo consente senza OBS).
2. **Timing strutturale**: quando la pressione di durata è moderata, attendere il prossimo confine
   musicale probabile (transition, re-entry, frase confermata da downbeat confidente) entro una finestra
   massima, invece di tagliare alla sola scadenza della durata pianificata.
3. **Calibrazione guidata della latenza** (click di test + registrazione OBS) e indicatore di latenza
   end-to-end nella dashboard.
4. Poi la v0.2: analisi visiva leggera tramite `GetSourceScreenshot` a bassi fps, che alimenta il termine
   `VisualFit` già presente nell'engine.

## 5. Mappa della consegna

| Output richiesto | Dove |
|---|---|
| Audit tecnico | `docs/TECHNICAL_AUDIT.md` |
| Architettura motivata | `docs/ARCHITECTURE.md`, `docs/DIRECTOR.md` |
| Codice sorgente | `src/dj_director/` (~6 000 righe) |
| Test automatizzati | `tests/` (95 test) |
| GUI | `src/dj_director/ui/` |
| Integrazione OBS | `src/dj_director/obs/` |
| Motore audio e analisi | `src/dj_director/audio/`, `src/dj_director/music/` |
| Motore di regia | `src/dj_director/director/` |
| Simulazione e Shadow | `src/dj_director/simulation/`, pagina *Simulation*, pulsante *Start Shadow* |
| Documentazione | `README.md`, `docs/` |
| Script di build Windows | `scripts/build_windows.ps1`, `packaging/dj_director.spec`, `.github/workflows/build-windows.yml` |
| Eseguibile verificato | **in attesa della CI Windows** |
