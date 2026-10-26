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
`side` (SIDE, BEHIND, WIDE, CUSTOM).

| Classe | Minimo | Finestra preferita | Tetto assoluto (hard cap) | Cooldown |
|---|---|---|---|---|
| Master | 12 s | 20–50 s | nessuno | – |
| Detail | 5 s | 6–10 s | 14 s (configurabile 12–15) | 20 s |
| Side / Behind / Wide | 7 s | 10–18 s | 22 s (configurabile 20–25) | 15 s |

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
   Side 10–18 s).
   - Valore di base: la sequenza deterministica della classe.
   - **Budget**: una classe sopra target accorcia la lunghezza verso `pref_min`, una sotto target la
     allunga verso `pref_max`.
2. **Continuità**: se la novità visiva sta ancora calando, o se dura il momento musicale che ha
   giustificato l'ingresso (per esempio la transizione per un Detail), lo shot può arrivare a
   `pref_max`. **Mai oltre.**
3. **Hard cap**: limite di sicurezza assoluto. L'uscita viene programmata in modo da cadere entro il
   tetto: un cambio in attesa non lo supera mai, e senza un beat utile il taglio è immediato.
4. **Drop (re-entry)**: apre sulla camera di rivelazione (vedi *Grammatica del drop*) già a metà della
   durata minima. Senza camera di rivelazione riporta presto al Master. Se l'inquadratura in onda è già
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
mosaico numerato** di tutte le camere e il contesto musicale (stato, BPM, camera in onda, tempo
dall'ultimo drop). Risponde in JSON validato con uno schema:

- `usable` / `issue`: un'inquadratura non utilizzabile (puntata sul soffitto, persona davanti) diventa un
  **veto** ("AI: …");
- `interest` 0–1: entra nel punteggio di scelta della camera come `0.6 × (interesse − 0.4)`;
- `drop_pick`: la camera migliore per rivelare il prossimo drop, se l'utente non ne ha scelta una.

Il giudizio vale per `max(15 s, 2 × intervallo)` e poi scade: **orienta** le scelte, non decide mai il
momento del taglio, che resta sul beat. Un errore del modello (non avviato, rete assente, risposta non
valida) fa solo aspettare di più prima del tentativo successivo: il regista continua senza.

| Fornitore | Dove gira | Note |
|---|---|---|
| Locale (Ollama) | su questo PC, offline | modelli scaricabili dalla pagina Visione; consigliato Qwen3-VL 4B (3.3 GB) per una RTX 4060 |
| Cloud (API Claude) | online | SDK ufficiale `anthropic`; modello predefinito Claude Opus 5.5 con fallback lato server; le istantanee lasciano il PC |

## Grammatica del drop (v0.2)

- **Camera di rivelazione**, nell'ordine: la scena scelta in *Regia › Inquadratura che si apre sul drop*;
  altrimenti il suggerimento dell'occhio lento; altrimenti la camera più larga tra WIDE, BEHIND, SIDE e
  CUSTOM. Con l'opzione *Spenta* (`drop_reveal = false`) il drop torna al Master come in 0.1.3.
- **Build-up** (stato *Attività in aumento*): la rivelazione viene **tenuta da parte**, cioè non usata
  come insert se ci sono alternative. La tensione resta sulle inquadrature strette.
- **Drop**: taglio sulla rivelazione sul beat successivo, dal Master oppure da un insert (già a metà della
  sua durata minima). Il cooldown non si applica, basta che la camera non sia stata in onda negli ultimi
  3 s. Poi la rivelazione esce come ogni insert: durata pianificata, tetto, ritorno al Master.

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
