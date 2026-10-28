# Come decide il regista

Codice: `src/dj_director/director/engine.py`. A ogni tick (20 Hz) l'engine riceve il tempo, l'ultimo
`MusicSnapshot` e gli eventi musicali, e restituisce **una** `DirectorDecisionEvent`:
`HOLD`, `SCHEDULE`, `SWITCH`, `CANCEL` o `OVERRIDE`. Ogni decisione porta un motivo costruito dai fattori
realmente usati. L'engine non parla mai con OBS: chi lo chiama esegue gli `SWITCH` e riporta l'esito.

Dalla v0.1.3 il modello editoriale separa **ingresso** e **uscita**. Il Master è la "casa". Un taglio
può essere solo di due tipi:

- **entra** su una camera secondaria, partendo dal Master;
- **esce** dalla secondaria, quasi sempre tornando al Master.

Le due decisioni seguono regole diverse.

Dalla v0.4 (**Director 2.0**) ogni taglio risponde esplicitamente a quattro domande, gestite dallo Shot
Planner (`director/shot_planner.py`):

| Domanda | Chi la decide | Cosa si vede nel log / in Dashboard |
|---|---|---|
| **WHY** – perché cambiare | logica di ingresso / uscita dell'engine (evento musicale, gesto del DJ, novità visiva, permanenza editoriale, tetto, veto) | `plan.why` |
| **WHAT** – quale inquadratura | `ShotPlanner.what`: punteggio con ogni termine visibile | `plan.what` (camera, inquadratura, i tre termini più forti, note di continuità) |
| **WHEN** – quando | timing musicale (frase > downbeat > beat, latenza compensata) | `plan.when` |
| **HOW LONG** – per quanto | `how_long_master` / `how_long_insert` dentro la finestra preferita | `plan.how_long` |

## Priorità per tick

1. **Cambio programmato in attesa**: viene rivalidato (vedi *Rivalidazione*). Poi viene eseguito quando
   è il momento, oppure resta in attesa, oppure viene annullato.
2. **Override manuale attivo**: `HOLD` ("manual override respected").
3. **Niente in onda, o camera in onda sparita**: taglio immediato sull'ancora (Master, poi fallback, poi
   la camera più larga disponibile).
4. **Nessun segnale audio**: ritorno al Master oppure `HOLD`, secondo la policy. Nessun altro taglio.
5. **Altrimenti**:
   - con il Master in onda → **Entry Decision**;
   - con una secondaria in onda → **Exit Decision**.

## Ruoli, classi di copertura e durate

Ogni ruolo appartiene a una **classe di copertura**: `master`, `detail` (DETAIL, OVERHEAD) oppure
`side` (SIDE, BEHIND, WIDE, CROWD, CUSTOM).

Valori del preset Natural (v0.4):

| Ruolo | Minimo | Finestra preferita | Massimo ordinario (hard cap) | Cooldown |
|---|---|---|---|---|
| MASTER | 12 s | 30–50 s | nessun tetto editoriale | – |
| DETAIL | 5 s | 6–10 s | 14 s (classe detail, 12–15) | 20 s |
| OVERHEAD | 5 s | 7–11 s | 15 s | 20 s |
| SIDE / BEHIND / WIDE | 7 s | 10–17 s | 23 s (classe side, 20–25) | 15 s |
| CROWD | 5 s | 6–12 s | 23 s, configurabile | 15 s |

Obiettivo di esposizione: circa **70% Master e 30% inserti** su una finestra mobile di 5 minuti (quota
morbida, vedi sotto). I massimi degli inserti sono limiti **effettivi**: né il movimento continuo delle
mani né un punteggio visivo alto li allungano. Fanno eccezione solo l'override manuale (decide l'operatore)
e le emergenze (camera sparita, veto visivo), che accorciano.

- Il tetto di classe si regola con `detail_hard_cap` e `side_hard_cap`.
- Ogni camera può avere i propri `preferred_min`, `preferred_max` e `hard_cap`.
- Il tetto viene sempre riportato dentro l'intervallo della classe. `pref_max` resta almeno 1 s sotto il
  tetto.
- Il Master **non ha un massimo rigido**: oltre i 50 s cresce solo la spinta editoriale a uscire.

**Compatibilità con le configurazioni 0.1.x**:
- `mean_duration` viene letto come finestra preferita 0.8×–1.2× la media;
- `max_duration` viene letto come hard cap;
- i file vecchi si caricano senza modifiche.

## Editorial Coverage Budget (quota morbida)

Il budget è la quota di tempo in onda di ogni classe in una **finestra mobile** (`coverage_window`,
300 s). Viene confrontata con i target `coverage_master` / `coverage_detail` / `coverage_side`
(70 / 15 / 15).

- I target vengono rinormalizzati sulle classi presenti. Senza camere side, per esempio, diventano
  Master 82% e Detail 18%.
- La finestra parte con un *prior* di 45 secondi fittizi già "ai target". Così i primi minuti non
  producono correzioni brusche.
- Il budget **orienta, non insegue**. In base al deficit cambiano:
  - la permanenza pianificata sul Master: fattore `1 − gain·eccesso·8`, limitato tra 0.5 e 1.5;
  - la scelta della camera di ingresso;
  - la lunghezza pianificata degli insert, spostata verso un estremo della finestra preferita.
- Nessuna regola taglia "perché al secondo X la quota è sotto". `coverage_gain` regola quanto il
  budget pesa (Minimal 0.6, Natural 1.0, Dynamic 1.2).

## Entry Decision (sul Master)

Un ingresso nasce da un'**opportunità**, mai dal solo timer.

| Opportunità | Origine | Condizione |
|---|---|---|
| **Musicale** | eventi probabilistici: `reentry`, `transition`, `sparse_start` | confidenza ≥ `musical_cut_confidence`, pesata dal preset; prima di `pref_min` del Master serve un evento più forte (×1.25) |
| **Visiva** | **novità** dell'attività su una camera rispetto alla sua baseline (vedi sotto) | novità ≥ `visual_novelty_threshold`; ogni camera ha un periodo refrattario di 15 s |
| **Editoriale** | il Master ha avuto la sua parte e la permanenza pianificata (20–50 s, corretta dal budget) è stata raggiunta | `editorial_cuts` attivo; vale anche con musica stabile |

**La camera di ingresso** viene scelta per punteggio. Il punteggio somma:

- deficit di copertura della classe (×8: è il fattore principale);
- fit musicale ruolo × stato;
- novità visiva;
- peso e preferenza della camera;
- freschezza (tempo dall'ultimo uso);
- contesto di sequenza: l'ultimo insert usato è escluso se esistono alternative, così niente A-M-A-M.

Sono escluse le camere in cooldown, disabilitate, mancanti in OBS, non ammesse nello stato corrente o con
un cambio appena fallito.

**Timing dell'ingresso**:

- **beat affidabile** e ingresso **editoriale** → confine di frase (8 battute, poi 4) entro
  `max_phrase_wait`, altrimenti downbeat, altrimenti beat;
- **beat affidabile** e ingresso **musicale o visivo** → il momento conta: downbeat o beat successivo,
  senza aspettare la frase;
- **beat inaffidabile** → taglio immediato: niente falsa precisione ritmica.

**Tagli editoriali con musica stabile**: restano agganciati alle opportunità ritmiche (confini di frase)
e alla sequenza contestuale. Le durate pianificate si muovono su una sequenza a bassa discrepanza, quindi
non si ripetono e non sono casuali.

## Visual Novelty: novità, non presenza

`VisualNovelty` confronta l'attività visiva di una camera con la sua **baseline**.

- **Baseline asimmetrica**: sale in fretta (assuefazione, τ 4 s) e scende piano (τ 30 s).
- **Formula**: `novelty = (attività − baseline − margine) / scala`, limitata tra 0 e 1.
- **Presenza continua** (le mani appoggiate sul mixer): la novità torna a zero in pochi secondi. Quindi
  **non** è un trigger persistente e **non** può allungare un Detail.
- **Picco**: genera un evento visivo con TTL di 3 s, poi un periodo refrattario di 15 s per quella camera.
- **Da dove arriva l'attività** (v0.2): dall'occhio veloce, cioè il movimento misurato nelle istantanee
  della camera, solo dentro la **zona di interesse** se è impostata (per esempio il mixer). Senza
  immagini l'attività è `None`, la novità resta 0 e il regista lavora solo su musica e budget.

## Exit Decision (su una secondaria)

L'uscita dipende soprattutto dalla **durata**.

1. **Lunghezza pianificata**: viene scelta all'ingresso dentro la finestra preferita (Detail 6–10 s,
   Side 10–17 s).
   - Valore di base: la sequenza deterministica della classe.
   - **Budget**: una classe sopra target accorcia la lunghezza verso `pref_min`, una sotto target la
     allunga verso `pref_max`.
2. **Continuità**: se la novità visiva sta ancora calando, o se dura il momento musicale che ha
   giustificato l'ingresso (per esempio la transizione per un Detail), lo shot può arrivare a
   `pref_max`. **Mai oltre.**
3. **Hard cap**: limite di sicurezza assoluto. L'uscita viene programmata in modo da cadere entro il
   tetto: un cambio in attesa non lo supera mai, e senza un beat utile il taglio è immediato.
4. **Drop (re-entry)**: apre sulla camera di rivelazione (vedi *Grammatica del drop*), appena superata la
   durata minima (vincolo rigido anche per i drop). Senza camera di rivelazione riporta presto al Master. Se l'inquadratura in onda è già
   la rivelazione, resta.
5. **Veto visivo**: se l'immagine in onda diventa inutilizzabile, l'uscita arriva entro 1 s (sul beat se
   possibile). Se è il Master a essere inutilizzabile, il regista resta sugli insert finché non torna
   buono.
6. **Destinazione**: il Master. Fa eccezione il *chaining* (secondaria → secondaria), possibile solo se:
   - il Master è oltre il target di almeno 5 punti;
   - un'altra classe è in deficit;
   - il fit musicale è sufficiente;
   - non si supera `max_consecutive_inserts`.

   `hysteresis` misura la resistenza al chaining.

Anche l'uscita cade sul downbeat o sul beat, senza attendere la frase. Ha una scadenza (`deadline`)
che non supera mai il tetto.

## Gesti del DJ sentiti nell'audio (v0.3)

Codice: `src/dj_director/music/mixing.py`. Funziona con **qualsiasi** sorgente (console, controller, MPC,
sampler, vinili attraverso un mixer), perché ascolta solo l'uscita master tramite l'interfaccia audio:
il MIDI non serve.

**Chi ha tolto i bassi?** Livello per banda (20-60-120-250-500-1k-2k-4k-8k-14k Hz) dell'ultimo secondo,
confrontato con un riferimento preso tra 1,5 e 6 s prima. Il riferimento viene congelato appena qualcosa
inizia a scendere.

| Caso | Cosa si sente | Evento |
|---|---|---|
| **Kill dell'EQ / isolatore** | le basse (o le alte) crollano di oltre 10 dB in meno di 1,5 s, **tutto il resto resta identico** (entro 4 dB): stesse clap, hat, pad | `eq_cut` (detail `low` / `high`) |
| **Filtro** (passa-alto / passa-basso) | il taglio sale (o scende) banda dopo banda in qualche secondo, il resto è intatto | `filter` |
| **La traccia si svuota da sola** | le basse se ne vanno ma cambia anche il resto: clap che spariscono, pad che entrano, livello e densità che si muovono | nessun gesto → stati *sparse* / *re-entry* come prima |
| **Il basso rientra** | la parte tagliata torna entro 4 dB del riferimento. Ritorno secco (bass swap, drop) = confidenza alta; ritorno lento (filtro rilasciato piano) = bassa | `eq_return` |

- La confidenza dipende soprattutto dalla **forma**: un estremo sparito, il resto intatto.
- **Limite onesto**: se è la produzione a togliere solo cassa e basso lasciando *tutto* il resto identico,
  all'ascolto è indistinguibile da un kill dell'EQ, e viene trattata come un gesto.
- Se durante un gesto cambia tutto il mix (nuova traccia, silenzio), il gesto viene abbandonato senza
  eventi.
- Durante un passaggio, il DJ che toglie il basso a una traccia mentre l'altra ha ancora il suo non abbassa
  le basse complessive: in quel caso non c'è un gesto da sentire, e resta la rilevazione della transizione.

**Regia dei gesti**:

1. **Gesto** (`eq_cut`, `filter`): **aumenta la rilevanza** della **camera del mixer** (la scena scelta
   in *Regia › Inquadratura del mixer per i gesti del DJ*, altrimenti la DETAIL / OVERHEAD con il peso
   più alto). Dalla v0.4 un evento spettrale **da solo non prova** un'azione del DJ: il suo peso viene
   moltiplicato per la conferma dell'immagine.
   - mani visibilmente in movimento nella zona del mixer: ×1;
   - nessuna informazione visiva: ×0,75;
   - immagine del mixer ferma: ×0,4.

   Con il solo audio quindi un Master giovane (sotto i 30 s preferiti) non viene interrotto, mentre a fine
   permanenza il gesto fa scegliere il mixer. Da un altro insert il gesto porta al mixer dopo la durata
   minima, se non è smentito dall'immagine.
2. **Inquadratura del mixer**: finestra preferita **~0,72×cap – (cap−1) s** e un tetto proprio,
   `mix_shot_cap`, che di default è **14 s, uguale al massimo ordinario del Detail** (regolabile
   12–25 s; in 0.3 era 20 s).
   - Resta finché il gesto dura ("continuità: il DJ lavora sul mixer"), mai oltre il tetto.
   - Se il DJ molla senza un ritorno netto, esce circa 2 s dopo (non prima di 6 s).
3. **Il basso rientra** (`eq_return`, come un drop): apre la **camera di rivelazione** (laterale, dietro o
   wide) sul beat.

Le regole fisse restano: niente A-B-A-B (se la camera del mixer è appena stata usata attorno al Master, il
gesto sceglie un'altra camera), durata minima, veto visivo, override manuale.

## Continuità delle inquadrature (v0.4)

Codice: `director/continuity.py`. Usa i **metadati delle inquadrature**, tutti facoltativi e impostati a
mano in *Camere › Inquadrature*:
- scala: `shot_size` (WIDE, MEDIUM, CLOSE, EXTREME_CLOSE);
- soggetto: `subject` (DJ, HANDS, EQUIPMENT, CROWD, ENVIRONMENT);
- angolazione: `view_angle` (posizione attorno alla consolle: FRONT, FRONT_LEFT … BACK, TOP);
- `visual_group` (inquadrature quasi identiche);
- `source_group` (la camera fisica);
- `screen_direction`;
- `crop_zoom`.

Un metadato **sconosciuto non pesa**: niente viene indovinato, e il regista funziona anche con metadati
parziali o assenti.

| Regola | Effetto sul punteggio |
|---|---|
| Stesso `visual_group` (quasi identiche) | −0,8 (jump cut) |
| Crop della stessa camera fisica: stessa scala / un passo / due o più passi | −0,9 / −0,5 / +0,1. Un crop è una **variazione di scala**, non un nuovo punto di vista |
| Altra camera ma angolo entro 30°, stessa scala e soggetto | −0,5 (rischio di jump cut) |
| Fronte ↔ retro (≥ 135°) | −0,35, **salvo un raccordo**: inquadratura dall'alto, dettaglio stretto di mani / attrezzatura, campo largo di sala / pubblico, oppure eccezione editoriale (rivelazione sul drop). Non è una regola dei 180° rigida |
| Direzione in scena invertita sullo stesso soggetto | −0,2 |
| Scala diversa di due passi / angolo 60–135° / soggetto diverso | +0,15 / +0,1 / +0,1 |
| Stessa transizione già usata 2 volte negli ultimi tagli | −0,3 (oltre al divieto rigido di A-B-A-B) |

Un taglio con penalità ≥ 0,5 conta come jump cut. Lo Shot Planner lo scarta **se esiste un'alternativa
pulita**; se non esiste, lo accetta (il regista non resta mai bloccato). `continuity_weight = 0` disattiva
la regola (comportamento 0.3).

## Grammatica per contesto musicale (v0.4)

| Contesto | Comportamento |
|---|---|
| **Groove stabile** | Master dominante; inserti regolari ma non periodici (sequenza deterministica), anche senza drop; tagli editoriali sui confini di frase |
| **Mixing attivo** | il gesto sul mixer aumenta la rilevanza del Detail in proporzione alla conferma visiva; l'attività continua delle mani non allunga oltre il massimo |
| **Breakdown** (*Ritmo ridotto*) | il Master respira di più (×1,3, il budget non lo accorcia), opportunità editoriale ×0,7, preferenza per inquadrature d'atmosfera o sul performer (DJ / sala / pubblico, WIDE / MEDIUM) che possono arrivare a fine finestra. Nessun taglio obbligatorio all'inizio del breakdown. Sul set di test: −28% di tagli |
| **Build-up** | la rivelazione viene tenuta da parte **solo se** beat e stato musicale sono affidabili |
| **Drop / ripartenza** | preferenza per CROWD / WIDE / sala (metadati), poi BEHIND / SIDE; mai un Detail. Il taglio prende il beat successivo solo se arriva entro mezzo beat, altrimenti è immediato: mai in ritardo |
| **Bassa affidabilità / niente segnale** | con stato *Incerto* contano solo eventi con confidenza ≥ 0,75; senza segnale valgono le politiche configurate; nessun taglio speculativo |

`contextual_grammar = false` disattiva questa tabella.

## Controllo qualità delle sorgenti (v0.4)

Ogni camera è in uno di tre stati:
- **HEALTHY**: immagini recenti, analisi a regime, nessun problema;
- **FAILED**: un problema che persiste → veto;
- **UNKNOWN**: nessuna immagine, dato vecchio o analisi in apprendimento.

UNKNOWN **non** vale come "buona": quando l'analisi gira (altre camere sono HEALTHY), una camera UNKNOWN
prende −0,05 nella scelta.

Problemi aggiunti in 0.4:
- **camera in movimento / in riposizionamento**: tutta l'immagine si sposta in modo coerente, misurato
  con la correlazione di fase. Un pubblico che balla o uno strobo non la attivano;
- **mixer non visibile**: con una zona di interesse impostata, solo quella zona perde dettaglio.

Le luci da club sono considerate:
- "nera" significa buia **e** piatta (immagine vuota), non semplicemente scura;
- la nitidezza è misurata in rapporto al contrasto.

## Sincronizzazione con OBS (v0.4)

Codice: `core/switching.py`, `obs/controller.py`.

- Ogni richiesta di cambio ha un identificativo univoco e uno stato: `REQUESTED` → `CONFIRMED`, `FAILED`
  oppure `CANCELLED`.
- Un comando in coda **sostituito** da un taglio più nuovo, o scartato perché l'operatore ha cambiato
  scena a mano, è `CANCELLED` e non viene mai inviato.
- Dopo un **timeout** la richiesta resta "attesa" per 3 s. Se OBS la applica in ritardo, viene riconosciuta
  come nostra (conferma tardiva), non come intervento dell'operatore, e il regista si allinea a ciò che è
  in onda.
- Le richieste senza risposta scadono. Un cambio rifiutato riporta l'engine all'inquadratura precedente
  (rollback).
- Il Program viene **riletto ogni 5 s**: un evento perso viene riconciliato come un cambio esterno.
- Se OBS si riconnette mostrando una scena diversa da quella attesa, qualcuno l'ha scelta mentre eravamo
  scollegati: vale come **cambio manuale** e segue la politica di override, senza essere sovrascritta.
- Le statistiche di esposizione usano il **tempo confermato in onda** (eventi Program di OBS; in Shadow, le
  decisioni). L'inizio di un'inquadratura nella storia dell'engine viene spostato all'istante confermato.

## Inquadrature digitali (crop) (v0.4)

Più scene OBS possono venire dalla stessa camera fisica (per esempio `CAM1_WIDE` e `CAM1_MEDIUM`). Si
indicano con lo stesso `source_group` e scale diverse: per la continuità sono **zoom**, non nuovi punti di
vista. DJ Director **non ritaglia nulla**: i crop sono scene preparate dall'utente.

Prima della diretta `config/framing.py` segnala:
- crop senza camera fisica;
- zoom digitale oltre ×2 (perdita di risoluzione);
- dettagli ritagliati (controllare che mani e mixer restino in quadro);
- crop della stessa camera alla stessa scala.

Gli avvisi compaiono nella pagina Camere e all'avvio della regia (mai bloccanti). Il controllo PTZ fisico
non è incluso.

## Aspetto dei tagli: zoom e transizione personalizzata (v0.6)

Il motore decide **cosa** e **quando**. `director/cut_style.py` decide soltanto **come appare** il taglio,
quando il taglio è programmato (`SCHEDULE`), in modo deterministico:

| Stile | Quando |
|---|---|
| `special` (transizione dell'utente) | conteggio dei tagli dall'ultima ≥ N (`special_every`, 25) e cambio puntuale; oppure, con `special_on_moments`, dal 60% di N su un momento (rivelazione del drop, ripartenza, timing di frase); oppure a 1,5 × N anche se in ritardo. Mai su gesti al mixer o uscite d'emergenza |
| `zoom` | gli altri tagli, se attivo e se il taglio è noto con almeno 0,12 s di anticipo |
| `cut` | uscite d'emergenza (inquadratura inutilizzabile, hard cap), tagli immediati, opzioni spente |

**Transizione personalizzata**:
- al passaggio in LIVE `ObsController.probe_transition` legge tipo, durata e **punto di cambio** (stinger:
  `transition_point`, in ms o in fotogrammi; altre transizioni: metà durata; Cut: 0). Per leggerli
  seleziona la transizione per un istante e poi rimette quella di prima, tenendo il lock delle
  transizioni: nessun taglio può avvenire nel frattempo;
- il taglio programmato viene **anticipato** del punto di cambio (`engine.advance_pending`), così
  l'immagine cambia sul beat;
- sul thread dei comandi: `GetCurrentSceneTransition`, `SetCurrentSceneTransition(speciale)`,
  `SetCurrentProgramScene`, poi la transizione precedente torna al suo posto alla fine (durata + 0,3 s)
  e in ogni caso **prima del taglio successivo**.

**Zoom** (`obs/motion.py`, connessione propria con soli `GetVideoSettings`, `GetSceneItemList`,
`GetSceneItemTransform`, `SetSceneItemTransform`):
- in ogni scena camera viene animata la sorgente visibile più grande e non ruotata; loghi e grafiche
  restano fermi. La scala avviene attorno al centro dell'elemento, per qualsiasi allineamento, e con i
  *bounds* se presenti (`P' = C + (P − C)·k`). k non scende mai sotto 1, quindi niente bordi;
- *zoom passante*:
  - appena il taglio è programmato, la scena entrante viene portata a `1 + a` mentre è ancora fuori onda;
  - nell'ultimo mezzo beat (0,2–0,45 s) la scena uscente stringe da 1 a `1 + a` con curva accelerata;
  - alla conferma del taglio la scena entrante si assesta a 1 in un beat (0,35–0,9 s) con curva
    decelerata;
  - la scena uscente torna esattamente com'era una volta fuori onda, dopo l'eventuale transizione
    abituale;
- l'animazione invia circa 50 aggiornamenti al secondo, calcolati sul tempo reale;
- un taglio annullato riporta tutto a 1;
- cambi manuali, pausa e stop ripristinano tutte le scene;
- prima di toccare un elemento la sua trasformazione originale viene scritta in `zoom_restore.json`;
  al riavvio successivo, se il file c'è (crash, connessione persa), le scene vengono ripristinate;
- Shadow non invia nulla.

Limite: lo zoom passa da obs-websocket, quindi la sua fluidità dipende dal carico di OBS. Va verificato
con OBS reale e in registrazione.

## Consapevolezza della scena (v0.2)

Codice: `src/dj_director/visual/`. Le immagini arrivano da OBS (`GetSourceScreenshot`, una richiesta di
sola lettura) su una **seconda connessione**: le istantanee non rallentano mai i cambi di scena.

**Occhio veloce** (sempre locale, nessun modello AI), su istantanee 384×216 ridotte a 128 px:

| Misura | Come | Effetto |
|---|---|---|
| Nera / buia | luminosità media < 5% | **veto** |
| Sovraesposta | > 60% dei pixel saturi | **veto** |
| Bloccata | fotogrammi identici bit per bit per 4 s | **veto** |
| Sfocata o coperta | nitidezza (laplaciano) < 35% del riferimento *della camera stessa* | **veto** |
| Movimento | differenza tra fotogrammi, nella zona di interesse se impostata | attività → novità visiva |
| A tempo | concentrazione di fase del movimento (al beat e al mezzo beat) e della luminosità (luci) sul periodo del beat, corretta per il caso | piccolo bonus nel punteggio (+0.25 al massimo) |

- Un problema deve **durare 1.5 s** per diventare veto (un flash o uno strobo non lo sono) e sparisce dopo
  1 s di immagini buone.
- Una camera senza immagini recenti (3 s) non dà informazioni: **mai** un veto per mancanza di dati.
- La nitidezza è relativa alla camera: si accorge di una camera che **perde** il fuoco o viene coperta,
  non di una che è sfocata fin dall'inizio.
- Budget di istantanee: 5 / 10 / 16 al secondo (Bassa / Normale / Alta). Il 60% va alla camera di
  rivelazione, così la sua lettura del beat ha abbastanza campioni (6 al secondo a Normale). A Bassa la
  lettura del beat non è possibile.

**Occhio lento** (facoltativo): ogni `judge_interval` secondi (8 s) un modello visivo riceve **un solo
mosaico numerato** di tutte le camere. Il prompt di sistema lo fa ragionare da regista multicamera (casa
sul Master, cambi netti di scala / angolo / soggetto, niente quasi-doppioni, niente salti fronte-retro
senza raccordo). Dalla 0.5 il testo contiene anche:

- per ogni camera, i **metadati dell'inquadratura** (scala, soggetto, angolazione, camera fisica e crop)
  e il **movimento** dell'occhio veloce (`activity`, nella zona di interesse se c'è, e coerenza col beat):
  una foto non mostra il movimento;
- lo **stato del montaggio**: camera in onda, da quanti secondi e con quale finestra preferita, gli ultimi
  6 tagli con la durata, il taglio già programmato, la copertura per classe;
- la **musica**: stato, BPM, confidenza del beat, gesto sul mixer in corso, tempo dall'ultimo drop.

Risponde in JSON validato con uno schema (`JUDGE_SCHEMA`):

- `usable` / `issue`: un'inquadratura non utilizzabile (puntata sul soffitto, persona davanti) diventa un
  **veto** ("AI: …");
- `interest` 0–1, `composition` 0–1 e `moment` (none, hands_on_mixer, dj_gesture, crowd_peak, lights,
  dj_with_crowd) entrano nel punteggio visivo della camera:
  `0.6 × (interesse − 0.4) + 0.3 × (composizione − 0.6) + 0.1 se c'è un momento`;
- `next_pick`, `hold`, `reason` formano il **consiglio da regista** (`EditorHint`), pesato da
  `vision.ai_influence` (0 = spento, 0.5 leggero, 1 normale, 1.6 forte):
  - `next_pick` aggiunge al candidato la voce `ai = 0.3 × peso` nello Shot Planner, una tra copertura,
    musica, continuità e qualità, e il motivo del taglio riporta "AI suggests it: …";
  - `hold` sull'inquadratura in onda vale come **continuità** per un insert, che arriva al massimo alla
    fine della finestra preferita e mai oltre il tetto. Sul Master riduce del `50 % × peso` solo la
    spinta editoriale e solo prima di `pref_max`: drop e gesti tagliano comunque;
  - una scelta su una camera con veto, o un giudizio scaduto, viene ignorata;
- `drop_pick`: la camera migliore per rivelare il prossimo drop, se l'utente non ne ha scelta una.

Le risposte vengono validate: numeri di camera fuori elenco o senza immagine scartati, valori limitati a
0–1, `next_pick` uguale alla camera in onda letto come `hold`, `hold` accettato solo per la camera
effettivamente in onda al momento dello sguardo.

**Revisione post-serata** (`review/`): a ogni taglio eseguito (LIVE o Shadow) il controller salva in
`decisions-…-frames/` due JPEG 384×216, l'ultima immagine dell'inquadratura uscente e la prima di quella
entrante, e un record `ShotFrames` nel log. La revisione:
- ricostruisce i tagli e le durate dal log;
- calcola numeri e controlli senza AI (inquadratura precedente < 3 s, A-B-A-B, jump cut e asse dai
  metadati);
- manda al modello dell'occhio lento gruppi di 6 tagli, una riga prima / dopo per taglio, con il motivo
  del regista e il contesto musicale (`REVIEW_SCHEMA`: giudizio, problemi da un elenco chiuso, commento);
- trasforma i problemi ricorrenti (≥ 3 o ≥ 15 % dei tagli) in suggerimenti fissi, senza mai applicarli.

Un errore del modello interrompe solo la critica: il report esce comunque con numeri e controlli.

Il giudizio vale per `max(15 s, 2 × intervallo)` e poi scade: **orienta** le scelte, non decide mai il
momento del taglio, che resta sul beat. Un errore del modello (non avviato, rete assente, risposta non
valida) fa solo aspettare di più prima del tentativo successivo: il regista continua senza.

| Fornitore | Dove gira | Note |
|---|---|---|
| Locale (Ollama) | su questo PC, offline | modelli scaricabili dalla pagina Visione; consigliato Qwen3-VL 4B (3.3 GB) per una RTX 4060 |
| Cloud (API Claude) | online | SDK ufficiale `anthropic`; modello predefinito Claude Opus 5.5 con fallback lato server; le istantanee lasciano il PC |

## Grammatica del drop (v0.2)

- **Camera di rivelazione**, nell'ordine: la scena scelta in *Regia › Inquadratura che si apre sul drop*;
  altrimenti il suggerimento dell'occhio lento; altrimenti, dalla v0.4, prima le camere che i metadati
  indicano come larghe o su pubblico / sala, poi per ruolo CROWD, WIDE, BEHIND, SIDE, CUSTOM. Con l'opzione *Spenta* (`drop_reveal = false`) il drop torna al Master come in 0.1.3.
- **Build-up** (stato *Attività in aumento*): con beat e stato affidabili la rivelazione viene **tenuta
  da parte**, cioè non usata come insert se ci sono alternative. La tensione resta sulle inquadrature strette.
- **Drop**: taglio sulla rivelazione sul beat successivo, dal Master oppure da un insert che abbia superato
  la sua durata minima. Il cooldown non si applica, basta che la camera non sia stata in onda negli ultimi
  3 s e che il taglio non completi uno schema A-B-A-B. Altrimenti il drop sceglie un'altra camera. Poi la rivelazione esce come ogni insert: durata pianificata, tetto, ritorno al Master.

## Rivalidazione

Un cambio programmato viene ricontrollato a ogni tick fino all'esecuzione. Viene **annullato** (`CANCEL`)
se:

- il target non è più disponibile, oppure un cambio verso di lui è appena fallito;
- il segnale audio è perso;
- arriva un override manuale;
- l'attesa per il confine metrico è scaduta;
- (solo per gli ingressi) il target non è più adatto allo stato musicale ("target no longer suits …");
- (solo per gli ingressi editoriali) la classe ha già superato il suo target di oltre 10 punti
  ("coverage target already reached").

Le uscite non vengono annullate per motivi editoriali: il tetto ha la precedenza.

## Determinismo

Il motore non usa numeri casuali.

- **Variazione delle durate**: arriva da una sequenza del rapporto aureo per ogni classe, sfasata dal
  `seed`, e dalla musica stessa (allineamento ai confini).
- **Riproducibilità**: stessi ingressi + stesso seed ⇒ stesse decisioni. Il replay offline e i test lo
  verificano.
- **Nessuna ripetizione immediata**: due shot consecutivi della stessa classe non hanno mai la stessa
  durata pianificata.
- **Ricostruzione dell'engine**: quando si cambiano i parametri in diretta, il controller copia lo stato
  delle sequenze e della novità, così la regia continua senza salti.

## Shadow Mode

In **Shadow** il motore lavora esattamente come in LIVE:

- stesse decisioni, stessi motivi, stessa copertura nella Dashboard;
- nessun comando inviato a OBS (lo verifica un test end-to-end).

È il modo previsto per provare la regia su un set reale prima di andare in onda.

## Preset

| Parametro | Minimal | Natural | Dynamic |
|---|---|---|---|
| cut_threshold | 1.0 | 1.0 | 0.85 |
| scala durata Master / insert | 1.4 / 1.0 | 1.0 / 1.0 | 0.75 / 0.9 |
| distanza minima tra tagli | 8 s | 5 s | 3 s |
| max tagli/minuto | 3 | 5 | 9 |
| max insert consecutivi | 1 | 2 | 2 |
| guadagno trigger musicali | 0.6 | 1.0 | 1.3 |
| isteresi (resistenza al chaining) | 0.35 | 0.25 | 0.18 |
| attesa massima per un confine di frase | 12 s | 9 s | 5 s |
| coverage_gain | 0.6 | 1.0 | 1.2 |

I target di copertura (70/15/15), la finestra (300 s) e i tetti sono comuni a tutti i preset: i preset
cambiano il **ritmo**, non le proporzioni.

Tutti i parametri si modificano in *Regia › Avanzate*. Le modifiche vengono salvate come override del
preset.

## Override manuale

`on_external_change(scene)` viene chiamato quando OBS segnala un cambio di Program che non abbiamo
richiesto (finestra di attribuzione di 3 s).

- **Policy**: `timed` (default 30 s), `until_resume` e `adopt`.
- **Scena non gestita**: sospende sempre fino a Resume.
- **Force Master**: è un comando esplicito dell'operatore e scavalca l'opportunità.
