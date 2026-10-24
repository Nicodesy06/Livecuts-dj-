# Changelog

Tutte le modifiche rilevanti di DJ Director AI. Formato ispirato a
[Keep a Changelog](https://keepachangelog.com/it/1.1.0/); versioni [SemVer](https://semver.org/lang/it/).

## [0.1.2] — 2026-10-24

### Aggiunto
- README con screenshot dell'interfaccia, tutorial di installazione e utilizzo pagina per pagina.
- Guida PDF con immagini; script riproducibile per gli screenshot (`scripts/make_screenshots.py`).
- Changelog e modelli per le segnalazioni su GitHub.

### Corretto
- Configurazione guidata: intestazione illeggibile su sfondo bianco, pulsanti Avanti/Indietro/Annulla
  e intestazioni della tabella camere non tradotti.
- Tabella del rilevamento automatico con colonne troncate; angolo bianco nelle tabelle.
- Nome del preset non tradotto nella Simulazione; percorso completo del file audio in Dashboard.

## [0.1.1] — 2026-10-24

### Aggiunto
- Supporto **Behringer UCA202/UCA222/UFO202** ("USB Audio CODEC") e riconoscimento di interfacce audio,
  hardware DJ e microfoni ambientali.
- **Rilevamento automatico della sorgente audio** su tutti gli ingressi e le uscite (loopback WASAPI).
- Avviso **zeri digitali** (privacy microfono di Windows che blocca le app desktop).
- **Timing di frase**: i tagli non urgenti cadono sui confini di 4/8 battute dopo un drop rilevato.
- **Calibrazione della sincronizzazione** con alternanza di due camere sul beat e offset regolabile dal vivo.
- **Interfaccia in italiano** (automatica dalla lingua di Windows), compresi i motivi delle decisioni.
- Guida all'utilizzo in Markdown e PDF, pubblicazione delle release dalla CI.

### Corretto
- 90 BPM con basso in levare letto come 180; 172 BPM che scendeva a 86 nei build-up (inerzia del tempo).
- Loopback WASAPI: durante il silenzio non arrivano dati, il che desincronizzava il beat e riavviava lo
  stream di continuo.
- Apertura WASAPI più robusta (tutti i canali, conversione del sample rate, tentativi su 48/44.1 kHz).

## [0.1.0] — 2026-10-24

### Aggiunto
- Prima versione: analisi musicale causale (BPM, beat, stati musicali), Director Engine deterministico
  con preset Minimal/Natural/Dynamic, controllo di OBS via obs-websocket 5, modalità Shadow, override
  manuale, simulazione con metriche editoriali, GUI desktop, configurazione persistente, build Windows
  portable verificata in CI.
