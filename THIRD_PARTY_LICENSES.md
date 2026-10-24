# Licenze di terze parti

DJ Director AI (codice originale) è coperto dal file `LICENSE`. La distribuzione Windows include le
seguenti componenti, ciascuna con la propria licenza. Verificare questo elenco a ogni aggiornamento delle
dipendenze (`requirements-lock.txt`).

| Componente | Uso | Licenza | Note di conformità |
|---|---|---|---|
| Python runtime (CPython) | interprete incluso dalla build | PSF License | – |
| NumPy | DSP / FFT | BSD-3-Clause | – |
| PySide6-Essentials / shiboken6 (Qt for Python) | GUI | LGPL-3.0 (o GPL / commerciale) | Usato in modalità LGPL: librerie Qt **dinamiche** e sostituibili nella cartella `_internal` (build one-folder); sorgenti Qt disponibili da qt.io / download.qt.io; nessuna modifica a Qt |
| Qt 6 (librerie incluse da PySide6) | GUI | LGPL-3.0 | come sopra |
| sounddevice | ingresso audio | MIT | – |
| PortAudio (incluso nella wheel di sounddevice) | ingresso audio | MIT-style (PortAudio License) | – |
| soundfile (python-soundfile) | lettura file audio | BSD-3-Clause | – |
| libsndfile (incluso nella wheel di soundfile) | lettura file audio | LGPL-2.1 | libreria dinamica sostituibile |
| cffi, pycparser | dipendenze di soundfile/sounddevice | MIT, BSD-3-Clause | – |
| websocket-client | client obs-websocket | Apache-2.0 | includere il testo della licenza Apache-2.0 nella distribuzione |
| PyAudioWPatch (opzionale, solo Windows) | WASAPI loopback | MIT (fork di PyAudio) | verificare la licenza della versione distribuita |
| PyInstaller (solo build) | packaging | GPL-2.0 con eccezione per il bootloader | l'eccezione consente di distribuire eseguibili con licenza qualsiasi |

## Componenti di sviluppo e test (non distribuite)

| Componente | Licenza |
|---|---|
| pytest | MIT |
| websockets (finto server OBS nei test) | BSD-3-Clause |

## Valutati e NON inclusi

| Componente | Motivo |
|---|---|
| BTrack | GPL-3.0: renderebbe GPL l'intero prodotto. Il beat tracker di DJ Director è un'implementazione originale |
| BeatNet, madmom | dipendenze pesanti (PyTorch); alcuni modelli pre-addestrati hanno licenze non commerciali |
| obsws-python | non necessario (client proprio più controllabile) |
| scipy | non necessario |

Nessun modello di machine learning è incluso nella v0.1.
