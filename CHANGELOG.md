# Changelog

Tutte le modifiche rilevanti di DJ Director AI. Formato ispirato a
[Keep a Changelog](https://keepachangelog.com/it/1.1.0/); versioni [SemVer](https://semver.org/lang/it/).

## [0.4.0] — 2026-10-28 · Director 2.0: grammatica multicamera professionale

### Affidabilità (P0)
- **Stato di ogni cambio in OBS**: identificativo univoco, stati REQUESTED → CONFIRMED / FAILED / CANCELLED.
- **Comandi in coda**: un comando sostituito da un taglio più recente, o superato da un cambio manuale,
  viene annullato e mai inviato.
- **Timeout**: OBS che applica una richiesta dopo un timeout viene riconosciuto come conferma tardiva, non
  come operatore.
- **Riconciliazione**: il Program viene riletto ogni 5 s per riconciliare gli eventi persi.
- **Riconnessione**: se OBS torna su una scena diversa, è un **cambio manuale** e viene rispettato.
- **Rollback** dei cambi falliti o senza risposta.
- **Statistiche di esposizione** dal tempo **confermato in onda**.
- **Inquadratura del mixer durante un gesto**: di default ora si ferma al massimo ordinario del Detail
  (14 s; regolabile 12–25 s, in 0.3 era 20 s).

### Regia professionale (P1)
- **Metadati delle inquadrature** per camera, tutti facoltativi e mai indovinati: scala, soggetto,
  angolazione, gruppo visivo, camera fisica, direzione in scena, fattore di crop. Nuovo ruolo **CROWD**.
- **Motore di continuità** (`director/continuity.py`):
  - jump cut tra inquadrature quasi uguali;
  - crop della stessa camera trattati come variazioni di scala;
  - cambio d'asse fronte-retro con eccezioni di raccordo (non una regola dei 180° rigida);
  - inversione della direzione in scena;
  - transizioni ripetute;
  - bonus per cambi netti di scala, angolazione o soggetto.
- **Shot Planner** (`director/shot_planner.py`):
  - separa *perché, cosa, quando, per quanto*;
  - punteggio spiegabile (copertura, musica, visione, preferenza, freschezza, sequenza, continuità,
    contesto, qualità) e deterministico;
  - ogni taglio porta il suo piano nel log e in Dashboard;
  - i jump cut si fanno solo se non esiste un'alternativa pulita.

### Contesto musicale (P2)
- **Durate Natural**: MASTER 30–50 s; DETAIL 6–10 s (max 14); OVERHEAD 7–11 s (max 15); SIDE / BEHIND /
  WIDE 10–17 s (max 23); CROWD 6–12 s. Obiettivo 70% Master / 30% inserti, morbido.
- **Gesto audio**: aumenta la rilevanza del mixer in proporzione alla conferma dell'immagine. Un evento
  spettrale da solo non prova un'azione del DJ.
- **Breakdown**: Master più lungo, budget che non lo accorcia, meno tagli (−28% sul test), preferenza per
  atmosfera e performer.
- **Drop**: preferenza per pubblico / campi larghi, mai un dettaglio, mai in ritardo (beat successivo solo
  entro mezzo beat).
- **Build-up**: la rivelazione viene tenuta da parte solo con beat e stato affidabili.
- **Musica incerta**: contano solo eventi con confidenza ≥ 0,75.

### Qualità e diagnostica (P3)
- **Stati delle sorgenti** HEALTHY / UNKNOWN / FAILED: UNKNOWN non vale mai come "buona".
- **Nuovi controlli**:
  - camera in movimento / riposizionamento (correlazione di fase: pubblico e strobo non la attivano);
  - mixer non visibile (solo la zona di interesse perde dettaglio).
- **Luci da club**: "nera" solo se buia e piatta; nitidezza relativa al contrasto.
- **Pagina Camere**: editor delle inquadrature, con avvisi pre-diretta.
- **Dashboard**: diagnostica della regia.
  - Program reale e tipo di inquadratura;
  - stato della sorgente;
  - perché dell'ultimo taglio;
  - prossimo taglio;
  - tempo reale in onda;
  - ultimo taglio rifiutato o annullato.

### Crop digitali e confronto (P4)
- **Crop digitali**: più scene OBS dalla stessa camera fisica, validate prima della diretta (zoom
  eccessivo, crop senza sorgente, dettagli ritagliati, crop alla stessa scala). Avvisi anche all'avvio
  della regia. Il controllo PTZ non è incluso.
- **`scripts/compare_engines.py`**: esegue il codice reale di una release precedente e quello attuale
  sullo stesso audio, con le stesse camere e lo stesso preset.

### Compatibilità
- Configurazioni, preset, Shadow Mode, simulazione e interventi manuali invariati. Le configurazioni
  precedenti si caricano senza modifiche, con metadati "sconosciuto".
- `continuity_weight = 0` e `contextual_grammar = false` riportano la scelta delle camere al
  comportamento 0.3. Restano invece le nuove durate e i controlli qualità.

## [0.3.0] — 2026-10-26

### Aggiunto — il regista sente i gesti del DJ nell'audio
- **Gesti sul mixer riconosciuti dal solo audio**, senza MIDI e con qualsiasi sorgente (console,
  controller, MPC, giradischi tramite la UCA222):
  - **kill dell'EQ / isolatore** sulle basse o sulle alte;
  - **filtri** passa-alto e passa-basso;
  - **rientro del basso**.
- **Il gesto del DJ è distinto dalla traccia che si svuota da sola**: in un kill sparisce un estremo dello
  spettro e tutto il resto resta identico; in un breakdown cambia anche il resto. Sui set sintetici i
  breakdown della traccia non producono gesti.
- **Regia dei gesti**:
  - un gesto porta sul beat alla **camera del mixer**, tenuta 15–20 s finché il gesto dura, con un tetto
    proprio (`mix_shot_cap`, 15–25 s);
  - il rientro del basso apre la **camera di rivelazione** (laterale, dietro o wide) sul beat;
  - se il DJ molla senza un rientro netto, l'inquadratura del mixer termina poco dopo.
- **Regia › Inquadratura del mixer per i gesti del DJ**: automatica (DETAIL / OVERHEAD), una scena a
  scelta o spenta.
- Dashboard: il gesto in corso ("DJ: bassi tolti sul mixer"). Timeline della simulazione: i nuovi eventi.
- **Mixer virtuale nel generatore sintetico** (`apply_mixer`: kill e filtri) per test e demo.

### Cambiato
- La sonda MIDI resta, ma come extra: i gesti si sentono già dall'audio.

### Compatibilità
- Le configurazioni precedenti si caricano senza modifiche; i gesti sono attivi di default (si spengono
  da Regia).

## [0.2.0] — 2026-10-26

### Aggiunto — il regista guarda le camere
- **Occhio veloce** (sempre locale, senza AI):
  - legge piccole istantanee di ogni scena camera da OBS con `GetSourceScreenshot`, una richiesta di sola
    lettura, su una seconda connessione che non rallenta i tagli;
  - riconosce camere **nere**, **sovraesposte**, **bloccate** e **sfocate o coperte** (rispetto alla
    nitidezza abituale della camera). Una camera così non va in onda; se lo diventa mentre è in onda, il
    regista esce entro 1 s;
  - un flash o uno strobo non bastano: il problema deve durare 1,5 s.
- **Movimento e zona di interesse**: misura il movimento di ogni camera, anche solo dentro un rettangolo
  disegnato sull'immagine (per esempio il mixer). Alimenta la novità visiva della 0.1.3: conta il gesto
  nuovo, non le mani ferme.
- **A tempo**: riconosce movimento e luci che pulsano con il beat. È un piccolo bonus nella scelta della
  camera.
- **Occhio lento** (facoltativo):
  - un modello visivo riceve ogni pochi secondi un mosaico numerato di tutte le camere con il contesto
    musicale;
  - risponde in JSON validato: interesse, inquadrature da evitare, camera migliore per il drop;
  - il giudizio orienta le scelte per circa 15 s e i tagli restano sul beat.
  - **Locale** con Ollama (offline): pagina con i modelli consigliati, pro e contro, download con
    avanzamento e rimozione. Consigliato Qwen3-VL 4B per una RTX 4060.
  - **Cloud** con l'API Claude (SDK ufficiale `anthropic`, chiave salvata cifrata).
- **Grammatica del drop**:
  - scegli l'inquadratura che si apre sul drop (una qualsiasi scena; automatica o spenta);
  - durante il build-up il regista la tiene da parte, sul drop la apre sul beat, anche partendo da un
    insert.
- **Sonda MIDI**: verifica in 30 s se il controller invia messaggi al PC. Una Pioneer XDJ-RX3 in
  standalone, secondo il manuale, non ne invia.
- Nuova pagina **Visione**; in Dashboard compaiono la camera del drop, le camere viste o evitate e la nota
  dell'occhio lento.
- `selftest` verifica anche codec JPEG, analisi visiva e SDK cloud nell'eseguibile.

### Cambiato
- Con il drop a *Spenta*, il comportamento è quello della 0.1.3: il drop riporta al Master.

### Compatibilità
- Le configurazioni 0.1.x si caricano senza modifiche: la sezione `vision` prende i valori predefiniti e
  l'occhio lento parte spento.
- OBS: l'unica richiesta nuova è `GetSourceScreenshot`, di sola lettura. Le altre e la gestione di
  Studio Mode e degli interventi manuali sono invariate.

## [0.1.3] — 2026-10-26

### Cambiato — Director Engine
- **Entry Decision e Exit Decision separate.** L'ingresso su una camera secondaria nasce da
  un'opportunità musicale, visiva o editoriale. Cade preferibilmente su un confine metrico affidabile
  (frase > downbeat > beat), ed è immediato se il beat non è affidabile. L'uscita è governata dalla
  durata preferita, dal tetto assoluto, dalla continuità e dal ritorno al Master.
- **Nuove durate per ruolo**:
  - Detail 6–10 s con hard cap 14 s (configurabile 12–15);
  - Side/Behind 10–18 s con hard cap 22 s (configurabile 20–25);
  - Master in genere 20–50 s, senza un massimo rigido.
- **Editorial Coverage Budget**: target Master 70%, Detail 15%, Side 15% su una finestra mobile di
  5 minuti. È una quota morbida: orienta la permanenza sul Master, la scelta della camera e la lunghezza
  degli insert, senza inseguire i numeri secondo per secondo.
- **Novità visiva invece di presenza**: l'attività di una camera viene confrontata con la sua baseline.
  La presenza continua (mani appoggiate sul mixer) torna a zero in pochi secondi e non può né provocare
  tagli ripetuti né allungare un Detail.
- **Tagli editoriali anche con musica stabile**, agganciati ai confini di frase e alla sequenza
  (niente ripetizione immediata dello stesso inserto).
- **Rivalidazione** di ogni cambio in attesa: il cambio viene annullato se il target sparisce o non è
  più adatto, se arriva un override, se il segnale si perde o se la copertura è già raggiunta. Le uscite
  non superano mai il tetto.
- **Determinismo senza numeri casuali**: le durate variano con una sequenza del rapporto aureo, sfasata
  dal seed. Stessi ingressi e stesso seed producono la stessa regia, e due shot consecutivi non hanno mai
  la stessa durata pianificata.

### Aggiunto
- Ruolo **BEHIND** (camera dietro il DJ).
- Colonne **Pref. min**, **Pref. max** e **Hard cap** nella pagina Camere.
- Nuovi parametri avanzati: copertura, tetti di classe, tagli editoriali, soglia di novità visiva.
- Copertura in finestra mobile nella Dashboard.
- `simulate --mix`: set sintetico di 12.8 minuti con sei brani mixati.
- Metriche di copertura, violazioni del tetto e intervalli ripetuti nel replay.
- Harness per **sessioni reali** (`tests/sessions/` o `DJD_SESSIONS_DIR`).

### Compatibilità
- Le configurazioni 0.1.x si caricano senza modifiche: `mean_duration` diventa la finestra preferita
  (0.8×–1.2×) e `max_duration` diventa l'hard cap.
- Il collegamento a OBS è invariato (stessa allow-list di richieste).

### Corretto
- Il registro delle decisioni non ripete più lo stesso HOLD quando cambiano solo i numeri nel motivo.

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
