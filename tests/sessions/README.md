# Sessioni reali per i test

Metti qui le registrazioni di DJ set reali (WAV, FLAC, OGG o AIFF, almeno 10 minuti consigliati), oppure
indica un'altra cartella con la variabile d'ambiente `DJD_SESSIONS_DIR`. I file audio sono esclusi da git.

```bash
DJD_SESSIONS_DIR=/percorso/registrazioni pytest tests/simulation/test_real_sessions.py -s
```

Ogni sessione passa attraverso la vera analisi e il vero Director Engine (4 camere: Master, Detail, Side,
Behind, preset Natural), e il test verifica:

- nessun limite massimo superato;
- nessuna durata minima o cooldown violati;
- nessuno schema A-B-A-B;
- intervalli tra i tagli non ripetitivi;
- regia attiva (almeno un taglio al minuto);
- per sessioni di almeno 10 minuti: copertura master entro 70 % ± 8 punti, laterali sotto il 25 %.

Il report completo di ogni sessione (`-s`) mostra copertura per classe, durate mediane e massime, tagli
sincronizzati al beat e la timeline. Per un report singolo:

```bash
dj-director simulate registrazione.wav --cameras 4 --json report.json
```
