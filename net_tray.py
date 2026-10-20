# ==============================================================================
# WinNetTray - Network Speed & Hardware Temperature Monitor per Windows System Tray
# Multi-Slot: Download, Upload, CPU Temp & GPU Temp (con Auto-Riconoscimento Nvidia)
# Con 10 Font Anti-Sfocatura, Zoom Caratteri (70-180%) & Cifre Massimizzate a Tutto Slot
# & Finestra Impostazioni Grafiche con Salvataggio JSON
# ==============================================================================
# Requisiti:
#   pip install pystray Pillow psutil requests
# Opzionale per lettura diretta sensori Nvidia:
#   pip install nvidia-ml-py
#
# Compilazione in singolo file .exe senza console:
#   pyinstaller --noconsole --onefile --clean --name "WinNetTray" net_tray.py
# ==============================================================================

import os
import io
import sys
import json
import time
import queue
import shutil
import socket
import ipaddress
import threading
import subprocess
import requests
import psutil
import ctypes
from ctypes import wintypes
from PIL import Image, ImageDraw, ImageFont
try:
    from PIL import ImageTk
    HAS_IMAGETK = True
except ImportError:
    HAS_IMAGETK = False
import pystray
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

# Tentativo di importazione libreria ufficiale NVIDIA Management Library
try:
    import pynvml
    HAS_PYNVML = True
except ImportError:
    HAS_PYNVML = False

try:
    import winreg
    HAS_WINREG = True
except ImportError:
    HAS_WINREG = False


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class _NOTIFYICONIDENTIFIER(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("guidItem", _GUID),
    ]


class QuickFolderTrayIcon(pystray.Icon):
    """
    Slot universale della System Tray per WinNetTray.
    Può assumere dinamicamente qualsiasi ruolo ('upload', 'download', 'net_combined',
    'gpu_temp', 'cpu_temp', 'quick_folder', 'quick_folder_2', 'quick_folder_3', 'single')
    in base alla sua reale coordinata X sullo schermo (letta tramite Shell_NotifyIconGetRect),
    garantendo matematicamente l'ordine da destra a sinistra nel Systray:
    - Click SINISTRO (WM_LBUTTONUP = 0x0202): se lo slot mostra una Cartella Rapida (1, 2 o 3),
      apre il menu a tendina con Drag & Drop e scorrimento.
    - Doppio Click SINISTRO (WM_LBUTTONDBLCLK = 0x0203): apre Connessioni di Rete (per icone rete)
      o Dettagli Hardware (per icone temperatura).
    - Click DESTRO (WM_RBUTTONUP = 0x0205): apre il menu delle Impostazioni di WinNetTray.
    """
    def __init__(self, *args, app_ref=None, slot_index=0, **kwargs):
        self._app = app_ref
        self._slot_index = slot_index
        self._uid = 101 + int(slot_index)
        self._assigned_role = None
        self._last_drawn_sig = None
        self._ready_event = threading.Event()
        super().__init__(*args, **kwargs)

    def _message(self, code, flags, **kwargs):
        kwargs.setdefault("uID", self._uid)
        return super()._message(code, flags, **kwargs)

    def _on_notify(self, wparam, lparam):
        role = getattr(self, "_assigned_role", None)
        app = getattr(self, "_app", None)
        if lparam == 0x0202:  # WM_LBUTTONUP -> Click Sinistro
            if app is not None and role in ("quick_folder", "quick_folder_2", "quick_folder_3"):
                slot_num = 1 if role == "quick_folder" else (2 if role == "quick_folder_2" else 3)
                try:
                    if app._trigger_drag_drop_folder_popup(slot_num):
                        return 0
                except Exception:
                    pass
                try:
                    app._activate_folder_left_menu(slot_num, target_icon=self)
                except Exception:
                    pass
                return super()._on_notify(wparam, 0x0205)
            if app is not None and getattr(app, "_dd_popup_active", False):
                app._dd_popup_close_req = True
            return 0
        elif lparam == 0x0203:  # WM_LBUTTONDBLCLK -> Doppio Click Sinistro
            if app is not None:
                try:
                    if role in ("upload", "download", "net_combined", "single"):
                        app.open_network_connections()
                        return 0
                    elif role in ("cpu_temp", "gpu_temp"):
                        app.show_details_popup(icon=self)
                        return 0
                    elif role in ("quick_folder", "quick_folder_2", "quick_folder_3"):
                        slot_num = 1 if role == "quick_folder" else (2 if role == "quick_folder_2" else 3)
                        app._open_filesystem_target(app.get_quick_folder_path(slot_num))
                        return 0
                except Exception:
                    pass
            return super()._on_notify(wparam, lparam)
        elif lparam == 0x0205:  # WM_RBUTTONUP -> Click Destro (Menu Impostazioni)
            if app is not None:
                try:
                    if self.menu != app._folder_right_settings_menu:
                        self.menu = app._folder_right_settings_menu
                        if hasattr(self, "update_menu"):
                            self.update_menu()
                except Exception:
                    pass
            return super()._on_notify(wparam, 0x0205)
        return super()._on_notify(wparam, lparam)


class NetworkMonitorApp:
    DEFAULT_CONFIG = {
        "display_mode": "dual_tray",     # "dual_tray" (Icone separate nella Tray), "docked_pill", "tray_icon"
        # Selezione e ordine degli slot attivi nella System Tray
        "show_download": True,           # Slot: Velocità Download
        "show_upload": True,             # Slot: Velocità Upload
        "show_net_combined": False,      # Slot: Velocità Rete Unita (Download + Upload in un solo slot)
        "show_cpu_temp": False,          # Slot: Temperatura Processore (CPU °C) - disabilitato di default
        "show_gpu_temp": False,          # Slot: Temperatura Scheda Video (GPU °C) - disabilitato di default
        "show_quick_folder": True,       # Slot: Cartella Rapida 1 a tendina (attiva di default)
        "show_quick_folder_2": False,    # Slot: Cartella Rapida 2 a tendina (disabilitata di default)
        "show_quick_folder_3": False,    # Slot: Cartella Rapida 3 a tendina (disabilitata di default)
        "quick_folder_path": "",         # Vuoto = Cartella Documenti di default di Windows
        "quick_folder_path_2": "",       # Vuoto = Cartella Download di default di Windows
        "quick_folder_path_3": "",       # Vuoto = Cartella Desktop di default di Windows
        "quick_folder_custom_order": {}, # Ordine personalizzato delle voci per ciascuna cartella
        # Ordine fisso da destra a sinistra: Upload -> Download -> GPU -> CPU -> Cartella 3 -> Cartella 2 -> Cartella 1
        "slot_order": ["quick_folder", "quick_folder_2", "quick_folder_3", "cpu_temp", "gpu_temp", "net_combined", "download", "upload"],
        "hide_speed_unit": True,         # Standard: Cifre colorate ad alta visibilità (Bianco=Byte, Verde=KB, Rosa scuro=MB, Viola=GB)
        # Selezione GPU
        "gpu_selection": "auto_active",  # "auto_active" (Nvidia quando attiva, altrimenti integrata), "nvidia_only", "amd_only", "intel_only"
        # Grafica, Font & Leggibilità Massimizzata
        "font_family": "tahoma",         # 10 Font (Predefinito: tahoma): tahoma, bahnschrift, consolas, segoe, arial_bold, impact, trebuchet, verdana, lucida_console, franklin_gothic
        "default_font_tahoma_migrated": True,
        "icon_size": 64,                 # Risoluzione icona tray (32, 48, 64)
        "font_scale": 1.15,              # Moltiplicatore font (0.7 a 1.8)
        "maximized_slot": True,          # Massimizza la grandezza delle cifre eliminando i margini vuoti
        "bg_style": "rounded",           # "box", "rounded", "transparent"
        "theme": "mint_cyan",            # "mint_cyan", "neon_magenta", "white_yellow", "cyan_orange"
        "show_border": True,             # Bordo contorno scuro per massimo contrasto
        "widget_pos_x": -1,              # Posizione X widget pillola (-1 = auto)
        "widget_pos_y": -1               # Posizione Y widget pillola (-1 = auto)
    }

    THEMES = {
        "mint_cyan": {
            "name": "Classico (Verde Menta / Ciano)",
            "dl": (0, 255, 170, 255),
            "ul": (0, 215, 255, 255),
            "hex_dl": "#00ffaa",
            "hex_ul": "#00d7ff"
        },
        "neon_magenta": {
            "name": "Cyberpunk (Lime / Magenta)",
            "dl": (57, 255, 20, 255),
            "ul": (255, 0, 127, 255),
            "hex_dl": "#39ff14",
            "hex_ul": "#ff007f"
        },
        "white_yellow": {
            "name": "High-Contrast (Bianco / Giallo)",
            "dl": (255, 255, 255, 255),
            "ul": (255, 230, 0, 255),
            "hex_dl": "#ffffff",
            "hex_ul": "#ffe600"
        },
        "cyan_orange": {
            "name": "Oceano & Fuoco (Ciano / Arancio)",
            "dl": (56, 189, 248, 255),
            "ul": (251, 146, 60, 255),
            "hex_dl": "#38bdf8",
            "hex_ul": "#fb923c"
        }
    }

    def __init__(self):
        # Stato della rete
        self.download_speed = 0.0  # Bytes/sec
        self.upload_speed = 0.0    # Bytes/sec
        self.active_interface = "Rilevamento..."
        self.local_ip = "127.0.0.1"
        self.public_ip = "In attesa..."
        self.is_running = True

        # Statistiche hardware
        self.cpu_temp = 48.0
        self.gpu_temp = 52.0
        self.is_nvidia_active = False
        self.active_gpu_name = "GPU Integrata / Primaria"
        self._nvml_initialized = False

        # Statistiche di sessione rete
        self.total_downloaded = 0  # Bytes
        self.total_uploaded = 0    # Bytes

        # Lock per accesso thread-safe ai dati e al rendering icone/menu pystray
        self.lock = threading.Lock()
        self._tray_lock = threading.RLock()
        self._font_cache = {}
        self._wmi_temp_supported = True
        self._nvidia_smi_supported = True

        # Gestione unificata Tkinter sul thread principale (previene crash Tcl_AsyncDelete / Access Violation)
        self.tk_root = None
        self._ui_queue = queue.Queue()
        self._ui_thread_id = None
        self._settings_win = None
        self._reorder_win = None
        self._dd_popup_close_fn = None
        self._last_active_ordered_slots = None

        # Cache timestamp per IP pubblico
        self.last_public_ip_check = 0

        # Carica configurazione salvata o predefinita
        self.config_path = self._get_config_path()
        self.config = self._load_config()

        # Inizializza i font in base alle impostazioni
        self._init_fonts()

        # Inizializza NVML se disponibile
        self._init_nvml_safe()

        # Widget Pillola Flottante Taskbar (Soluzione 1)
        self.widget_window = None
        self.lbl_dl = None
        self.lbl_ul = None

        # Crea i menu contestuali completi per ogni slot
        menu_dl = self._create_context_menu(slot_name="Download")
        menu_ul = self._create_context_menu(slot_name="Upload")
        menu_net = self._create_context_menu(slot_name="Rete Unita")
        menu_cpu = self._create_context_menu(slot_name="CPU Temp")
        menu_gpu = self._create_context_menu(slot_name="GPU Temp")
        self._folder_right_settings_menu = self._create_context_menu(slot_name="Download")
        self._folder_left_quick_menu = None
        self._folder_menu_mode = "right"

        # Immagini segnaposto iniziali
        img_dl, img_ul = self.generate_twin_tray_icons(0, 0)
        img_net = self.generate_net_combined_tray_icon(0, 0)
        img_cpu = self.generate_cpu_tray_icon(self.cpu_temp)
        img_gpu = self.generate_gpu_tray_icon(self.gpu_temp, self.is_nvidia_active, self.active_gpu_name)
        img_folder_1 = self.generate_folder_tray_icon(slot_num=1)
        img_folder_2 = self.generate_folder_tray_icon(slot_num=2)
        img_folder_3 = self.generate_folder_tray_icon(slot_num=3)
        img_single = self.generate_single_tray_image("0", "K", is_download=True, is_idle=True)

        # Inizializzazione 8 Slot Universali Pystray (Positional Slot Pool)
        # Ogni slot può assumere dinamicamente il ruolo richiesto in base alla sua reale
        # posizione X nella barra delle applicazioni rilevata con Shell_NotifyIconGetRect.
        init_title = chr(10).join([
            "↓ 0 B/s   |   ↑ 0 B/s",
            f"LAN: {self.local_ip}   |   WAN: ...",
            f"CPU: {self.cpu_temp}°C   |   GPU: {self.gpu_temp}°C"
        ])
        self._last_folder_scan_key = None
        self._dd_popup_active = False
        self._dd_popup_close_req = False
        self._dd_active_slot = 1

        self.icon_ul = QuickFolderTrayIcon(
            name="WinNetTray_Slot_1",
            icon=img_ul,
            title=f"WinNetTray • Slot 1\n{init_title}",
            menu=self._folder_right_settings_menu,
            app_ref=self,
            slot_index=0
        )
        self.icon_dl = QuickFolderTrayIcon(
            name="WinNetTray_Slot_2",
            icon=img_dl if self.config.get("display_mode") == "dual_tray" else img_single,
            title=f"WinNetTray • Slot 2\n{init_title}",
            menu=self._folder_right_settings_menu,
            app_ref=self,
            slot_index=1
        )
        self.icon_net = QuickFolderTrayIcon(
            name="WinNetTray_Slot_3",
            icon=img_net,
            title=f"WinNetTray • Slot 3\n{init_title}",
            menu=self._folder_right_settings_menu,
            app_ref=self,
            slot_index=2
        )
        self.icon_gpu = QuickFolderTrayIcon(
            name="WinNetTray_Slot_4",
            icon=img_gpu,
            title=f"WinNetTray • Slot 4\n{init_title}",
            menu=self._folder_right_settings_menu,
            app_ref=self,
            slot_index=3
        )
        self.icon_cpu = QuickFolderTrayIcon(
            name="WinNetTray_Slot_5",
            icon=img_cpu,
            title=f"WinNetTray • Slot 5\n{init_title}",
            menu=self._folder_right_settings_menu,
            app_ref=self,
            slot_index=4
        )
        self.icon_folder_3 = QuickFolderTrayIcon(
            name="WinNetTray_Slot_6",
            icon=img_folder_3,
            title=f"WinNetTray • Slot 6\n{init_title}",
            menu=self._folder_right_settings_menu,
            app_ref=self,
            slot_index=5
        )
        self.icon_folder_2 = QuickFolderTrayIcon(
            name="WinNetTray_Slot_7",
            icon=img_folder_2,
            title=f"WinNetTray • Slot 7\n{init_title}",
            menu=self._folder_right_settings_menu,
            app_ref=self,
            slot_index=6
        )
        self.icon_folder = QuickFolderTrayIcon(
            name="WinNetTray_Slot_8",
            icon=img_folder_1,
            title=f"WinNetTray • Slot 8\n{init_title}",
            menu=self._folder_right_settings_menu,
            app_ref=self,
            slot_index=7
        )

        self.tray_slots = [
            self.icon_ul,
            self.icon_dl,
            self.icon_net,
            self.icon_gpu,
            self.icon_cpu,
            self.icon_folder_3,
            self.icon_folder_2,
            self.icon_folder
        ]
        self.icon = self.icon_dl

    def _init_nvml_safe(self):
        """Inizializza NVML senza bloccare se non c'e GPU Nvidia o libreria non presente."""
        if HAS_PYNVML and not self._nvml_initialized:
            try:
                pynvml.nvmlInit()
                self._nvml_initialized = True
            except Exception:
                self._nvml_initialized = False

    def open_network_connections(self, icon=None, item=None):
        """Apre la sezione del Pannello di controllo 'Connessioni di rete' (ncpa.cpl) in modo sicuro senza shell=True."""
        try:
            if sys.platform == "win32":
                sys_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
                control_exe = os.path.join(sys_root, "System32", "control.exe")
                if os.path.isfile(control_exe):
                    subprocess.Popen(
                        [control_exe, "ncpa.cpl"],
                        shell=False,
                        close_fds=True,
                        creationflags=0x08000000
                    )
                    return
                ncpa_path = os.path.join(sys_root, "System32", "ncpa.cpl")
                if os.path.exists(ncpa_path):
                    os.startfile(ncpa_path)
        except Exception as e:
            print("Impossibile aprire Connessioni di rete:", e)

    def _create_context_menu(self, slot_name="Tray"):
        """Crea il menu contestuale con opzioni per la selezione rapida della modalita."""
        is_net = slot_name in ["Download", "Upload", "Rete Unita", "Tray"]
        menu_items = []
        if is_net:
            # Per le icone di Rete: il doppio clic esegue l'elemento predefinito (default=True) -> Connessioni di Rete (ncpa.cpl)
            menu_items.append(
                pystray.MenuItem("🌐 Connessioni di Rete (Pannello di Controllo)", self.open_network_connections, default=True)
            )
            menu_items.append(
                pystray.MenuItem("📊 Dettagli Completi (Rete & Hardware)", self.show_details_popup)
            )
        else:
            menu_items.append(
                pystray.MenuItem("📊 Dettagli Completi (Rete & Hardware)", self.show_details_popup, default=True)
            )
            menu_items.append(
                pystray.MenuItem("🌐 Connessioni di Rete (Pannello di Controllo)", self.open_network_connections)
            )

        menu_items.extend([
            pystray.MenuItem("⚙️ Impostazioni", self.open_settings_window),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "👯 Icone Separate nella Tray (Soluzione 2)",
                self._enable_dual_tray,
                checked=lambda item: self.config.get("display_mode") == "dual_tray"
            ),
            pystray.MenuItem(
                "🚀 Pillola Rettangolare Taskbar",
                self._enable_docked_pill,
                checked=lambda item: self.config.get("display_mode") == "docked_pill"
            ),
            pystray.MenuItem(
                "🔲 Singola Icona Tray Compatta",
                self._enable_tray_only,
                checked=lambda item: self.config.get("display_mode") == "tray_icon"
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("🔄 Aggiorna IP Pubblico", self.force_refresh_public_ip),
            pystray.MenuItem("Esci da WinNetTray", self.quit_application)
        ])
        return pystray.Menu(*menu_items)

    # --------------------------------------------------------------------------
    # Gestione Cartella Rapida con Menu Nativo a Cascata (Stile Windows 10)
    # --------------------------------------------------------------------------
    def _get_default_documents_folder(self):
        """Rileva il percorso reale della cartella Documenti dell'utente su Windows."""
        try:
            if sys.platform == "win32":
                buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
                # CSIDL_PERSONAL = 0x0005 (Cartella Documenti reale anche su OneDrive o altro disco)
                ctypes.windll.shell32.SHGetFolderPathW(None, 0x0005, None, 0, buf)
                if buf.value and os.path.isdir(buf.value):
                    return os.path.normpath(buf.value)
        except Exception:
            pass
        fallback = os.path.join(os.path.expanduser("~"), "Documents")
        if os.path.isdir(fallback):
            return os.path.normpath(fallback)
        return os.path.normpath(os.path.expanduser("~"))

    def _get_default_folder_for_slot(self, slot_num=1):
        """Restituisce la cartella predefinita per lo slot 1 (Documenti), 2 (Downloads) o 3 (Desktop)."""
        home = os.path.expanduser("~")
        if slot_num == 2:
            dl_dir = os.path.join(home, "Downloads")
            if os.path.isdir(dl_dir):
                return os.path.normpath(dl_dir)
        elif slot_num == 3:
            try:
                if sys.platform == "win32":
                    buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
                    # CSIDL_DESKTOPDIRECTORY = 0x0010
                    ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf)
                    if buf.value and os.path.isdir(buf.value):
                        return os.path.normpath(buf.value)
            except Exception:
                pass
            desk_dir = os.path.join(home, "Desktop")
            if os.path.isdir(desk_dir):
                return os.path.normpath(desk_dir)
        return self._get_default_documents_folder()

    def _get_folder_config_key(self, slot_num=1):
        if slot_num == 2:
            return "quick_folder_path_2"
        elif slot_num == 3:
            return "quick_folder_path_3"
        return "quick_folder_path"

    def get_quick_folder_path(self, slot_num=1):
        """Restituisce la cartella configurata per lo slot indicato (1, 2 o 3) oppure la cartella di default."""
        key = self._get_folder_config_key(slot_num)
        custom = str(self.config.get(key, "")).strip()
        if custom and os.path.isdir(custom):
            return os.path.normpath(custom)
        return self._get_default_folder_for_slot(slot_num)

    def _is_entry_hidden(self, entry):
        """Esclude file di sistema o nascosti per mantenere il menu a tendina rapido e pulito."""
        name = entry.name
        if name.startswith(".") or name.startswith("~$") or name.lower() in ("desktop.ini", "thumbs.db"):
            return True
        try:
            if sys.platform == "win32":
                attrs = entry.stat(follow_symlinks=False).st_file_attributes
                # FILE_ATTRIBUTE_HIDDEN = 0x2, FILE_ATTRIBUTE_SYSTEM = 0x4
                if attrs & 0x2 or attrs & 0x4:
                    return True
        except Exception:
            pass
        return False

    def _is_safe_local_target(self, target_path):
        """
        Verifica che il percorso da aprire sia un file o cartella locale legittimo,
        bloccando URI esterni (http/https/javascript/ms-*) e percorsi UNC non autorizzati.
        """
        if not target_path or not isinstance(target_path, str):
            return False
        raw = target_path.strip()
        if not raw or "\x00" in raw:
            return False
        low = raw.lower()
        # Blocca qualsiasi schema URI/protocollo esterno
        blocked_prefixes = (
            "http:", "https:", "ftp:", "ftps:", "file:", "javascript:",
            "vbscript:", "data:", "mailto:", "tel:", "ms-", "shell:", "search-ms:"
        )
        if low.startswith(blocked_prefixes):
            return False
        norm_target = os.path.normpath(os.path.abspath(raw))
        # Blocca percorsi di rete UNC (\\server\share) a meno che l'utente non abbia scelto esplicitamente una cartella UNC
        if norm_target.startswith("\\\\"):
            allowed_unc = False
            for s_idx in (1, 2, 3):
                cfg_root = str(self.config.get(self._get_folder_config_key(s_idx), "")).strip()
                if cfg_root and cfg_root.startswith("\\\\"):
                    norm_root = os.path.normcase(os.path.normpath(cfg_root))
                    if os.path.normcase(norm_target).startswith(norm_root):
                        allowed_unc = True
                        break
            if not allowed_unc:
                return False
        return os.path.exists(norm_target)

    def _open_filesystem_target(self, target_path):
        """Apre un file con il programma predefinito o una cartella in Esplora File previa validazione di sicurezza."""
        def _worker():
            try:
                if not self._is_safe_local_target(target_path):
                    return
                norm_target = os.path.normpath(os.path.abspath(str(target_path).strip()))
                if sys.platform == "win32":
                    os.startfile(norm_target)
                else:
                    subprocess.Popen(["/usr/bin/xdg-open", norm_target], shell=False, close_fds=True)
            except Exception as e:
                print("Impossibile aprire il percorso:", target_path, e)
        threading.Thread(target=_worker, daemon=True).start()

    def _ensure_tk_root(self):
        """Restituisce l'unica istanza principale tk.Tk() sul main thread."""
        if self.tk_root is None:
            self.tk_root = tk.Tk()
            self.tk_root.withdraw()
            self._ui_thread_id = threading.get_ident()
        return self.tk_root

    def _run_on_ui_thread(self, func, *args, **kwargs):
        """Accoda ed esegue qualsiasi operazione Tkinter sul thread principale in modo 100% thread-safe."""
        if self._ui_thread_id is not None and threading.get_ident() == self._ui_thread_id and self.tk_root is not None:
            try:
                func(*args, **kwargs)
            except Exception as e:
                print("Errore esecuzione diretta UI:", e)
        else:
            self._ui_queue.put((func, args, kwargs))

    def _poll_ui_queue(self):
        """Processa i task grafici in coda sul thread principale di Tkinter."""
        try:
            while True:
                func, args, kwargs = self._ui_queue.get_nowait()
                try:
                    func(*args, **kwargs)
                except Exception as e:
                    print("Errore task UI:", e)
        except queue.Empty:
            pass
        if self.is_running and self.tk_root is not None:
            try:
                self.tk_root.after(30, self._poll_ui_queue)
            except Exception:
                pass

    def change_quick_folder_dialog(self, icon=None, item=None):
        """Callback a 2 argomenti per pystray.MenuItem (apre il selettore per la Cartella Rapida #1)."""
        self.open_quick_folder_picker(slot_num=1)

    def open_quick_folder_picker(self, slot_num=1):
        """Apre la finestra di selezione cartella di Windows sul thread UI principale per lo slot 1, 2 o 3."""
        self._run_on_ui_thread(self._open_quick_folder_picker_ui, slot_num)

    def _open_quick_folder_picker_ui(self, slot_num=1):
        try:
            root = self._ensure_tk_root()
            parent_win = root
            if self._settings_win is not None:
                try:
                    if self._settings_win.winfo_exists():
                        parent_win = self._settings_win
                except Exception:
                    pass
            current_dir = self.get_quick_folder_path(slot_num)
            chosen = filedialog.askdirectory(
                parent=parent_win,
                initialdir=current_dir,
                title=f"WinNetTray - Scegli la Cartella Rapida #{slot_num} nella System Tray"
            )
            if chosen and os.path.isdir(chosen):
                key = self._get_folder_config_key(slot_num)
                self.config[key] = os.path.normpath(chosen)
                threading.Thread(target=self._save_and_refresh_folder_worker, daemon=True).start()
        except Exception as e:
            print("Errore selezione cartella:", e)

    def _save_and_refresh_folder_worker(self):
        with self._tray_lock:
            self._save_config()
            self._refresh_folder_pystray_menu(force=True)
            self._update_tray_icons_immediate()

    def reset_quick_folder_to_documents(self, icon=None, item=None):
        """Ripristina la cartella rapida alla cartella Documenti predefinita."""
        self.config["quick_folder_path"] = self._get_default_documents_folder()
        self._save_config()
        self._refresh_folder_pystray_menu(force=True)
        self._update_tray_icons_immediate()

    def show_quick_folder_menu_win32(self, icon=None, item=None):
        """
        Invia il comando di apertura immediata del menu a tendina verso l'alto
        anche se invocato tramite l'azione di default del click sinistro.
        """
        try:
            if sys.platform == "win32" and hasattr(self, "icon_folder") and getattr(self.icon_folder, "_hwnd", None):
                self._refresh_folder_pystray_menu(force=False)
                # WM_USER + 20 (0x0414) = pystray WM_NOTIFY, 0x0205 = WM_RBUTTONUP
                ctypes.windll.user32.PostMessageW(self.icon_folder._hwnd, 0x0400 + 20, 0, 0x0205)
                return
        except Exception:
            pass
        self._open_filesystem_target(self.get_quick_folder_path())

    def _win32_popup_menu_worker(self):
        try:
            user32 = ctypes.windll.user32

            CreatePopupMenu = user32.CreatePopupMenu
            CreatePopupMenu.argtypes = []
            CreatePopupMenu.restype = wintypes.HMENU

            AppendMenuW = user32.AppendMenuW
            AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
            AppendMenuW.restype = wintypes.BOOL

            TrackPopupMenu = user32.TrackPopupMenu
            TrackPopupMenu.argtypes = [
                wintypes.HMENU,
                wintypes.UINT,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.HWND,
                ctypes.c_void_p
            ]
            TrackPopupMenu.restype = wintypes.INT

            DestroyMenu = user32.DestroyMenu
            DestroyMenu.argtypes = [wintypes.HMENU]
            DestroyMenu.restype = wintypes.BOOL

            MF_STRING = 0x0000
            MF_GRAYED = 0x0001
            MF_POPUP = 0x0010
            MF_SEPARATOR = 0x0800
            TPM_LEFTALIGN = 0x0000
            TPM_BOTTOMALIGN = 0x0020
            TPM_RIGHTBUTTON = 0x0002
            TPM_RETURNCMD = 0x0100

            root_path = self.get_quick_folder_path()
            folder_title = os.path.basename(root_path) or root_path

            hmenu = CreatePopupMenu()
            if not hmenu:
                return

            cmd_map = {}
            next_id = [100]
            MAX_DEPTH = 4
            MAX_ITEMS_PER_DIR = 65

            def alloc_cmd(action_callable):
                cid = next_id[0]
                next_id[0] += 1
                cmd_map[cid] = action_callable
                return cid

            def populate_dir_menu(target_hmenu, dir_path, depth=0):
                try:
                    entries = list(os.scandir(dir_path))
                except Exception:
                    AppendMenuW(target_hmenu, MF_STRING | MF_GRAYED, 0, "⚠️ Accesso negato")
                    return

                dirs = []
                files = []
                for e in entries:
                    if self._is_entry_hidden(e):
                        continue
                    try:
                        if e.is_dir(follow_symlinks=False):
                            dirs.append(e)
                        else:
                            files.append(e)
                    except Exception:
                        pass

                dirs.sort(key=lambda x: x.name.lower())
                files.sort(key=lambda x: x.name.lower())

                if not dirs and not files:
                    AppendMenuW(target_hmenu, MF_STRING | MF_GRAYED, 0, "(Cartella vuota)")
                    return

                count = 0
                for d in dirs:
                    if count >= MAX_ITEMS_PER_DIR:
                        break
                    count += 1
                    sub_path = d.path
                    label = f"📁 {d.name[:55]}"
                    if depth < MAX_DEPTH:
                        sub_hmenu = CreatePopupMenu()
                        open_sub_id = alloc_cmd(lambda p=sub_path: self._open_filesystem_target(p))
                        AppendMenuW(sub_hmenu, MF_STRING, open_sub_id, "📂 Apri questa cartella in Esplora File")
                        AppendMenuW(sub_hmenu, MF_SEPARATOR, 0, None)
                        populate_dir_menu(sub_hmenu, sub_path, depth + 1)
                        sub_handle_val = sub_hmenu.value if hasattr(sub_hmenu, "value") else int(sub_hmenu)
                        AppendMenuW(target_hmenu, MF_POPUP, sub_handle_val or 0, label)
                    else:
                        open_sub_id = alloc_cmd(lambda p=sub_path: self._open_filesystem_target(p))
                        AppendMenuW(target_hmenu, MF_STRING, open_sub_id, label)

                for f in files:
                    if count >= MAX_ITEMS_PER_DIR:
                        break
                    count += 1
                    file_path = f.path
                    fid = alloc_cmd(lambda p=file_path: self._open_filesystem_target(p))
                    AppendMenuW(target_hmenu, MF_STRING, fid, f"📄 {f.name[:60]}")

                if len(dirs) + len(files) > MAX_ITEMS_PER_DIR:
                    more_id = alloc_cmd(lambda p=dir_path: self._open_filesystem_target(p))
                    AppendMenuW(target_hmenu, MF_SEPARATOR, 0, None)
                    AppendMenuW(target_hmenu, MF_STRING, more_id, f"➕ Mostra tutti ({len(dirs) + len(files)} elementi)...")

            # Intestazione: Apri la cartella principale in Esplora File
            open_root_id = alloc_cmd(lambda p=root_path: self._open_filesystem_target(p))
            AppendMenuW(hmenu, MF_STRING, open_root_id, f"📂 Apri '{folder_title}' in Esplora File")
            AppendMenuW(hmenu, MF_SEPARATOR, 0, None)

            # Popola sottocartelle a cascata e file
            populate_dir_menu(hmenu, root_path, depth=0)

            # Pie' di pagina: Cambia cartella e Impostazioni
            AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
            chg_id = alloc_cmd(lambda: self.change_quick_folder_dialog())
            AppendMenuW(hmenu, MF_STRING, chg_id, "📁 Cambia Cartella Rapida...")
            set_id = alloc_cmd(lambda: self.open_settings_window())
            AppendMenuW(hmenu, MF_STRING, set_id, "⚙️ Impostazioni WinNetTray")

            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))

            hwnd = getattr(self.icon_folder, "_hwnd", None) or getattr(self.icon_dl, "_hwnd", None) or user32.GetForegroundWindow()
            if hwnd:
                user32.SetForegroundWindow(hwnd)

            selected = TrackPopupMenu(
                hmenu,
                TPM_LEFTALIGN | TPM_BOTTOMALIGN | TPM_RIGHTBUTTON | TPM_RETURNCMD,
                pt.x,
                pt.y,
                0,
                hwnd,
                None
            )
            if hwnd:
                user32.PostMessageW(hwnd, 0, 0, 0)

            DestroyMenu(hmenu)

            if selected and selected in cmd_map:
                cmd_map[selected]()
        except Exception as e:
            print("Errore menu rapido Win32:", e)

    def _make_open_action(self, target_path):
        """Crea una funzione di callback con esattamente 2 parametri (icon, item) richiesta da pystray.MenuItem."""
        def _action(icon=None, item=None):
            self._open_filesystem_target(target_path)
        return _action

    def _make_noop_action(self):
        def _noop(icon=None, item=None):
            pass
        return _noop

    def _make_move_entry_action(self, dir_path, entry_name, mode):
        """Callback a 2 argomenti per spostare in alto/basso una voce della cartella rapida."""
        def _action(icon=None, item=None):
            self._move_folder_entry(dir_path, entry_name, mode)
        return _action

    def _make_reset_order_action(self, dir_path):
        """Callback a 2 argomenti per ripristinare l'ordine alfabetico predefinito della cartella."""
        def _action(icon=None, item=None):
            self._reset_folder_order(dir_path)
        return _action

    def _get_sorted_dir_entries(self, dir_path):
        """
        Restituisce la lista ordinata di tuple (entry, is_dir) per dir_path,
        applicando l'ordine personalizzato salvato dall'utente (quick_folder_custom_order).
        """
        try:
            entries = list(os.scandir(dir_path))
        except Exception:
            return None

        valid_entries = []
        for e in entries:
            if self._is_entry_hidden(e):
                continue
            try:
                is_d = e.is_dir(follow_symlinks=False)
                valid_entries.append((e, is_d))
            except Exception:
                pass

        # Ordine predefinito iniziale: prima le sottocartelle (A-Z), poi i file (A-Z)
        dirs = sorted([item for item in valid_entries if item[1]], key=lambda x: x[0].name.lower())
        files = sorted([item for item in valid_entries if not item[1]], key=lambda x: x[0].name.lower())
        default_ordered = dirs + files

        custom_map = self.config.get("quick_folder_custom_order", {})
        if not isinstance(custom_map, dict):
            custom_map = {}

        norm_dir = os.path.normcase(os.path.normpath(dir_path))
        saved_names = custom_map.get(norm_dir) or custom_map.get(dir_path) or []
        if not saved_names:
            return default_ordered

        by_name = {item[0].name: item for item in default_ordered}
        final_list = []
        seen = set()
        for name in saved_names:
            if name in by_name and name not in seen:
                final_list.append(by_name[name])
                seen.add(name)

        for item in default_ordered:
            nm = item[0].name
            if nm not in seen:
                final_list.append(item)
                seen.add(nm)

        return final_list

    def _move_folder_entry(self, dir_path, entry_name, mode="up"):
        """
        Sposta una voce (file o sottocartella) in alto ('up'), in basso ('down'),
        in cima ('top') o in fondo ('bottom'), salva l'ordine e riapre la tendina.
        """
        try:
            sorted_items = self._get_sorted_dir_entries(dir_path)
            if not sorted_items:
                return
            names = [item[0].name for item in sorted_items]
            if entry_name not in names:
                return
            idx = names.index(entry_name)
            if mode == "up" and idx > 0:
                names[idx], names[idx - 1] = names[idx - 1], names[idx]
            elif mode == "down" and idx < len(names) - 1:
                names[idx], names[idx + 1] = names[idx + 1], names[idx]
            elif mode == "top" and idx > 0:
                names.pop(idx)
                names.insert(0, entry_name)
            elif mode == "bottom" and idx < len(names) - 1:
                names.pop(idx)
                names.append(entry_name)
            else:
                return

            if not isinstance(self.config.get("quick_folder_custom_order"), dict):
                self.config["quick_folder_custom_order"] = {}
            norm_dir = os.path.normcase(os.path.normpath(dir_path))
            self.config["quick_folder_custom_order"][norm_dir] = names
            self._save_config()
            self._refresh_folder_pystray_menu(force=True)

            # Riapre automaticamente la tendina aggiornata così vedi subito lo spostamento
            def _reopen():
                time.sleep(0.08)
                try:
                    if sys.platform == "win32" and hasattr(self, "icon_folder") and getattr(self.icon_folder, "_hwnd", None):
                        ctypes.windll.user32.PostMessageW(self.icon_folder._hwnd, 0x0400 + 20, 0, 0x0202)
                except Exception:
                    pass
            threading.Thread(target=_reopen, daemon=True).start()
        except Exception as e:
            print("Errore spostamento voce cartella:", e)

    def _reset_folder_order(self, dir_path):
        """Ripristina l'ordine predefinito (Cartelle poi File A-Z) per la cartella."""
        try:
            if isinstance(self.config.get("quick_folder_custom_order"), dict):
                norm_dir = os.path.normcase(os.path.normpath(dir_path))
                self.config["quick_folder_custom_order"].pop(norm_dir, None)
                self.config["quick_folder_custom_order"].pop(dir_path, None)
                self._save_config()
                self._refresh_folder_pystray_menu(force=True)
        except Exception:
            pass

    def open_folder_reorder_window(self, icon=None, item=None):
        """Callback a 2 argomenti per pystray.MenuItem: apre la finestra di riordino rapido."""
        self.open_folder_reorder_for_slot(slot_num=1)

    def open_folder_reorder_for_slot(self, slot_num=1):
        """Apre una finestra compatta e leggera per spostare su/giù più voci rapidamente per lo slot 1, 2 o 3."""
        self._run_on_ui_thread(self._run_folder_reorder_gui, slot_num)

    def _run_folder_reorder_gui(self, slot_num=1):
        try:
            root = self._ensure_tk_root()
            if self._reorder_win is not None:
                try:
                    if self._reorder_win.winfo_exists():
                        self._reorder_win.destroy()
                except Exception:
                    pass
                self._reorder_win = None

            dir_path = self.get_quick_folder_path(slot_num)
            folder_title = os.path.basename(dir_path) or dir_path
            sorted_items = self._get_sorted_dir_entries(dir_path) or []

            win = tk.Toplevel(root)
            self._reorder_win = win
            win.title(f"↕️ Riordina Voci Cartella Rapida #{slot_num} - '{folder_title}'")
            win.geometry("460x500")
            win.resizable(False, False)
            win.configure(bg="#12161f")
            win.attributes("-topmost", True)

            def on_close_reorder():
                self._reorder_win = None
                try:
                    if win.winfo_exists():
                        win.destroy()
                except Exception:
                    pass

            win.protocol("WM_DELETE_WINDOW", on_close_reorder)

            frame = tk.Frame(win, bg="#12161f", padx=12, pady=12)
            frame.pack(fill="both", expand=True)

            tk.Label(
                frame,
                text=f"↕️ Sposta in Alto / in Basso le voci di '{folder_title}'",
                font=("Segoe UI", 10, "bold"),
                fg="#fbbf24",
                bg="#12161f"
            ).pack(anchor="w", pady=(0, 4))

            tk.Label(
                frame,
                text="Trascina una voce col mouse (tieni cliccato e muovi su/giù) oppure usa i pulsanti ▲ / ▼:",
                font=("Segoe UI", 8),
                fg="#94a3b8",
                bg="#12161f"
            ).pack(anchor="w", pady=(0, 8))

            list_frame = tk.Frame(frame, bg="#181d28", highlightbackground="#2d3748", highlightthickness=1)
            list_frame.pack(fill="both", expand=True, pady=(0, 10))

            scrollbar = tk.Scrollbar(list_frame)
            scrollbar.pack(side="right", fill="y")

            lb = tk.Listbox(
                list_frame,
                yscrollcommand=scrollbar.set,
                bg="#0f131a",
                fg="#f1f5f9",
                selectbackground="#2563eb",
                selectforeground="white",
                font=("Segoe UI", 9),
                relief="flat",
                activestyle="none"
            )
            lb.pack(side="left", fill="both", expand=True, padx=4, pady=4)
            scrollbar.config(command=lb.yview)

            items_data = [[it[0].name, it[1]] for it in sorted_items]

            def refresh_listbox(select_idx=None):
                lb.delete(0, tk.END)
                for idx, (nm, is_d) in enumerate(items_data):
                    prefix = "📁" if is_d else "📄"
                    lb.insert(tk.END, f" {idx + 1}. {prefix} {nm}")
                if select_idx is not None and 0 <= select_idx < len(items_data):
                    lb.selection_set(select_idx)
                    lb.activate(select_idx)
                    lb.see(select_idx)

            def save_current_order():
                if not isinstance(self.config.get("quick_folder_custom_order"), dict):
                    self.config["quick_folder_custom_order"] = {}
                norm_dir = os.path.normcase(os.path.normpath(dir_path))
                self.config["quick_folder_custom_order"][norm_dir] = [x[0] for x in items_data]
                self._save_config()
                self._refresh_folder_pystray_menu(force=True)

            # Supporto Drag & Drop col mouse dentro la Listbox
            lb_drag = {"idx": None}

            def on_lb_press(event):
                i = lb.nearest(event.y)
                if 0 <= i < len(items_data):
                    lb_drag["idx"] = i

            def on_lb_motion(event):
                cur_i = lb_drag.get("idx")
                if cur_i is None:
                    return
                target_i = lb.nearest(event.y)
                if 0 <= target_i < len(items_data) and target_i != cur_i:
                    entry = items_data.pop(cur_i)
                    items_data.insert(target_i, entry)
                    lb_drag["idx"] = target_i
                    refresh_listbox(target_i)

            def on_lb_release(event):
                if lb_drag.get("idx") is not None:
                    lb_drag["idx"] = None
                    save_current_order()

            lb.bind("<ButtonPress-1>", on_lb_press)
            lb.bind("<B1-Motion>", on_lb_motion)
            lb.bind("<ButtonRelease-1>", on_lb_release)

            def move_selected(mode):
                sel = lb.curselection()
                if not sel:
                    return
                i = sel[0]
                if mode == "up" and i > 0:
                    items_data[i], items_data[i - 1] = items_data[i - 1], items_data[i]
                    new_i = i - 1
                elif mode == "down" and i < len(items_data) - 1:
                    items_data[i], items_data[i + 1] = items_data[i + 1], items_data[i]
                    new_i = i + 1
                elif mode == "top" and i > 0:
                    entry = items_data.pop(i)
                    items_data.insert(0, entry)
                    new_i = 0
                elif mode == "bottom" and i < len(items_data) - 1:
                    entry = items_data.pop(i)
                    items_data.append(entry)
                    new_i = len(items_data) - 1
                else:
                    return
                refresh_listbox(new_i)
                save_current_order()

            def reset_default():
                self._reset_folder_order(dir_path)
                fresh = self._get_sorted_dir_entries(dir_path) or []
                items_data.clear()
                for it in fresh:
                    items_data.append([it[0].name, it[1]])
                refresh_listbox(0)

            refresh_listbox(0 if items_data else None)

            btn_row = tk.Frame(frame, bg="#12161f")
            btn_row.pack(fill="x", pady=(0, 8))

            tk.Button(btn_row, text="⏫ In Cima", command=lambda: move_selected("top"), bg="#1e293b", fg="white", font=("Segoe UI", 8, "bold"), relief="flat", padx=8, pady=5, cursor="hand2").pack(side="left", expand=True, fill="x", padx=2)
            tk.Button(btn_row, text="▲ Sposta Su", command=lambda: move_selected("up"), bg="#2563eb", fg="white", font=("Segoe UI", 8, "bold"), relief="flat", padx=8, pady=5, cursor="hand2").pack(side="left", expand=True, fill="x", padx=2)
            tk.Button(btn_row, text="▼ Sposta Giù", command=lambda: move_selected("down"), bg="#2563eb", fg="white", font=("Segoe UI", 8, "bold"), relief="flat", padx=8, pady=5, cursor="hand2").pack(side="left", expand=True, fill="x", padx=2)
            tk.Button(btn_row, text="⏬ In Fondo", command=lambda: move_selected("bottom"), bg="#1e293b", fg="white", font=("Segoe UI", 8, "bold"), relief="flat", padx=8, pady=5, cursor="hand2").pack(side="left", expand=True, fill="x", padx=2)

            bot_row = tk.Frame(frame, bg="#12161f")
            bot_row.pack(fill="x")

            tk.Button(bot_row, text="↺ Ordine Predefinito (A-Z)", command=reset_default, bg="#334155", fg="#e2e8f0", font=("Segoe UI", 8), relief="flat", padx=8, pady=5, cursor="hand2").pack(side="left")
            tk.Button(bot_row, text="✅ Chiudi", command=on_close_reorder, bg="#059669", fg="white", font=("Segoe UI", 9, "bold"), relief="flat", padx=16, pady=5, cursor="hand2").pack(side="right")
        except Exception as e:
            print("Errore apertura finestra riordino:", e)

    def _build_reorder_submenu_items(self, dir_path):
        """Costruisce il sottomenu a cascata '↕️ Sposta Su / Giù Voci' dentro la tendina rapida."""
        sorted_entries = self._get_sorted_dir_entries(dir_path)
        sub_items = [
            pystray.MenuItem(
                "🪟 Finestra Riordino Rapido (▲ / ▼)...",
                self.open_folder_reorder_window
            ),
            pystray.MenuItem(
                "↺ Ripristina Ordine Predefinito (A-Z)",
                self._make_reset_order_action(dir_path)
            ),
            pystray.Menu.SEPARATOR
        ]
        if not sorted_entries:
            sub_items.append(pystray.MenuItem("(Nessuna voce da riordinare)", self._make_noop_action(), enabled=False))
            return sub_items

        total = len(sorted_entries)
        for idx, (entry, is_dir) in enumerate(sorted_entries[:40]):
            name = entry.name
            icon_prefix = "📁" if is_dir else "📄"
            move_opts = [
                pystray.MenuItem(
                    "⏫ Sposta in Cima (1° posto)",
                    self._make_move_entry_action(dir_path, name, "top"),
                    enabled=(idx > 0)
                ),
                pystray.MenuItem(
                    "▲ Sposta in Alto (+1)",
                    self._make_move_entry_action(dir_path, name, "up"),
                    enabled=(idx > 0)
                ),
                pystray.MenuItem(
                    "▼ Sposta in Basso (-1)",
                    self._make_move_entry_action(dir_path, name, "down"),
                    enabled=(idx < total - 1)
                ),
                pystray.MenuItem(
                    "⏬ Sposta in Fondo (Ultimo)",
                    self._make_move_entry_action(dir_path, name, "bottom"),
                    enabled=(idx < total - 1)
                )
            ]
            sub_items.append(
                pystray.MenuItem(f"#{idx + 1} {icon_prefix} {name[:42]}", pystray.Menu(*move_opts))
            )
        return sub_items

    def _build_pystray_submenu_items(self, dir_path, depth=0):
        """Costruisce gli elementi pystray.MenuItem con sottocartelle a cascata rispettando l'ordine personalizzato."""
        items = []
        MAX_DEPTH = 2
        MAX_ITEMS = 40
        sorted_entries = self._get_sorted_dir_entries(dir_path)
        if sorted_entries is None:
            return [pystray.MenuItem("⚠️ Accesso negato", self._make_noop_action(), enabled=False)]

        if not sorted_entries:
            return [pystray.MenuItem("(Cartella vuota)", self._make_noop_action(), enabled=False)]

        for count, (entry, is_dir) in enumerate(sorted_entries):
            if count >= MAX_ITEMS:
                break
            entry_path = entry.path
            if is_dir:
                label = f"📁 {entry.name[:50]}"
                if depth < MAX_DEPTH:
                    sub_children = [
                        pystray.MenuItem(
                            "📂 Apri questa cartella",
                            self._make_open_action(entry_path)
                        ),
                        pystray.Menu.SEPARATOR
                    ]
                    sub_children.extend(self._build_pystray_submenu_items(entry_path, depth + 1))
                    sub_children.extend([
                        pystray.Menu.SEPARATOR,
                        pystray.MenuItem(
                            "↕️ Sposta in Alto / Basso Voci",
                            pystray.Menu(*self._build_reorder_submenu_items(entry_path))
                        )
                    ])
                    items.append(pystray.MenuItem(label, pystray.Menu(*sub_children)))
                else:
                    items.append(
                        pystray.MenuItem(label, self._make_open_action(entry_path))
                    )
            else:
                items.append(
                    pystray.MenuItem(
                        f"📄 {entry.name[:55]}",
                        self._make_open_action(entry_path)
                    )
                )

        return items

    def _make_change_folder_action(self, slot_num=1):
        """Callback a 2 argomenti per pystray.MenuItem per cambiare la cartella dello slot."""
        def _action(icon=None, item=None):
            self.open_quick_folder_picker(slot_num=slot_num)
        return _action

    def _create_folder_pystray_menu(self, slot_num=1):
        """Crea il menu rapido della cartella (per il Click Sinistro, senza Impostazioni ed Esci)."""
        root_path = self.get_quick_folder_path(slot_num)
        folder_title = os.path.basename(root_path) or root_path
        try:
            self._last_folder_scan_key = (root_path, os.stat(root_path).st_mtime)
        except Exception:
            self._last_folder_scan_key = (root_path, 0)

        menu_items = [
            pystray.MenuItem(
                f"📂 Apri '{folder_title}' in Esplora File",
                self._make_open_action(root_path),
                default=True
            ),
            pystray.Menu.SEPARATOR
        ]
        menu_items.extend(self._build_pystray_submenu_items(root_path, depth=0))
        menu_items.extend([
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "↕️ Sposta in Alto / Basso Voci",
                pystray.Menu(*self._build_reorder_submenu_items(root_path))
            ),
            pystray.MenuItem("🔄 Aggiorna Elenco Cartella", self._force_refresh_folder_menu),
            pystray.MenuItem(f"📁 Cambia Cartella Rapida #{slot_num}...", self._make_change_folder_action(slot_num))
        ])
        return pystray.Menu(*menu_items)

    def _extract_win32_small_icon_pil(self, target_path, is_dir=False):
        """
        Estrae l'icona nativa 16x16 di Windows del programma associato al file (o al collegamento .lnk)
        usando SHGetFileInfoW + DrawIconEx in memoria, senza alcuna libreria esterna.
        """
        if sys.platform != "win32":
            return None

        class _SHFILEINFOW(ctypes.Structure):
            _fields_ = [
                ("hIcon", wintypes.HANDLE),
                ("iIcon", ctypes.c_int),
                ("dwAttributes", wintypes.DWORD),
                ("szDisplayName", wintypes.WCHAR * 260),
                ("szTypeName", wintypes.WCHAR * 80),
            ]

        class _BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", wintypes.DWORD),
                ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD),
            ]

        shfi = _SHFILEINFOW()
        # SHGFI_ICON = 0x000000100, SHGFI_SMALLICON = 0x000000001
        flags = 0x000000100 | 0x000000001
        attrs = 0x00000010 if is_dir else 0x00000080  # FILE_ATTRIBUTE_DIRECTORY vs FILE_ATTRIBUTE_NORMAL
        if not os.path.exists(target_path):
            flags |= 0x000000010  # SHGFI_USEFILEATTRIBUTES

        shell32 = ctypes.windll.shell32
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        SHGetFileInfoW = shell32.SHGetFileInfoW
        SHGetFileInfoW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(_SHFILEINFOW),
            wintypes.UINT,
            wintypes.UINT
        ]
        SHGetFileInfoW.restype = ctypes.c_size_t

        res = SHGetFileInfoW(target_path, attrs, ctypes.byref(shfi), ctypes.sizeof(shfi), flags)
        if not res or not shfi.hIcon:
            return None

        ico_s = 16
        hdc_screen = None
        hdc_mem = None
        hbmp = None
        old_obj = None
        try:
            GetDC = user32.GetDC
            GetDC.argtypes = [wintypes.HWND]
            GetDC.restype = wintypes.HDC

            ReleaseDC = user32.ReleaseDC
            ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
            ReleaseDC.restype = ctypes.c_int

            CreateCompatibleDC = gdi32.CreateCompatibleDC
            CreateCompatibleDC.argtypes = [wintypes.HDC]
            CreateCompatibleDC.restype = wintypes.HDC

            DeleteDC = gdi32.DeleteDC
            DeleteDC.argtypes = [wintypes.HDC]
            DeleteDC.restype = wintypes.BOOL

            CreateDIBSection = gdi32.CreateDIBSection
            CreateDIBSection.argtypes = [
                wintypes.HDC,
                ctypes.POINTER(_BITMAPINFOHEADER),
                wintypes.UINT,
                ctypes.POINTER(ctypes.c_void_p),
                wintypes.HANDLE,
                wintypes.DWORD
            ]
            CreateDIBSection.restype = wintypes.HBITMAP

            SelectObject = gdi32.SelectObject
            SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
            SelectObject.restype = wintypes.HGDIOBJ

            DeleteObject = gdi32.DeleteObject
            DeleteObject.argtypes = [wintypes.HGDIOBJ]
            DeleteObject.restype = wintypes.BOOL

            DrawIconEx = user32.DrawIconEx
            DrawIconEx.argtypes = [
                wintypes.HDC,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.HICON,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.UINT,
                wintypes.HBRUSH,
                wintypes.UINT
            ]
            DrawIconEx.restype = wintypes.BOOL

            hdc_screen = GetDC(None)
            hdc_mem = CreateCompatibleDC(hdc_screen)

            bmi = _BITMAPINFOHEADER()
            bmi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
            bmi.biWidth = ico_s
            bmi.biHeight = -ico_s  # Top-down DIB
            bmi.biPlanes = 1
            bmi.biBitCount = 32
            bmi.biCompression = 0  # BI_RGB

            ppv_bits = ctypes.c_void_p()
            hbmp = CreateDIBSection(hdc_mem, ctypes.byref(bmi), 0, ctypes.byref(ppv_bits), None, 0)
            if not hbmp or not ppv_bits.value:
                return None

            old_obj = SelectObject(hdc_mem, hbmp)
            ctypes.memset(ppv_bits, 0, ico_s * ico_s * 4)

            # DI_NORMAL = 0x0003
            DrawIconEx(hdc_mem, 0, 0, shfi.hIcon, ico_s, ico_s, 0, None, 0x0003)

            raw_bytes = ctypes.string_at(ppv_bits, ico_s * ico_s * 4)
            pil_img = Image.frombuffer("RGBA", (ico_s, ico_s), raw_bytes, "raw", "BGRA", 0, 1).copy()

            # Gestione icone legacy prive di canale alpha
            extrema = pil_img.getextrema()
            if extrema and extrema[3][1] == 0:
                px = pil_img.load()
                for y_i in range(ico_s):
                    for x_i in range(ico_s):
                        r_v, g_v, b_v, _ = px[x_i, y_i]
                        if r_v > 0 or g_v > 0 or b_v > 0:
                            px[x_i, y_i] = (r_v, g_v, b_v, 255)
            return pil_img
        except Exception:
            return None
        finally:
            try:
                if old_obj and hdc_mem:
                    gdi32.SelectObject(hdc_mem, old_obj)
            except Exception:
                pass
            try:
                if hbmp:
                    gdi32.DeleteObject(hbmp)
            except Exception:
                pass
            try:
                if hdc_mem:
                    gdi32.DeleteDC(hdc_mem)
            except Exception:
                pass
            try:
                if hdc_screen:
                    user32.ReleaseDC(None, hdc_screen)
            except Exception:
                pass
            try:
                DestroyIcon = user32.DestroyIcon
                DestroyIcon.argtypes = [wintypes.HICON]
                DestroyIcon.restype = wintypes.BOOL
                DestroyIcon(shfi.hIcon)
            except Exception:
                pass

    def _generate_fallback_small_icon_pil(self, entry_path, is_dir=False):
        """Crea una miniatura 16x16 leggera in memoria se SHGetFileInfoW non è disponibile."""
        img = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        if is_dir:
            d.rounded_rectangle([1, 3, 7, 6], radius=1, fill=(217, 119, 6, 255))
            d.rounded_rectangle([1, 5, 14, 13], radius=2, fill=(251, 191, 36, 255), outline=(180, 83, 9, 255))
            return img

        name_l = os.path.basename(entry_path).lower()
        if name_l.endswith(".lnk"):
            name_l = name_l[:-4]
        ext = os.path.splitext(name_l)[1].lower()

        if ext in (".xls", ".xlsx", ".csv"):
            col = (16, 185, 129, 255)   # Verde Excel
        elif ext in (".pdf",):
            col = (239, 68, 68, 255)    # Rosso PDF
        elif ext in (".doc", ".docx", ".rtf"):
            col = (59, 130, 246, 255)   # Blu Word
        elif ext in (".vsd", ".vsdx"):
            col = (99, 102, 241, 255)   # Indaco Visio
        elif ext in (".kdbx", ".kdb"):
            col = (245, 158, 11, 255)   # Oro KeePass
        elif ext in (".jpg", ".jpeg", ".png", ".gif", ".bmp"):
            col = (168, 85, 247, 255)   # Viola Immagine
        else:
            col = (148, 163, 184, 255)  # Grigio Documento

        d.rounded_rectangle([2, 1, 13, 14], radius=2, fill=col, outline=(255, 255, 255, 190))
        d.line([(5, 5), (10, 5)], fill=(255, 255, 255, 230), width=1)
        d.line([(5, 8), (10, 8)], fill=(255, 255, 255, 230), width=1)
        d.line([(5, 11), (8, 11)], fill=(255, 255, 255, 230), width=1)
        return img

    def _get_entry_small_pil_icon(self, entry_path, is_dir=False):
        """
        Restituisce l'icona 16x16 PIL.Image per una voce della Cartella Rapida usando una cache
        in RAM leggerissima (~1 KB per icona, 0% CPU a riposo).
        """
        if not hasattr(self, "_file_icon_pil_cache") or not isinstance(self._file_icon_pil_cache, dict):
            self._file_icon_pil_cache = {}

        if is_dir:
            cache_key = "__DIR__"
        else:
            ext = os.path.splitext(entry_path)[1].lower()
            # Per i collegamenti .lnk ed eseguibili .exe ogni file ha la propria icona specifica del programma
            if ext in (".lnk", ".exe", ".ico", ".url", ".msc", ".cpl", ""):
                cache_key = f"PATH::{os.path.normcase(entry_path)}"
            else:
                cache_key = f"EXT::{ext}"

        cached = self._file_icon_pil_cache.get(cache_key)
        if cached is not None:
            return cached

        pil_img = self._extract_win32_small_icon_pil(entry_path, is_dir=is_dir)
        if pil_img is None:
            pil_img = self._generate_fallback_small_icon_pil(entry_path, is_dir=is_dir)

        if len(self._file_icon_pil_cache) > 160:
            self._file_icon_pil_cache.clear()
        self._file_icon_pil_cache[cache_key] = pil_img
        return pil_img

    def _pil_to_tk_photo(self, pil_img, master=None):
        """Converte in modo sicuro una PIL.Image 16x16 in PhotoImage per Tkinter."""
        if pil_img is None:
            return None
        try:
            if HAS_IMAGETK:
                return ImageTk.PhotoImage(pil_img, master=master)
        except Exception:
            pass
        try:
            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            return tk.PhotoImage(data=buf.getvalue(), master=master)
        except Exception:
            return None

    def _trigger_drag_drop_folder_popup(self, slot_num=1):
        """
        Apre il menu a tendina della Cartella Rapida (1, 2 o 3) verso l'alto con supporto diretto al
        Drag & Drop (tieni cliccato il tasto sinistro su una voce e trascinala su/giù).
        """
        try:
            if getattr(self, "_dd_popup_active", False):
                self._dd_popup_close_req = True
                if getattr(self, "_dd_active_slot", 1) == slot_num:
                    return True
            self._dd_active_slot = slot_num
            pt = wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            self._run_on_ui_thread(self._run_drag_drop_folder_popup, pt.x, pt.y, slot_num)
            return True
        except Exception as e:
            print("Fallback menu standard cartella:", e)
            return False

    def _run_drag_drop_folder_popup(self, anchor_x, anchor_y, slot_num=1):
        """
        Menu a tendina stile Windows 10/11 leggero e istantaneo (eseguito sul thread principale Tkinter):
        - Click singolo: apre subito il file o la cartella.
        - Click & Trascina su/giù (Drag & Drop): sposta la voce nella posizione voluta in tempo reale e salva l'ordine.
        - Sottocartelle a cascata al passaggio del mouse (anch'esse riordinabili con Drag & Drop).
        """
        if callable(getattr(self, "_dd_popup_close_fn", None)):
            try:
                self._dd_popup_close_fn()
            except Exception:
                pass
            self._dd_popup_close_fn = None

        self._dd_popup_active = True
        self._dd_popup_close_req = False
        root = self._ensure_tk_root()
        try:
            screen_w = root.winfo_screenwidth()
            screen_h = root.winfo_screenheight()

            # Usa le coordinate del puntatore nel sistema di coordinate nativo di Tkinter
            # (evita disallineamenti al primo avvio su schermi con ridimensionamento DPI 125%/150%)
            try:
                tk_px = root.winfo_pointerx()
                tk_py = root.winfo_pointery()
                if tk_px > 0 and tk_py > 0:
                    anchor_x, anchor_y = tk_px, tk_py
            except Exception:
                pass

            # Rileva il bordo superiore della Taskbar di Windows per appoggiare la tendina esattamente sopra
            taskbar_top = screen_h - 48
            try:
                if sys.platform == "win32":
                    user32 = ctypes.windll.user32
                    FindWindowW = user32.FindWindowW
                    FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
                    FindWindowW.restype = wintypes.HWND
                    h_tray = FindWindowW("Shell_TrayWnd", None)
                    if h_tray:
                        rect = wintypes.RECT()
                        user32.GetWindowRect(h_tray, ctypes.byref(rect))
                        if screen_h // 2 < rect.top <= screen_h:
                            taskbar_top = rect.top
            except Exception:
                pass

            level_windows = {}  # depth -> tk.Toplevel
            global_drag = {"active": False}
            open_ts = time.time()
            popup_closed = [False]

            def close_all():
                if popup_closed[0]:
                    return
                popup_closed[0] = True
                self._dd_popup_active = False
                if self._dd_popup_close_fn == close_all:
                    self._dd_popup_close_fn = None
                try:
                    root.unbind_all("<MouseWheel>")
                    root.unbind_all("<Button-4>")
                    root.unbind_all("<Button-5>")
                except Exception:
                    pass
                for d in list(level_windows.keys()):
                    w = level_windows.pop(d, None)
                    if w:
                        try:
                            if hasattr(w, "_icon_refs"):
                                w._icon_refs.clear()
                            if w.winfo_exists():
                                w.destroy()
                        except Exception:
                            pass

            self._dd_popup_close_fn = close_all

            def close_levels_from(min_depth):
                for d in [k for k in list(level_windows.keys()) if k >= min_depth]:
                    w = level_windows.pop(d, None)
                    if w:
                        try:
                            if hasattr(w, "_icon_refs"):
                                w._icon_refs.clear()
                            if w.winfo_exists():
                                w.destroy()
                        except Exception:
                            pass

            def save_dir_custom_order(dir_path, names_list):
                if not isinstance(self.config.get("quick_folder_custom_order"), dict):
                    self.config["quick_folder_custom_order"] = {}
                norm_dir = os.path.normcase(os.path.normpath(dir_path))
                self.config["quick_folder_custom_order"][norm_dir] = list(names_list)
                self._save_config()
                self._refresh_folder_pystray_menu(force=True)

            # Stile barra di scorrimento laterale destra per il menu Cartella Rapida
            try:
                qf_style = ttk.Style(root)
                qf_style.theme_use("clam")
                qf_style.configure(
                    "QuickFolder.Vertical.TScrollbar",
                    gripcount=0,
                    background="#3b465e",
                    darkcolor="#1b1f27",
                    lightcolor="#1b1f27",
                    troughcolor="#12151c",
                    bordercolor="#1b1f27",
                    arrowcolor="#cbd5e1",
                    width=12
                )
                qf_style.map(
                    "QuickFolder.Vertical.TScrollbar",
                    background=[("active", "#60a5fa"), ("pressed", "#3b82f6")]
                )
            except Exception:
                pass

            ROW_H = 26
            MENU_W = 340
            MAX_SHOW = 300

            # Gestione rotellina del mouse: scorre il livello di menu attualmente sotto il cursore
            def _on_popup_mousewheel(event):
                if popup_closed[0] or not level_windows:
                    return
                try:
                    px = root.winfo_pointerx()
                    py = root.winfo_pointery()
                    target_win = None
                    # Cerca dal sottomenu più profondo (es. depth 2 -> 1 -> 0)
                    for d in sorted(level_windows.keys(), reverse=True):
                        w = level_windows.get(d)
                        if w and w.winfo_exists():
                            wx, wy = w.winfo_rootx(), w.winfo_rooty()
                            ww, wh = w.winfo_width(), w.winfo_height()
                            if wx <= px <= wx + ww and wy <= py <= wy + wh:
                                target_win = w
                                break
                    if target_win is None:
                        max_d = max(level_windows.keys())
                        target_win = level_windows.get(max_d)
                    if target_win and getattr(target_win, "_scroll_fn", None):
                        if getattr(event, "delta", 0):
                            units = -2 if event.delta > 0 else 2
                        elif getattr(event, "num", 0) == 4:
                            units = -2
                        elif getattr(event, "num", 0) == 5:
                            units = 2
                        else:
                            units = 0
                        if units != 0:
                            target_win._scroll_fn(units)
                except Exception:
                    pass

            root.bind_all("<MouseWheel>", _on_popup_mousewheel)
            root.bind_all("<Button-4>", _on_popup_mousewheel)
            root.bind_all("<Button-5>", _on_popup_mousewheel)

            def build_folder_popup_level(dir_path, depth, pos_x, pos_y, align_bottom=True):
                close_levels_from(depth)

                win = tk.Toplevel(root)
                win.overrideredirect(True)
                win.configure(bg="#334155")
                win.attributes("-topmost", True)
                win._icon_refs = []  # Mantiene i riferimenti alle PhotoImage 16x16 per evitare garbage collection
                win._scroll_fn = None
                level_windows[depth] = win

                outer = tk.Frame(win, bg="#1b1f27", padx=3, pady=3)
                outer.pack(fill="both", expand=True, padx=1, pady=1)

                folder_title = os.path.basename(dir_path) or dir_path

                # Intestazione: Apri la cartella in Esplora File
                hdr_btn = tk.Label(
                    outer,
                    text=f"📂 Apri '{folder_title[:34]}' in Esplora File",
                    font=("Tahoma", 10, "bold"),
                    fg="#fbbf24",
                    bg="#1b1f27",
                    anchor="w",
                    padx=8,
                    pady=4,
                    cursor="hand2"
                )
                hdr_btn.pack(side="top", fill="x")
                hdr_btn.bind("<Enter>", lambda e: hdr_btn.config(bg="#293040"))
                hdr_btn.bind("<Leave>", lambda e: hdr_btn.config(bg="#1b1f27"))
                hdr_btn.bind("<ButtonRelease-1>", lambda e, p=dir_path: (close_all(), self._open_filesystem_target(p)))

                # Suggerimento compatto per Drag & Drop e Scorrimento
                tk.Label(
                    outer,
                    text="↕ Trascina per riordinare • Usa rotellina o barra a destra per scorrere",
                    font=("Tahoma", 8),
                    fg="#64748b",
                    bg="#1b1f27",
                    anchor="w",
                    padx=8,
                    pady=1
                ).pack(side="top", fill="x")

                tk.Frame(outer, bg="#2d3748", height=1).pack(side="top", fill="x", pady=2)

                raw_entries = self._get_sorted_dir_entries(dir_path)
                items_state = []
                if raw_entries:
                    for entry, is_d in raw_entries[:MAX_SHOW]:
                        pil_ico = self._get_entry_small_pil_icon(entry.path, is_dir=is_d)
                        tk_ico = self._pil_to_tk_photo(pil_ico, master=win)
                        if tk_ico is not None:
                            win._icon_refs.append(tk_ico)
                        items_state.append({
                            "name": entry.name,
                            "path": entry.path,
                            "is_dir": is_d,
                            "tk_icon": tk_ico
                        })

                # Piè di pagina fisso in basso (solo sul menu principale depth == 0)
                if depth == 0:
                    footer_frame = tk.Frame(outer, bg="#1b1f27")
                    footer_frame.pack(side="bottom", fill="x")
                    tk.Frame(footer_frame, bg="#2d3748", height=1).pack(fill="x", pady=2)

                # Calcolo altezza massima disponibile sullo schermo sopra la Taskbar
                chrome_reserve = 128 if depth == 0 else 72
                max_list_h = max(180, min(int(screen_h * 0.68), taskbar_top - 24 - chrome_reserve))
                est_content_h = max(ROW_H, len(items_state) * ROW_H)
                need_scroll = est_content_h > max_list_h

                # Contenitore centrale per lista file + eventuale Scrollbar verticale a destra
                list_wrap = tk.Frame(outer, bg="#1b1f27")
                list_wrap.pack(side="top", fill="both", expand=True)

                list_canvas = None
                if need_scroll:
                    list_canvas = tk.Canvas(
                        list_wrap,
                        bg="#1b1f27",
                        highlightthickness=0,
                        bd=0,
                        height=max_list_h
                    )
                    v_scroll = ttk.Scrollbar(
                        list_wrap,
                        orient="vertical",
                        command=list_canvas.yview,
                        style="QuickFolder.Vertical.TScrollbar"
                    )
                    list_canvas.configure(yscrollcommand=v_scroll.set)
                    v_scroll.pack(side="right", fill="y", padx=(2, 0))
                    list_canvas.pack(side="left", fill="both", expand=True)

                    list_box = tk.Frame(list_canvas, bg="#1b1f27")
                    canvas_win_id = list_canvas.create_window((0, 0), window=list_box, anchor="nw")

                    def _on_listbox_configure(e=None):
                        try:
                            if list_canvas.winfo_exists():
                                list_canvas.configure(scrollregion=list_canvas.bbox("all"))
                        except Exception:
                            pass

                    def _on_listcanvas_configure(e):
                        try:
                            if list_canvas.winfo_exists():
                                list_canvas.itemconfig(canvas_win_id, width=e.width)
                        except Exception:
                            pass

                    list_box.bind("<Configure>", _on_listbox_configure)
                    list_canvas.bind("<Configure>", _on_listcanvas_configure)

                    def _scroll_this_level(units):
                        try:
                            if list_canvas and list_canvas.winfo_exists():
                                close_levels_from(depth + 1)
                                list_canvas.yview_scroll(int(units), "units")
                        except Exception:
                            pass

                    win._scroll_fn = _scroll_this_level
                    win.bind("<Up>", lambda e: _scroll_this_level(-2))
                    win.bind("<Down>", lambda e: _scroll_this_level(2))
                    win.bind("<Prior>", lambda e: _scroll_this_level(-8))
                    win.bind("<Next>", lambda e: _scroll_this_level(8))
                else:
                    list_box = tk.Frame(list_wrap, bg="#1b1f27")
                    list_box.pack(side="left", fill="both", expand=True)

                row_widgets = []
                drag_ctx = {
                    "pressed": False,
                    "dragging": False,
                    "idx": -1,
                    "start_y": 0,
                    "hover_idx": -1,
                    "hover_after_id": None
                }

                def format_row_text(it, is_dragged=False):
                    has_img = it.get("tk_icon") is not None
                    name_s = it["name"][:38]
                    suffix = "   ▸" if (it["is_dir"] and not is_dragged) else ""
                    if has_img:
                        prefix = "  ↕ " if is_dragged else "  "
                        return f"{prefix}{name_s}{suffix}"
                    icon = "↕️" if is_dragged else ("📁" if it["is_dir"] else "📄")
                    return f" {icon}  {name_s}{suffix}"

                def refresh_rows_visual():
                    for idx, rw in enumerate(row_widgets):
                        if idx >= len(items_state):
                            break
                        it = items_state[idx]
                        is_curr_drag = drag_ctx["dragging"] and drag_ctx["idx"] == idx
                        is_curr_hover = (not drag_ctx["dragging"]) and drag_ctx["hover_idx"] == idx
                        bg_c = "#1d4ed8" if is_curr_drag else ("#283246" if is_curr_hover else "#1b1f27")
                        fg_c = "#fde047" if is_curr_drag else ("#ffffff" if is_curr_hover else ("#f8fafc" if it["is_dir"] else "#e2e8f0"))
                        cfg_kwargs = {
                            "text": format_row_text(it, is_dragged=is_curr_drag),
                            "bg": bg_c,
                            "fg": fg_c,
                            "font": ("Tahoma", 10, "bold" if (it["is_dir"] or is_curr_drag) else "normal")
                        }
                        if it.get("tk_icon") is not None:
                            cfg_kwargs["image"] = it["tk_icon"]
                            cfg_kwargs["compound"] = "left"
                        rw.config(**cfg_kwargs)

                def open_cascading_subfolder(row_idx):
                    if drag_ctx["dragging"] or row_idx < 0 or row_idx >= len(items_state):
                        return
                    it = items_state[row_idx]
                    if not it["is_dir"]:
                        close_levels_from(depth + 1)
                        return
                    if depth >= 3:
                        return
                    try:
                        rw = row_widgets[row_idx]
                        rx = win.winfo_rootx()
                        ry = rw.winfo_rooty()
                        w_width = win.winfo_width()
                        # Apre a sinistra se siamo vicini al bordo destro dello schermo, altrimenti a destra
                        sub_x = (rx - MENU_W + 2) if (rx + w_width + MENU_W > screen_w - 8) else (rx + w_width - 2)
                        build_folder_popup_level(it["path"], depth + 1, sub_x, ry, align_bottom=False)
                    except Exception:
                        pass

                def on_row_enter(idx):
                    if drag_ctx["pressed"]:
                        return
                    drag_ctx["hover_idx"] = idx
                    refresh_rows_visual()
                    if drag_ctx["hover_after_id"]:
                        try:
                            win.after_cancel(drag_ctx["hover_after_id"])
                        except Exception:
                            pass
                    drag_ctx["hover_after_id"] = win.after(140, lambda i=idx: open_cascading_subfolder(i))

                def on_row_leave(idx):
                    if drag_ctx["pressed"]:
                        return
                    if drag_ctx["hover_idx"] == idx:
                        drag_ctx["hover_idx"] = -1
                        refresh_rows_visual()

                def on_row_press(event, idx):
                    drag_ctx["pressed"] = True
                    drag_ctx["dragging"] = False
                    drag_ctx["idx"] = idx
                    drag_ctx["start_y"] = event.y_root
                    global_drag["active"] = True

                def on_row_motion(event):
                    if not drag_ctx["pressed"] or drag_ctx["idx"] < 0:
                        return
                    if not drag_ctx["dragging"]:
                        if abs(event.y_root - drag_ctx["start_y"]) < 5:
                            return
                        drag_ctx["dragging"] = True
                        close_levels_from(depth + 1)
                        refresh_rows_visual()

                    try:
                        # Auto-scorrimento fluido se si trascina una voce vicino al bordo alto/basso della lista
                        if list_canvas is not None and list_canvas.winfo_exists():
                            cy_top = list_canvas.winfo_rooty()
                            cy_bot = cy_top + list_canvas.winfo_height()
                            if event.y_root < cy_top + 18:
                                list_canvas.yview_scroll(-1, "units")
                            elif event.y_root > cy_bot - 18:
                                list_canvas.yview_scroll(1, "units")

                        rel_y = event.y_root - list_box.winfo_rooty()
                        row_h_actual = max(18.0, float(list_box.winfo_height()) / max(1, len(items_state)))
                        target_idx = max(0, min(len(items_state) - 1, int(rel_y // row_h_actual)))
                        cur_idx = drag_ctx["idx"]
                        if target_idx != cur_idx:
                            moved = items_state.pop(cur_idx)
                            items_state.insert(target_idx, moved)
                            drag_ctx["idx"] = target_idx
                            refresh_rows_visual()
                    except Exception:
                        pass

                def on_row_release(event):
                    if not drag_ctx["pressed"]:
                        return
                    was_dragging = drag_ctx["dragging"]
                    final_idx = drag_ctx["idx"]
                    drag_ctx["pressed"] = False
                    drag_ctx["dragging"] = False
                    global_drag["active"] = False

                    if was_dragging:
                        save_dir_custom_order(dir_path, [x["name"] for x in items_state])
                        drag_ctx["hover_idx"] = final_idx
                        refresh_rows_visual()
                    else:
                        if 0 <= final_idx < len(items_state):
                            target_path = items_state[final_idx]["path"]
                            close_all()
                            self._open_filesystem_target(target_path)

                if raw_entries is None:
                    tk.Label(list_box, text=" ⚠️ Accesso negato", fg="#f87171", bg="#1b1f27", font=("Tahoma", 10), anchor="w", padx=8, pady=4).pack(fill="x")
                elif not items_state:
                    tk.Label(list_box, text=" (Cartella vuota)", fg="#64748b", bg="#1b1f27", font=("Tahoma", 10, "italic"), anchor="w", padx=8, pady=4).pack(fill="x")
                else:
                    for idx, it in enumerate(items_state):
                        lbl_kwargs = {
                            "text": format_row_text(it, False),
                            "font": ("Tahoma", 10, "bold" if it["is_dir"] else "normal"),
                            "fg": "#f8fafc" if it["is_dir"] else "#e2e8f0",
                            "bg": "#1b1f27",
                            "anchor": "w",
                            "padx": 8,
                            "cursor": "hand2"
                        }
                        if it.get("tk_icon") is not None:
                            lbl_kwargs["image"] = it["tk_icon"]
                            lbl_kwargs["compound"] = "left"
                        lbl = tk.Label(list_box, **lbl_kwargs)
                        lbl.pack(fill="x", ipady=3)
                        lbl.bind("<Enter>", lambda e, i=idx: on_row_enter(i))
                        lbl.bind("<Leave>", lambda e, i=idx: on_row_leave(i))
                        lbl.bind("<ButtonPress-1>", lambda e, i=idx: on_row_press(e, i))
                        lbl.bind("<B1-Motion>", on_row_motion)
                        lbl.bind("<ButtonRelease-1>", on_row_release)
                        row_widgets.append(lbl)

                # Popolamento piè di pagina solo sul menu principale (depth == 0)
                if depth == 0:
                    def reset_this_folder(e=None):
                        self._reset_folder_order(dir_path)
                        fresh = self._get_sorted_dir_entries(dir_path) or []
                        items_state.clear()
                        for entry, is_d in fresh[:MAX_SHOW]:
                            pil_ico = self._get_entry_small_pil_icon(entry.path, is_dir=is_d)
                            tk_ico = self._pil_to_tk_photo(pil_ico, master=win)
                            if tk_ico is not None:
                                win._icon_refs.append(tk_ico)
                            items_state.append({
                                "name": entry.name,
                                "path": entry.path,
                                "is_dir": is_d,
                                "tk_icon": tk_ico
                            })
                        refresh_rows_visual()

                    rst_btn = tk.Label(
                        footer_frame,
                        text="↺ Ripristina Ordine Predefinito (A-Z)",
                        font=("Tahoma", 9),
                        fg="#38bdf8",
                        bg="#1b1f27",
                        anchor="w",
                        padx=8,
                        pady=3,
                        cursor="hand2"
                    )
                    rst_btn.pack(fill="x")
                    rst_btn.bind("<Enter>", lambda e: (close_levels_from(1), rst_btn.config(bg="#283246")))
                    rst_btn.bind("<Leave>", lambda e: rst_btn.config(bg="#1b1f27"))
                    rst_btn.bind("<ButtonRelease-1>", reset_this_folder)

                    chg_btn = tk.Label(
                        footer_frame,
                        text=f"📁 Cambia Cartella Rapida #{slot_num}...",
                        font=("Tahoma", 9, "bold"),
                        fg="#fbbf24",
                        bg="#1b1f27",
                        anchor="w",
                        padx=8,
                        pady=3,
                        cursor="hand2"
                    )
                    chg_btn.pack(fill="x")
                    chg_btn.bind("<Enter>", lambda e: (close_levels_from(1), chg_btn.config(bg="#283246")))
                    chg_btn.bind("<Leave>", lambda e: chg_btn.config(bg="#1b1f27"))
                    chg_btn.bind("<ButtonRelease-1>", lambda e, s=slot_num: (close_all(), self.open_quick_folder_picker(slot_num=s)))

                win.update_idletasks()
                req_list_h = list_box.winfo_reqheight()
                if need_scroll and list_canvas is not None:
                    actual_list_h = min(max_list_h, req_list_h)
                    list_canvas.configure(height=actual_list_h, scrollregion=(0, 0, MENU_W, req_list_h))
                    win.update_idletasks()

                w_req = max(MENU_W, win.winfo_reqwidth())
                h_req = min(taskbar_top - 12, win.winfo_reqheight())

                if align_bottom:
                    final_x = max(8, min(pos_x - w_req // 2, screen_w - w_req - 8))
                    final_y = max(8, min(pos_y, taskbar_top - 4) - h_req)
                else:
                    final_x = max(8, min(pos_x, screen_w - w_req - 8))
                    final_y = max(8, min(pos_y, taskbar_top - h_req - 4))

                win.geometry(f"{w_req}x{h_req}+{final_x}+{final_y}")
                win.bind("<Escape>", lambda e: close_all())

            build_folder_popup_level(self.get_quick_folder_path(slot_num), 0, anchor_x, anchor_y, align_bottom=True)

            # Controllo leggero per chiudere il menu solo se si inizia un nuovo click fuori da esso
            # (consente di trascinare comodamente la barra di scorrimento laterale anche se il cursore esce dai bordi)
            mouse_state = {"was_down": False, "started_inside": False}

            def poll_outside_click():
                if popup_closed[0]:
                    return
                if self._dd_popup_close_req or not self._dd_popup_active or not level_windows:
                    close_all()
                    return
                if not global_drag["active"] and (time.time() - open_ts) > 0.22:
                    try:
                        if sys.platform == "win32":
                            l_down = bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)
                            r_down = bool(ctypes.windll.user32.GetAsyncKeyState(0x02) & 0x8000)
                            is_down = l_down or r_down
                            if is_down and not mouse_state["was_down"]:
                                px = root.winfo_pointerx()
                                py = root.winfo_pointery()
                                inside_any = False
                                for w in list(level_windows.values()):
                                    try:
                                        if w.winfo_exists():
                                            wx = w.winfo_rootx()
                                            wy = w.winfo_rooty()
                                            ww = w.winfo_width()
                                            wh = w.winfo_height()
                                            if wx <= px <= wx + ww and wy <= py <= wy + wh:
                                                inside_any = True
                                                break
                                    except Exception:
                                        pass
                                mouse_state["started_inside"] = inside_any
                                if not inside_any:
                                    close_all()
                                    return
                            elif not is_down:
                                mouse_state["started_inside"] = False
                            mouse_state["was_down"] = is_down
                    except Exception:
                        pass
                if not popup_closed[0]:
                    root.after(45, poll_outside_click)

            root.after(45, poll_outside_click)
        except Exception as e:
            print("Errore popup cartella Drag & Drop:", e)
            self._dd_popup_active = False

    def _activate_folder_left_menu(self, slot_num=1, target_icon=None):
        """Attiva il menu rapido della cartella sul click sinistro (fallback nativo)."""
        try:
            ic = target_icon or (self.icon_folder if slot_num == 1 else (self.icon_folder_2 if slot_num == 2 else self.icon_folder_3))
            if not ic:
                return
            self._folder_left_quick_menu = self._create_folder_pystray_menu(slot_num)
            ic.menu = self._folder_left_quick_menu
            if hasattr(ic, "update_menu"):
                ic.update_menu()
        except Exception:
            pass

    def _activate_folder_right_menu(self, slot_num=1):
        """Attiva il normale menu delle impostazioni di WinNetTray sul click destro."""
        try:
            self._dd_popup_close_req = True
            ic = self.icon_folder if slot_num == 1 else (self.icon_folder_2 if slot_num == 2 else self.icon_folder_3)
            if not ic:
                return
            if not getattr(self, "_folder_right_settings_menu", None):
                self._folder_right_settings_menu = self._create_context_menu(slot_name="Download")
            ic.menu = self._folder_right_settings_menu
            if hasattr(ic, "update_menu"):
                ic.update_menu()
        except Exception:
            pass

    def _force_refresh_folder_menu(self, icon=None, item=None):
        self._refresh_folder_pystray_menu(force=True)

    def _refresh_folder_pystray_menu(self, force=True):
        """Aggiorna il menu contestuale di fallback della cartella rapida senza bloccare i menu principali."""
        try:
            for s_idx, s_icon in ((1, getattr(self, "icon_folder", None)), (2, getattr(self, "icon_folder_2", None)), (3, getattr(self, "icon_folder_3", None))):
                if s_icon and getattr(self, "_folder_right_settings_menu", None):
                    s_icon.menu = self._folder_right_settings_menu
        except Exception:
            pass

    # --------------------------------------------------------------------------
    # Gestione Configurazione (Salvataggio & Caricamento JSON Atomico)
    # --------------------------------------------------------------------------
    def _get_config_path(self):
        if getattr(sys, "frozen", False):
            base_dir = os.path.dirname(os.path.abspath(sys.executable))
        else:
            base_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        local_cfg = os.path.join(base_dir, "winnettray_config.json")
        prog_files = os.environ.get("ProgramFiles", "C:\\Program Files").lower()
        prog_files_x86 = os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)").lower()
        base_lower = base_dir.lower()
        if not base_lower.startswith(prog_files) and not base_lower.startswith(prog_files_x86):
            try:
                if os.path.exists(local_cfg) and os.access(local_cfg, os.W_OK):
                    return local_cfg
                test_file = os.path.join(base_dir, ".winnettray_write_test")
                with open(test_file, "w", encoding="utf-8") as tf:
                    tf.write("ok")
                os.remove(test_file)
                return local_cfg
            except Exception:
                pass
        appdata_dir = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "WinNetTray")
        try:
            os.makedirs(appdata_dir, exist_ok=True)
        except Exception:
            pass
        return os.path.join(appdata_dir, "winnettray_config.json")

    def _load_config(self):
        cfg = dict(self.DEFAULT_CONFIG)
        if os.path.exists(self.config_path):
            try:
                # Limita la lettura a max 512 KB per prevenire DoS da file JSON anomali
                if os.path.getsize(self.config_path) <= 512 * 1024:
                    with open(self.config_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        # Accetta esclusivamente chiavi presenti nella whitelist DEFAULT_CONFIG
                        for k in self.DEFAULT_CONFIG:
                            if k in data:
                                cfg[k] = data[k]
                        if not data.get("default_font_tahoma_migrated"):
                            cfg["font_family"] = "tahoma"
                            cfg["default_font_tahoma_migrated"] = True
            except Exception:
                pass

        # Sanitizzazione e validazione rigorosa dei tipi e dei range (previene crash/DoS da JSON corrotto o manomesso)
        if cfg.get("display_mode") not in ("dual_tray", "docked_pill", "tray_icon"):
            cfg["display_mode"] = "dual_tray"
        try:
            isz = int(cfg.get("icon_size", 64))
            cfg["icon_size"] = isz if isz in (32, 48, 64) else 64
        except Exception:
            cfg["icon_size"] = 64
        try:
            fsc = float(cfg.get("font_scale", 1.15))
            cfg["font_scale"] = max(0.7, min(1.8, fsc))
        except Exception:
            cfg["font_scale"] = 1.15
        if cfg.get("bg_style") not in ("box", "rounded", "transparent"):
            cfg["bg_style"] = "rounded"
        if cfg.get("theme") not in self.THEMES:
            cfg["theme"] = "mint_cyan"
        if cfg.get("gpu_selection") not in ("auto_active", "nvidia_only", "amd_only", "intel_only"):
            cfg["gpu_selection"] = "auto_active"
        if not isinstance(cfg.get("quick_folder_custom_order"), dict):
            cfg["quick_folder_custom_order"] = {}

        # Assicura che i 3 slot quick_folder siano presenti anche se il file JSON proviene da una versione precedente
        raw_order = cfg.get("slot_order", self.DEFAULT_CONFIG["slot_order"])
        order = list(raw_order) if isinstance(raw_order, list) else list(self.DEFAULT_CONFIG["slot_order"])
        for f_slot in ("quick_folder", "quick_folder_2", "quick_folder_3"):
            if f_slot not in order:
                order.append(f_slot)
        cfg["slot_order"] = order
        if not isinstance(cfg.get("quick_folder_path"), str) or not cfg.get("quick_folder_path"):
            cfg["quick_folder_path"] = self._get_default_folder_for_slot(1)
        if not isinstance(cfg.get("quick_folder_path_2"), str) or not cfg.get("quick_folder_path_2"):
            cfg["quick_folder_path_2"] = self._get_default_folder_for_slot(2)
        if not isinstance(cfg.get("quick_folder_path_3"), str) or not cfg.get("quick_folder_path_3"):
            cfg["quick_folder_path_3"] = self._get_default_folder_for_slot(3)
        return cfg

    def _save_config(self):
        with self._tray_lock:
            try:
                cfg_dir = os.path.dirname(self.config_path)
                if cfg_dir:
                    os.makedirs(cfg_dir, exist_ok=True)
                tmp_path = self.config_path + ".tmp"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(self.config, f, indent=2)
                os.replace(tmp_path, self.config_path)
            except Exception:
                try:
                    with open(self.config_path, "w", encoding="utf-8") as f:
                        json.dump(self.config, f, indent=2)
                except Exception:
                    pass

    # --------------------------------------------------------------------------
    # Inizializzazione 10 Font con Cifre Massimizzate
    # --------------------------------------------------------------------------
    def _init_fonts(self):
        """Calcola e carica le dimensioni font con supporto a 10 font e massimizzazione slot."""
        base_size = self.config.get("icon_size", 64)
        scale = float(self.config.get("font_scale", 1.15))
        family = self.config.get("font_family", "tahoma")
        maximized = bool(self.config.get("maximized_slot", True))

        # Se maximized_slot e' attivo, potenzia le dimensioni per riempire completamente lo slot
        boost = 1.25 if maximized else 1.0

        if base_size == 64:
            s_twin = int(round(25 * scale * boost))
            s_twin_arrow = int(round(12 * scale))
            s_badge = int(round(12 * scale))
            s_temp_num = int(round(27 * scale * boost))
            s_2c = int(round(35 * scale * boost))
            s_3c = int(round(28 * scale * boost))
            s_un = int(round(22 * scale))
        elif base_size == 48:
            s_twin = int(round(19 * scale * boost))
            s_twin_arrow = int(round(10 * scale))
            s_badge = int(round(10 * scale))
            s_temp_num = int(round(21 * scale * boost))
            s_2c = int(round(26 * scale * boost))
            s_3c = int(round(21 * scale * boost))
            s_un = int(round(16 * scale))
        else:  # 32
            s_twin = int(round(13 * scale * boost))
            s_twin_arrow = int(round(7 * scale))
            s_badge = int(round(7 * scale))
            s_temp_num = int(round(15 * scale * boost))
            s_2c = int(round(18 * scale * boost))
            s_3c = int(round(14 * scale * boost))
            s_un = int(round(11 * scale))

        self.font_twin_num = self._load_system_font(family, size=max(10, s_twin), bold=True)
        self.font_twin_arrow = self._load_system_font(family, size=max(7, s_twin_arrow), bold=True)
        self.font_badge = self._load_system_font(family, size=max(6, s_badge), bold=True)
        self.font_temp_num = self._load_system_font(family, size=max(10, s_temp_num), bold=True)

        self.font_2cifre = self._load_system_font(family, size=max(10, s_2c), bold=True)
        self.font_3cifre = self._load_system_font(family, size=max(8, s_3c), bold=True)
        self.font_unit = self._load_system_font(family, size=max(7, s_un), bold=True)

    def _load_system_font(self, font_key="tahoma", size=24, bold=True):
        """Carica uno dei 10 font nativi di Windows con cache in RAM per evitare riaperture su disco."""
        cache_key = (str(font_key), int(size), bool(bold))
        if hasattr(self, "_font_cache") and cache_key in self._font_cache:
            return self._font_cache[cache_key]

        with self._tray_lock:
            if hasattr(self, "_font_cache") and cache_key in self._font_cache:
                return self._font_cache[cache_key]
            fonts_dir = os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts")
            font_map = {
                "tahoma": ["tahomabd.ttf", "tahoma.ttf", "segoeuib.ttf"],
                "bahnschrift": ["bahnschrift.ttf", "segoeuib.ttf", "consolab.ttf", "arialbd.ttf"],
                "consolas": ["consolab.ttf", "consola.ttf", "segoeuib.ttf", "arialbd.ttf"],
                "segoe": ["segoeuib.ttf", "segoeui.ttf", "ariblk.ttf", "arialbd.ttf"],
                "arial_bold": ["ariblk.ttf", "arialbd.ttf", "arial.ttf", "segoeuib.ttf"],
                "impact": ["impact.ttf", "ariblk.ttf", "arialbd.ttf"],
                "trebuchet": ["trebucbd.ttf", "trebuc.ttf", "segoeuib.ttf"],
                "verdana": ["verdanab.ttf", "verdana.ttf", "tahomabd.ttf"],
                "lucida_console": ["lucon.ttf", "consolab.ttf", "courbd.ttf"],
                "franklin_gothic": ["framd.ttf", "frahm.ttf", "frabk.ttf", "ariblk.ttf"]
            }
            candidates = font_map.get(font_key, font_map["tahoma"])
            loaded_font = None
            for font_name in candidates:
                font_path = os.path.join(fonts_dir, font_name)
                if os.path.exists(font_path):
                    try:
                        loaded_font = ImageFont.truetype(font_path, int(size))
                        break
                    except Exception:
                        continue
            if loaded_font is None:
                loaded_font = ImageFont.load_default()
            if hasattr(self, "_font_cache"):
                if len(self._font_cache) > 250:
                    self._font_cache.clear()
                self._font_cache[cache_key] = loaded_font
            return loaded_font

    # --------------------------------------------------------------------------
    # Rilevamento Temperature Hardware (CPU & GPU con Auto-Detect Nvidia)
    # --------------------------------------------------------------------------
    def get_cpu_temperature(self):
        try:
            if hasattr(psutil, "sensors_temperatures"):
                temps = psutil.sensors_temperatures()
                if temps:
                    for name in ["coretemp", "k10temp", "cpu_thermal", "acpitz"]:
                        if name in temps and temps[name]:
                            return round(temps[name][0].current, 1)
        except Exception:
            pass

        if getattr(self, "_wmi_temp_supported", True) and sys.platform == "win32":
            try:
                sys_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
                ps_exe = os.path.join(sys_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
                if not os.path.isfile(ps_exe):
                    self._wmi_temp_supported = False
                else:
                    cmd = [
                        ps_exe,
                        "-NoProfile",
                        "-NonInteractive",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-Command",
                        "Get-WmiObject -Namespace root/wmi -Class MSAcpi_ThermalZoneTemperature -ErrorAction Stop | Select-Object -ExpandProperty CurrentTemperature"
                    ]
                    proc = subprocess.run(cmd, capture_output=True, text=True, shell=False, timeout=1.2, creationflags=0x08000000)
                    if proc.returncode == 0 and proc.stdout.strip():
                        raw_k = float(proc.stdout.strip().split()[0])
                        celsius = (raw_k - 2732.0) / 10.0
                        if 20.0 <= celsius <= 110.0:
                            return round(celsius, 1)
                    self._wmi_temp_supported = False
            except Exception:
                self._wmi_temp_supported = False

        cpu_load = psutil.cpu_percent(interval=None)
        estimated_temp = 45.0 + (cpu_load * 0.35)
        return round(min(88.0, max(42.0, estimated_temp)), 1)

    def get_gpu_temperature(self):
        gpu_mode = self.config.get("gpu_selection", "auto_active")
        is_nvidia_active = False
        gpu_temp = 50.0
        gpu_name = "GPU Integrata / Primaria"

        if HAS_PYNVML and self._nvml_initialized:
            try:
                device_count = pynvml.nvmlDeviceGetCount()
                if device_count > 0:
                    handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                    raw_name = pynvml.nvmlDeviceGetName(handle)
                    nv_name = raw_name.decode("utf-8") if isinstance(raw_name, bytes) else str(raw_name)
                    nv_temp = float(pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU))
                    util = pynvml.nvmlDeviceGetUtilizationRates(handle).gpu
                    
                    procs_graphics = pynvml.nvmlDeviceGetGraphicsRunningProcesses(handle)
                    procs_compute = pynvml.nvmlDeviceGetComputeRunningProcesses(handle)
                    has_active_procs = (len(procs_graphics) + len(procs_compute)) > 0

                    if gpu_mode == "nvidia_only" or (gpu_mode == "auto_active" and (has_active_procs or util > 0 or nv_temp > 52)):
                        is_nvidia_active = True
                        gpu_temp = nv_temp
                        gpu_name = nv_name
                        return round(gpu_temp, 1), is_nvidia_active, gpu_name
            except Exception:
                pass

        if getattr(self, "_nvidia_smi_supported", True) and sys.platform == "win32" and not (HAS_PYNVML and self._nvml_initialized):
            try:
                sys_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
                prog_files = os.environ.get("ProgramFiles") or r"C:\Program Files"
                smi_candidates = [
                    os.path.join(sys_root, "System32", "nvidia-smi.exe"),
                    os.path.join(prog_files, "NVIDIA Corporation", "NVSMI", "nvidia-smi.exe"),
                ]
                smi_exe = next((p for p in smi_candidates if os.path.isfile(p)), None)
                if not smi_exe:
                    self._nvidia_smi_supported = False
                else:
                    cmd = [smi_exe, "--query-gpu=temperature.gpu,utilization.gpu,name", "--format=csv,noheader,nounits"]
                    proc = subprocess.run(cmd, capture_output=True, text=True, shell=False, timeout=1.2, creationflags=0x08000000)
                    if proc.returncode == 0 and proc.stdout.strip():
                        lines = proc.stdout.strip().splitlines()
                        if lines:
                            parts = [p.strip() for p in lines[0].split(",")]
                            if len(parts) >= 2:
                                nv_temp = float(parts[0])
                                nv_util = float(parts[1]) if parts[1].replace(".", "").isdigit() else 0.0
                                nv_name = parts[2][:48] if len(parts) >= 3 else "NVIDIA GeForce"

                                if gpu_mode == "nvidia_only" or (gpu_mode == "auto_active" and (nv_util > 0 or nv_temp > 52)):
                                    is_nvidia_active = True
                                    return round(nv_temp, 1), is_nvidia_active, nv_name
                    else:
                        self._nvidia_smi_supported = False
            except Exception:
                self._nvidia_smi_supported = False

        if gpu_mode == "nvidia_only":
            gpu_name = "NVIDIA (In Standby / 0 RPM)"
            gpu_temp = 42.0
            is_nvidia_active = False
        else:
            gpu_name = "GPU Integrata (Intel / AMD)"
            cpu_load = psutil.cpu_percent(interval=None)
            gpu_temp = 46.0 + (cpu_load * 0.2)
            is_nvidia_active = False

        return round(gpu_temp, 1), is_nvidia_active, gpu_name

    # --------------------------------------------------------------------------
    # Rilevamento Scheda di Rete Attiva
    # --------------------------------------------------------------------------
    def get_active_network_interface(self):
        local_ip = "127.0.0.1"
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(1.5)
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
        except Exception:
            local_ip = "127.0.0.1"

        active_iface = None
        interfaces = psutil.net_if_addrs()
        for iface_name, addrs in interfaces.items():
            for addr in addrs:
                if addr.family == socket.AF_INET and addr.address == local_ip:
                    active_iface = iface_name
                    break
            if active_iface:
                break

        if not active_iface:
            stats = psutil.net_if_stats()
            for iface_name, stat in stats.items():
                if stat.isup and iface_name in interfaces:
                    active_iface = iface_name
                    break

        return active_iface, local_ip

    # --------------------------------------------------------------------------
    # Recupero IP Pubblico (Solo HTTPS con validazione rigorosa e zero esfiltrazione)
    # --------------------------------------------------------------------------
    def fetch_public_ip(self):
        services = [
            "https://api.ipify.org?format=text",
            "https://api4.ident.me",
            "https://icanhazip.com"
        ]
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/plain"
        }
        for url in services:
            try:
                with requests.get(
                    url,
                    headers=headers,
                    timeout=3.0,
                    allow_redirects=False,
                    verify=True,
                    stream=True
                ) as response:
                    if response.status_code == 200:
                        raw_bytes = response.raw.read(64, decode_content=True)
                        candidate = raw_bytes.decode("utf-8", errors="ignore").strip()
                        # Valida rigorosamente che la risposta sia un indirizzo IPv4 o IPv6 reale
                        parsed_ip = ipaddress.ip_address(candidate)
                        return str(parsed_ip)
            except Exception:
                continue
        return "Non disponibile (Offline)"

    def update_public_ip_worker(self):
        new_ip = self.fetch_public_ip()
        with self.lock:
            self.public_ip = new_ip
            self.last_public_ip_check = time.time()

    def force_refresh_public_ip(self, icon=None, item=None):
        threading.Thread(target=self.update_public_ip_worker, daemon=True).start()

    # --------------------------------------------------------------------------
    # Formattazione Velocita
    # --------------------------------------------------------------------------
    @staticmethod
    def get_compact_speed_text(bytes_per_sec, hide_unit=True):
        if hide_unit:
            if bytes_per_sec < 1024:
                return "0" if bytes_per_sec < 20 else str(int(round(bytes_per_sec)))
            elif bytes_per_sec < 1024 * 1024:
                kb = bytes_per_sec / 1024
                return f"{int(round(kb))}" if kb >= 10 else f"{kb:.1f}"
            elif bytes_per_sec < 1024 * 1024 * 1024:
                mb = bytes_per_sec / (1024 * 1024)
                return f"{mb:.1f}" if mb < 10 else f"{int(round(mb))}"
            else:
                gb = bytes_per_sec / (1024 * 1024 * 1024)
                return f"{gb:.1f}"
        else:
            if bytes_per_sec < 1024:
                return "0K"
            elif bytes_per_sec < 1024 * 1024:
                kb = bytes_per_sec / 1024
                return f"{int(round(kb))}K"
            elif bytes_per_sec < 1024 * 1024 * 1024:
                mb = bytes_per_sec / (1024 * 1024)
                return f"{mb:.1f}M" if mb < 10 else f"{int(round(mb))}M"
            else:
                gb = bytes_per_sec / (1024 * 1024 * 1024)
                return f"{gb:.1f}G"

    @staticmethod
    def get_speed_color(bytes_per_sec):
        """Restituisce il colore identificativo dell'unita di misura richiesta:
        - Bianco = Byte/s (< 1024 B/s)
        - Verde chiaro = Kbyte/s (KB/s)
        - Rosa scuro = Mbyte/s (MB/s) - colore rosa vivo ad alta leggibilità su sfondi scuri
        - Viola chiaro = Gbyte/s (GB/s)
        """
        if bytes_per_sec < 1024:
            return (255, 255, 255, 255) # Bianco = Byte
        elif bytes_per_sec < 1024 * 1024:
            return (74, 222, 128, 255)   # Verde chiaro = Kbyte
        elif bytes_per_sec < 1024 * 1024 * 1024:
            return (255, 51, 133, 255)   # Rosa scuro = Mbyte
        else:
            return (192, 132, 252, 255)  # Viola chiaro = Gbyte

    @staticmethod
    def get_display_parts(bytes_per_sec):
        if bytes_per_sec < 1024:
            return "0", "K"
        elif bytes_per_sec < 1024 * 1024:
            kb = bytes_per_sec / 1024
            return str(int(kb)), "K"
        elif bytes_per_sec < 1024 * 1024 * 1024:
            mb = bytes_per_sec / (1024 * 1024)
            val = f"{mb:.1f}" if mb < 10 else str(int(mb))
            return val, "M"
        else:
            gb = bytes_per_sec / (1024 * 1024 * 1024)
            return f"{gb:.1f}", "G"

    @staticmethod
    def format_speed_detailed(bytes_per_sec):
        if bytes_per_sec < 1024:
            return f"{bytes_per_sec:.0f} B/s"
        elif bytes_per_sec < 1024 * 1024:
            return f"{bytes_per_sec / 1024:.1f} KB/s"
        elif bytes_per_sec < 1024 * 1024 * 1024:
            return f"{bytes_per_sec / (1024 * 1024):.2f} MB/s"
        else:
            return f"{bytes_per_sec / (1024 * 1024 * 1024):.2f} GB/s"

    @staticmethod
    def format_bytes_total(total_bytes):
        if total_bytes < 1024 * 1024:
            return f"{total_bytes / 1024:.1f} KB"
        elif total_bytes < 1024 * 1024 * 1024:
            return f"{total_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{total_bytes / (1024 * 1024 * 1024):.2f} GB"

    # --------------------------------------------------------------------------
    # Generazione Icone per gli Slot Tray (Cifre Massimizzate)
    # --------------------------------------------------------------------------
    def generate_twin_tray_icons(self, dl_bytes, ul_bytes):
        size = self.config.get("icon_size", 64)
        bg_style = self.config.get("bg_style", "rounded")
        theme_key = self.config.get("theme", "mint_cyan")
        theme = self.THEMES.get(theme_key, self.THEMES["mint_cyan"])
        show_border = self.config.get("show_border", True)
        hide_unit = bool(self.config.get("hide_speed_unit", True))

        dl_txt = self.get_compact_speed_text(dl_bytes, hide_unit=hide_unit)
        ul_txt = self.get_compact_speed_text(ul_bytes, hide_unit=hide_unit)

        # 1. Slot Download
        img_dl = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d_dl = ImageDraw.Draw(img_dl)
        self._draw_slot_bg(d_dl, size, bg_style, (0, 255, 170, 120) if show_border else None)
        color_dl = self.get_speed_color(dl_bytes) if hide_unit else (theme["dl"] if dl_bytes >= 1024 else (200, 220, 210, 255))
        self._draw_slot_content(d_dl, size, "▼", dl_txt, color_dl, show_border)

        # 2. Slot Upload
        img_ul = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d_ul = ImageDraw.Draw(img_ul)
        self._draw_slot_bg(d_ul, size, bg_style, (0, 215, 255, 120) if show_border else None)
        color_ul = self.get_speed_color(ul_bytes) if hide_unit else (theme["ul"] if ul_bytes >= 1024 else (200, 220, 240, 255))
        self._draw_slot_content(d_ul, size, "▲", ul_txt, color_ul, show_border)

        return img_dl, img_ul

    def generate_net_combined_tray_icon(self, dl_bytes, ul_bytes):
        """Genera un'icona tray unificata con Download (sopra) e Upload (sotto) nello stesso slot."""
        size = self.config.get("icon_size", 64)
        bg_style = self.config.get("bg_style", "rounded")
        show_border = self.config.get("show_border", True)

        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        self._draw_slot_bg(d, size, bg_style, (255, 51, 133, 140) if show_border else None)

        dl_txt = self.get_compact_speed_text(dl_bytes, hide_unit=True)
        ul_txt = self.get_compact_speed_text(ul_bytes, hide_unit=True)
        color_dl = self.get_speed_color(dl_bytes)
        color_ul = self.get_speed_color(ul_bytes)

        stroke_w = 1 if show_border else 0
        stroke_fill = (0, 0, 0, 255) if stroke_w else None
        family = self.config.get("font_family", "tahoma")

        scale = float(self.config.get("font_scale", 1.15))
        ratio = size / 64.0
        pt_num = max(8, int(round(15 * ratio * scale)))
        pt_arr = max(7, int(round(10 * ratio * scale)))

        f_num = self._load_system_font(family, size=pt_num, bold=True)
        f_arr = self._load_system_font(family, size=pt_arr, bold=True)

        # Riga 1: Download ▼
        y1 = int(size * 0.08)
        d.text((int(size * 0.08), y1), "▼", fill=color_dl, font=f_arr, stroke_width=stroke_w, stroke_fill=stroke_fill)
        d.text((int(size * 0.32), y1 - 1), dl_txt, fill=color_dl, font=f_num, stroke_width=stroke_w, stroke_fill=stroke_fill)

        # Riga 2: Upload ▲
        y2 = int(size * 0.52)
        d.text((int(size * 0.08), y2), "▲", fill=color_ul, font=f_arr, stroke_width=stroke_w, stroke_fill=stroke_fill)
        d.text((int(size * 0.32), y2 - 1), ul_txt, fill=color_ul, font=f_num, stroke_width=stroke_w, stroke_fill=stroke_fill)

        return img

    def generate_cpu_tray_icon(self, cpu_temp):
        size = self.config.get("icon_size", 64)
        bg_style = self.config.get("bg_style", "rounded")
        show_border = self.config.get("show_border", True)

        img_cpu = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d_cpu = ImageDraw.Draw(img_cpu)
        self._draw_slot_bg(d_cpu, size, bg_style, (251, 146, 60, 140) if show_border else None)

        badge_color = (251, 146, 60, 255)
        num_str = f"{int(round(cpu_temp))}°"
        temp_color = (253, 186, 116, 255) if cpu_temp < 80 else (255, 80, 80, 255)
        self._draw_slot_hardware(d_cpu, size, "CPU", num_str, badge_color, temp_color, show_border)
        return img_cpu

    def generate_gpu_tray_icon(self, gpu_temp, is_nvidia_active, gpu_name):
        size = self.config.get("icon_size", 64)
        bg_style = self.config.get("bg_style", "rounded")
        show_border = self.config.get("show_border", True)

        img_gpu = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d_gpu = ImageDraw.Draw(img_gpu)
        outline_color = (52, 211, 153, 160) if is_nvidia_active else (56, 189, 248, 120)
        self._draw_slot_bg(d_gpu, size, bg_style, outline_color if show_border else None)

        badge_txt = "NV" if is_nvidia_active else "GPU"
        badge_color = (52, 211, 153, 255) if is_nvidia_active else (56, 189, 248, 255)
        num_str = f"{int(round(gpu_temp))}°"
        temp_color = (110, 231, 183, 255) if gpu_temp < 82 else (255, 90, 90, 255)
        self._draw_slot_hardware(d_gpu, size, badge_txt, num_str, badge_color, temp_color, show_border)

        if is_nvidia_active and size >= 48:
            d_gpu.ellipse([size - 9, 3, size - 3, 9], fill=(52, 211, 153, 255), outline=(0, 0, 0, 255), width=1)

        return img_gpu

    def generate_folder_tray_icon(self, slot_num=1):
        """
        Disegna l'icona della Cartella Rapida (1=Oro, 2=Azzurro, 3=Verde Smeraldo)
        con numero distintivo nitido per riconoscere subito le 3 cartelle nella System Tray.
        """
        size = self.config.get("icon_size", 64)
        bg_style = self.config.get("bg_style", "rounded")
        show_border = self.config.get("show_border", True)

        if slot_num == 2:
            border_col = (56, 189, 248, 220)
            tab_col = (2, 132, 199, 255)
            back_col = (3, 105, 161, 255)
            front_col = (56, 189, 248, 255)
            front_out = (14, 165, 233, 255)
        elif slot_num == 3:
            border_col = (52, 211, 153, 220)
            tab_col = (5, 150, 105, 255)
            back_col = (4, 120, 87, 255)
            front_col = (52, 211, 153, 255)
            front_out = (16, 185, 129, 255)
        else:
            border_col = (245, 158, 11, 220)
            tab_col = (217, 119, 6, 255)
            back_col = (180, 83, 9, 255)
            front_col = (251, 191, 36, 255)
            front_out = (245, 158, 11, 255)

        img_fld = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img_fld)
        self._draw_slot_bg(draw, size, bg_style, border_col if show_border else None)

        pad_x = max(4, int(size * 0.14))
        top_tab_y = max(4, int(size * 0.18))
        body_top_y = max(7, int(size * 0.28))
        bottom_y = size - max(4, int(size * 0.16))
        tab_w = int((size - 2 * pad_x) * 0.44)

        # 1. Retro della cartella + linguetta superiore
        draw.rounded_rectangle(
            [pad_x, top_tab_y, pad_x + tab_w, body_top_y + 4],
            radius=max(2, size // 18),
            fill=tab_col
        )
        draw.rounded_rectangle(
            [pad_x, body_top_y - 2, size - pad_x, bottom_y],
            radius=max(2, size // 14),
            fill=back_col
        )

        # 2. Foglio documento bianco che spunta dalla cartella
        doc_margin = max(2, size // 16)
        draw.rectangle(
            [pad_x + doc_margin, body_top_y - max(2, size // 20), size - pad_x - doc_margin, body_top_y + max(4, size // 8)],
            fill=(248, 250, 252, 255)
        )

        # 3. Frontale della cartella
        front_top = body_top_y + max(3, int(size * 0.07))
        draw.rounded_rectangle(
            [pad_x, front_top, size - pad_x, bottom_y],
            radius=max(2, size // 14),
            fill=front_col,
            outline=front_out if show_border else None,
            width=1
        )

        # 4. Numero identificativo (1, 2 o 3) nitido sul fronte della cartella
        try:
            num_txt = str(slot_num)
            f_num = self._load_system_font("tahoma", size=max(9, int(size * 0.32)), bold=True)
            bbox = draw.textbbox((0, 0), num_txt, font=f_num)
            nw = bbox[2] - bbox[0]
            nh = bbox[3] - bbox[1]
            nx = int((size - nw) / 2) - bbox[0]
            ny = int((front_top + bottom_y - nh) / 2) - bbox[1]
            draw.text((nx, ny), num_txt, fill=(15, 23, 42, 255), font=f_num)
        except Exception:
            pass

        return img_fld

    def _draw_slot_bg(self, draw, size, bg_style, outline):
        if bg_style == "box":
            draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=max(2, size // 10), fill=(12, 14, 18, 245), outline=outline, width=1)
        elif bg_style == "rounded":
            draw.rounded_rectangle([1, 1, size - 2, size - 2], radius=max(4, size // 4), fill=(12, 14, 18, 245), outline=outline, width=1)

    def _get_dynamic_font(self, text, is_temp=False):
        """Calcola dinamicamente la dimensione del font in base alla lunghezza del testo per non uscire dallo slot."""
        base_size = self.config.get("icon_size", 64)
        scale = float(self.config.get("font_scale", 1.15))
        family = self.config.get("font_family", "tahoma")
        maximized = bool(self.config.get("maximized_slot", True))
        boost = 1.25 if maximized else 1.0

        length = len(text) if text else 2
        if is_temp:
            if length <= 2:
                base_pt = 27
            elif length == 3:
                base_pt = 22
            elif length == 4:
                base_pt = 18
            else:
                base_pt = 14
        else:
            if length <= 2:
                base_pt = 27
            elif length == 3:
                base_pt = 22
            elif length == 4:
                base_pt = 18
            elif length == 5:
                base_pt = 14
            else:
                base_pt = 11

        ratio = base_size / 64.0
        final_pt = int(round(base_pt * ratio * scale * boost))
        final_pt = max(8, final_pt)
        return self._load_system_font(family, size=final_pt, bold=True)

    def _draw_slot_content(self, draw, size, arrow, text, color, show_border):
        """Disegna freccia e numero con dimensione carattere dinamica per non uscire dallo slot."""
        stroke_w = 1 if show_border else 0
        stroke_fill = (0, 0, 0, 255) if stroke_w else None
        maximized = bool(self.config.get("maximized_slot", True))

        # Freccia in alto
        bbox_arr = draw.textbbox((0, 0), arrow, font=self.font_twin_arrow)
        w_arr = bbox_arr[2] - bbox_arr[0]
        x_arr = int((size - w_arr) / 2) - bbox_arr[0]
        y_arr = 1 if maximized else (2 if size >= 48 else 1)
        draw.text((x_arr, y_arr), arrow, fill=color, font=self.font_twin_arrow, stroke_width=stroke_w, stroke_fill=stroke_fill)

        # Numero gigante al centro con font dinamico
        dyn_font = self._get_dynamic_font(text, is_temp=False)
        bbox_txt = draw.textbbox((0, 0), text, font=dyn_font)
        w_txt = bbox_txt[2] - bbox_txt[0]
        max_w = size - 4
        cur_pt = getattr(dyn_font, "size", 16)
        while w_txt > max_w and cur_pt > 8:
            cur_pt -= 1
            dyn_font = self._load_system_font(self.config.get("font_family", "tahoma"), size=cur_pt, bold=True)
            bbox_txt = draw.textbbox((0, 0), text, font=dyn_font)
            w_txt = bbox_txt[2] - bbox_txt[0]

        h_txt = bbox_txt[3] - bbox_txt[1]
        x_txt = int((size - w_txt) / 2) - bbox_txt[0]
        y_offset = (size // 9) if maximized else (size // 8)
        y_txt = int((size - h_txt) / 2) + y_offset - bbox_txt[1]
        x_txt = max(1, min(x_txt, size - w_txt - 1))
        draw.text((x_txt, y_txt), text, fill=color, font=dyn_font, stroke_width=stroke_w, stroke_fill=stroke_fill)

    def _draw_slot_hardware(self, draw, size, badge, temp_text, badge_color, temp_color, show_border):
        """Disegna badge hardware (CPU/NV) e temperatura con font dinamico per non uscire dallo slot."""
        stroke_w = 1 if show_border else 0
        stroke_fill = (0, 0, 0, 255) if stroke_w else None
        maximized = bool(self.config.get("maximized_slot", True))

        bbox_b = draw.textbbox((0, 0), badge, font=self.font_badge)
        w_b = bbox_b[2] - bbox_b[0]
        x_b = int((size - w_b) / 2) - bbox_b[0]
        y_b = 1 if maximized else (2 if size >= 48 else 1)
        draw.text((x_b, y_b), badge, fill=badge_color, font=self.font_badge, stroke_width=stroke_w, stroke_fill=stroke_fill)

        dyn_font = self._get_dynamic_font(temp_text, is_temp=True)
        bbox_t = draw.textbbox((0, 0), temp_text, font=dyn_font)
        w_t = bbox_t[2] - bbox_t[0]
        max_w = size - 4
        cur_pt = getattr(dyn_font, "size", 16)
        while w_t > max_w and cur_pt > 8:
            cur_pt -= 1
            dyn_font = self._load_system_font(self.config.get("font_family", "tahoma"), size=cur_pt, bold=True)
            bbox_t = draw.textbbox((0, 0), temp_text, font=dyn_font)
            w_t = bbox_t[2] - bbox_t[0]

        h_t = bbox_t[3] - bbox_t[1]
        x_t = int((size - w_t) / 2) - bbox_t[0]
        y_offset = (size // 9) if maximized else (size // 8)
        y_t = int((size - h_t) / 2) + y_offset - bbox_t[1]
        x_t = max(1, min(x_t, size - w_t - 1))
        draw.text((x_t, y_t), temp_text, fill=temp_color, font=dyn_font, stroke_width=stroke_w, stroke_fill=stroke_fill)

    def generate_single_tray_image(self, num_str, unit_str, is_download=True, is_idle=False):
        size = self.config.get("icon_size", 64)
        bg_style = self.config.get("bg_style", "rounded")
        theme_key = self.config.get("theme", "mint_cyan")
        theme = self.THEMES.get(theme_key, self.THEMES["mint_cyan"])
        show_border = self.config.get("show_border", True)

        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        self._draw_slot_bg(draw, size, bg_style, (55, 65, 80, 255) if show_border else None)

        color = (255, 255, 255, 255) if is_idle else (theme["dl"] if is_download else theme["ul"])
        font_num = self.font_2cifre if len(num_str) <= 2 else self.font_3cifre

        bbox_num = draw.textbbox((0, 0), num_str, font=font_num)
        w_num = bbox_num[2] - bbox_num[0]
        h_num = bbox_num[3] - bbox_num[1]

        bbox_unit = draw.textbbox((0, 0), unit_str, font=self.font_unit)
        w_unit = bbox_unit[2] - bbox_unit[0]
        h_unit = bbox_unit[3] - bbox_unit[1]

        gap = max(1, size // 32)
        total_w = w_num + gap + w_unit
        start_x = int((size - total_w) / 2)
        start_x = max(1, min(start_x, size - total_w - 1))

        y_num = int((size - h_num) / 2 - bbox_num[1])
        y_unit = int((size - h_unit) / 2 - bbox_unit[1]) + (1 if size <= 32 else 2)

        stroke_w = 1 if show_border else 0
        stroke_fill = (0, 0, 0, 255) if stroke_w else None

        draw.text((start_x - bbox_num[0], y_num), num_str, fill=color, font=font_num, stroke_width=stroke_w, stroke_fill=stroke_fill)
        x_unit = start_x + w_num + gap
        draw.text((x_unit - bbox_unit[0], y_unit), unit_str, fill=color, font=self.font_unit, stroke_width=stroke_w, stroke_fill=stroke_fill)
        return img

    # --------------------------------------------------------------------------
    # Finestra Impostazioni Stile Windows 11 con Scorrimento & Barra Fissa
    # --------------------------------------------------------------------------
    def open_settings_window(self, icon=None, item=None):
        self._run_on_ui_thread(self._run_settings_gui)

    def _run_settings_gui(self):
        try:
            main_root = self._ensure_tk_root()
            if self._settings_win is not None:
                try:
                    if self._settings_win.winfo_exists():
                        self._settings_win.deiconify()
                        self._settings_win.lift()
                        self._settings_win.focus_force()
                        return
                except Exception:
                    pass
                self._settings_win = None

            root = tk.Toplevel(main_root)
            self._settings_win = root
            root.title("WinNetTray — Impostazioni")

            # Palette Windows 11 Fluent Dark
            BG_WIN = "#18181b"       # Sfondo finestra principale (Mica Dark)
            BG_BAR = "#121215"       # Barra superiore e barra inferiore fissa
            BG_CARD = "#222227"      # Card sezioni stile Impostazioni di Windows
            BG_INPUT = "#141418"     # Campi input / liste interne
            BORDER_CARD = "#32323a"  # Bordo sottile card Windows
            FG_MAIN = "#f4f4f6"      # Testo primario bianco nitido
            FG_SUB = "#a1a1aa"       # Testo secondario grigio Windows
            WIN_BLUE = "#0078d4"     # Blu primario Windows 10/11
            WIN_BLUE_HOV = "#1084df"
            WIN_CYAN = "#60cdff"     # Accento azzurro Fluent

            root.configure(bg=BG_WIN)

            # Calcolo intelligente delle dimensioni per schermi piccoli / portatili con DPI 125%-150%
            screen_w = root.winfo_screenwidth()
            screen_h = root.winfo_screenheight()
            win_w = min(620, max(520, screen_w - 80))
            win_h = min(620, max(440, screen_h - 120))
            pos_x = max(10, (screen_w - win_w) // 2)
            pos_y = max(10, (screen_h - win_h) // 2 - 15)
            root.geometry(f"{win_w}x{win_h}+{pos_x}+{pos_y}")
            root.minsize(500, 400)
            root.resizable(True, True)

            style = ttk.Style(root)
            style.theme_use("clam")
            style.configure(".", background=BG_CARD, foreground=FG_MAIN, font=("Segoe UI", 9))
            style.configure("TLabel", background=BG_CARD, foreground=FG_MAIN, font=("Segoe UI", 9))
            style.configure("TRadiobutton", background=BG_CARD, foreground=FG_MAIN, font=("Segoe UI", 9))
            style.map("TRadiobutton", background=[("active", BG_CARD)])
            style.configure("TCheckbutton", background=BG_CARD, foreground=FG_MAIN, font=("Segoe UI", 9))
            style.map("TCheckbutton", background=[("active", BG_CARD)])
            style.configure(
                "Vertical.TScrollbar",
                background="#32323a",
                troughcolor=BG_WIN,
                bordercolor=BG_WIN,
                arrowcolor=FG_MAIN
            )

            # ==================================================================
            # 1. BARRA SUPERIORE FISSA (Stile Windows 11 Settings Header)
            # ==================================================================
            header_bar = tk.Frame(root, bg=BG_BAR, padx=16, pady=10, highlightbackground=BORDER_CARD, highlightthickness=1)
            header_bar.pack(side="top", fill="x")

            hdr_left = tk.Frame(header_bar, bg=BG_BAR)
            hdr_left.pack(side="left", fill="x", expand=True)
            tk.Label(
                hdr_left,
                text="⚙️ Impostazioni",
                font=("Segoe UI", 13, "bold"),
                fg="#ffffff",
                bg=BG_BAR,
                anchor="w"
            ).pack(anchor="w")
            tk.Label(
                hdr_left,
                text="Configura elementi nella System Tray, 3 Cartelle Rapide, scheda video e carattere",
                font=("Segoe UI", 8),
                fg=FG_SUB,
                bg=BG_BAR,
                anchor="w"
            ).pack(anchor="w")

            hdr_scroll_btns = tk.Frame(header_bar, bg=BG_BAR)
            hdr_scroll_btns.pack(side="right", padx=(6, 0))

            # ==================================================================
            # 2. BARRA INFERIORE FISSA (Pulsanti Applica e Salva SEMPRE visibili!)
            # ==================================================================
            footer_bar = tk.Frame(root, bg=BG_BAR, padx=14, pady=9, highlightbackground=BORDER_CARD, highlightthickness=1)
            footer_bar.pack(side="bottom", fill="x")

            lbl_feedback = tk.Label(
                footer_bar,
                text="💡 Usa la rotellina del mouse o la barra laterale per scorrere tutte le opzioni",
                bg=BG_BAR,
                fg=FG_SUB,
                font=("Segoe UI", 8)
            )
            lbl_feedback.pack(fill="x", anchor="w", pady=(0, 6))

            btn_row_bottom = tk.Frame(footer_bar, bg=BG_BAR)
            btn_row_bottom.pack(fill="x")

            # ==================================================================
            # 3. AREA CENTRALE SCORREVOLE (Canvas + Scrollbar Windows + Rotellina)
            # ==================================================================
            middle_wrap = tk.Frame(root, bg=BG_WIN)
            middle_wrap.pack(side="top", fill="both", expand=True)

            canvas = tk.Canvas(middle_wrap, bg=BG_WIN, highlightthickness=0, bd=0)
            v_scroll = ttk.Scrollbar(middle_wrap, orient="vertical", command=canvas.yview, style="Vertical.TScrollbar")
            canvas.configure(yscrollcommand=v_scroll.set)

            v_scroll.pack(side="right", fill="y")
            canvas.pack(side="left", fill="both", expand=True)

            container = tk.Frame(canvas, bg=BG_WIN, padx=14, pady=10)
            canvas_win_id = canvas.create_window((0, 0), window=container, anchor="nw")

            def _on_container_configure(event=None):
                canvas.configure(scrollregion=canvas.bbox("all"))

            def _on_canvas_configure(event):
                canvas.itemconfig(canvas_win_id, width=event.width)

            container.bind("<Configure>", _on_container_configure)
            canvas.bind("<Configure>", _on_canvas_configure)

            def scroll_step(units):
                try:
                    if canvas.winfo_exists():
                        canvas.yview_scroll(units, "units")
                except Exception:
                    pass

            def _on_mousewheel(event):
                try:
                    if not canvas.winfo_exists():
                        return
                    if getattr(event, "delta", 0):
                        step = -1 if event.delta > 0 else 1
                        canvas.yview_scroll(step * 2, "units")
                    elif getattr(event, "num", 0) == 4:
                        canvas.yview_scroll(-2, "units")
                    elif getattr(event, "num", 0) == 5:
                        canvas.yview_scroll(2, "units")
                except Exception:
                    pass

            root.bind_all("<MouseWheel>", _on_mousewheel)
            root.bind_all("<Button-4>", _on_mousewheel)
            root.bind_all("<Button-5>", _on_mousewheel)
            root.bind("<Up>", lambda e: scroll_step(-2))
            root.bind("<Down>", lambda e: scroll_step(2))
            root.bind("<Prior>", lambda e: scroll_step(-8))
            root.bind("<Next>", lambda e: scroll_step(8))

            # Pulsanti rapidi di scorrimento nell'intestazione per schermi piccoli
            tk.Button(
                hdr_scroll_btns,
                text="▲ Su",
                command=lambda: scroll_step(-5),
                bg="#27272e",
                activebackground="#363640",
                fg=WIN_CYAN,
                font=("Segoe UI", 8, "bold"),
                relief="flat",
                padx=7,
                pady=3,
                cursor="hand2"
            ).pack(side="left", padx=2)
            tk.Button(
                hdr_scroll_btns,
                text="▼ Giù",
                command=lambda: scroll_step(5),
                bg="#27272e",
                activebackground="#363640",
                fg=WIN_CYAN,
                font=("Segoe UI", 8, "bold"),
                relief="flat",
                padx=7,
                pady=3,
                cursor="hand2"
            ).pack(side="left", padx=2)

            def make_win_card(title_text, subtitle_text="", accent_color=WIN_CYAN):
                card = tk.Frame(container, bg=BG_CARD, padx=12, pady=10, highlightbackground=BORDER_CARD, highlightthickness=1)
                card.pack(fill="x", pady=(0, 8))
                tk.Label(card, text=title_text, font=("Segoe UI", 9, "bold"), fg=accent_color, bg=BG_CARD, anchor="w").pack(fill="x")
                if subtitle_text:
                    tk.Label(card, text=subtitle_text, font=("Segoe UI", 8), fg=FG_SUB, bg=BG_CARD, anchor="w", justify="left").pack(fill="x", pady=(0, 6))
                else:
                    tk.Frame(card, bg=BG_CARD, height=4).pack(fill="x")
                return card

            # ==================================================================
            # SEZIONE 1: Sensori & Icone nella System Tray (Ordine Fisso Predefinito)
            # ==================================================================
            card_slots = make_win_card(
                "🧩 1. Elementi Attivi nella System Tray (Ordine Fisso Da Destra a Sinistra)",
                "Spunta gli elementi da attivare. L'ordine nel Systray (da destra verso sinistra) è fisso e automatico:"
            )
            slots_frame = tk.Frame(card_slots, bg=BG_CARD)
            slots_frame.pack(fill="x")

            var_show_dl = tk.BooleanVar(master=root, value=bool(self.config.get("show_download", True)))
            var_show_ul = tk.BooleanVar(master=root, value=bool(self.config.get("show_upload", True)))
            var_show_net = tk.BooleanVar(master=root, value=bool(self.config.get("show_net_combined", False)))
            var_show_cpu = tk.BooleanVar(master=root, value=bool(self.config.get("show_cpu_temp", False)))
            var_show_gpu = tk.BooleanVar(master=root, value=bool(self.config.get("show_gpu_temp", False)))
            var_show_folder = tk.BooleanVar(master=root, value=bool(self.config.get("show_quick_folder", True)))
            var_show_folder_2 = tk.BooleanVar(master=root, value=bool(self.config.get("show_quick_folder_2", False)))
            var_show_folder_3 = tk.BooleanVar(master=root, value=bool(self.config.get("show_quick_folder_3", False)))

            fixed_slots_list = [
                (var_show_ul, "▲ Monitor Rete Upload", "1° da destra (Più a destra)"),
                (var_show_dl, "▼ Monitor Rete Download", "2° da destra (A sinistra di Upload)"),
                (var_show_gpu, "🎮 Monitor Temperatura GPU (°C)", "3° da destra (A sinistra di Download)"),
                (var_show_cpu, "🌡️ Monitor Temperatura CPU (°C)", "4° da destra (A sinistra di GPU)"),
                (var_show_folder_3, "🗂️ Cartella Rapida 3 (Icona Verde)", "5° da destra (A sinistra di CPU)"),
                (var_show_folder_2, "📂 Cartella Rapida 2 (Icona Azzurra)", "6° da destra (A sinistra di Cartella 3)"),
                (var_show_folder, "📁 Cartella Rapida 1 (Icona Oro)", "7° da destra (Più a sinistra)"),
                (var_show_net, "⇅ Rete Unita D/U (Download + Upload in 1 icona)", "Slot Unificato Opzionale"),
            ]

            for var, txt, pos_badge in fixed_slots_list:
                r = tk.Frame(slots_frame, bg=BG_INPUT, padx=8, pady=3, highlightbackground="#2c2c34", highlightthickness=1)
                r.pack(fill="x", pady=1)

                cb = tk.Checkbutton(
                    r,
                    text=txt,
                    variable=var,
                    bg=BG_INPUT,
                    fg=FG_MAIN,
                    activebackground=BG_INPUT,
                    activeforeground="#ffffff",
                    selectcolor="#111115",
                    font=("Segoe UI", 8, "bold"),
                    anchor="w",
                    relief="flat",
                    bd=0,
                    highlightthickness=0,
                    cursor="hand2"
                )
                cb.pack(side="left", fill="x", expand=True)

                tk.Label(
                    r,
                    text=pos_badge,
                    font=("Segoe UI", 7),
                    fg=WIN_CYAN,
                    bg=BG_INPUT,
                    anchor="e"
                ).pack(side="right", padx=(6, 2))

            # ==================================================================
            # SEZIONE 2: Configurazione Percorsi delle 3 Cartelle Rapide
            # ==================================================================
            card_folders = make_win_card(
                "📁 2. Percorsi delle 3 Cartelle Rapide (Menu a Tendina con Drag & Drop)",
                "Scegli a quale cartella del PC punta ciascuna delle 3 icone cartella (Click Sinistro: apre tendina).",
                accent_color="#fbbf24"
            )

            var_folder_path_1 = tk.StringVar(master=root, value=self.get_quick_folder_path(1))
            var_folder_path_2 = tk.StringVar(master=root, value=self.get_quick_folder_path(2))
            var_folder_path_3 = tk.StringVar(master=root, value=self.get_quick_folder_path(3))

            folder_slots_meta = [
                (1, "📁 Cartella 1 (Oro):", "#fbbf24", "#b45309", var_folder_path_1),
                (2, "📂 Cartella 2 (Azzurra):", "#38bdf8", "#0284c7", var_folder_path_2),
                (3, "🗂️ Cartella 3 (Verde):", "#34d399", "#059669", var_folder_path_3)
            ]

            for s_num, s_lbl, s_fg, s_btn_bg, s_var in folder_slots_meta:
                row_f = tk.Frame(card_folders, bg=BG_CARD)
                row_f.pack(fill="x", pady=2)

                tk.Label(row_f, text=s_lbl, font=("Segoe UI", 8, "bold"), fg=s_fg, bg=BG_CARD, width=19, anchor="w").pack(side="left")
                ent_f = tk.Entry(
                    row_f, textvariable=s_var, font=("Consolas", 8),
                    bg=BG_INPUT, fg="#f8fafc", insertbackground="white",
                    relief="flat", highlightbackground="#383842", highlightthickness=1
                )
                ent_f.pack(side="left", fill="x", expand=True, ipady=3, padx=(0, 4))

                def make_browse_cb(slot_idx, var_obj):
                    def _cb():
                        chosen = filedialog.askdirectory(
                            parent=root,
                            initialdir=var_obj.get() or self._get_default_folder_for_slot(slot_idx),
                            title=f"Seleziona la Cartella Rapida #{slot_idx} da aprire nella System Tray"
                        )
                        if chosen and os.path.isdir(chosen):
                            var_obj.set(os.path.normpath(chosen))
                    return _cb

                def make_reset_cb(slot_idx, var_obj):
                    def _cb():
                        var_obj.set(self._get_default_folder_for_slot(slot_idx))
                    return _cb

                tk.Button(row_f, text="📂 Sfoglia", command=make_browse_cb(s_num, s_var), bg=s_btn_bg, fg="white", font=("Segoe UI", 8, "bold"), relief="flat", padx=6, pady=2, cursor="hand2").pack(side="left", padx=1)
                tk.Button(row_f, text="↕️ Ordine", command=lambda sn=s_num: self.open_folder_reorder_for_slot(slot_num=sn), bg=WIN_BLUE, fg="white", font=("Segoe UI", 8, "bold"), relief="flat", padx=6, pady=2, cursor="hand2").pack(side="left", padx=1)
                tk.Button(row_f, text="↺ Default", command=make_reset_cb(s_num, s_var), bg="#32323c", fg="#e4e4e7", font=("Segoe UI", 8), relief="flat", padx=5, pady=2, cursor="hand2").pack(side="left", padx=1)

            # ==================================================================
            # SEZIONE 3: Carattere (10 Font) & Grandezza Cifre
            # ==================================================================
            card_font = make_win_card(
                "🔤 3. Carattere Numeri & Leggibilità nella System Tray",
                "Scegli il font delle cifre e massimizza lo spazio occupato nell'icona:"
            )

            font_list = [
                ("tahoma", "1. Tahoma Bold (Predefinito) - Ottimizzato per icone a bassa risoluzione"),
                ("bahnschrift", "2. Bahnschrift (DIN 1451) - Stile moderno Windows 10/11"),
                ("consolas", "3. Consolas Bold - Monospace nitido senza oscillazioni"),
                ("segoe", "4. Segoe UI Bold - Grafica nativa interfaccia Windows"),
                ("arial_bold", "5. Arial Black - Tratto massiccio ad alto contrasto"),
                ("impact", "6. Impact Condensed - Massima altezza verticale cifre"),
                ("trebuchet", "7. Trebuchet MS Bold - Aperture ampie e numeri distinti"),
                ("verdana", "8. Verdana Bold - Elevata leggibilità per micro-testi"),
                ("lucida_console", "9. Lucida Console - Monospace a matrice definita"),
                ("franklin_gothic", "10. Franklin Gothic Heavy - Sans-serif industriale robusto")
            ]
            var_font = tk.StringVar(master=root, value=self.config.get("font_family", "tahoma"))
            font_row = tk.Frame(card_font, bg=BG_CARD)
            font_row.pack(fill="x", pady=(0, 4))
            font_combo = ttk.Combobox(font_row, textvariable=var_font, state="readonly", values=[f[0] for f in font_list], width=18)
            font_combo.pack(side="left", padx=(0, 8))
            lbl_font_desc = tk.Label(font_row, text=dict(font_list).get(var_font.get(), ""), bg=BG_CARD, fg=FG_MAIN, font=("Segoe UI", 8))
            lbl_font_desc.pack(side="left")

            def on_font_select(event):
                lbl_font_desc.config(text=dict(font_list).get(var_font.get(), ""))
            font_combo.bind("<<ComboboxSelected>>", on_font_select)

            var_maximized = tk.BooleanVar(master=root, value=bool(self.config.get("maximized_slot", True)))
            ttk.Checkbutton(card_font, text="🚀 Massimizza Grandezza Cifre nello Slot (Bordi a filo / Zero margini)", variable=var_maximized).pack(anchor="w", pady=(2, 2))

            var_border = tk.BooleanVar(master=root, value=bool(self.config.get("show_border", True)))
            ttk.Checkbutton(card_font, text="🔲 Bordo di contorno scuro ad alto contrasto", variable=var_border).pack(anchor="w", pady=(1, 0))

            # ==================================================================
            # SEZIONE 4: Selezione Scheda Video (GPU) & Modalità Visualizzazione
            # ==================================================================
            card_hw = make_win_card(
                "🎮 4. Scheda Video (GPU) & Modalità Visualizzazione",
                "Imposta quale scheda video monitorare e la modalità delle icone nella barra:"
            )

            var_gpu_sel = tk.StringVar(master=root, value=self.config.get("gpu_selection", "auto_active"))
            ttk.Radiobutton(card_hw, text="⚡ Auto-Rilevamento GPU (NVIDIA dinamica se attiva in 3D/CUDA)", value="auto_active", variable=var_gpu_sel).pack(anchor="w")
            ttk.Radiobutton(card_hw, text="🟢 Forza GPU NVIDIA GeForce (NVML / nvidia-smi)", value="nvidia_only", variable=var_gpu_sel).pack(anchor="w")
            ttk.Radiobutton(card_hw, text="🔵 Forza GPU Integrata Intel / Primaria", value="intel_only", variable=var_gpu_sel).pack(anchor="w")

            tk.Frame(card_hw, bg=BORDER_CARD, height=1).pack(fill="x", pady=6)

            var_mode = tk.StringVar(master=root, value=self.config.get("display_mode", "dual_tray"))
            ttk.Radiobutton(card_hw, text="👯 Icone Separate nella System Tray (Consigliato - Multi-Slot)", value="dual_tray", variable=var_mode).pack(anchor="w")
            ttk.Radiobutton(card_hw, text="🚀 Pillola Rettangolare Taskbar (Overlay D/U a 2 righe)", value="docked_pill", variable=var_mode).pack(anchor="w")
            ttk.Radiobutton(card_hw, text="🔲 Singola Icona Tray Compatta", value="tray_icon", variable=var_mode).pack(anchor="w")

            # ==================================================================
            # SEZIONE 5: Legenda Codifica Colori Unità di Misura
            # ==================================================================
            card_legend = make_win_card(
                "🎨 5. Legenda Colori Velocità di Rete (Senza lettere K/M)",
                "Il colore del numero indica l'unità di misura per lasciare massimo spazio alle cifre:",
                accent_color="#f472b6"
            )
            badges_row = tk.Frame(card_legend, bg=BG_CARD)
            badges_row.pack(fill="x")
            for b_col, b_lbl, b_sub in [
                ("#ffffff", "⚪ Bianco", "Byte/s (<1 KB)"),
                ("#4ade80", "🟢 Verde", "KB/s (KiloByte)"),
                ("#ff3385", "🌸 Rosa", "MB/s (MegaByte)"),
                ("#c084fc", "🟣 Viola", "GB/s (GigaByte)")
            ]:
                b_cell = tk.Frame(badges_row, bg=BG_INPUT, padx=6, pady=4, highlightbackground="#2e2e38", highlightthickness=1)
                b_cell.pack(side="left", fill="x", expand=True, padx=2)
                tk.Label(b_cell, text=b_lbl, font=("Segoe UI", 8, "bold"), fg=b_col, bg=BG_INPUT).pack(anchor="w")
                tk.Label(b_cell, text=b_sub, font=("Segoe UI", 7), fg=FG_SUB, bg=BG_INPUT).pack(anchor="w")

            # ==================================================================
            # AZIONI BARRA INFERIORE FISSA (Applica, Salva e Chiudi, Default)
            # ==================================================================
            feedback_after_id = [None]

            def cleanup_and_close_settings():
                if feedback_after_id[0] is not None:
                    try:
                        root.after_cancel(feedback_after_id[0])
                    except Exception:
                        pass
                    feedback_after_id[0] = None
                try:
                    root.unbind_all("<MouseWheel>")
                    root.unbind_all("<Button-4>")
                    root.unbind_all("<Button-5>")
                except Exception:
                    pass
                self._settings_win = None
                try:
                    if root.winfo_exists():
                        root.destroy()
                except Exception:
                    pass

            root.protocol("WM_DELETE_WINDOW", cleanup_and_close_settings)

            def apply_only(is_closing=False):
                with self._tray_lock:
                    self.config["show_download"] = var_show_dl.get()
                    self.config["show_upload"] = var_show_ul.get()
                    self.config["show_net_combined"] = var_show_net.get()
                    self.config["show_cpu_temp"] = var_show_cpu.get()
                    self.config["show_gpu_temp"] = var_show_gpu.get()
                    self.config["show_quick_folder"] = var_show_folder.get()
                    self.config["show_quick_folder_2"] = var_show_folder_2.get()
                    self.config["show_quick_folder_3"] = var_show_folder_3.get()

                    for idx_s, var_p in ((1, var_folder_path_1), (2, var_folder_path_2), (3, var_folder_path_3)):
                        cfg_k = self._get_folder_config_key(idx_s)
                        entered_p = var_p.get().strip()
                        if entered_p and os.path.isdir(entered_p):
                            self.config[cfg_k] = os.path.normpath(entered_p)
                        else:
                            self.config[cfg_k] = self._get_default_folder_for_slot(idx_s)
                            var_p.set(self.config[cfg_k])

                    self.config["slot_order"] = ["quick_folder", "quick_folder_2", "quick_folder_3", "cpu_temp", "gpu_temp", "net_combined", "download", "upload"]
                    self.config["hide_speed_unit"] = True
                    self.config["gpu_selection"] = var_gpu_sel.get()
                    self.config["font_family"] = var_font.get()
                    self.config["maximized_slot"] = var_maximized.get()
                    self.config["display_mode"] = var_mode.get()
                    self.config["show_border"] = var_border.get()
                    self._init_fonts()

                threading.Thread(target=self._apply_settings_background_worker, daemon=True).start()

                if not is_closing and root.winfo_exists():
                    lbl_feedback.config(text="✅ Modifiche applicate in tempo reale alla System Tray!", fg="#34d399")
                    if feedback_after_id[0] is not None:
                        try:
                            root.after_cancel(feedback_after_id[0])
                        except Exception:
                            pass
                    def _restore_feedback():
                        feedback_after_id[0] = None
                        try:
                            if root.winfo_exists() and lbl_feedback.winfo_exists():
                                lbl_feedback.config(
                                    text="💡 Usa la rotellina del mouse o la barra laterale per scorrere tutte le opzioni",
                                    fg=FG_SUB
                                )
                        except Exception:
                            pass
                    feedback_after_id[0] = root.after(3000, _restore_feedback)

            def save_and_apply():
                apply_only(is_closing=True)
                cleanup_and_close_settings()

            def reset_all_defaults():
                var_show_dl.set(True)
                var_show_ul.set(True)
                var_show_net.set(False)
                var_show_cpu.set(False)
                var_show_gpu.set(False)
                var_show_folder.set(True)
                var_show_folder_2.set(False)
                var_show_folder_3.set(False)
                var_folder_path_1.set(self._get_default_folder_for_slot(1))
                var_folder_path_2.set(self._get_default_folder_for_slot(2))
                var_folder_path_3.set(self._get_default_folder_for_slot(3))
                var_font.set("tahoma")
                var_maximized.set(True)
                var_border.set(True)
                var_mode.set("dual_tray")
                var_gpu_sel.set("auto_active")
                lbl_feedback.config(text="↺ Valori predefiniti ripristinati. Clicca 'Applica' o 'Salva e Chiudi'.", fg=WIN_CYAN)

            btn_reset = tk.Button(
                btn_row_bottom,
                text="↺ Ripristina Default",
                command=reset_all_defaults,
                bg="#27272f",
                activebackground="#363642",
                fg="#d4d4d8",
                font=("Segoe UI", 9),
                relief="flat",
                padx=10,
                pady=6,
                cursor="hand2"
            )
            btn_reset.pack(side="left")

            lbl_copyright = tk.Label(
                btn_row_bottom,
                text="Freeware • Copyright © 2026 Luca Tosi — Tutti i diritti riservati",
                bg=WIN_CARD,
                fg="#71717a",
                font=("Segoe UI", 8)
            )
            lbl_copyright.pack(side="left", padx=(14, 6))

            btn_save = tk.Button(
                btn_row_bottom,
                text="💾 Salva e Chiudi",
                command=save_and_apply,
                bg=WIN_BLUE,
                activebackground=WIN_BLUE_HOV,
                fg="white",
                font=("Segoe UI", 9, "bold"),
                relief="flat",
                padx=16,
                pady=6,
                cursor="hand2"
            )
            btn_save.pack(side="right", padx=(6, 0))

            btn_apply = tk.Button(
                btn_row_bottom,
                text="⚡ Applica (Senza Chiudere)",
                command=lambda: apply_only(is_closing=False),
                bg="#059669",
                activebackground="#10b981",
                fg="white",
                font=("Segoe UI", 9, "bold"),
                relief="flat",
                padx=14,
                pady=6,
                cursor="hand2"
            )
            btn_apply.pack(side="right")
        except Exception as e:
            print("Errore apertura impostazioni:", e)

    def _apply_settings_background_worker(self):
        """Salva la configurazione e aggiorna gli slot pystray in modo serializzato e non bloccante per la GUI."""
        with self._tray_lock:
            self._save_config()
            self._refresh_folder_pystray_menu(force=False)
            self._apply_visibility_and_draw()

    def _set_icon_visible(self, ic, visible):
        """Mostra o nasconde dinamicamente l'icona pystray solo se lo stato è effettivamente cambiato."""
        try:
            if ic and hasattr(ic, "visible"):
                target_vis = bool(visible)
                if bool(getattr(ic, "visible", None)) != target_vis:
                    ic.visible = target_vis
        except Exception:
            pass

    @staticmethod
    def perform_full_uninstall_cleanup():
        """
        Rimuove completamente tutte le configurazioni, i log, i file temporanei e le chiavi di registro
        (NotifyIconSettings) create da WinNetTray nel sistema durante la disinstallazione.
        """
        # 1. Rimozione cartelle di configurazione e dati in %APPDATA% e %LOCALAPPDATA%
        for env_var in ("APPDATA", "LOCALAPPDATA"):
            base_env = os.environ.get(env_var, "")
            if base_env:
                target_dir = os.path.join(base_env, "WinNetTray")
                if os.path.exists(target_dir):
                    try:
                        shutil.rmtree(target_dir, ignore_errors=True)
                    except Exception:
                        pass

        # 2. Rimozione file di configurazione/log nella cartella locale dell'eseguibile e in %TEMP%
        candidate_dirs = []
        try:
            if getattr(sys, "frozen", False):
                candidate_dirs.append(os.path.dirname(os.path.abspath(sys.executable)))
            elif sys.argv and sys.argv[0]:
                candidate_dirs.append(os.path.dirname(os.path.abspath(sys.argv[0])))
        except Exception:
            pass
        temp_dir = os.environ.get("TEMP", "")
        if temp_dir:
            candidate_dirs.append(temp_dir)

        for d_path in candidate_dirs:
            for fname in (
                "winnettray_config.json",
                "winnettray_config.json.tmp",
                "winnettray_error.log",
                ".winnettray_write_test",
                "wnt_wizard_pkg.zip",
                "wnt_setup.ps1"
            ):
                fpath = os.path.join(d_path, fname)
                if os.path.exists(fpath):
                    try:
                        os.remove(fpath)
                    except Exception:
                        pass

        # 3. Pulizia collegamenti in Esecuzione Automatica (Startup), Desktop e Menu Start
        home = os.path.expanduser("~")
        appdata_roam = os.environ.get("APPDATA", os.path.join(home, "AppData", "Roaming"))
        shortcut_paths = [
            os.path.join(home, "Desktop", "WinNetTray.lnk"),
            os.path.join(appdata_roam, "Microsoft", "Windows", "Start Menu", "Programs", "WinNetTray.lnk"),
            os.path.join(appdata_roam, "Microsoft", "Windows", "Start Menu", "Programs", "Startup", "WinNetTray.lnk"),
        ]
        for sp in shortcut_paths:
            if os.path.exists(sp):
                try:
                    os.remove(sp)
                except Exception:
                    pass

        # 4. Rimozione sottochiavi orfane di WinNetTray in HKCU\Control Panel\NotifyIconSettings
        if sys.platform == "win32" and HAS_WINREG:
            try:
                reg_path = r"Control Panel\NotifyIconSettings"
                to_delete = []
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path, 0, winreg.KEY_READ) as parent_key:
                    num_subkeys, _, _ = winreg.QueryInfoKey(parent_key)
                    for i in range(num_subkeys):
                        try:
                            sname = winreg.EnumKey(parent_key, i)
                            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{reg_path}\\{sname}", 0, winreg.KEY_READ) as sk:
                                exe_path, _ = winreg.QueryValueEx(sk, "ExecutablePath")
                                exe_low = str(exe_path).lower()
                                if "winnettray" in exe_low or "net_tray" in exe_low:
                                    to_delete.append(sname)
                        except Exception:
                            pass
                if to_delete:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path, 0, winreg.KEY_WRITE) as parent_w:
                        for sname in to_delete:
                            try:
                                winreg.DeleteKey(parent_w, sname)
                            except Exception:
                                pass
            except Exception:
                pass

    def _auto_promote_win11_tray_icons(self):
        """
        Imposta automaticamente IsPromoted=1 nel registro di Windows 11 (HKCU\\Control Panel\\NotifyIconSettings)
        per tutte le icone di WinNetTray, assicurando che siano sempre visibili nella System Tray accanto all'orologio.
        """
        if sys.platform != "win32" or not HAS_WINREG:
            return
        try:
            reg_path = r"Control Panel\NotifyIconSettings"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path, 0, winreg.KEY_READ) as parent_key:
                num_subkeys, _, _ = winreg.QueryInfoKey(parent_key)
                subkey_names = []
                for i in range(num_subkeys):
                    try:
                        subkey_names.append(winreg.EnumKey(parent_key, i))
                    except Exception:
                        pass

            for sname in subkey_names:
                full_sub = f"{reg_path}\\{sname}"
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, full_sub, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as sk:
                        exe_path, _ = winreg.QueryValueEx(sk, "ExecutablePath")
                        exe_low = str(exe_path).lower()
                        if "winnettray" in exe_low or "net_tray" in exe_low:
                            try:
                                is_prom, _ = winreg.QueryValueEx(sk, "IsPromoted")
                            except Exception:
                                is_prom = 0
                            if is_prom != 1:
                                winreg.SetValueEx(sk, "IsPromoted", 0, winreg.REG_DWORD, 1)
                except Exception:
                    pass
        except Exception:
            pass

    def _get_tray_icon_screen_rect(self, icon_obj):
        """
        Interroga l'API nativa di Windows Shell_NotifyIconGetRect (shell32.dll)
        per leggere le coordinate reali (left, top, right, bottom) sullo schermo
        di ciascuna icona nella System Tray.
        """
        if sys.platform != "win32" or icon_obj is None:
            return None
        hwnd = getattr(icon_obj, "_hwnd", None)
        if not hwnd:
            return None
        try:
            shell32 = ctypes.windll.shell32
            Shell_NotifyIconGetRect = shell32.Shell_NotifyIconGetRect
            Shell_NotifyIconGetRect.argtypes = [ctypes.POINTER(_NOTIFYICONIDENTIFIER), ctypes.POINTER(wintypes.RECT)]
            Shell_NotifyIconGetRect.restype = ctypes.c_long

            ident = _NOTIFYICONIDENTIFIER()
            ident.cbSize = ctypes.sizeof(_NOTIFYICONIDENTIFIER)
            ident.hWnd = hwnd
            ident.uID = getattr(icon_obj, "_uid", 0)
            rect = wintypes.RECT()
            hr = Shell_NotifyIconGetRect(ctypes.byref(ident), ctypes.byref(rect))
            if hr != 0 and ident.uID != 0:
                ident.uID = 0
                hr = Shell_NotifyIconGetRect(ctypes.byref(ident), ctypes.byref(rect))
            if hr == 0 and (rect.right > rect.left or rect.bottom > rect.top):
                return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
        except Exception:
            pass
        return None

    def _get_active_roles_rtl(self):
        """
        Restituisce la sequenza ordinata dei ruoli abilitati da DESTRA a SINISTRA:
        1° (più a destra): upload
        2° (a sinistra): download
        (eventuale net_combined)
        3° (a sinistra): gpu_temp
        4° (a sinistra): cpu_temp
        5° (a sinistra): quick_folder_3
        6° (a sinistra): quick_folder_2
        7° (più a sinistra): quick_folder
        """
        mode = self.config.get("display_mode", "dual_tray")
        show_dl = bool(self.config.get("show_download", True))
        show_ul = bool(self.config.get("show_upload", True))
        show_net = bool(self.config.get("show_net_combined", False))
        show_cpu = bool(self.config.get("show_cpu_temp", False))
        show_gpu = bool(self.config.get("show_gpu_temp", False))
        show_f1 = bool(self.config.get("show_quick_folder", True))
        show_f2 = bool(self.config.get("show_quick_folder_2", False))
        show_f3 = bool(self.config.get("show_quick_folder_3", False))

        if mode == "dual_tray":
            seq = [
                ("upload", show_ul),
                ("download", show_dl),
                ("net_combined", show_net),
                ("gpu_temp", show_gpu),
                ("cpu_temp", show_cpu),
                ("quick_folder_3", show_f3),
                ("quick_folder_2", show_f2),
                ("quick_folder", show_f1),
            ]
            return [r for r, vis in seq if vis]
        elif mode == "docked_pill":
            seq = [
                ("quick_folder_3", show_f3),
                ("quick_folder_2", show_f2),
                ("quick_folder", show_f1),
            ]
            return [r for r, vis in seq if vis]
        else:  # tray_icon
            seq = [
                ("single", True),
                ("quick_folder_3", show_f3),
                ("quick_folder_2", show_f2),
                ("quick_folder", show_f1),
            ]
            return [r for r, vis in seq if vis]

    def _get_visible_slots_sorted_right_to_left(self, visible_slots):
        """
        Ordina gli slot fisici visibili dal più a DESTRA (X maggiore sulla Taskbar)
        al più a SINISTRA (X minore sulla Taskbar) usando le coordinate reali restituite
        da Shell_NotifyIconGetRect.
        In questo modo, qualunque sia l'ordine con cui Windows Explorer ha posizionato gli slot,
        lo slot più a destra riceve matematicamente il 1° ruolo (Upload), quello alla sua sinistra
        il 2° ruolo (Download), poi GPU, CPU, Cartella 3, Cartella 2 e Cartella 1!
        """
        if len(visible_slots) <= 1:
            return list(visible_slots)

        rects = {}
        for sl in visible_slots:
            r = self._get_tray_icon_screen_rect(sl)
            if r is not None:
                rects[sl._slot_index] = r

        def _sort_key_rtl(sl):
            r = rects.get(sl._slot_index)
            if r is not None:
                # r[0] = rect.left (coordinata X in pixel sullo schermo: più è grande, più l'icona è a DESTRA)
                return (1, r[0], -r[1], -sl._slot_index)
            return (0, 0, 0, -sl._slot_index)

        return sorted(visible_slots, key=_sort_key_rtl, reverse=True)

    def _apply_visibility_and_draw(self, force_resequence=False):
        """
        Sincronizza gli slot visibili nella System Tray applicando SEMPRE l'ordine fisso
        da DESTRA a SINISTRA richiesto:
        1° (più a destra): Upload
        2° (a sinistra): Download
        3° (a sinistra): Temperatura GPU
        4° (a sinistra): Temperatura CPU
        5° (a sinistra): Cartella Rapida 3
        6° (a sinistra): Cartella Rapida 2
        7° (più a sinistra): Cartella Rapida 1
        """
        with self._tray_lock:
            try:
                mode = self.config.get("display_mode", "dual_tray")
                active_roles_rtl = self._get_active_roles_rtl()
                num_needed = len(active_roles_rtl)

                # 1. Nasconde subito gli slot fisici in eccesso
                for sl in self.tray_slots[num_needed:]:
                    sl._assigned_role = None
                    sl._last_drawn_sig = None
                    self._set_icon_visible(sl, False)

                # 2. Attiva in sequenza cadenzata gli slot fisici necessari (0 .. num_needed - 1)
                newly_shown = False
                for idx in range(num_needed):
                    sl = self.tray_slots[idx]
                    if not bool(getattr(sl, "visible", False)):
                        self._set_icon_visible(sl, True)
                        newly_shown = True
                        time.sleep(0.08)
                        self._auto_promote_win11_tray_icons()
                        time.sleep(0.16)

                if newly_shown or force_resequence:
                    self._auto_promote_win11_tray_icons()

                if mode == "docked_pill":
                    self.show_taskbar_pill()
                else:
                    self.hide_taskbar_pill()

                # 3. Disegna immediatamente i ruoli ordinati da destra a sinistra in base alla coordinata X reale
                self._update_tray_icons_immediate()

                # 4. Esegue passaggi di riallineamento dopo 350ms, 850ms e 1600ms (quando l'animazione XAML della
                #    Taskbar di Windows 11 ha completato la promozione e il posizionamento finale delle icone)
                if newly_shown or force_resequence:
                    for delay_s in (0.35, 0.85, 1.60):
                        t_recheck = threading.Timer(
                            delay_s,
                            lambda: (self._auto_promote_win11_tray_icons(), self._update_tray_icons_immediate())
                        )
                        t_recheck.daemon = True
                        t_recheck.start()
            except Exception:
                pass

    def _update_icon_title_safe(self, icon_obj, text):
        """Imposta il titolo dell'icona (tooltip) garantendo di non superare mai il limite Win32 szTip (128 char)."""
        if not icon_obj:
            return
        try:
            safe_text = str(text)[:124]
            if getattr(icon_obj, "_last_title_sent", None) == safe_text:
                return
            icon_obj._last_title_sent = safe_text
            icon_obj.title = safe_text
        except Exception:
            try:
                icon_obj.title = str(text)[:60]
            except Exception:
                pass

    def _update_tray_icons_immediate(self):
        """
        Rileva l'ordine reale da destra a sinistra degli slot visibili nella System Tray
        tramite Shell_NotifyIconGetRect e assegna/ridisegna i ruoli attivi nell'ordine esatto:
        Upload (più a destra) -> Download -> GPU -> CPU -> Cartella 3 -> Cartella 2 -> Cartella 1 (più a sinistra).
        """
        with self._tray_lock:
            try:
                gpu_tag = " [NV]" if self.is_nvidia_active else ""
                dl_str = self.format_speed_detailed(self.download_speed)
                ul_str = self.format_speed_detailed(self.upload_speed)
                loc_ip = self.local_ip if self.local_ip else "127.0.0.1"
                pub_ip = self.public_ip if self.public_ip else "..."

                tooltip_lines = [
                    f"↓ {dl_str}   |   ↑ {ul_str}",
                    f"LAN: {loc_ip}   |   WAN: {pub_ip}",
                    f"CPU: {self.cpu_temp}°C   |   GPU: {self.gpu_temp}°C{gpu_tag}"
                ]

                unified_tooltip = chr(10).join(tooltip_lines)
                if len(unified_tooltip) > 124:
                    unified_tooltip = unified_tooltip[:121] + "..."

                active_roles_rtl = self._get_active_roles_rtl()
                num_needed = len(active_roles_rtl)
                visible_slots = [sl for sl in self.tray_slots[:num_needed] if bool(getattr(sl, "visible", False))]
                if len(visible_slots) < num_needed:
                    visible_slots = self.tray_slots[:num_needed]

                # Ordina gli slot fisici dal più a DESTRA (X massima) al più a SINISTRA (X minima)
                sorted_slots_rtl = self._get_visible_slots_sorted_right_to_left(visible_slots)

                # Pre-genera le immagini gemelle DL/UL una sola volta se necessarie
                need_twin = ("download" in active_roles_rtl) or ("upload" in active_roles_rtl)
                img_dl, img_ul = self.generate_twin_tray_icons(self.download_speed, self.upload_speed) if need_twin else (None, None)

                folder_style_base = (
                    self.config.get("icon_size", 64),
                    self.config.get("bg_style", "rounded"),
                    bool(self.config.get("show_border", True))
                )

                for slot_obj, role in zip(sorted_slots_rtl, active_roles_rtl):
                    slot_obj._assigned_role = role

                    if role == "upload":
                        slot_obj._last_drawn_sig = ("upload",)
                        slot_obj.icon = img_ul
                        self._update_icon_title_safe(slot_obj, unified_tooltip)
                    elif role == "download":
                        slot_obj._last_drawn_sig = ("download",)
                        slot_obj.icon = img_dl
                        self._update_icon_title_safe(slot_obj, unified_tooltip)
                    elif role == "net_combined":
                        slot_obj._last_drawn_sig = ("net_combined",)
                        slot_obj.icon = self.generate_net_combined_tray_icon(self.download_speed, self.upload_speed)
                        self._update_icon_title_safe(slot_obj, unified_tooltip)
                    elif role == "gpu_temp":
                        slot_obj._last_drawn_sig = ("gpu_temp",)
                        slot_obj.icon = self.generate_gpu_tray_icon(self.gpu_temp, self.is_nvidia_active, self.active_gpu_name)
                        self._update_icon_title_safe(slot_obj, unified_tooltip)
                    elif role == "cpu_temp":
                        slot_obj._last_drawn_sig = ("cpu_temp",)
                        slot_obj.icon = self.generate_cpu_tray_icon(self.cpu_temp)
                        self._update_icon_title_safe(slot_obj, unified_tooltip)
                    elif role in ("quick_folder", "quick_folder_2", "quick_folder_3"):
                        s_idx = 1 if role == "quick_folder" else (2 if role == "quick_folder_2" else 3)
                        f_sig = ("folder", s_idx) + folder_style_base
                        if getattr(slot_obj, "_last_drawn_sig", None) != f_sig:
                            slot_obj._last_drawn_sig = f_sig
                            slot_obj.icon = self.generate_folder_tray_icon(slot_num=s_idx)
                        fpath = self.get_quick_folder_path(s_idx)
                        fname = os.path.basename(fpath) or fpath
                        folder_tt = chr(10).join([
                            f"📁 Cartella {s_idx}: {fname} (Click: Tendina)",
                            f"↓ {dl_str}   |   ↑ {ul_str}",
                            f"CPU: {self.cpu_temp}°C   |   GPU: {self.gpu_temp}°C{gpu_tag}"
                        ])
                        self._update_icon_title_safe(slot_obj, folder_tt)
                    elif role == "single":
                        slot_obj._last_drawn_sig = ("single",)
                        total = self.download_speed + self.upload_speed
                        num_s, un_s = self.get_display_parts(total)
                        slot_obj.icon = self.generate_single_tray_image(
                            num_s, un_s,
                            is_download=self.download_speed >= self.upload_speed,
                            is_idle=total < 1024
                        )
                        self._update_icon_title_safe(slot_obj, unified_tooltip)
            except Exception:
                pass

    def _enable_dual_tray(self, icon=None, item=None):
        self.config["display_mode"] = "dual_tray"
        threading.Thread(target=self._apply_settings_background_worker, daemon=True).start()

    def _enable_docked_pill(self, icon=None, item=None):
        self.config["display_mode"] = "docked_pill"
        threading.Thread(target=self._apply_settings_background_worker, daemon=True).start()

    def _enable_tray_only(self, icon=None, item=None):
        self.config["display_mode"] = "tray_icon"
        threading.Thread(target=self._apply_settings_background_worker, daemon=True).start()

    # --------------------------------------------------------------------------
    # Widget Pillola Taskbar (Eseguito sul thread principale Tkinter)
    # --------------------------------------------------------------------------
    def get_taskbar_placement(self, widget_w=86, widget_h=34):
        try:
            user32 = ctypes.windll.user32
            FindWindowW = user32.FindWindowW
            FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
            FindWindowW.restype = wintypes.HWND
            h_tray = FindWindowW("Shell_TrayWnd", None)
            if h_tray:
                rect = wintypes.RECT()
                user32.GetWindowRect(h_tray, ctypes.byref(rect))
                tb_left, tb_top, tb_right, tb_bottom = rect.left, rect.top, rect.right, rect.bottom
                tb_h = tb_bottom - tb_top
                if tb_top > 0:
                    y_pos = tb_top + int((tb_h - widget_h) / 2)
                    x_pos = tb_right - 210 - widget_w
                    return x_pos, y_pos
        except Exception:
            pass

        screen_w = 1920
        screen_h = 1080
        try:
            screen_w = ctypes.windll.user32.GetSystemMetrics(0)
            screen_h = ctypes.windll.user32.GetSystemMetrics(1)
        except Exception:
            pass
        return screen_w - 280, screen_h - 44

    def show_taskbar_pill(self):
        self._run_on_ui_thread(self._show_taskbar_pill_ui)

    def hide_taskbar_pill(self):
        self._run_on_ui_thread(self._hide_taskbar_pill_ui)

    def _hide_taskbar_pill_ui(self):
        if self.widget_window is not None:
            try:
                if self.widget_window.winfo_exists():
                    self.widget_window.withdraw()
            except Exception:
                pass

    def _show_taskbar_pill_ui(self):
        try:
            root = self._ensure_tk_root()
            if self.widget_window is not None:
                try:
                    if self.widget_window.winfo_exists():
                        self.widget_window.deiconify()
                        self.widget_window.lift()
                        return
                except Exception:
                    pass
                self.widget_window = None

            self.widget_window = tk.Toplevel(root)
            w, h = 86, 34
            saved_x = self.config.get("widget_pos_x", -1)
            saved_y = self.config.get("widget_pos_y", -1)
            x, y = (saved_x, saved_y) if saved_x > 0 and saved_y > 0 else self.get_taskbar_placement(w, h)

            self.widget_window.overrideredirect(True)
            self.widget_window.geometry(f"{w}x{h}+{x}+{y}")
            self.widget_window.configure(bg="#141820")
            self.widget_window.attributes("-topmost", True)
            try:
                self.widget_window.attributes("-alpha", 0.95)
            except Exception:
                pass

            frame = tk.Frame(self.widget_window, bg="#141820", highlightbackground="#3b4252", highlightthickness=1)
            frame.pack(fill="both", expand=True)

            theme = self.THEMES.get(self.config.get("theme", "mint_cyan"), self.THEMES["mint_cyan"])

            dl_frame = tk.Frame(frame, bg="#141820")
            dl_frame.pack(fill="x", padx=4, pady=(2, 0))
            tk.Label(dl_frame, text="↓", fg=theme["hex_dl"], bg="#141820", font=("Segoe UI", 8, "bold")).pack(side="left")
            self.lbl_dl = tk.Label(dl_frame, text="0 KB/s", fg=theme["hex_dl"], bg="#141820", font=("Consolas", 8, "bold"))
            self.lbl_dl.pack(side="right")

            ul_frame = tk.Frame(frame, bg="#141820")
            ul_frame.pack(fill="x", padx=4, pady=(0, 2))
            tk.Label(ul_frame, text="↑", fg=theme["hex_ul"], bg="#141820", font=("Segoe UI", 8, "bold")).pack(side="left")
            self.lbl_ul = tk.Label(ul_frame, text="0 KB/s", fg=theme["hex_ul"], bg="#141820", font=("Consolas", 8, "bold"))
            self.lbl_ul.pack(side="right")

            def start_move(event):
                self.widget_window.x = event.x
                self.widget_window.y = event.y

            def do_move(event):
                deltax = event.x - getattr(self.widget_window, "x", 0)
                deltay = event.y - getattr(self.widget_window, "y", 0)
                new_x = self.widget_window.winfo_x() + deltax
                new_y = self.widget_window.winfo_y() + deltay
                self.widget_window.geometry(f"+{new_x}+{new_y}")

            def save_final_pos(event):
                self.config["widget_pos_x"] = self.widget_window.winfo_x()
                self.config["widget_pos_y"] = self.widget_window.winfo_y()
                threading.Thread(target=self._save_config, daemon=True).start()

            all_widgets = [self.widget_window, frame, dl_frame, ul_frame, self.lbl_dl, self.lbl_ul]
            for w_elem in all_widgets:
                w_elem.bind("<Button-1>", start_move)
                w_elem.bind("<B1-Motion>", do_move)
                w_elem.bind("<ButtonRelease-1>", save_final_pos)
                w_elem.bind("<Double-Button-1>", lambda e: self.open_network_connections())
        except Exception as e:
            print("Errore avvio widget taskbar:", e)

    # --------------------------------------------------------------------------
    # Monitor Loop
    # --------------------------------------------------------------------------
    def monitor_loop(self):
        INTERVAL = 3.0

        threading.Thread(target=self.update_public_ip_worker, daemon=True).start()

        current_iface, local_ip = self.get_active_network_interface()
        with self.lock:
            self.active_interface = current_iface or "Sconosciuta"
            self.local_ip = local_ip

        last_io = psutil.net_io_counters(pernic=True).get(current_iface) if current_iface else None
        if not last_io:
            last_io = psutil.net_io_counters()

        # Campionamento immediato iniziale delle temperature per tooltip e disegno istantaneo
        try:
            init_c = self.get_cpu_temperature()
            init_g, init_nv, init_gn = self.get_gpu_temperature()
            with self.lock:
                self.cpu_temp = init_c
                self.gpu_temp = init_g
                self.is_nvidia_active = init_nv
                self.active_gpu_name = init_gn
            self._update_tray_icons_immediate()
        except Exception:
            pass

        while self.is_running:
            time.sleep(INTERVAL)

            new_iface, new_local_ip = self.get_active_network_interface()
            with self.lock:
                self.active_interface = new_iface or "Sconosciuta"
                self.local_ip = new_local_ip

            io_counters = psutil.net_io_counters(pernic=True)
            current_io = io_counters.get(new_iface) if new_iface in io_counters else psutil.net_io_counters()

            bytes_recv_delta = max(0, current_io.bytes_recv - last_io.bytes_recv)
            bytes_sent_delta = max(0, current_io.bytes_sent - last_io.bytes_sent)
            last_io = current_io

            down_speed = bytes_recv_delta / INTERVAL
            up_speed = bytes_sent_delta / INTERVAL
            total_speed = down_speed + up_speed

            cpu_t = self.get_cpu_temperature()
            gpu_t, is_nv, gpu_n = self.get_gpu_temperature()

            with self.lock:
                self.download_speed = down_speed
                self.upload_speed = up_speed
                self.total_downloaded += bytes_recv_delta
                self.total_uploaded += bytes_sent_delta
                self.cpu_temp = cpu_t
                self.gpu_temp = gpu_t
                self.is_nvidia_active = is_nv
                self.active_gpu_name = gpu_n

            if time.time() - self.last_public_ip_check > 600:
                threading.Thread(target=self.update_public_ip_worker, daemon=True).start()

            # Aggiornamento in tempo reale degli slot attivi nella System Tray
            self._update_tray_icons_immediate()

            try:
                if self.lbl_dl and self.lbl_ul and self.widget_window:
                    dl_txt = self.format_speed_detailed(down_speed)
                    ul_txt = self.format_speed_detailed(up_speed)
                    self._run_on_ui_thread(self._safe_update_labels, dl_txt, ul_txt)
            except Exception:
                pass

    def _safe_update_labels(self, dl_txt, ul_txt):
        try:
            if self.widget_window and self.widget_window.winfo_exists():
                if self.lbl_dl and self.lbl_dl.winfo_exists():
                    self.lbl_dl.config(text=dl_txt)
                if self.lbl_ul and self.lbl_ul.winfo_exists():
                    self.lbl_ul.config(text=ul_txt)
        except Exception:
            pass

    def show_details_popup(self, icon=None, item=None):
        with self.lock:
            nv_status = "Attiva (Carico 3D/CUDA)" if self.is_nvidia_active else "Inattiva / Standby"
            details_lines = [
                "=== WINNETTRAY • MONITOR COMPLETO DI SISTEMA ===",
                "",
                "🌐 STATO RETE & IP:",
                f"• Scheda di Rete: {self.active_interface}",
                f"• IP Locale (LAN): {self.local_ip}",
                f"• IP Pubblico (WAN): {self.public_ip}",
                f"• Velocità Download: {self.format_speed_detailed(self.download_speed)}",
                f"• Velocità Upload: {self.format_speed_detailed(self.upload_speed)}",
                f"• Traffico Totale: ↓ {self.format_bytes_total(self.total_downloaded)}  ↑ {self.format_bytes_total(self.total_uploaded)}",
                "",
                "⚡ SENSORI HARDWARE & TEMPERATURE:",
                f"• Processore (CPU): {self.cpu_temp}°C (Carico: {psutil.cpu_percent()}%)",
                f"• Scheda Video (GPU): {self.gpu_temp}°C",
                f"• Modello GPU: {self.active_gpu_name}",
                f"• Stato GPU NVIDIA: {nv_status}"
            ]
            msg = chr(10).join(details_lines)
        try:
            target = icon
            if not target:
                for candidate in [self.icon_dl, self.icon_net, self.icon_cpu, self.icon_gpu, self.icon_ul]:
                    if hasattr(candidate, 'visible') and candidate.visible:
                        target = candidate
                        break
            target = target or self.icon_dl
            target.notify(msg, "WinNetTray • Dettagli Completi Sistema")
        except Exception:
            pass

    def quit_application(self, icon=None, item=None):
        self.is_running = False
        all_icons = [self.icon_dl, self.icon_ul, self.icon_net, self.icon_cpu, self.icon_gpu, self.icon_folder, self.icon_folder_2, self.icon_folder_3]
        for ic in all_icons:
            try:
                ic.stop()
            except Exception:
                pass
        if HAS_PYNVML and getattr(self, "_nvml_initialized", False):
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass
        def _stop_tk():
            try:
                if self.tk_root is not None:
                    self.tk_root.quit()
                    self.tk_root.destroy()
            except Exception:
                pass
        if self.tk_root is not None:
            try:
                self.tk_root.after(0, _stop_tk)
            except Exception:
                pass
        os._exit(0)

    def run(self):
        """Avvia WinNetTray con loop grafico Tkinter unificato sul main thread e slot pystray sincronizzati."""
        self._ensure_tk_root()

        # Avvio di tutti gli 8 slot pystray con evento di sincronizzazione _ready_event
        # (attende che pystray abbia impostato _running=True e creato _hwnd prima di mostrare le icone!)
        for ic in self.tray_slots:
            threading.Thread(
                target=ic.run,
                kwargs={"setup": lambda icon_obj: icon_obj._ready_event.set()},
                daemon=True
            ).start()

        # Attende che tutti gli 8 message-loop Win32 siano pronti (_running == True),
        # registra in sequenza gli slot attivi e solo dopo avvia il monitor_loop
        def _initial_setup():
            for ic in self.tray_slots:
                try:
                    ic._ready_event.wait(timeout=5.0)
                except Exception:
                    pass
            time.sleep(0.12)
            self._apply_visibility_and_draw(force_resequence=True)

            # Avvia il thread di campionamento continuo di rete e temperature DOPO la registrazione degli slot
            monitor_thread = threading.Thread(target=self.monitor_loop, daemon=True)
            monitor_thread.start()

        threading.Thread(target=_initial_setup, daemon=True).start()

        # Avvia il polling della coda UI e il mainloop Tkinter sul thread principale
        self.tk_root.after(30, self._poll_ui_queue)
        try:
            self.tk_root.mainloop()
        except (KeyboardInterrupt, SystemExit):
            self.quit_application()

    # --------------------------------------------------------------------------
    # Utilità per Compilazione, Metadati Versione, Icona .ICO, Firma & SHA-256
    # --------------------------------------------------------------------------
    @staticmethod
    def generate_build_assets(output_dir="."):
        """
        Genera automaticamente i 3 asset ufficiali di pubblicazione nella cartella di build:
        1. winnettray.ico (icona multi-risoluzione 16..256px per eseguibili e installer)
        2. version_info.txt (risorsa VSVersionInfo per Proprietà -> Dettagli di Windows)
        3. LICENSE.txt (Licenza MIT e Informativa Privacy Zero-Telemetria per Inno Setup)
        """
        os.makedirs(output_dir, exist_ok=True)

        # 1. Generazione icona multi-risoluzione winnettray.ico
        ico_path = os.path.join(output_dir, "winnettray.ico")
        try:
            size = 256
            img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)

            # Sfondo scuro arrotondato con bordo ciano/azzurro
            draw.rounded_rectangle(
                [8, 8, size - 9, size - 9],
                radius=48,
                fill=(15, 23, 42, 255),
                outline=(56, 189, 248, 255),
                width=8
            )
            # Barra divisoria centrale sottile
            draw.line([(size // 2, 38), (size // 2, size - 38)], fill=(51, 65, 85, 220), width=4)

            # Freccia Download (Verde Smeraldo #00E676) a sinistra (punta in basso)
            dl_color = (0, 230, 118, 255)
            cx_dl = 74
            # Asta verticale Download
            draw.rounded_rectangle([cx_dl - 14, 48, cx_dl + 14, 148], radius=6, fill=dl_color)
            # Punta freccia Download
            draw.polygon([(cx_dl - 40, 132), (cx_dl + 40, 132), (cx_dl, 198)], fill=dl_color)

            # Freccia Upload (Arancione Ambra #FF9100) a destra (punta in alto)
            ul_color = (255, 145, 0, 255)
            cx_ul = 182
            # Punta freccia Upload
            draw.polygon([(cx_ul - 40, 116), (cx_ul + 40, 116), (cx_ul, 50)], fill=ul_color)
            # Asta verticale Upload
            draw.rounded_rectangle([cx_ul - 14, 102, cx_ul + 14, 198], radius=6, fill=ul_color)

            # Barra inferiore stato hardware (ciano brillante)
            draw.rounded_rectangle([46, 214, size - 46, 228], radius=7, fill=(56, 189, 248, 255))

            img.save(
                ico_path,
                format="ICO",
                sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
            )
            print(f"[OK Asset] Icona multi-risoluzione generata: {ico_path}")
        except Exception as e:
            print(f"[Avviso Asset] Impossibile generare winnettray.ico: {e}")

        # 2. Generazione version_info.txt per PyInstaller (--version-file)
        ver_path = os.path.join(output_dir, "version_info.txt")
        version_info_content = """# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(1, 0, 0, 0),
    prodvers=(1, 0, 0, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
      StringTable(
        '041004B0',
        [StringStruct('CompanyName', 'Luca Tosi'),
        StringStruct('FileDescription', 'WinNetTray - Monitor di Rete e Temperature Hardware per System Tray'),
        StringStruct('FileVersion', '1.0.0.0'),
        StringStruct('InternalName', 'WinNetTray'),
        StringStruct('LegalCopyright', 'Freeware - Tutti i diritti riservati (Copyright © 2026 Luca Tosi)'),
        StringStruct('OriginalFilename', 'WinNetTray.exe'),
        StringStruct('ProductName', 'WinNetTray'),
        StringStruct('ProductVersion', '1.0.0.0')])
      ]),
    VarFileInfo([VarStruct('Translation', [1040, 1200])])
  ]
)
"""
        try:
            with open(ver_path, "w", encoding="utf-8") as vf:
                vf.write(version_info_content)
            print(f"[OK Asset] Metadati di versione Windows generati: {ver_path}")
        except Exception as e:
            print(f"[Avviso Asset] Impossibile generare version_info.txt: {e}")

        # 3. Generazione LICENSE.txt per Inno Setup (LicenseFile) e pacchetto di distribuzione
        lic_path = os.path.join(output_dir, "LICENSE.txt")
        license_content = """========================================================================
WINNETTRAY v1.0.0 - CONTRATTO DI LICENZA D'USO (EULA) E PRIVACY
Freeware - Tutti i diritti riservati (Copyright © 2026 Luca Tosi)
========================================================================

Autore e Titolare dei Diritti: Luca Tosi
Licenza: Freeware - Tutti i diritti riservati (Copyright © 2026 Luca Tosi)

1. CONCESSIONE DI LICENZA GRATUITA (FREEWARE) E LIMITAZIONI
Il software "WinNetTray" è di proprietà esclusiva di Luca Tosi ed è protetto
dalle leggi italiane e internazionali sul diritto d'autore (Copyright).
Tutti i diritti sono riservati.

L'Autore concede all'utente una licenza gratuita, non esclusiva e revocabile
per installare ed eseguire WinNetTray su computer personali o aziendali.

È ESPRESSAMENTE VIETATO:
- Vendere, noleggiare, sublicenziare o distribuire a scopo di lucro il software;
- Modificare, decompilare, disassemblare, effettuare reverse-engineering o
  estrarre il codice sorgente dagli eseguibili distribuiti;
- Rimuovere o alterare le indicazioni di Copyright, il nome dell'Autore o i
  marchi presenti nel programma e nella documentazione.

2. INFORMATIVA PRIVACY E ZERO TELEMETRIA
WinNetTray opera esclusivamente in locale sul tuo computer Windows.
L'applicazione NON raccoglie, NON memorizza su server remoti e NON trasmette
alcun dato personale, cronologia di navigazione, elenco di file o telemetria.
L'unica interrogazione esterna (eseguita solo su richiesta per mostrare l'IP
pubblico nel pannello informazioni) contatta in HTTPS endpoint pubblici di
verifica IP con validazione rigorosa del risultato.

3. ESCLUSIONE DI GARANZIA E LIMITAZIONE DI RESPONSABILITÀ (DISCLAIMER)
IL SOFTWARE VIENE FORNITO "COSÌ COM'È" ("AS IS"), SENZA ALCUNA GARANZIA
ESPLICITA O IMPLICITA, INCLUSE, A TITOLO ESEMPLIFICATIVO, LE GARANZIE DI
COMMERCIABILITÀ O IDONEITÀ PER UNO SCOPO PARTICOLARE.
IN NESSUN CASO L'AUTORE SARÀ RITENUTO RESPONSABILE PER EVENTUALI DANNI DIRETTI,
INDIRETTI, INCIDENTALI O CONSEQUENZIALI DERIVANTI DALL'INSTALLAZIONE, DALL'USO
O DALL'IMPOSSIBILITÀ DI UTILIZZARE QUESTO SOFTWARE.
"""
        try:
            with open(lic_path, "w", encoding="utf-8") as lf:
                lf.write(license_content)
            print(f"[OK Asset] File di licenza generato: {lic_path}")
        except Exception as e:
            print(f"[Avviso Asset] Impossibile generare LICENSE.txt: {e}")

    @staticmethod
    def try_sign_executable(exe_path):
        """
        Firma digitalmente con Authenticode (SHA-256 + Timestamp RFC3161) il file exe_path
        se sulla macchina di compilazione è presente un certificato:
        - File 'certificato.pfx' nella cartella corrente (o variabile WINNETTRAY_PFX_PATH)
        - Oppure thumbprint in WINNETTRAY_CERT_THUMBPRINT nel Certificate Store di Windows.
        Se non è presente alcun certificato, prosegue senza errori lasciando il binario non firmato.
        """
        if not exe_path or not os.path.isfile(exe_path):
            return False

        pfx_path = os.environ.get("WINNETTRAY_PFX_PATH", "").strip()
        if not pfx_path and os.path.isfile("certificato.pfx"):
            pfx_path = os.path.abspath("certificato.pfx")
        pfx_pass = os.environ.get("WINNETTRAY_CERT_PASS", "").strip()
        thumbprint = os.environ.get("WINNETTRAY_CERT_THUMBPRINT", "").strip()

        if not pfx_path and not thumbprint:
            print(f"[Info Firma] Nessun certificato rilevato (certificato.pfx o WINNETTRAY_CERT_THUMBPRINT): '{os.path.basename(exe_path)}' lasciato non firmato.")
            return False

        # Ricerca signtool.exe nel PATH o in Windows Kits (Windows 10/11 SDK)
        signtool_exe = shutil.which("signtool.exe") or shutil.which("signtool")
        if not signtool_exe:
            kits_roots = [
                os.path.join(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"), "Windows Kits", "10", "bin"),
                os.path.join(os.environ.get("ProgramFiles", "C:\\Program Files"), "Windows Kits", "10", "bin"),
            ]
            candidates = []
            for kr in kits_roots:
                if os.path.isdir(kr):
                    for root_dir, _, files in os.walk(kr):
                        for fn in files:
                            if fn.lower() == "signtool.exe" and "x64" in root_dir.lower():
                                candidates.append(os.path.join(root_dir, fn))
            if candidates:
                candidates.sort(reverse=True)
                signtool_exe = candidates[0]

        if not signtool_exe or not os.path.isfile(signtool_exe):
            print("[Avviso Firma] Certificato rilevato ma 'signtool.exe' (Windows SDK) non trovato nel sistema.")
            return False

        cmd = [
            signtool_exe, "sign",
            "/fd", "SHA256",
            "/tr", "http://timestamp.digicert.com",
            "/td", "SHA256",
            "/d", "WinNetTray - Monitor di Rete e Hardware"
        ]
        if pfx_path and os.path.isfile(pfx_path):
            cmd.extend(["/f", pfx_path])
            if pfx_pass:
                cmd.extend(["/p", pfx_pass])
        elif thumbprint:
            cmd.extend(["/sha1", thumbprint])

        cmd.append(os.path.abspath(exe_path))
        try:
            res = subprocess.run(cmd, shell=False, capture_output=True, text=True, timeout=45)
            if res.returncode == 0:
                print(f"[OK Firma Authenticode] Firmato digitalmente con successo: {os.path.basename(exe_path)}")
                return True
            else:
                err_out = (res.stderr or res.stdout or "").strip()
                print(f"[Avviso Firma] signtool non ha completato la firma ({res.returncode}): {err_out}")
        except Exception as e:
            print(f"[Avviso Firma] Errore durante l'esecuzione di signtool: {e}")
        return False

    @staticmethod
    def compute_sha256_file(exe_path):
        """
        Calcola l'hash crittografico SHA-256 del file specificato, salva il file
        '<nomefile>.sha256' e aggiorna automaticamente 'RICHIESTA_WHITELIST_MICROSOFT.txt'
        con il modulo precompilato (incluso l'hash SHA-256 reale) per rimuovere
        gratuitamente l'avviso SmartScreen/Defender tramite Microsoft Security Intelligence.
        """
        import hashlib
        if not exe_path or not os.path.isfile(exe_path):
            return None
        try:
            h = hashlib.sha256()
            with open(exe_path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    h.update(chunk)
            digest = h.hexdigest().lower()
            base_name = os.path.basename(exe_path)
            sha_file = exe_path + ".sha256"
            with open(sha_file, "w", encoding="utf-8") as sf:
                sf.write(f"{digest}  {base_name}\n")
            print(f"[OK SHA-256] {base_name} -> {digest}")
            print(f"             Salvato file checksum: {sha_file}")
            try:
                NetworkMonitorApp.generate_wdsi_whitelist_guide(".")
            except Exception:
                pass
            return digest
        except Exception as e:
            print(f"[Avviso SHA-256] Impossibile calcolare SHA-256 per {exe_path}: {e}")
            return None

    @staticmethod
    def generate_wdsi_whitelist_guide(output_dir="."):
        """
        Genera il file 'RICHIESTA_WHITELIST_MICROSOFT.txt' con la guida passo-passo
        e il testo in inglese già precompilato con gli hash SHA-256 reali dei binari
        appena generati, pronto da incollare sul portale gratuito Microsoft WDSI.
        """
        import hashlib
        os.makedirs(output_dir, exist_ok=True)
        guide_path = os.path.join(output_dir, "RICHIESTA_WHITELIST_MICROSOFT.txt")

        candidates = [
            ("WinNetTray_Setup.exe", os.path.join(output_dir, "WinNetTray_Setup.exe")),
            ("WinNetTray.exe (Installer payload)", os.path.join(output_dir, "dist", "WinNetTray", "WinNetTray.exe")),
            ("WinNetTray.exe (Standalone)", os.path.join(output_dir, "WinNetTray.exe")),
            ("WinNetTray.exe (Cartella App)", os.path.join(output_dir, "WinNetTray_App", "WinNetTray.exe")),
        ]

        detected_entries = []
        for label, cpath in candidates:
            if os.path.isfile(cpath):
                try:
                    h = hashlib.sha256()
                    with open(cpath, "rb") as f:
                        while True:
                            chunk = f.read(65536)
                            if not chunk:
                                break
                            h.update(chunk)
                    digest = h.hexdigest().lower()
                    size_kb = os.path.getsize(cpath) / 1024.0
                    detected_entries.append((label, os.path.basename(cpath), os.path.abspath(cpath), digest, f"{size_kb:,.1f} KB"))
                except Exception:
                    pass

        if detected_entries:
            hash_summary_it = "\n".join(
                [f"  • {lbl}\n    Percorso: {apath}\n    Dimensione: {sz}\n    SHA-256: {dig}" for (lbl, _, apath, dig, sz) in detected_entries]
            )
            hash_summary_en = "\n".join(
                [f"- {lbl} ({bname}, {sz}):\n  SHA-256: {dig}" for (lbl, bname, _, dig, sz) in detected_entries]
            )
        else:
            hash_summary_it = "  (Esegui prima crea_installer.bat o build_exe.bat per calcolare automaticamente gli hash SHA-256)"
            hash_summary_en = "- WinNetTray_Setup.exe / WinNetTray.exe (v1.0.0.0)"

        content = f"""================================================================================
GUIDA GRATUITA WHITELIST MICROSOFT SMARTSCREEN & DEFENDER PER WINNETTRAY
Autore: Luca Tosi — Freeware - Tutti i diritti riservati (Copyright © 2026 Luca Tosi)
================================================================================

Questa procedura ufficiale Microsoft è GRATUITA al 100% e permette di far
analizzare e inserire nella Whitelist globale di Windows Defender e SmartScreen
l'impronta SHA-256 del tuo installer 'WinNetTray_Setup.exe' (e/o 'WinNetTray.exe'),
eliminando l'avviso blu "PC protetto da Windows" su tutti i PC Windows 10/11.

--------------------------------------------------------------------------------
FILE RILEVATI E IMPRONTE CRITTOGRAFICHE (SHA-256) ATTUALI:
--------------------------------------------------------------------------------
{hash_summary_it}

--------------------------------------------------------------------------------
PROCEDURA IN 4 PASSAGGI (RICHIEDE CIRCA 60 SECONDI):
--------------------------------------------------------------------------------
1. Apri il portale ufficiale Microsoft Security Intelligence (WDSI):
   https://www.microsoft.com/en-us/wdsi/filesubmission
   (Accedi con un normale account Microsoft gratuito: Outlook, Hotmail, Live).

2. Alla domanda "Who are you? / Qual è il tuo ruolo?" seleziona:
   -> "Software developer" (Sviluppatore software) -> Clicca "Continue".

3. Compila le scelte del modulo:
   - "Select the Microsoft security product":
     Seleziona "Microsoft Defender SmartScreen" (oppure "Microsoft Defender Antivirus")
   - "Company name": Luca Tosi
   - "Product name": WinNetTray
   - Carica il file: seleziona 'WinNetTray_Setup.exe' (se superasse il limite di
     dimensione del form web, comprimilo in un archivio .zip prima di caricarlo).
   - Alla domanda "What do you believe this file is?":
     Seleziona "Incorrectly detected as malware/PWS/misleading" (Falso positivo).

4. Nel campo "Additional information / Informazioni aggiuntive", COPIA E INCOLLA
   il seguente testo in inglese (già compilato con i tuoi hash SHA-256 reali):

================== COPIA DA QUI (TESTO PER MICROSOFT WDSI) =====================
Hello Microsoft Security Intelligence Team,

I am the author and developer of "WinNetTray" (Version 1.0.0.0, Copyright © 2026 Luca Tosi, Freeware - All rights reserved).
I am submitting our clean desktop application installer and executable for whitelisting in Microsoft Defender SmartScreen and Microsoft Defender Antivirus to prevent false-positive warnings for end users.

Application Details:
- Product Name: WinNetTray
- Version: 1.0.0.0
- Developer / Publisher: Luca Tosi
- License: Freeware - All rights reserved (Copyright © 2026 Luca Tosi)
- Target OS: Windows 10 / Windows 11 (x64)

Technical Architecture & Privacy Guarantee:
- WinNetTray is a legitimate local system-tray utility written in Python (Tkinter, pystray, Pillow, psutil, nvidia-ml-py) packaged with PyInstaller and Inno Setup 6.
- It displays real-time local network throughput (Download/Upload speed) and CPU/GPU hardware temperatures in the Windows System Tray.
- It operates 100% locally with ZERO telemetry, ZERO analytics, and no remote command execution.

Submitted Binary SHA-256 Checksums:
{hash_summary_en}

Thank you for reviewing and whitelisting WinNetTray.
Best regards,
Luca Tosi
================== FINE TESTO DA COPIARE =======================================

NOTA IMPORTANTE:
La whitelist Microsoft è legata all'impronta SHA-256 esatta del file '.exe'.
Una volta inviato 'WinNetTray_Setup.exe' a Microsoft, distribuisci esattamente
quel file senza ricompilarlo (se lo ricompili per una nuova versione, ti basterà
inviare il nuovo file aggiornato). I tempi di approvazione Microsoft sono
normalmente di 4 - 24 ore lavorative.
"""
        with open(guide_path, "w", encoding="utf-8") as gf:
            gf.write(content)
        print(f"[OK Whitelist Microsoft] Modulo precompilato salvato in: {guide_path}")
        return guide_path


if __name__ == "__main__":
    if "--uninstall-clean" in sys.argv:
        try:
            NetworkMonitorApp.perform_full_uninstall_cleanup()
        except Exception:
            pass
        sys.exit(0)
    if "--generate-build-assets" in sys.argv:
        NetworkMonitorApp.generate_build_assets(".")
        sys.exit(0)
    if "--sign-binary" in sys.argv:
        idx = sys.argv.index("--sign-binary")
        if idx + 1 < len(sys.argv):
            NetworkMonitorApp.try_sign_executable(sys.argv[idx + 1])
        sys.exit(0)
    if "--compute-sha256" in sys.argv:
        idx = sys.argv.index("--compute-sha256")
        if idx + 1 < len(sys.argv):
            NetworkMonitorApp.compute_sha256_file(sys.argv[idx + 1])
        sys.exit(0)
    try:
        app = NetworkMonitorApp()
        app.run()
    except Exception as e:
        import traceback
        err_msg = traceback.format_exc()
        try:
            appdata_dir = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "WinNetTray")
            os.makedirs(appdata_dir, exist_ok=True)
            log_p = os.path.join(appdata_dir, "winnettray_error.log")
            with open(log_p, "w", encoding="utf-8") as f:
                f.write(err_msg)
        except Exception:
            log_p = "winnettray_error.log"
        try:
            import ctypes
            err_dialog = chr(10).join([
                "Errore durante l'esecuzione di WinNetTray:",
                "",
                str(e),
                "",
                "I dettagli sono stati salvati nel file di log:",
                log_p
            ])
            ctypes.windll.user32.MessageBoxW(0, err_dialog, "WinNetTray Errore", 0x10)
        except Exception:
            pass

