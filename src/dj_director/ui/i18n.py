"""User-interface translations (English source strings, Italian translation).

``tr(text)`` returns the translation for the active language (English text is
the key, so untranslated strings fall back to English). The language is chosen
at start-up (config ``ui_language``: auto / it / en); changing it requires a
restart because widgets are built once.

Decision reasons produced by the engine are English, composed of fixed
fragments joined by "; ". ``tr_reason`` translates them fragment by fragment
with regular expressions, so the logic stays language-independent and the
JSONL log keeps the original wording.
"""

from __future__ import annotations

import locale
import re

from dj_director.core.events import DirectorDecisionEvent, MusicState

_lang = "en"


def set_language(code: str) -> str:
    """Set 'it' / 'en' / 'auto' (system locale). Returns the effective code."""
    global _lang
    if code == "auto":
        code = _system_language()
    _lang = "it" if code == "it" else "en"
    return _lang


def language() -> str:
    return _lang


def _system_language() -> str:
    try:
        from PySide6.QtCore import QLocale
        name = QLocale.system().name()
    except Exception:
        name = (locale.getlocale()[0] or "")
    return "it" if name.lower().startswith("it") else "en"


def tr(text: str) -> str:
    if _lang == "it":
        return IT.get(text, text)
    return text


STATE_IT = {
    MusicState.NO_SIGNAL: "Nessun segnale",
    MusicState.UNCERTAIN: "Incerto",
    MusicState.STABLE_GROOVE: "Groove stabile",
    MusicState.INCREASING: "Attività in aumento",
    MusicState.DECREASING: "Attività in calo",
    MusicState.SPARSE: "Ritmo ridotto",
    MusicState.TRANSITION: "Possibile transizione",
    MusicState.REENTRY: "Ripartenza forte",
}

ACTION_IT = {"HOLD": "TIENI", "SWITCH": "TAGLIO", "SCHEDULE": "PROGRAMMATO", "CANCEL": "ANNULLATO",
             "OVERRIDE": "MANUALE"}


def tr_state(state: MusicState) -> str:
    return STATE_IT.get(state, state.label) if _lang == "it" else state.label


def tr_action(action: str) -> str:
    return ACTION_IT.get(action, action) if _lang == "it" else action


def tr_decision(d: DirectorDecisionEvent) -> str:
    action = tr_action(d.action.value)
    cam = d.candidate_camera if d.action.value in ("SWITCH", "SCHEDULE") else (d.current_camera or "-")
    return f"{action} {cam} — {tr_reason(d.reason)}"


_EVENT_IT = {"reentry": "ripartenza", "transition": "transizione", "sparse start": "inizio sezione ridotta"}
_TIMING_IT = {"beat": "beat", "downbeat": "downbeat", "immediate": "subito",
              "phrase boundary (4/8 bars after the last drop)": "confine di frase (4/8 battute dall'ultimo drop)",
              "phrase": "frase"}
_STATE_LOWER_IT = {s.label.lower(): v.lower() for s, v in STATE_IT.items()}


def _ev(m: re.Match, g: str = "k") -> str:
    return _EVENT_IT.get(m.group(g), m.group(g))


_REASONS_IT: list[tuple[str, object]] = [
    (r"initial shot", "inquadratura iniziale"),
    (r"camera '(.+)' unavailable; returning to anchor", r"camera '\1' non disponibile; ritorno all'ancora"),
    (r"no usable camera configured", "nessuna camera utilizzabile configurata"),
    (r"manual override respected \(until Resume\)", "intervento manuale rispettato (fino a Riprendi)"),
    (r"manual override respected \((\d+)s left\)", r"intervento manuale rispettato (ancora \1 s)"),
    (r"manual change respected for (\d+)s", r"cambio manuale rispettato per \1 s"),
    (r"manual change respected until Resume", "cambio manuale rispettato fino a Riprendi"),
    (r"manual change adopted; automatic direction continues",
     "cambio manuale adottato; la regia automatica continua"),
    (r"manual change adopted", "cambio manuale adottato"),
    (r"automatic direction continues", "la regia automatica continua"),
    (r"switched to unmanaged scene '(.+)'", r"passato alla scena non gestita '\1'"),
    (r"automatic direction paused until Resume", "regia automatica in pausa fino a Riprendi"),
    (r"no audio signal", "nessun segnale audio"),
    (r"returning to master", "ritorno al master"),
    (r"holding", "tengo l'inquadratura"),
    (r"minimum shot length not reached \((\d+)/(\d+)s\)", r"durata minima non raggiunta (\1/\2 s)"),
    (r"(?P<k>reentry|transition|sparse start) \((?P<c>[\d.]+)\)", lambda m: f"{_ev(m)} ({m.group('c')})"),
    (r"insert at max length \((\d+)s\)", r"inserto alla durata massima (\1 s)"),
    (r"shot length (\d+)s of ~(\d+)s planned", r"inquadratura da \1 s su ~\2 s previsti"),
    (r"stable", "stabile"),
    (r"no editorial reason to cut", "nessun motivo editoriale per tagliare"),
    (r"(?P<k>reentry|transition|sparse start) noted but not enough reason to cut yet",
     lambda m: f"{_ev(m)} rilevata ma non ancora motivo sufficiente per tagliare"),
    (r"stable groove", "groove stabile"),
    (r"insufficient editorial benefit", "beneficio editoriale insufficiente"),
    (r"cut wanted but too soon after previous cut", "taglio desiderato ma troppo vicino al precedente"),
    (r"cut wanted but cuts-per-minute limit reached", "taglio desiderato ma limite di tagli al minuto raggiunto"),
    (r"only one usable camera", "una sola camera utilizzabile"),
    (r"all alternatives cooling down or unsuitable", "tutte le alternative in cooldown o non adatte"),
    (r"current shot still fits", "l'inquadratura attuale è ancora adatta"),
    (r"best alternative (.+) not clearly better", r"la migliore alternativa (\1) non è nettamente migliore"),
    (r"min length satisfied \((\d+)s\)", r"durata minima soddisfatta (\1 s)"),
    (r"back to (.+) after (\d+) inserts", r"ritorno a \1 dopo \2 inserti"),
    (r"back to anchor (.+)", r"ritorno all'ancora \1"),
    (r"(\w+) suits (.+)", lambda m: f"{m.group(1)} adatta a {_STATE_LOWER_IT.get(m.group(2), m.group(2))}"),
    (r"cooldown passed", "cooldown trascorso"),
    (r"waiting for next (.+)", lambda m: f"in attesa del prossimo {_TIMING_IT.get(m.group(1), m.group(1))}"),
    (r"target (.+) became unavailable", r"il target \1 non è più disponibile"),
    (r"audio signal lost while waiting", "segnale audio perso durante l'attesa"),
    (r"beat sync window expired", "finestra di sincronizzazione scaduta"),
    (r"manual override", "intervento manuale"),
    (r"manual change", "cambio manuale"),
    (r"switch to (.+) scheduled on (.+)",
     lambda m: f"taglio su {m.group(1)} programmato sul {_TIMING_IT.get(m.group(2), m.group(2))}"),
    (r"Force Master \(operator\)", "Forza Master (operatore)"),
    (r"adopted from OBS", "adottata da OBS"),
    # controller / calibration status messages
    (r"starting", "avvio"),
    (r"paused by user", "in pausa (utente)"),
    (r"resuming", "ripresa"),
    (r"waiting: no usable camera scene in OBS", "in attesa: nessuna scena camera utilizzabile in OBS"),
    (r"no cameras configured", "nessuna camera configurata"),
    (r"waiting for OBS connection", "in attesa della connessione a OBS"),
    (r"decisions are logged, OBS is not commanded", "le decisioni vengono registrate, OBS non viene comandato"),
    (r"internal error", "errore interno"),
    (r"see log", "vedi il log"),
    (r"waiting for a steady beat \(play a track with a clear kick\)",
     "in attesa di un beat stabile (suona un brano con cassa evidente)"),
    (r"waiting for a steady beat", "in attesa di un beat stabile"),
    (r"cut (\d+) at ([\d.]+) BPM — does it land on the kick\?", r"taglio \1 a \2 BPM — cade sulla cassa?"),
]
_COMPILED = [(re.compile(rf"^{p}$"), r) for p, r in _REASONS_IT]


def tr_reason(reason: str) -> str:
    if _lang != "it" or not reason:
        return reason
    out = []
    for frag in reason.split("; "):
        for rx, rep in _COMPILED:
            m = rx.match(frag)
            if m:
                frag = rep(m) if callable(rep) else m.expand(rep)
                break
        out.append(frag)
    return "; ".join(out)


def untranslated_fragments(reason: str) -> list[str]:
    """Fragments of an engine reason that no pattern matches (used by tests)."""
    return [f for f in reason.split("; ") if not any(rx.match(f) for rx, _ in _COMPILED)]


IT: dict[str, str] = {
    # ---- navigation / window
    "Dashboard": "Dashboard", "Cameras": "Camere", "Audio": "Audio", "OBS": "OBS", "Director": "Regia",
    "Simulation": "Simulazione", "Decisions & log": "Decisioni e log", "Setup wizard": "Configurazione guidata",
    "DJ Director": "DJ Director", "AI multicam director · v{0}": "Regista multicamera · v{0}",
    "Quit": "Esci",
    "Auto Director is running. Stop it and quit?\n(OBS recording/streaming is NOT affected.)":
        "La regia automatica è attiva. Fermarla e uscire?\n(Registrazione e streaming di OBS NON vengono toccati.)",
    "Unexpected error: {0}\n\nDetails were written to the log. OBS is not affected.":
        "Errore imprevisto: {0}\n\nI dettagli sono stati scritti nel log. OBS non è coinvolto.",
    "The configuration file was damaged and has been reset.\nA backup was kept next to it.":
        "Il file di configurazione era danneggiato ed è stato reimpostato.\nUna copia di sicurezza è stata salvata accanto.",
    # ---- dashboard
    "Ready to direct": "Pronto per la regia", "Not ready: ": "Non pronto: ",
    "OBS not connected": "OBS non connesso", "configure at least 2 cameras": "configura almeno 2 camere",
    "no master camera": "nessuna camera master", "no audio signal": "nessun segnale audio",
    "Start Auto Director": "Avvia regia automatica", "Start Shadow (no OBS changes)": "Avvia Shadow (OBS non cambia)",
    "Pause": "Pausa", "Resume": "Riprendi", "Stop": "Stop", "Force Master": "Forza Master",
    "OBS Studio": "OBS Studio", "Audio & Music": "Audio e musica", "Camera map": "Mappa camere",
    "Recent decisions": "Decisioni recenti", "Connected": "Connesso", "Connecting…": "Connessione…",
    "Wrong password": "Password errata", "Disconnected": "Disconnesso", "scene collection": "collezione scene",
    " · {0} scenes": " · {0} scene", "Program: {0}": "Program: {0}",
    "Waiting for OBS (Tools › WebSocket Server Settings)":
        "In attesa di OBS (Strumenti › Impostazioni server WebSocket)",
    "No device": "Nessun dispositivo", "No audio source selected": "Nessuna sorgente audio selezionata",
    "Input is digitally silent: check Windows microphone privacy":
        "Ingresso digitalmente muto: controlla la privacy del microfono di Windows",
    "— BPM": "— BPM", "NO SIGNAL": "NESSUN SEGNALE", "dropouts {0}": "dropout {0}",
    "beat conf {0:.2f}": "conf. beat {0:.2f}", "latency ~{0:.0f} ms": "latenza ~{0:.0f} ms",
    "on air {0} · preset {1} · {2} cuts/min": "in onda {0} · preset {1} · {2} tagli/min",
    "preset {0}": "preset {0}", "Last cut → {0}: {1}": "Ultimo taglio → {0}: {1}",
    "No cameras configured yet — open Cameras to map OBS scenes.":
        "Nessuna camera configurata — apri Camere per associare le scene di OBS.",
    "ON AIR": "IN ONDA", "NEXT": "PROSSIMA", "MISSING": "MANCANTE", "DISABLED": "DISATTIVATA",
    "Minimal": "Minimal", "Natural": "Natural", "Dynamic": "Dynamic",
    # ---- cameras page
    "Map OBS scenes to editorial roles. The master is the narrative anchor: it gets the longest shots and the "
    "director returns to it naturally. Durations left at 0 use the role defaults (scaled by the preset).":
        "Associa le scene di OBS ai ruoli editoriali. Il master è l'ancora narrativa: ha le inquadrature più lunghe "
        "e il regista ci torna in modo naturale. Le durate lasciate a 0 usano i valori del ruolo (scalati dal preset).",
    "Reload scenes from OBS": "Ricarica scene da OBS", "Save camera map": "Salva mappa camere",
    "Use": "Usa", "OBS scene": "Scena OBS", "Role": "Ruolo", "Master": "Master", "Weight": "Peso",
    "Min s": "Min s", "Mean s": "Media s", "Max s": "Max s", "Cooldown s": "Cooldown s", "Status": "Stato",
    "default": "predefinito", "in OBS": "in OBS", "missing in OBS": "mancante in OBS", "OBS offline": "OBS offline",
    "{0} scenes in OBS": "{0} scene in OBS",
    "OBS not connected: showing saved cameras only": "OBS non connesso: mostro solo le camere salvate",
    "Role defaults (Natural): MASTER 8–90 s (mean 36), DETAIL 5–15 s, SIDE/WIDE 7–25 s, OVERHEAD 5–18 s. "
    "Cooldown = minimum time before an insert is reused.":
        "Valori dei ruoli (Natural): MASTER 8–90 s (media 36), DETAIL 5–15 s, SIDE/WIDE 7–25 s, OVERHEAD 5–18 s. "
        "Cooldown = tempo minimo prima di riusare un inserto.",
    "Master camera": "Camera master", "Please choose a master camera (the narrative anchor).":
        "Scegli una camera master (l'ancora narrativa).",
    "Saved {0} cameras": "Salvate {0} camere",
    # ---- audio page
    "Feed the director the master output of your mixer (audio interface / USB mixer input) — not a room "
    "microphone. Capture is read-only and never alters the signal OBS records.":
        "Collega al regista l'uscita master del mixer (ingresso dell'interfaccia audio / mixer USB), non un "
        "microfono ambientale. L'acquisizione è in sola lettura e non altera mai il segnale registrato da OBS.",
    "Source": "Sorgente", "Type": "Tipo", "Device": "Dispositivo", "Rescan": "Aggiorna", "Channels": "Canali",
    "File": "File", "Browse…": "Sfoglia…", "Loop file": "Riproduci in loop", "Audio file": "File audio",
    "Audio input (interface / mixer)": "Ingresso audio (interfaccia / mixer)",
    "WASAPI loopback (what Windows plays)": "Loopback WASAPI (ciò che Windows riproduce)",
    " — unavailable": " — non disponibile", "Audio file (testing)": "File audio (test)",
    "(no devices found)": "(nessun dispositivo trovato)",
    "Analysis (advanced)": "Analisi (avanzate)", "Silence threshold": "Soglia di silenzio",
    "Musical sensitivity": "Sensibilità musicale", "BPM range min": "BPM minimo", "BPM range max": "BPM massimo",
    "Typical tempo (prior)": "Tempo tipico (prior)", "Beats per bar (heuristic)": "Beat per battuta (euristico)",
    "Set the typical tempo near your genre (e.g. 95 for hip-hop, 174 for drum & bass) to reduce half/double-time "
    "errors.": "Imposta il tempo tipico del tuo genere (es. 95 per l'hip-hop, 174 per la drum & bass) per ridurre "
               "gli errori di metà/doppio tempo.",
    "Live input": "Ingresso dal vivo", "Apply audio settings": "Applica impostazioni audio",
    "BPM range": "Intervallo BPM", "The BPM range must span at least a factor of 1.5.":
        "L'intervallo BPM deve coprire almeno un fattore 1,5.",
    "{0} · {1} Hz · {2} ch · level {3:.1f} dBFS · peak {4:.1f} dBFS · {5}signal {6} · dropouts {7} · stream errors "
    "{8} · input latency {9:.0f} ms":
        "{0} · {1} Hz · {2} canali · livello {3:.1f} dBFS · picco {4:.1f} dBFS · {5}segnale {6} · dropout {7} · "
        "errori stream {8} · latenza ingresso {9:.0f} ms",
    "present": "presente", "absent": "assente", " · analysis latency {0:.0f} ms": " · latenza analisi {0:.0f} ms",
    "Not running": "Non attivo",
    "⚠ The input delivers exact digital zeros. On Windows check Settings › Privacy & security › Microphone › "
    "'Let desktop apps access your microphone', and that the device is not muted. (USB interfaces like the UCA222 "
    "appear to Windows as a microphone.)":
        "⚠ L'ingresso restituisce zeri digitali esatti. Su Windows controlla Impostazioni › Privacy e sicurezza › "
        "Microfono › 'Consenti alle app desktop di accedere al microfono', e che il dispositivo non sia disattivato. "
        "(Le interfacce USB come la UCA222 appaiono a Windows come microfono.)",
    "No signal: check the mixer output → interface input cable and the channel selection. If the level is very "
    "low, raise the recording level in Windows Sound settings.":
        "Nessun segnale: controlla il cavo uscita mixer → ingresso interfaccia e la scelta dei canali. Se il livello "
        "è molto basso, alza il livello di registrazione nelle impostazioni audio di Windows.",
    # ---- device hints (audio.devices)
    "Line input of an audio interface: connect the mixer REC/booth/master out here.":
        "Ingresso di linea di un'interfaccia audio: collega qui l'uscita REC/booth/master del mixer.",
    "DJ hardware sound card: choose the channels carrying the master (often 1+2).":
        "Scheda audio di un hardware DJ: scegli i canali che portano il master (spesso 1+2).",
    "Probably a room microphone: it hears the speakers and the crowd, not the clean mix.":
        "Probabilmente un microfono ambientale: sente casse e pubblico, non il mix pulito.",
    "Virtual device: works if your DJ software outputs to it.":
        "Dispositivo virtuale: funziona se il software DJ esce su di esso.",
    # ---- source detection
    "Automatic detection": "Rilevamento automatico",
    "Play music on the mixer, then press Detect: every input and every speaker endpoint (WASAPI loopback) is "
    "listened to for about a second and the best source carrying the mix is suggested.":
        "Fai suonare musica sul mixer, poi premi Rileva: ogni ingresso e ogni uscita altoparlanti (loopback WASAPI) "
        "viene ascoltato per circa un secondo e viene suggerita la sorgente migliore che porta il mix.",
    "Detect audio source": "Rileva sorgente audio", "Use selected": "Usa selezionata", "Level": "Livello",
    "Activity": "Attività", "Result": "Esito", "Listening to each device…": "Ascolto di ogni dispositivo…",
    "Detection failed: {0}": "Rilevamento fallito: {0}", "Line input": "Ingresso di linea",
    "DJ hardware": "Hardware DJ", "Room mic": "Microfono ambiente", "Virtual": "Virtuale", "Other": "Altro",
    " (speaker loopback)": " (loopback altoparlanti)", "error: {0}": "errore: {0}",
    "music detected": "musica rilevata", "signal": "segnale", "silent": "silenzio",
    "Warning: it looks like a room microphone; prefer a line input if you have one.":
        "Attenzione: sembra un microfono ambientale; se possibile usa un ingresso di linea.",
    "Suggested: {0}.": "Suggerita: {0}.",
    "No device is receiving sound. Is music playing? Is the mixer output connected to the interface input? If the "
    "level is exactly zero, check Windows Settings › Privacy › Microphone (allow desktop apps).":
        "Nessun dispositivo riceve suono. La musica sta suonando? L'uscita del mixer è collegata all'ingresso "
        "dell'interfaccia? Se il livello è esattamente zero, controlla Impostazioni di Windows › Privacy › "
        "Microfono (consenti alle app desktop).",
    "No audio devices found.": "Nessun dispositivo audio trovato.",
    # ---- OBS page
    "OBS connection": "Connessione OBS",
    "Enable the server in OBS: Tools › WebSocket Server Settings (OBS 28 or newer includes obs-websocket 5). DJ "
    "Director only reads the scene list and changes the Program scene; it never creates, deletes or edits scenes, "
    "sources, filters, audio, profiles, recording or streaming.":
        "Abilita il server in OBS: Strumenti › Impostazioni server WebSocket (OBS 28 o successivo include "
        "obs-websocket 5). DJ Director legge solo l'elenco delle scene e cambia la scena Program; non crea, "
        "elimina o modifica mai scene, sorgenti, filtri, audio, profili, registrazione o streaming.",
    "WebSocket server": "Server WebSocket", "Host": "Host", "Port": "Porta", "Password": "Password",
    "stored encrypted (Windows DPAPI)": "salvata cifrata (Windows DPAPI)",
    "Connect automatically and reconnect if OBS restarts": "Connetti automaticamente e riconnetti se OBS si riavvia",
    "Test connection": "Prova connessione", "Save && connect": "Salva e connetti", "Testing…": "Prova in corso…",
    "Failed: {0}": "Non riuscito: {0}", "Connected: {0}": "Connesso: {0}", " — {0} scenes: ": " — {0} scene: ",
    "Saved. Connecting…": "Salvato. Connessione…", "Studio Mode": "Studio Mode",
    "When Studio Mode is enabled, DJ Director still switches the PROGRAM scene (what is recorded/streamed). The "
    "Preview scene is never changed. A scene change you make yourself in OBS is detected as a manual override and "
    "respected.":
        "Con lo Studio Mode attivo, DJ Director cambia comunque la scena PROGRAM (ciò che viene registrato/"
        "trasmesso). La Preview non viene mai cambiata. Un cambio scena fatto da te in OBS viene riconosciuto come "
        "intervento manuale e rispettato.",
    "State: {0}": "Stato: {0}", " · OBS {0} · websocket {1} · Studio Mode {2} · Program: {3}":
        " · OBS {0} · websocket {1} · Studio Mode {2} · Program: {3}",
    "on": "attivo", "off": "disattivo", "connected": "connesso", "connecting": "connessione",
    "disconnected": "disconnesso", "auth_failed": "password errata",
    # ---- director page
    "Choose a style. Presets are parameter sets of the same engine; Natural is recommended. Advanced settings are "
    "optional.": "Scegli uno stile. I preset sono insiemi di parametri dello stesso motore; Natural è consigliato. "
                 "Le impostazioni avanzate sono facoltative.",
    "  (recommended)": "  (consigliato)",
    "Very conservative. Long shots, few inserts, returns to the master quickly.":
        "Molto conservativo. Inquadrature lunghe, pochi inserti, ritorno rapido al master.",
    "Balanced, musical, professional. Recommended for most DJ sets.":
        "Bilanciato, musicale, professionale. Consigliato per la maggior parte dei DJ set.",
    "More variation and shorter shots, still never cutting on every beat.":
        "Più variazioni e inquadrature più brevi, senza mai tagliare a ogni beat.",
    "Master ×{0:g} · inserts ×{1:g} · ≤{2:g} cuts/min": "Master ×{0:g} · inserti ×{1:g} · ≤{2:g} tagli/min",
    "Behaviour": "Comportamento", "Respect for a while, then resume": "Rispetta per un po', poi riprendi",
    "Respect until I press Resume": "Rispetta finché non premo Riprendi",
    "Adopt the new shot and continue": "Adotta la nuova inquadratura e continua",
    "Return to master": "Torna al master", "Hold current shot": "Tieni l'inquadratura attuale",
    "Shadow mode by default (log decisions, never command OBS)":
        "Modalità Shadow come predefinita (registra le decisioni, non comanda mai OBS)",
    "Write decision log (JSONL) for every session": "Scrivi il log delle decisioni (JSONL) per ogni sessione",
    "Manual change in OBS": "Cambio manuale in OBS", "Manual override duration": "Durata dell'intervento manuale",
    "When the audio signal is lost": "Quando si perde il segnale audio",
    "Sync offset (send cut earlier by)": "Offset di sincronizzazione (anticipo del taglio)",
    "Random seed (fixed = reproducible)": "Seed casuale (fisso = riproducibile)", "random": "casuale",
    "Sync calibration": "Calibrazione della sincronizzazione",
    "Play a track with a clear kick and watch the OBS program output (or record a short test): the two cameras "
    "alternate exactly on the beat grid. Move the slider until each cut lands on the kick. Later cut → increase the "
    "offset. The value is saved when you stop.":
        "Suona un brano con una cassa evidente e guarda l'uscita Program di OBS (o registra un breve test): le due "
        "camere si alternano esattamente sulla griglia dei beat. Sposta il cursore finché ogni taglio cade sulla "
        "cassa. Taglio in ritardo → aumenta l'offset. Il valore viene salvato quando fermi.",
    "Camera A": "Camera A", "Camera B": "Camera B", "Cut": "Taglio", "every {0} beats": "ogni {0} beat",
    "Offset": "Offset", "Start calibration": "Avvia calibrazione", "Stop calibration": "Ferma calibrazione",
    "Calibration": "Calibrazione", "Choose two different cameras.": "Scegli due camere diverse.",
    "Calibration needs a connection to OBS.": "La calibrazione richiede la connessione a OBS.",
    "Advanced (overrides the preset)": "Avanzate (modificano il preset)",
    "Prefer downbeats when confident": "Preferisci i downbeat quando affidabili",
    "Cut on 4/8-bar phrase boundaries after a drop": "Taglia sui confini di frase (4/8 battute) dopo un drop",
    "Reset advanced to preset": "Ripristina i valori del preset", "Save director settings": "Salva impostazioni regia",
    "Cut threshold (higher = fewer cuts)": "Soglia di taglio (più alta = meno tagli)",
    "Master shot length ×": "Durata inquadrature master ×", "Insert shot length ×": "Durata inserti ×",
    "Shot length variation": "Variabilità della durata", "Minimum gap between cuts (s)": "Distanza minima tra tagli (s)",
    "Max cuts per minute": "Tagli massimi al minuto",
    "Max inserts before returning to master": "Inserti massimi prima di tornare al master",
    "Musical sensitivity of cuts": "Sensibilità musicale dei tagli",
    "Min confidence for music-triggered cuts": "Confidenza minima per tagli musicali",
    "Min beat confidence for beat sync": "Confidenza minima del beat per la sincronizzazione",
    "Min downbeat confidence (heuristic)": "Confidenza minima del downbeat (euristico)",
    "Max wait for beat sync (s)": "Attesa massima per la sincronizzazione (s)",
    "Hysteresis (resistance to change)": "Isteresi (resistenza al cambio)", "Variety randomness": "Casualità della varietà",
    "Max wait for a phrase boundary (s)": "Attesa massima per un confine di frase (s)",
    # ---- simulation page
    "Replay a recorded set (or a synthetic one) through the real analysis and director, faster than real time, "
    "without OBS or cameras. Compare with a fixed-timer switcher and a random switcher.":
        "Riproduci un set registrato (o sintetico) attraverso la vera analisi e il vero regista, più veloce del "
        "tempo reale, senza OBS né camere. Confronta con uno switcher a timer fisso e uno casuale.",
    "Input": "Ingresso", "Audio input": "Ingresso audio", "Synthetic DJ set": "DJ set sintetico",
    "Synthetic tempo": "Tempo sintetico", "Preset": "Preset",
    "Uses your configured cameras (or 3 demo cameras if none are configured).":
        "Usa le camere configurate (o 3 camere dimostrative se non ce ne sono).",
    "Run simulation": "Avvia simulazione", "Export JSON": "Esporta JSON", "Export CSV": "Esporta CSV",
    "Shot timeline  (marks: red = re-entry, amber = transition, blue = sparse start)":
        "Timeline delle inquadrature  (segni: rosso = ripartenza, ambra = transizione, blu = sezione ridotta)",
    "Music-aware director": "Regista musicale", "Timer (12 s)": "Timer (12 s)", "Random": "Casuale",
    "Metrics describe behaviour, not artistic quality: watch the timeline and judge.":
        "Le metriche descrivono il comportamento, non la qualità artistica: guarda la timeline e giudica.",
    "Start": "Inizio", "Length": "Durata", "Camera": "Camera", "Why": "Perché", "Recorded set": "Set registrato",
    "Simulation failed: {0}": "Simulazione fallita: {0}",
    "{0:.1f} min analysed · {1} musical events · Metrics describe behaviour, not artistic quality.":
        "{0:.1f} min analizzati · {1} eventi musicali · Le metriche descrivono il comportamento, non la qualità "
        "artistica.",
    "Run a simulation to see the shot timeline": "Avvia una simulazione per vedere la timeline",
    "Cuts per minute": "Tagli al minuto", "Time on master": "Tempo sul master", "Mean shot (s)": "Inquadratura media (s)",
    "Median shot (s)": "Inquadratura mediana (s)", "Shortest shot (s)": "Inquadratura più breve (s)",
    "Longest shot (s)": "Inquadratura più lunga (s)", "Shot length variation (CV)": "Variabilità durate (CV)",
    "A-B-A-B patterns": "Schemi A-B-A-B", "Min-length violations": "Violazioni durata minima",
    "Cooldown violations": "Violazioni cooldown", "Max consecutive inserts": "Inserti consecutivi massimi",
    "Beat-synced cuts": "Tagli sincronizzati al beat", "Beat alignment, median (ms)": "Allineamento al beat, mediana (ms)",
    # ---- log page
    "Decisions & diagnostics": "Decisioni e diagnostica",
    "Every decision with the reason the engine actually used. The full session log is saved as JSONL in the logs "
    "folder.": "Ogni decisione con il motivo effettivamente usato dal motore. Il log completo della sessione è "
               "salvato in JSONL nella cartella dei log.",
    "Latency (indicative)": "Latenza (indicativa)",
    "Capture = audio driver input latency · Analysis = time from sound to music state (includes analysis window) · "
    "Decision = engine cost per tick · Command = WebSocket round trip to OBS. Video presentation latency of your "
    "cameras is not measurable here: tune the sync offset by recording a test and checking cuts against the beat.":
        "Acquisizione = latenza d'ingresso del driver audio · Analisi = tempo dal suono allo stato musicale (include "
        "la finestra di analisi) · Decisione = costo del motore per tick · Comando = andata e ritorno WebSocket "
        "verso OBS. La latenza video delle camere qui non è misurabile: regola l'offset con la calibrazione o con "
        "una registrazione di prova.",
    "Capture {0:.0f} ms · Analysis {1:.0f} ms · DSP cost {2:.1f} ms/iteration · Decision {3:.2f} ms · Command round "
    "trip {4:.1f} ms · Sync offset {5:.0f} ms":
        "Acquisizione {0:.0f} ms · Analisi {1:.0f} ms · Costo DSP {2:.1f} ms/iterazione · Decisione {3:.2f} ms · "
        "Comando {4:.1f} ms · Offset {5:.0f} ms",
    "Open logs folder": "Apri cartella log", "Time": "Ora", "Action": "Azione", "Music state": "Stato musicale",
    "Conf.": "Conf.", "Reason": "Motivo",
    # ---- wizard
    "DJ Director AI — Setup": "DJ Director AI — Configurazione", "Welcome": "Benvenuto",
    "DJ Director follows your set and switches OBS cameras for you.":
        "DJ Director segue il tuo set e cambia le camere di OBS al posto tuo.",
    "You will: connect OBS, map your camera scenes to roles, choose the audio input carrying your mixer's master "
    "output, and pick a directing style. Nothing in OBS is created, deleted or modified — only the Program scene is "
    "switched while Auto Director is running.":
        "Ora: colleghi OBS, associ le scene delle camere ai ruoli, scegli l'ingresso audio che porta il master del "
        "mixer e uno stile di regia. In OBS non viene creato, eliminato o modificato nulla: solo la scena Program "
        "viene cambiata mentre la regia automatica è attiva.",
    "Connect OBS": "Collega OBS",
    "OBS › Tools › WebSocket Server Settings: enable the server, copy port and password.":
        "OBS › Strumenti › Impostazioni server WebSocket: abilita il server, copia porta e password.",
    "Not tested yet. You can also skip and configure OBS later.":
        "Non ancora provato. Puoi anche saltare e configurare OBS più tardi.",
    "Connected — {0}. {1} scenes found.": "Connesso — {0}. Trovate {1} scene.",
    "Tick the scenes that are camera shots and give each a role. Choose one master (the main, wide-ish shot of the "
    "DJ).": "Spunta le scene che sono inquadrature e assegna un ruolo a ciascuna. Scegli un master (l'inquadratura "
            "principale, abbastanza larga, del DJ).",
    "No scenes available (OBS not connected). You can map cameras later on the Cameras page.":
        "Nessuna scena disponibile (OBS non connesso). Potrai associare le camere più tardi nella pagina Camere.",
    "Select the input that carries the mixer master (not a room microphone).":
        "Seleziona l'ingresso che porta il master del mixer (non un microfono ambientale).",
    "WASAPI loopback": "Loopback WASAPI", "Use this input and check the signal": "Usa questo ingresso e verifica il segnale",
    "Play some music on the mixer: the meter should move.": "Fai suonare musica sul mixer: l'indicatore deve muoversi.",
    "Signal detected ✓": "Segnale rilevato ✓",
    "No signal yet ({0:.0f} dBFS). Check channels and mixer output.":
        "Ancora nessun segnale ({0:.0f} dBFS). Controlla canali e uscita del mixer.",
    "The input is digitally silent: allow desktop apps to use the microphone in Windows privacy settings (the UCA222 "
    "input counts as a microphone).":
        "L'ingresso è digitalmente muto: consenti alle app desktop di usare il microfono nelle impostazioni privacy "
        "di Windows (l'ingresso della UCA222 conta come microfono).",
    "Directing style": "Stile di regia", "You can change this any time.": "Puoi cambiarlo in qualsiasi momento.",
    "Ready": "Pronto",
    "Recommended: start with a Shadow run to see the decisions without touching OBS.":
        "Consigliato: inizia con una prova Shadow per vedere le decisioni senza toccare OBS.",
    "Use Shadow mode by default (decisions logged, OBS not commanded)":
        "Usa la modalità Shadow come predefinita (decisioni registrate, OBS non comandato)",
    "From the Dashboard: Start Auto Director to go live, Pause/Resume, Stop, or Force Master at any time. A scene "
    "change you make in OBS is always respected.":
        "Dalla Dashboard: Avvia regia automatica per andare in onda, Pausa/Riprendi, Stop o Forza Master in "
        "qualsiasi momento. Un cambio scena fatto da te in OBS viene sempre rispettato.",
    # ---- settings
    "Interface language (restart required)": "Lingua dell'interfaccia (richiede riavvio)",
    "Automatic": "Automatica",
    "< Back": "< Indietro", "Next >": "Avanti >", "Cancel": "Annulla", "Finish": "Fine",
    "File: {0}": "File: {0}", "Scene": "Scena",
}
