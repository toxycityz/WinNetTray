# WinNetTray 🚀

> **Monitor ultraleggero e non invasivo per la barra delle applicazioni (System Tray) di Windows.**  
> Mostra in tempo reale la velocità di rete (**Download & Upload**) e le temperature hardware (**CPU & GPU**) con cifre giganti e auto-riconoscimento NVIDIA.

[![Piattaforma](https://img.shields.io/badge/Windows-10%20%7C%2011-0078D6?logo=windows&logoColor=white)](https://github.com)
[![Download](https://img.shields.io/badge/Download-WinNetTray.exe-brightgreen?logo=windows&logoColor=white)](https://github.com/lucatosi1980/WinNetTray/releases/latest)
[![Licenza: MIT](https://img.shields.io/badge/Licenza-MIT-yellow.svg)](LICENSE)
[![Zero Invasività](https://img.shields.io/badge/Installazione-Zero%20(Portabile%20.exe)-success)](#-guida-rapida-alluso)

---

## 📥 Download Immediato

👉 **[Scarica l'ultima versione di WinNetTray.exe (Clicca qui)](https://github.com/lucatosi1980/WinNetTray/releases/latest)**

*Non richiede installazione: è un unico file eseguibile portabile pronto all'uso.*

---

## ⚡ Perché WinNetTray?

A differenza di altri programmi di monitoraggio spesso pesanti, invasivi o con finestre galleggianti sopra lo schermo:
- **100% Nativo nella System Tray**: Vive esclusivamente nell'area accanto all'orologio di Windows, senza barre o popup fluttuanti fastidiosi.
- **Cifre Giganti & Nessuna Lettera Microscopica**: Invece di rimpicciolire i numeri per farci stare lettere minuscole come "KB" o "MB", **la velocità si riconosce all'istante dal colore del testo**. Il numero è grande il doppio ed è leggibile all'istante anche da lontano.
- **Auto-Rilevamento Intelligente GPU NVIDIA**: Sui PC e laptop con doppia scheda video (integrata Intel + dedicata NVIDIA GeForce), WinNetTray rileva quando un gioco o programma 3D risveglia la scheda NVIDIA e ne mostra la temperatura con il badge `"NV"`. A riposo, evita di svegliare la GPU inutilmente, preservando la batteria.
- **Ultraleggero**: Consumo di RAM inferiore a 15 MB, impatto CPU pari a zero.

---

## 🎨 Tabella Colori Velocità (Senza Lettere K/M/B)

| Colore | Unità / Fascia di Velocità | Esempio d'Uso |
| :---: | :--- | :--- |
| ⚪ **Bianco** | **Byte/s** (< 1 KB/s) | Connessione a riposo, traffico di fondo |
| 🟢 **Verde Chiaro** | **Kbyte/s** (1 KB/s – 999 KB/s) | Navigazione web, chat, streaming audio |
| 🌸 **Rosa Scuro** | **Mbyte/s** (1 MB/s – 999 MB/s) | Download veloci, streaming video Full HD / 4K |
| 🟣 **Viola Chiaro** | **Gbyte/s** (≥ 1 GB/s) | Trasferimenti fibra ottica ultra-veloce |

*Ogni numero ha un sottile contorno di contrasto nero per risultare chiarissimo sia con il tema scuro che con il tema chiaro di Windows.*

---

## 🚀 Guida Rapida all'Uso

### 1. Primo Avvio
1. Scarica **`WinNetTray.exe`**.
2. Fai doppio click sul file: le icone compariranno subito nell'area di notifica in basso a destra (accanto all'orologio).
3. *(Opzionale)* Se Windows le nasconde dentro la freccetta `^`, trascinale semplicemente sulla barra delle applicazioni per tenerle sempre visibili.

### 2. Impostazioni & Personalizzazione
Fai **click destro con il mouse su una qualsiasi icona** di WinNetTray per aprire il menu:
- **⚙️ Impostazioni...**: attiva o disattiva gli slot (Download, Upload, Rete Unita, CPU Temp, GPU Temp), scegli tra 10 font diversi o personalizza il bordo. Le modifiche sono istantanee in tempo reale.
- **Esci**: chiude l'applicazione.

### 3. Avvio Automatico con Windows (Consigliato)
Per far partire WinNetTray da solo all'accensione del computer:
1. Premi sulla tastiera i tasti **WIN + R**.
2. Scrivi `shell:startup` e premi **Invio** (si aprirà la cartella dell'esecuzione automatica).
3. Copia il file **`WinNetTray.exe`** (o crea un collegamento) dentro questa cartella. Fatto!

---

## 🧩 Gli Slot Disponibili

Puoi combinare o attivare solo quelli che ti interessano:
1. **▼ Download**: velocità di scaricamento in tempo reale.
2. **▲ Upload**: velocità di caricamento in tempo reale.
3. **⇅ Rete Unita D/U**: visualizza sia Download che Upload uniti su due righe compatte in una sola icona.
4. **CPU Temp**: temperatura del processore (es. `CPU 58°`).
5. **GPU Temp**: temperatura della scheda video (es. `NV 64°` o `GPU 45°`).

---

## 🔒 Sicurezza e Privacy
- Nessuna connessione a internet esterna né invio di dati (nessuna telemetria).
- Nessun driver di terze parti a livello kernel: legge esclusivamente le statistiche ufficiali di Windows.
- Codice sorgente 100% aperto e verificabile.

---

## 📄 Licenza

Distribuito sotto licenza **MIT License** - vedi il file [LICENSE](LICENSE) per tutti i dettagli.  
Creato da **Luca Tosi** ([lucatosi1980@gmail.com](mailto:lucatosi1980@gmail.com)).
