# Test e risultati

## Come eseguire

```bash
QT_QPA_PLATFORM=offscreen pytest            # suite completa (~3 min, include test real-time)
pytest tests/unit                           # solo unit (~1 min)
dj-director simulate --synthetic --preset natural --seed 11
```

## Suite automatica (eseguita in questa milestone su Linux, Python 3.13)

Risultato dell'ultima esecuzione (v0.2.0): **175 test superati, 1 saltato** (`test_real_sessions`: nessuna registrazione reale presente).

| Area | File | Copertura |
|---|---|---|
| Ring buffer | `unit/test_ringbuffer.py` | wraparound, overrun limitato e contato, scritture più grandi della capacità |
| Configurazione | `unit/test_config.py` | **configurazioni 0.1.2 caricate senza modifiche** (media → finestra preferita, max → hard cap), primo avvio, roundtrip, file corrotto (backup e default), clamp, chiavi sconosciute, sentinelle dei default di ruolo, più master, duplicati, export senza segreti, preset |
| Segreti | `unit/test_secrets.py` | set/get/rimozione, nessun testo in chiaro su disco, store corrotto |
| Protocollo OBS | `unit/test_obs_protocol.py` | formula di autenticazione, allow-list (CreateScene, StartRecord, … rifiutati) |
| Audio e analisi | `unit/test_audio_analysis.py` | silenzio, segnale debole, rumore, onset irregolari, BPM 124/128/140/172, half/double-time con prior, stabilità del BPM, cambio di tempo, fase del beat, breakdown senza beat, dropout, clipping e segnale tramite backend file, dispositivo mancante |
| Stati musicali | `unit/test_music_state.py` | breakdown e drop rilevati, **aumento di volume ≠ drop**, rarità degli eventi di transizione, groove stabile, confidenze |
| Director | `unit/test_engine.py` | 52 test. **Nuovi in v0.2**: il drop apre la camera di rivelazione sul beat (dal Master e da un insert), scena scelta dall'utente, suggerimento dell'occhio lento solo se non c'è una scelta esplicita, rivelazione tenuta da parte nel build-up, camera con veto mai scelta, uscita entro 1.5 s da un'inquadratura che diventa inutilizzabile, Master inutilizzabile evitato finché non torna buono. Riguardano anche: <ul><li>master iniziale, durata minima, HOLD con motivo, niente timer fisso, niente A-B-A-B, cooldown, camere disabilitate, camera singola, scena sparita;</li><li>sync al beat e beat incerto (niente falsa precisione), timing di frase, preset, tetti dei tagli, determinismo, override manuale, perdita del segnale, cambio fallito, motivi coerenti;</li><li>**nuovi in v0.1.3**: ingresso e uscita separati; durate preferite Detail/Side; hard cap mai superato, nemmeno con un cambio in attesa; Master senza massimo rigido; presenza continua delle mani che non provoca tagli e non allunga un Detail; novità visiva che fa entrare; refrattario; continuità limitata a `pref_max`; budget di copertura in finestra mobile; tagli editoriali con musica stabile; nessuna durata ripetuta di fila; rivalidazione (target non più adatto, copertura raggiunta); chaining solo con Master oltre budget; tetto configurabile riportato in 12–15 / 20–25.</li></ul> |
| OBS (integrazione) | `integration/test_obs_controller.py` | connessione, password errata (nessun tentativo ripetuto), OBS irraggiungibile senza blocchi, timeout, cambio riuscito via UUID, cambio fallito, scena sconosciuta, cambio esterno, rinomina/rimozione, Studio Mode (Preview intatta), riconnessione dopo riavvio, spegnimento di OBS |
| App end-to-end (real-time) | `integration/test_app_controller.py` | LIVE comanda OBS, **Shadow non invia mai comandi**, Stop ferma subito, override rispettato e poi ripresa, scena non gestita, disconnessione → ARMED → LIVE, rinomina persistita, Force Master |
| Sorgenti audio | `unit/test_audio_sources.py` | riconoscimento UCA222 / interfacce / microfoni ambientali, suggerimento, deduplica host API, attività musica vs ronzio, classifica del rilevamento automatico, zeri digitali (privacy microfono), riempimento del silenzio nel loopback |
| Calibrazione | `unit/test_calibration.py`, `integration/test_app_controller.py` | tagli ogni N beat anticipati dell'offset (anche negativo), nessun taglio senza beat stabile, alternanza su OBS finto, offset salvato |
| Interfaccia italiana | `unit/test_i18n.py` | ogni stringa UI tradotta, segnaposto preservati, ogni motivo del motore tradotto per intero |
| Desktop | `integration/test_gui.py` | primo avvio senza config, config corrotta, refresh di tutte le pagine, wizard, salvataggio override, esecuzione della simulazione |
| Qualità editoriale | `simulation/test_quality_harness.py` | nessuna violazione, più calmo delle baseline, master come ancora, allineamento al beat, determinismo, export |
| Visione (unit) | `unit/test_vision.py` | occhio veloce: veto nera solo se persiste, strobo ignorato, immagine bloccata, sfocatura relativa alla camera, movimento solo nella zona di interesse, nessun veto senza dati, movimento e luci a tempo riconosciuti (e non a un altro tempo); mosaico numerato; validazione del giudizio AI (numeri di camera fuori elenco, interesse limitato, drop su camera non utilizzabile scartato); scadenza del giudizio; errori del modello senza crash; Ollama con un server HTTP di prova (versione, modelli installati, download con avanzamento, risposta JSON, riprova senza "think", modello mancante, Ollama assente); forma della richiesta all'API Claude (immagine, schema JSON, fallback, rifiuto); servizio che unisce occhio veloce e lento; sonda MIDI |
| Visione (integrazione) | `integration/test_vision_obs.py`, `integration/test_gui.py` | istantanee vere via obs-websocket dall'OBS simulato, 60% alla camera del drop, solo `GetSourceScreenshot` su quella connessione, stop con password errata; **app completa: una camera nera non va mai in onda** (controprova: senza il nero la stessa camera va in onda 2 volte su 8); pagina Visione con immagine live, stato "nera", zona del mixer disegnata col mouse e salvata |
| Sessioni lunghe e reali | `simulation/test_real_sessions.py` | set sintetico mixato da 12.8 min per ogni preset (copertura Master nella banda, deviazione ≤ 13 punti, tetti rispettati, tagli sul beat). Replay delle **registrazioni reali** in `tests/sessions/` o `DJD_SESSIONS_DIR`: test saltato se non ce ne sono |

I test OBS usano `obs/fake_server.py`, un server obs-websocket 5 finto ma fedele al protocollo
(Hello/Identify con autenticazione, close code 4009, eventi), **non OBS reale**.

## Risultati misurati

### Analisi (set sintetico con ground truth, `simulation/synth.py`)

| Misura | Risultato |
|---|---|
| BPM 124 / 128 / 140 / 172 | errore < 1% (124 → 123.95) |
| Fase del beat | errore mediano ~8 ms (soglia nei test: < 30 ms) |
| Breakdown (`sparse_start`) | rilevati 2/2, entro ~2 s dall'inizio |
| Drop (`reentry`) | rilevati 2/2, entro 0.1 s; 0 falsi positivi; rampa di volume di +12 dB → nessun drop |
| Breakdown senza beat | confidenza del beat < 0.3, BPM mantenuto (coasting) |
| 90 BPM con basso in levare, 172 BPM con build-up senza cassa | corretti per tutto il set con le impostazioni predefinite (v0.1.1: inerzia del tempo) |
| Downbeat (euristico) | 124 BPM: 19/23 corretti quando dichiarato confidente (~83%), confidente raramente; 90 BPM: inaffidabile → **sperimentale** |
| Costo CPU analisi + director | 2.4% (44.1 kHz) – 2.8% (48 kHz) di un core Xeon 2.8 GHz |

Il materiale sintetico è più regolare della musica reale: questi numeri sono un limite superiore,
non una misura di accuratezza su DJ set veri.

### Director Engine v0.1.3: set mixato da 12.8 minuti (`simulate --mix`, seed 11)

Sei brani tra 122 e 128 BPM, mixati con sovrapposizioni di 16 battute. Camere: Master, Detail, Side,
Behind (4 camere, preset Natural, se non indicato altrimenti).

| Metrica | Natural (4 cam) | Natural (3 cam) | Minimal | Dynamic | Timer | Casuale |
|---|---|---|---|---|---|---|
| Copertura Master / Detail / Side | 71 / 12 / 17% | 71 / 12 / 17% | 79 / 8 / 13% | 70 / 13 / 17% | – | – |
| Deviazione massima dai target | 6.3 punti | 4.1 | 10.3 | 4.3 | 46 | – |
| Tagli/minuto | 3.27 | – | – | – | – | – |
| Durata mediana Master / Detail / Side | 26.0 / 9.4 / 11.6 s | – | – | – | – | – |
| Durata massima Detail / Side | 10.4 / 12.9 s | – | – | – | – | – |
| Violazioni dell'hard cap | 0 | 0 | 0 | 0 | – | 1 |
| Schemi A-B-A-B | 0 | 0 | 0 | 0 | – | 5 |
| Intervalli ripetuti | 1 | – | – | – | 63 | – |
| Allineamento al beat (mediana) | 6.5 ms | – | – | – | – | – |

**Con due camere di classe Detail** (Detail + Overhead, 5 camere), la copertura sale a:
- Natural: 70 / 13 / 16% (deviazione 4.6 punti);
- Dynamic: 70 / 15 / 15% (deviazione 3.7 punti).

**Lettura dei risultati**:
- **Detail con una sola camera**: si ferma intorno al 12%. Con un solo Detail il limite è strutturale.
  Tra un Master e l'altro gli insert alternano Detail e laterali (M-D-M-S), per un ciclo di circa
  26 + 9 + 26 + 12 s: così il Detail resta intorno al 12%. Per arrivare al 15% servirebbero Detail
  oltre la finestra preferita, oppure tagli più fitti o ripetuti sulla stessa camera. Il motore non lo
  fa: il budget è una quota morbida, non un obiettivo da inseguire.
- **Minimal**: resta volutamente più lungo sul Master, per via del guadagno di copertura ridotto e della
  durata del Master ×1.4.
- **Timer a intervalli fissi**: produce 63 intervalli ripetuti.
- **Regia casuale**: produce ping-pong e supera il tetto.

### Harness editoriale (set sintetico 3:53, 3 camere, seed 11, v0.1.1)

*Misure della v0.1.1, mantenute come riferimento storico. Il test `test_quality_harness.py` gira anche
sul motore v0.1.3.*

| Metrica | Minimal | Natural | Dynamic | Timer 12 s | Casuale |
|---|---|---|---|---|---|
| Tagli/minuto | 1.03 | 2.06 | 5.15 | 4.89 | 5.14 |
| Tempo sul Master | 86% | 75% | 61% | 36% | 41% |
| Inquadratura media | 46.6 s | 25.9 s | 11.1 s | 11.7 s | 11.1 s |
| Variazione delle durate (CV) | 0.58 | 0.49 | 0.44 | 0.13 | 0.46 |
| Schemi A-B-A-B | 0 | 0 | 0 | 0 | 10 |
| Violazioni durata minima | 0 | 0 | 0 | 0–7 | 3–7 |
| Violazioni cooldown | 0 | 0 | 0 | 0 | 6 |
| Tagli sincronizzati (beat/downbeat/frase) | 4 | 6 | 19 | – | – |
| Allineamento al beat (mediana) | 3.3 ms | 2.7 ms | 4.5 ms | – | – |
| Tagli esattamente sulla griglia di 4 battute | 2 | 3 | 6 | 0 | 1 |
| Tagli entro 2 s da un cambio di sezione | 0 | 0 | 4 | 1 | 2 |

Osservazione: con il timing di frase (v0.1.1), dopo il primo drop i tagli non urgenti cadono sui
confini di 4/8 battute. Prima del primo drop la griglia non esiste e i tagli cadono sul beat. Un drop
provoca un taglio solo se l'inquadratura corrente ha superato la durata minima e il guadagno editoriale
supera l'isteresi: è una scelta voluta.

Queste metriche descrivono il **comportamento**, non la qualità artistica: serve una valutazione umana
su registrazioni reali.

## Test manuali ancora da eseguire (richiedono Windows + OBS + hardware)

1. [x] Build CI `windows-latest` verde e `selftest` di entrambi gli eseguibili superato (run 40097924239).
2. [ ] Avvio di `DJDirectorAI.exe` su Windows 11 e Windows 10, wizard completo.
3. [ ] OBS 30/31 reale: connessione con e senza password, lettura scene, cambio Program, Studio Mode con
       transizione lunga, cambio di scene collection durante la sessione, chiusura e riapertura di OBS.
4. [ ] **Behringer UCA222**: rilevamento automatico, segnale su ingresso "Microfono (USB Audio CODEC)", eventuale blocco privacy microfono; altre interfacce reali (es. canali 1+2 e 3+4), mixer USB, hotplug (scollega/ricollega),
       dispositivo in uso esclusivo da un'altra app.
5. [ ] WASAPI loopback su "Altoparlanti (USB Audio CODEC)" con PyAudioWPatch, comprese pause di silenzio.
6. [ ] **Registrazioni reali per l'harness**: copiare 2–3 set veri (WAV/FLAC) in `tests/sessions/` e
       lanciare `pytest tests/simulation/test_real_sessions.py`. Poi rivedere copertura, durate e motivi.
7. [ ] DJ set reale di almeno 60 minuti in Shadow, poi LIVE con registrazione OBS: revisione umana della
       regia, CPU e RAM durante la registrazione, nessun drop di frame imputabile al programma.
8. [ ] Calibrazione del `latency_offset_ms` con lo strumento di calibrazione e una registrazione di prova (taglio rispetto al kick).
9. [ ] Password memorizzata con DPAPI: verificare che `secrets.json` non sia leggibile da un altro account.
10. [ ] **Visione con OBS reale** (0.2): `GetSourceScreenshot` su ogni scena camera mentre OBS registra.
        Verificare il carico (GPU/CPU, nessun frame perso a frequenza Normale e Alta); i veti con camera
        coperta, spenta e fuori fuoco; la zona del mixer su un gesto reale; il comportamento delle
        sorgenti con *Disattiva quando non mostrata*.
11. [ ] **Occhio lento locale** su RTX 4060: download di Qwen3-VL 4B da Ollama, latenza per sguardo con
        OBS in codifica, memoria video, qualità dei giudizi su un set reale in Shadow.
12. [ ] **Occhio lento cloud**: chiave API, latenza e costo reali per ora di serata.
13. [ ] **Sonda MIDI** con la Pioneer XDJ-RX3 in standalone e in SOFTWARE CONTROL.
