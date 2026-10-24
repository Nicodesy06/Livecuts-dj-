# Come decide il regista

Codice: `src/dj_director/director/engine.py`. A ogni tick (20 Hz) l'engine riceve tempo, ultimo
`MusicSnapshot` ed eventi musicali, e restituisce **una** `DirectorDecisionEvent`:
`HOLD`, `SCHEDULE`, `SWITCH`, `CANCEL` o `OVERRIDE`, sempre con un motivo costruito dai fattori
realmente usati.

## Priorità per tick

1. Se c'è un cambio programmato, lo esegue quando è il momento, altrimenti attende o lo annulla (target non
   più disponibile, segnale perso, finestra di sync scaduta, override manuale).
2. Se è attivo un override manuale: `HOLD` ("manual override respected").
3. Se non c'è nulla in onda o la camera in onda è sparita: taglio immediato sull'ancora (Master, poi
   fallback, poi la camera più larga disponibile).
4. Se non c'è segnale audio: ritorno al Master oppure `HOLD`, secondo la policy configurata. Nessun altro taglio.
5. Altrimenti le tre decisioni seguenti.

## A. Opportunità (conviene tagliare?)

```
opportunity = duration_pressure + musical_trigger
```

- **Durata minima** della camera (scalata dal preset): vincolo hard.
- **duration_pressure**: a ogni inquadratura viene campionata una *durata pianificata* da una lognormale
  con la media del ruolo (Master 36 s, Detail 9 s, Side/Wide 15 s, …) e una dispersione data dal preset.
  La pressione è 0 alla durata minima, circa 1 alla durata pianificata e sale oltre fino al massimo
  preferito. Durate campionate significano niente intervalli identici e niente metronomo.
- **musical_trigger**: eventi probabilistici, ciascuno pesato per la sua confidenza (`reentry` 0.95,
  `transition` 0.5, `sparse_start` 0.4) e per il guadagno del preset. Gli eventi sotto
  `musical_cut_confidence` vengono ignorati. Ogni evento viene consumato da al massimo un taglio.
- Stato `UNCERTAIN`: la pressione viene ridotta del 30%, così le inquadrature respirano.
- Sotto `cut_threshold` la decisione è `HOLD` ("stable groove; insufficient editorial benefit").
- Altri vincoli hard: distanza minima tra due tagli e tetto di tagli al minuto.
- Un insert che supera la sua durata massima viene riportato all'ancora in ogni caso.

## B. Destinazione (quale camera?)

```
CameraScore = EditorialFit + MusicalFit + VisualFit + VarietyBonus + Preference
              − RepetitionPenalty + jitter(seed)
```

- **EditorialFit**: l'ancora vale di più quanti più insert consecutivi ci sono stati, e diventa
  obbligatoria al raggiungimento di `max_consecutive_inserts`. Partendo dal Master, un insert riceve +0.3.
- **MusicalFit**: tabella ruolo × stato. Per esempio: durante una transizione il DETAIL (mani sul mixer)
  vale +0.45, durante una ripartenza il WIDE +0.45 e il MASTER +0.35, durante una sezione rarefatta
  DETAIL e OVERHEAD +0.3.
- **VisualFit**: 0 in v0.1 (interfaccia pronta per la v0.2).
- **Variety**: tempo dall'ultimo utilizzo dell'insert, fino a +0.3 a 120 s.
- **Repetition**: −0.6 per lo schema A-B-A-B; −0.25 per riusare lo stesso insert subito attorno al
  Master, penalità che svanisce in 60 s.
- Le camere in **cooldown**, disabilitate, mancanti in OBS, non idonee allo stato corrente o con un
  cambio appena fallito vengono escluse.
- **Isteresi**: il migliore candidato deve superare il *valore di restare* (fit musicale della camera
  corrente, meno una penalità crescente se si sta oltre la durata pianificata) di un margine
  `hysteresis × (1 − trigger)`. Un evento forte abbassa quindi la soglia per lasciare l'inquadratura.

## C. Timing

- Se la confidenza del beat è ≥ `sync_confidence`, il taglio cade sul prossimo beat, oppure sul prossimo
  downbeat se `prefer_downbeat` è attivo e la confidenza del downbeat è ≥ `downbeat_confidence`, purché
  entro `max_sync_wait`. Il comando parte con `latency_offset` di anticipo.
- **Confine di frase** (v0.1.1, euristico): per i tagli **non urgenti** (nessun evento musicale, insert
  non oltre la durata massima), se è stato rilevato un drop con confidenza sufficiente, il taglio aspetta
  il prossimo confine di **8 battute** (o di 4, se quello di 8 è troppo lontano), entro `max_phrase_wait`
  (Minimal 12 s, Natural 9 s, Dynamic 5 s). La griglia parte dall'istante del drop, riallineato al beat
  corrente, e si annulla se il tempo cambia di oltre il 2%. Si basa sul fatto che la musica dance è
  costruita a blocchi di 4/8/16 battute; si disattiva con `phrase_sync`.
- Altrimenti il taglio è immediato: nessuna falsa precisione ritmica.
- Mentre si attende, il cambio viene annullato se cambia il contesto.

## Preset

| Parametro | Minimal | Natural | Dynamic |
|---|---|---|---|
| cut_threshold | 1.0 | 1.0 | 0.8 |
| scala durata Master / insert | 1.6 / 1.15 | 1.0 / 1.0 | 0.6 / 0.8 |
| distanza minima tra tagli | 8 s | 5 s | 3 s |
| max tagli/minuto | 3 | 5 | 9 |
| max insert consecutivi | 1 | 2 | 3 |
| guadagno trigger musicali | 0.6 | 1.0 | 1.3 |
| isteresi | 0.35 | 0.25 | 0.18 |
| attesa massima per un confine di frase | 12 s | 9 s | 5 s |

Tutti i parametri si possono modificare in *Director › Advanced*. Le modifiche vengono salvate come override
del preset scelto.

## Override manuale

`on_external_change(scene)` viene chiamato quando OBS segnala un cambio di Program che non abbiamo
richiesto (finestra di attribuzione di 3 s). Le policy sono `timed` (default 30 s), `until_resume` e `adopt`.
Una scena non gestita sospende sempre fino a Resume. **Force Master** è un comando esplicito
dell'operatore e scavalca l'opportunità.
