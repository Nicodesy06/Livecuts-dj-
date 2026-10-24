# Report finale — milestone v0.1 "Music-Aware Director" (aggiornato v0.1.1)

> **v0.1.1** — correzioni dei problemi elencati nella prima versione di questo report e supporto
> specifico per la Behringer UCA222. Dettagli nella sezione 6.

Ambiente di sviluppo: container Linux x86-64, Python 3.13, **senza Windows, senza OBS reale e senza
hardware audio**. Questa distinzione è riportata in ogni sezione.

## 1. Cosa funziona realmente (verificato da test automatici)

| Requisito 0.1 | Stato | Come è stato verificato |
|---|---|---|
| GUI desktop reale | ✅ | test offscreen di tutte le pagine, wizard, avvio dell'eseguibile congelato (Linux) |
| Connessione a OBS, lettura scene | ✅ con server finto | 12 test di integrazione contro un server obs-websocket 5 finto ma fedele al protocollo |
| Ruoli delle camere, ≥ 2 camere, estendibile | ✅ | test dell'engine con 1, 3, 4 camere e simulazioni con 5 |
| Selezione dell'ingresso audio | ✅ backend file / ⚠️ dispositivi reali | backend file testato in real-time; PortAudio caricato, ma nessun dispositivo nel container |
| WASAPI loopback | ⚠️ implementato, non testato su hardware | PyAudioWPatch incluso nella build Windows e verificato dal `selftest`; gestione del silenzio testata con unit test |
| Attività audio, livello, clipping, dropout | ✅ | test su silenzio, segnale debole, rumore, clipping, gap |
| BPM e beat con confidenza | ✅ (sintetico) | errore < 1% a 90/124/128/140/172 BPM con le impostazioni predefinite, nessun salto di ottava lungo un set intero, fase ~8 ms |
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
| `.exe` costruito e verificato | ✅ in CI (headless) | CI `windows-latest` (run 40097924239): test superati su Windows/Python 3.12, build PyInstaller, `selftest` superato da `DJDirectorAI.exe` e `dj-director-cli.exe`, zip caricato come artifact `DJDirectorAI-win64`. Non ancora provato a mano su un PC con OBS e audio reali |

**Totale test automatici: 122, tutti superati**, durata ~3,5 min.

## 2. Cosa rimane sperimentale

- **Downbeat**: euristica sulla variazione armonica per posizione nella battuta. Su materiale sintetico
  a 124 BPM è corretta circa l'83% delle volte quando si dichiara confidente; a 90 BPM non è affidabile.
  Il director la usa solo sopra la soglia `downbeat_confidence`, altrimenti sincronizza sul beat.
- **Stati musicali** (sparse, re-entry, transition): regole con soglie tarate su materiale sintetico.
  Su DJ set reali vanno validate e probabilmente ritarate (parametro *Musical sensitivity*).
- **Confini di frase** (nuovo): la griglia di 4/8 battute è *prevista* a partire dall'ultimo drop rilevato.
  È un'ipotesi fondata sulla struttura tipica della musica dance, non una certezza; si può disattivare
  in *Regia › Avanzate*.

## 3. Problemi noti e limiti

1. Nessun test con OBS reale o interfacce audio reali; su Windows solo test automatici e selftest headless in CI (vedi la checklist in `docs/TESTING.md`).
2. Prima del primo drop rilevato non esiste una griglia di frase: fino ad allora i tagli non urgenti
   cadono sul beat (o sul downbeat, se affidabile).
3. La latenza video va calibrata a mano con lo strumento di calibrazione (non è misurabile dal software).
4. Le percentuali di tempo sul Master variano molto su clip brevi (pochi tagli); servono sessioni lunghe
   per valutarle. Non è un difetto, ma va tenuto presente leggendo le metriche.
5. Le soglie degli stati musicali sono tarate su materiale sintetico: vanno verificate su set reali.

## 4. Prossimo incremento prioritario

1. **Validazione su hardware reale** con la UCA222: rilevamento sorgente, un DJ set di 60+ minuti in
   Shadow, calibrazione dell'offset, revisione della timeline e ritaratura delle soglie (il replay
   deterministico di una registrazione lo consente senza OBS).
2. Griglia di frase anche prima del primo drop (ancorata ai breakdown con compensazione del ritardo di
   rilevamento).
3. Poi la v0.2: analisi visiva leggera tramite `GetSourceScreenshot` a bassi fps, che alimenta il termine
   `VisualFit` già presente nell'engine.

## 5. Mappa della consegna

| Output richiesto | Dove |
|---|---|
| Audit tecnico | `docs/TECHNICAL_AUDIT.md` |
| Architettura motivata | `docs/ARCHITECTURE.md`, `docs/DIRECTOR.md` |
| Codice sorgente | `src/dj_director/` (~6 000 righe) |
| Test automatizzati | `tests/` (122 test) |
| GUI | `src/dj_director/ui/` |
| Integrazione OBS | `src/dj_director/obs/` |
| Motore audio e analisi | `src/dj_director/audio/`, `src/dj_director/music/` |
| Motore di regia | `src/dj_director/director/` |
| Simulazione e Shadow | `src/dj_director/simulation/`, pagina *Simulation*, pulsante *Start Shadow* |
| Documentazione | `README.md`, `docs/` |
| Script di build Windows | `scripts/build_windows.ps1`, `packaging/dj_director.spec`, `.github/workflows/build-windows.yml` |
| Eseguibile verificato | artifact CI `DJDirectorAI-win64` (selftest superato su Windows) |

## 6. Aggiornamento v0.1.1 — problemi corretti

| Problema (report v0.1) | Correzione | Verifica |
|---|---|---|
| 90 BPM letto come 180 (e 172 che scendeva a 86 nei build-up) | "Inerzia del tempo": un tempo stabilito cambia ottava solo se la nuova lettura è nettamente più forte, con ritmo pieno e cassa presente, e dopo ~40 s di evidenza; correzione dell'alternanza forte/debole tra beat | 90/124/128/140/172 BPM corretti per tutto il set con le impostazioni predefinite; nuovi test |
| Tagli poco agganciati alla struttura | Timing di frase: i tagli non urgenti attendono (entro un limite per preset) il prossimo confine di 8 o 4 battute, calcolato dall'ultimo drop rilevato e riallineato alla griglia di beat corrente | Natural: 3 tagli su 8 esattamente sulla griglia di 4 battute (timer 0, casuale 1); test dell'engine |
| Latenza video non calibrabile | Modalità **Calibrazione**: due camere si alternano sulla griglia dei beat, l'offset si regola dal vivo con un cursore e viene salvato | unit test della temporizzazione + test end-to-end con OBS finto |
| Loopback non verificato | PyAudioWPatch nella build Windows, verificato dal `selftest`. Corretto un difetto: in silenzio il loopback WASAPI non invia dati, il che avrebbe desincronizzato il beat e fatto riavviare lo stream a ripetizione; ora il silenzio viene riempito con zeri | unit test |
| GUI solo in inglese | Interfaccia **in italiano** (automatica dalla lingua di Windows, selezionabile in *Regia*), compresi i motivi delle decisioni | test: ogni stringa ha la traduzione e ogni motivo prodotto dal motore viene tradotto per intero |

### Behringer UCA222

- Windows la vede come **"Microfono (USB Audio CODEC)"** e **"Altoparlanti (USB Audio CODEC)"**. Il
  "microfono" è in realtà l'**ingresso di linea RCA**: è la sorgente giusta. Il programma la riconosce, la
  indica con ★ e la preseleziona.
- **Rilevamento automatico**: con la musica che suona, ascolta per circa 1 s ogni ingresso e ogni uscita in
  loopback e suggerisce quella con il mix. I microfoni ambientali (portatile, webcam) vengono declassati.
- Le **uscite (altoparlanti)** sono utilizzabili tramite loopback WASAPI: utile se il software DJ suona dal
  PC attraverso la UCA222.
- **Zeri digitali**: se Windows blocca il microfono alle app desktop (privacy), l'ingresso restituisce zeri
  esatti; il programma lo riconosce e spiega dove sbloccarlo.
- Apertura WASAPI più robusta (tutti i canali del dispositivo, conversione automatica del sample rate,
  tentativi su 48/44.1 kHz).
