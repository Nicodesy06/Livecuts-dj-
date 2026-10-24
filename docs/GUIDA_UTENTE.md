# DJ Director AI — Guida all'utilizzo

Versione 0.1.1 · Windows 10/11 (64 bit)

DJ Director AI è un regista automatico per i tuoi DJ set. Ascolta la musica che esce dal mixer, ne capisce
l'andamento (tempo, beat, breakdown, drop) e cambia da solo le inquadrature in OBS Studio, come farebbe
un regista discreto. Tu suoni, lui fa la regia.

Il programma **non registra e non trasmette**: lo fa OBS, come sempre. DJ Director si limita a scegliere
quale scena di OBS mandare in onda. Se lo chiudi o si blocca, OBS continua a registrare normalmente.

---

## 1. Cosa ti serve

| Cosa | Note |
|---|---|
| PC Windows 10 o 11 a 64 bit | Basta una CPU recente: il programma usa circa il 3% di un core. Nessuna scheda video particolare |
| OBS Studio 28 o successivo | Contiene già il server WebSocket che DJ Director usa per comandarlo |
| Da 2 a 8 camere già configurate in OBS | Una **scena** di OBS per ogni inquadratura |
| Un ingresso audio con il **master del mixer** | Per esempio una Behringer UCA222 collegata all'uscita REC del mixer |

Non serve Python, non serve un account, non serve Internet. Nessun dato audio o video lascia il PC.

---

## 2. Installazione

1. Scarica **`DJDirectorAI-0.1.1-win64.zip`** dalla pagina *Releases* del progetto su GitHub.
2. Fai clic destro sullo zip → **Estrai tutto…** in una cartella a tua scelta, per esempio
   `C:\Programmi DJ\DJDirectorAI`. È una versione *portable*: non c'è un'installazione vera e propria.
3. Apri la cartella ed esegui **`DJDirectorAI.exe`**.

> **Avviso di Windows SmartScreen.** L'eseguibile non è firmato digitalmente, quindi al primo avvio Windows
> può mostrare "Windows ha protetto il PC". Fai clic su **Ulteriori informazioni** → **Esegui comunque**.
> Succede solo la prima volta.

Per comodità puoi creare un collegamento sul desktop: clic destro su `DJDirectorAI.exe` → *Invia a* →
*Desktop (crea collegamento)*.

**Disinstallazione:** elimina la cartella del programma. Se vuoi cancellare anche impostazioni e log,
elimina `%APPDATA%\DJDirectorAI` e `%LOCALAPPDATA%\DJDirectorAI` (incolla questi percorsi nella barra
degli indirizzi di Esplora file).

---

## 3. Collegamenti

### Audio (esempio con Behringer UCA222)

```
 Mixer DJ ── uscita REC / BOOTH ──(cavo RCA)──► UCA222 ingressi RCA ──(USB)──► PC
```

- Usa un'uscita del mixer che **non dipenda dal volume del master** del locale, se possibile (REC è ideale).
- Windows mostra la UCA222 come due dispositivi:
  - **Microfono (USB Audio CODEC)**: è l'**ingresso RCA**, non un microfono vero. È questo che devi usare.
  - **Altoparlanti (USB Audio CODEC)**: è l'uscita. Serve solo se il software DJ suona dal PC attraverso
    la UCA222; in quel caso DJ Director può "ascoltarla" in *loopback*.
- La UCA222 non ha una manopola di guadagno: regola il livello dall'uscita REC del mixer, oppure in
  Windows (*Impostazioni › Sistema › Audio › Microfono (USB Audio CODEC) › Volume di input*).

Vanno bene anche altre interfacce audio, la scheda audio interna di un mixer o di un controller DJ, o un
mixer USB: l'importante è che arrivi il **mix pulito**. Un microfono ambientale funziona solo in emergenza,
perché sente le casse e il pubblico.

### Video

Le camere si collegano a OBS come al solito. In OBS crea **una scena per ogni camera**, con nomi chiari
come `Cam Master`, `Cam Mixer`, `Cam Laterale`, `Cam Pubblico`. DJ Director legge i nomi delle scene e
li usa così come sono.

---

## 4. Preparare OBS (una volta sola)

1. Apri OBS → menu **Strumenti › Impostazioni server WebSocket**.
2. Spunta **Abilita server WebSocket**.
3. Lascia la porta **4455**. Clicca **Mostra informazioni di connessione** e copia la **password**.
4. Clicca **OK**.

DJ Director **non crea, non elimina e non modifica** scene, sorgenti, filtri, audio o profili, e **non
avvia né ferma** registrazione o streaming. Fa una sola cosa: cambia la scena **Program**.

---

## 5. Primo avvio: la configurazione guidata

Al primo avvio si apre la **Configurazione guidata**. Puoi riaprirla in qualsiasi momento dal pulsante in
basso a sinistra.

1. **Benvenuto**: un riepilogo di cosa farai.
2. **Collega OBS**: con OBS aperto, inserisci host `127.0.0.1`, porta `4455` e la password copiata, poi
   premi **Prova connessione**. Deve comparire "Connesso" con il numero di scene trovate.
3. **Camere**: spunta le scene che sono inquadrature (non "Inizio a breve", "Pausa" e simili), assegna a
   ciascuna un **ruolo** e scegli il **Master**.
4. **Ingresso audio**: fai suonare musica sul mixer e premi **Rileva sorgente audio**. Il programma
   ascolta ogni ingresso e ogni uscita per un secondo e seleziona quella con il mix. Poi premi **Usa
   selezionata** e controlla che l'indicatore di livello si muova.
5. **Stile di regia**: scegli **Natural** (consigliato).
6. **Pronto**: lascia spuntata **"Usa la modalità Shadow come predefinita"** per la prima prova.

### I ruoli delle camere

| Ruolo | Che inquadratura è | Come la usa il regista |
|---|---|---|
| **MASTER** | L'inquadratura principale del DJ, frontale e abbastanza larga | È l'"ancora": la più usata (60–75% del tempo), con riprese lunghe (30–90 s) |
| **DETAIL** | Mani, mixer, piatti, controller | Inserti brevi (5–15 s), soprattutto durante i mix e i breakdown |
| **SIDE** | Laterale sul DJ | Inserti medi (7–25 s) |
| **WIDE** | Campo largo, pubblico, sala | Inserti medi, preferiti sui drop |
| **OVERHEAD** | Dall'alto sulla consolle | Inserti brevi, durante le transizioni |
| **CUSTOM** | Qualsiasi altra | Uso neutro |

Puoi avere più camere con lo stesso ruolo. Serve **un solo Master**.

---

## 6. La Dashboard

È la schermata principale. In alto, sotto il titolo, trovi lo stato: **"Pronto per la regia"** in verde
oppure **"Non pronto: …"** in giallo, con l'elenco di cosa manca.

**Pulsanti**

| Pulsante | Cosa fa |
|---|---|
| **Avvia regia automatica** | Va in onda: il programma decide e cambia le scene in OBS |
| **Avvia Shadow (OBS non cambia)** | Prova a vuoto: decide e mostra tutto, ma **non tocca OBS** |
| **Pausa / Riprendi** | Sospende la regia, che resta sull'inquadratura attuale |
| **Stop** | Ferma la regia. OBS continua a registrare |
| **Forza Master** | Torna subito sulla camera Master |

**Stato del regista** (etichetta colorata in alto a destra)

| Stato | Significato |
|---|---|
| OFF | Fermo |
| ARMED | Avviato ma in attesa (per esempio OBS non connesso) |
| LIVE | In onda: sta comandando OBS |
| LIVE · MANUAL | Hai cambiato scena a mano: il programma ti sta rispettando |
| SHADOW | Prova a vuoto |
| PAUSED | In pausa |
| CALIBRATING | Calibrazione in corso |
| ERROR | Problema di configurazione: leggi il messaggio |

**Riquadri**

- **OBS Studio**: connessione, collezione di scene e scena in onda (Program).
- **Audio e musica**: dispositivo, livello (verde = ok, giallo = alto, rosso = clipping), **BPM** stimati,
  barra di affidabilità del beat e **stato musicale**: *Groove stabile*, *Attività in aumento/calo*,
  *Ritmo ridotto* (breakdown), *Possibile transizione* (stai mixando), *Ripartenza forte* (drop).
- **Regia**: camera in onda, da quanto tempo, ultimo taglio e il suo **motivo**.
- **Mappa camere**: la camera **IN ONDA** ha il bordo rosso, la **PROSSIMA** (taglio programmato sul
  beat) il bordo giallo.
- **Decisioni recenti**: ogni decisione con il motivo reale. *TIENI* (resto sull'inquadratura) è una
  decisione normale: un buon regista sa anche quando non tagliare.

---

## 7. Una serata tipo

**Prima del set (5 minuti)**

1. Accendi mixer e UCA222, apri **OBS** e controlla che le camere si vedano.
2. Apri **DJ Director**: OBS deve risultare *Connesso* (si collega da solo).
3. Fai partire un brano: il livello si muove, compaiono i BPM e la scritta in alto diventa
   **"Pronto per la regia"**.
4. In OBS avvia **la registrazione o lo streaming** come fai di solito.
5. In DJ Director premi **Avvia regia automatica**.

**Durante il set**

Suoni e basta. Non serve toccare il PC. Se vuoi intervenire:

- **Cambio manuale**: clicca una scena in OBS. DJ Director lo nota e ti lascia fare per 30 secondi
  (modificabile), poi riprende da solo.
- **Scena di servizio** ("Pausa", "Inizio a breve"…): se la metti tu in onda, la regia si sospende finché
  non premi **Riprendi**. Non verrai mai "tirato fuori" da quella scena.
- **Forza Master**: torna subito sull'inquadratura principale.

**Fine del set**

Premi **Stop** in DJ Director, poi ferma la registrazione in OBS.

---

## 8. Le altre pagine

### Camere

L'elenco delle scene di OBS. Per ciascuna: **Usa**, **Ruolo**, **Master**, **Peso** (più alto = scelta più
spesso) e, facoltative, le durate (**Min**, **Media**, **Max**) e il **Cooldown** (tempo minimo prima di
riusare un inserto). Lascia "predefinito" se non hai esigenze particolari. Premi **Salva mappa camere**.
Se rinomini una scena in OBS, DJ Director aggiorna il nome da solo.

### Audio

- **Tipo**: *Ingresso audio* (normale), *Loopback WASAPI* (ascolta un'uscita del PC) o *File audio*
  (per le prove).
- **Dispositivo** e **Canali** (di solito **1 + 2**).
- **Rilevamento automatico**: il pulsante **Rileva sorgente audio** spiegato al capitolo 5.
- **Ingresso dal vivo**: livello, clipping, dropout, latenza. Sotto compaiono anche i suggerimenti in caso
  di problemi.
- **Analisi (avanzate)**: in genere non serve toccarle. Fanno eccezione il **Tempo tipico**, che conviene
  impostare vicino al tuo genere (95 per hip-hop, 124 per house/techno, 174 per drum & bass), e la
  **Sensibilità musicale** (più alta = reagisce a cambi più piccoli).

### OBS

Host, porta e password (salvata **cifrata** con la protezione di Windows), più **Prova connessione** e
**Salva e connetti**. Con lo **Studio Mode** attivo, DJ Director cambia comunque la scena **Program**
(quella registrata) e non tocca la Preview.

### Regia

- **Stile**: *Minimal* (pochi tagli, riprese lunghe), *Natural* (consigliato), *Dynamic* (più movimento,
  ma mai un taglio a ogni beat).
- **Comportamento**: cosa fare con un cambio manuale in OBS, quanto rispettarlo, cosa fare se manca
  l'audio, offset di sincronizzazione e lingua dell'interfaccia.
- **Calibrazione della sincronizzazione**: vedi il capitolo 9.
- **Avanzate**: tutti i parametri del motore (soglia di taglio, durate, tagli massimi al minuto, attesa
  per i confini di frase…). Non servono per ottenere un buon risultato.

### Simulazione

Fa passare un set registrato (WAV/FLAC), o un set sintetico di prova, attraverso il vero regista, molto più
veloce del tempo reale e **senza OBS né camere**. Mostra la timeline delle inquadrature e la confronta
con uno switcher a timer e con uno casuale. Utile per provare stili e impostazioni sulla tua musica prima
della serata.

### Decisioni e log

L'elenco completo delle decisioni, le latenze misurate e il pulsante **Apri cartella log**. Ogni sessione
salva un file `decisions-AAAAMMGG-HHMMSS.jsonl`.

---

## 9. Calibrare la sincronizzazione (consigliato, una volta)

Tra il momento in cui il programma ordina il cambio e quello in cui vedi l'immagine cambiare passa un
piccolo ritardo, che dipende da camere e PC. La calibrazione serve a far cadere i tagli esattamente
sulla cassa.

1. Apri **Regia › Calibrazione della sincronizzazione**.
2. Scegli **Camera A** e **Camera B** (due inquadrature ben diverse) e "ogni 8 beat".
3. Fai suonare un brano con una cassa ben evidente (house/techno vanno benissimo).
4. Premi **Avvia calibrazione**: le due camere si alternano in OBS esattamente sul beat.
5. Guarda l'uscita di OBS (o registra 30 secondi e riguarda il video):
   - il cambio arriva **dopo** la cassa → sposta il cursore **a destra** (più anticipo);
   - il cambio arriva **prima** → sposta **a sinistra**.
6. Quando il taglio cade sulla cassa, premi **Ferma calibrazione**: il valore viene salvato.

Valori tipici: da 40 a 150 ms.

---

## 10. Come ragiona il regista (in breve)

- **Non taglia a tempo.** Ogni inquadratura ha una durata "prevista", diversa ogni volta, e il taglio
  avviene quando ha senso musicalmente.
- **Ascolta la musica.** Un drop, un breakdown o l'inizio di un mix sono buoni momenti per cambiare.
  Durante un mix preferisce il dettaglio del mixer, su un drop il campo largo o il master.
- **Taglia sul beat**, e dopo un drop sui **confini di frase** (ogni 4 o 8 battute), quando è sicuro del
  ritmo. Se non lo è, non finge precisione.
- **Torna al Master** con naturalezza, senza schemi meccanici e senza ping-pong tra due camere.
- **Rispetta dei limiti**: durata minima, distanza minima tra tagli, numero massimo di tagli al minuto.
- **Sa quando non tagliare.** Se l'inquadratura attuale va bene, la tiene.

---

## 11. Risoluzione dei problemi

| Problema | Soluzione |
|---|---|
| **OBS: "Disconnesso"** | OBS è aperto? Il server WebSocket è abilitato (capitolo 4)? Porta 4455? |
| **OBS: "Password errata"** | Ricopia la password da OBS nella pagina **OBS** e premi **Salva e connetti** |
| **Nessun segnale audio** | La musica suona? Il cavo va dall'uscita REC del mixer agli ingressi della UCA222? Usa **Rileva sorgente audio** |
| **Avviso "zeri digitali" / ingresso digitalmente muto** | Windows blocca il "microfono": *Impostazioni › Privacy e sicurezza › Microfono* → attiva **Consenti alle app desktop di accedere al microfono** |
| **Livello molto basso** | Alza l'uscita REC del mixer o il volume di input del dispositivo in Windows |
| **Indicatore rosso (clipping)** | Abbassa l'uscita REC del mixer |
| **BPM doppi o dimezzati** (es. 180 invece di 90) | In **Audio › Analisi** imposta il *Tempo tipico* vicino al tuo genere |
| **Taglia troppo** | Usa lo stile **Minimal**, oppure in *Avanzate* alza la soglia di taglio |
| **Taglia troppo poco** | Usa lo stile **Dynamic** |
| **Il regista resta su ARMED** | Leggi il messaggio: di solito OBS non è connesso o una scena camera non esiste più |
| **Ho cambiato scena a mano e non riprende** | Eri su una scena non gestita: premi **Riprendi** |
| **Windows non fa partire l'exe** | Vedi l'avviso SmartScreen al capitolo 2 |
| **Il dispositivo audio non compare** | Ricollega l'USB e premi **Aggiorna** nella pagina Audio |

Se un problema persiste, nella pagina **Decisioni e log** premi **Apri cartella log** e conserva i file
`dj-director.log` e `decisions-….jsonl`.

---

## 12. Dove sono i miei dati

| Cosa | Dove |
|---|---|
| Impostazioni | `%APPDATA%\DJDirectorAI\config.json` |
| Password di OBS (cifrata) | `%APPDATA%\DJDirectorAI\secrets.json` |
| Log | `%LOCALAPPDATA%\DJDirectorAI\logs\` |

Se il file delle impostazioni si rovina, il programma ne salva una copia e riparte con i valori
predefiniti, senza bloccarsi.

---

## 13. Limiti di questa versione

- È la prima versione: **provala in modalità Shadow** e su un set di prova prima di una serata importante.
- Il riconoscimento di breakdown e drop è affidabile sulla musica con cassa regolare; su generi
  irregolari il programma è più prudente e taglia soprattutto in base alla durata delle inquadrature.
- Il programma non "vede" ancora le camere: non sa se il DJ è inquadrato o se una camera è nera
  (previsto per la versione 0.2).
- Le funzioni che riguardano il "downbeat" (inizio battuta) e le frasi musicali sono stime, non certezze.
