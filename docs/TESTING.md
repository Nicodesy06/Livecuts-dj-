# Test e risultati

## Come eseguire

```bash
QT_QPA_PLATFORM=offscreen pytest            # suite completa (~3 min, include test real-time)
pytest tests/unit                           # solo unit (~1 min)
dj-director simulate --synthetic --preset natural --seed 11
```

## Suite automatica (eseguita in questa milestone su Linux, Python 3.13)

Risultato dell'ultima esecuzione: **tutti i 122 test superati**.

| Area | File | Copertura |
|---|---|---|
| Ring buffer | `unit/test_ringbuffer.py` | wraparound, overrun limitato e contato, scritture più grandi della capacità |
| Configurazione | `unit/test_config.py` | primo avvio, roundtrip, file corrotto (backup e default), clamp, chiavi sconosciute, sentinelle dei default di ruolo, più master, duplicati, export senza segreti, preset |
| Segreti | `unit/test_secrets.py` | set/get/rimozione, nessun testo in chiaro su disco, store corrotto |
| Protocollo OBS | `unit/test_obs_protocol.py` | formula di autenticazione, allow-list (CreateScene, StartRecord, … rifiutati) |
| Audio e analisi | `unit/test_audio_analysis.py` | silenzio, segnale debole, rumore, onset irregolari, BPM 124/128/140/172, half/double-time con prior, stabilità del BPM, cambio di tempo, fase del beat, breakdown senza beat, dropout, clipping e segnale tramite backend file, dispositivo mancante |
| Stati musicali | `unit/test_music_state.py` | breakdown e drop rilevati, **aumento di volume ≠ drop**, rarità degli eventi di transizione, groove stabile, confidenze |
| Director | `unit/test_engine.py` | 31 test (con timing di frase): master iniziale, durata minima, HOLD con motivo, niente timer fisso, master dominante ma non esclusivo, niente A-B-A-B, cooldown, camere disabilitate, camera singola, scena sparita, cambio programmato annullato, sync al beat, beat incerto (niente falsa precisione), re-entry, evento a bassa confidenza ignorato, preset, tetto tagli/minuto, max insert consecutivi, determinismo, quattro casi di override manuale, perdita del segnale (due policy), cambio fallito, motivi coerenti |
| OBS (integrazione) | `integration/test_obs_controller.py` | connessione, password errata (nessun tentativo ripetuto), OBS irraggiungibile senza blocchi, timeout, cambio riuscito via UUID, cambio fallito, scena sconosciuta, cambio esterno, rinomina/rimozione, Studio Mode (Preview intatta), riconnessione dopo riavvio, spegnimento di OBS |
| App end-to-end (real-time) | `integration/test_app_controller.py` | LIVE comanda OBS, **Shadow non invia mai comandi**, Stop ferma subito, override rispettato e poi ripresa, scena non gestita, disconnessione → ARMED → LIVE, rinomina persistita, Force Master |
| Sorgenti audio | `unit/test_audio_sources.py` | riconoscimento UCA222 / interfacce / microfoni ambientali, suggerimento, deduplica host API, attività musica vs ronzio, classifica del rilevamento automatico, zeri digitali (privacy microfono), riempimento del silenzio nel loopback |
| Calibrazione | `unit/test_calibration.py`, `integration/test_app_controller.py` | tagli ogni N beat anticipati dell'offset (anche negativo), nessun taglio senza beat stabile, alternanza su OBS finto, offset salvato |
| Interfaccia italiana | `unit/test_i18n.py` | ogni stringa UI tradotta, segnaposto preservati, ogni motivo del motore tradotto per intero |
| Desktop | `integration/test_gui.py` | primo avvio senza config, config corrotta, refresh di tutte le pagine, wizard, salvataggio override, esecuzione della simulazione |
| Qualità editoriale | `simulation/test_quality_harness.py` | nessuna violazione, più calmo delle baseline, master come ancora, allineamento al beat, determinismo, export |

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

### Harness editoriale (set sintetico 3:53, 3 camere, seed 11, v0.1.1)

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
6. [ ] DJ set reale di almeno 60 minuti in Shadow, poi LIVE con registrazione OBS: revisione umana della
       regia, CPU e RAM durante la registrazione, nessun drop di frame imputabile al programma.
7. [ ] Calibrazione del `latency_offset_ms` con lo strumento di calibrazione e una registrazione di prova (taglio rispetto al kick).
8. [ ] Password memorizzata con DPAPI: verificare che `secrets.json` non sia leggibile da un altro account.
