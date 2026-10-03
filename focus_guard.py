"""Monociclo — proyecto personal de makakas.

Lo hice para estudiar sin andar saltando entre veinte ventanas.
Está todo en este archivo y algunas partes quedaron medio mezcladas.
Python 3.10+; pip install -r requirements.txt.

Ejecutar: python focus_guard.py
Compilar: python -m PyInstaller --onefile --windowed --name Monociclo focus_guard.py
Configuración editable junto al script o al EXE, nunca en sys._MEIPASS.
El hilo guardián no llama a Tkinter ni termina procesos.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import json
import math
import os
import random
from pathlib import Path
import queue
import sys
import subprocess
import tempfile
import threading
import time
import tkinter as tk
from tkinter import messagebox, filedialog, ttk
from typing import Callable
from urllib.parse import urlsplit, urlunsplit, quote

if sys.platform == "win32":
    import psutil
    import pywintypes
    import win32api
    import win32con
    import win32gui
    import win32process


DEFAULT_CONFIG = {
    "work_minutes": 25,
    "break_minutes": 5,
    "whitelist": ["code.exe", "devenv.exe", "notepad.exe"],
    "strict_web": True,
    "urls": [],
}
POLL_SECONDS = 0.5
BROWSER_EXES = frozenset({
    "msedge.exe", "chrome.exe", "firefox.exe", "brave.exe", "opera.exe",
    "opera_gx.exe", "operagx.exe", "vivaldi.exe", "chromium.exe", "arc.exe",
    "browser.exe", "waterfox.exe", "librewolf.exe", "zen.exe", "thorium.exe",
})
APP_NAMES = {
    "code.exe": "Visual Studio Code", "devenv.exe": "Visual Studio",
    "notepad.exe": "Bloc de notas", "explorer.exe": "Explorador de archivos",
    "winword.exe": "Microsoft Word", "excel.exe": "Microsoft Excel",
    "powerpnt.exe": "PowerPoint", "obsidian.exe": "Obsidian",
    "msedge.exe": "Microsoft Edge", "chrome.exe": "Google Chrome",
    "firefox.exe": "Firefox", "brave.exe": "Brave", "discord.exe": "Discord",
    "spotify.exe": "Spotify", "slack.exe": "Slack", "teams.exe": "Microsoft Teams",
}


def canonical_url(raw: str, missing_scheme: bool = True) -> str:
    """Pongo las URLs en el mismo formato para no comparar peras con tomates."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("Escribe una URL, por ejemplo https://docs.python.org/3/")
    raw = raw.strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in raw) or "\\" in raw:
        raise ValueError("La URL contiene caracteres no permitidos.")
    if missing_scheme and "://" not in raw:
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
        if (parts.scheme.lower() not in {"https", "http"} or not parts.hostname
                or parts.username is not None or parts.password is not None):
            raise ValueError("Usa una URL http:// o https:// sin usuario ni contraseña.")
        host = parts.hostname.encode("idna").decode("ascii").lower()
        if any(char.isspace() for char in host) or any(char in host for char in '%/?#'):
            raise ValueError("El dominio no es válido.")
        if ":" in host:
            import ipaddress
            host = "[" + str(ipaddress.IPv6Address(host)) + "]"
        port = parts.port
        scheme = parts.scheme.lower()
        if port is not None and port != (443 if scheme == "https" else 80):
            host += f":{port}"
        return urlunsplit((scheme, host,
                           quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~"),
                           quote(parts.query, safe="%/?@:!$&'()*+,;=-._~"), ""))
    except (UnicodeError, ValueError) as error:
        raise ValueError(f"URL inválida: {error}") from error


@dataclass(frozen=True)
class UrlRule:
    url: str
    scope: str = "exact"

    @classmethod
    def parse(cls, data) -> UrlRule:
        if isinstance(data, str):
            data = {"url": data, "scope": "exact"}
        if not isinstance(data, dict) or data.get("scope", "exact") not in {"exact", "site"}:
            raise ValueError("Cada URL debe usar scope 'exact' o 'site'.")
        return cls(canonical_url(data.get("url", "")), data.get("scope", "exact"))

    def allows(self, candidate: str) -> bool:
        try:
            current = canonical_url(candidate, missing_scheme=False)
        except ValueError:
            return False
        if self.scope == "exact":
            return current == self.url
        expected, actual = urlsplit(self.url), urlsplit(current)
        return (expected.scheme, expected.netloc) == (actual.scheme, actual.netloc)


class UrlPolicy:
    def __init__(self, rules: tuple[UrlRule, ...]):
        self.rules = rules

    def allows(self, candidate: str) -> bool:
        return any(rule.allows(candidate) for rule in self.rules)


def config_path() -> Path:
    """El JSON va al lado del exe, así lo puedo encontrar sin buscar mil carpetas."""
    base = Path(sys.executable) if getattr(sys, "frozen", False) else Path(__file__)
    return base.resolve().parent / "whitelist.json"


@dataclass(frozen=True)
class Config:
    work_seconds: float
    break_seconds: float
    whitelist: frozenset[str]
    strict_web: bool = True
    urls: tuple[UrlRule, ...] = ()

    @property
    def effective_whitelist(self) -> frozenset[str]:
        return self.whitelist - BROWSER_EXES if self.strict_web else self.whitelist

    def to_dict(self) -> dict:
        return {"work_minutes": self.work_seconds / 60,
                "break_minutes": self.break_seconds / 60,
                "whitelist": sorted(self.whitelist), "strict_web": self.strict_web,
                "urls": [{"url": rule.url, "scope": rule.scope} for rule in self.urls]}

    def save(self, path: Path) -> None:
        """Guardo primero en otro archivo; si falla, no pierdo mis reglas viejas."""
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=".focus-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(self.to_dict(), stream, indent=2, ensure_ascii=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    @classmethod
    def load(cls, path: Path) -> Config:
        # Si ya hay reglas, ni las toco. Me pasó una vez y fue bastante molesto.
        if not path.exists():
            try:
                with path.open("x", encoding="utf-8") as stream:
                    json.dump(DEFAULT_CONFIG, stream, indent=2, ensure_ascii=False)
                    stream.write("\n")
            except FileExistsError:
                pass
        with path.open(encoding="utf-8-sig") as stream:
            data = json.load(stream)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> Config:
        if not isinstance(data, dict):
            raise ValueError("whitelist.json debe contener un objeto JSON.")

        def duration(key: str) -> float:
            value = data.get(key, DEFAULT_CONFIG[key])
            if (isinstance(value, bool) or not isinstance(value, (float, int))
                    or not math.isfinite(value) or not 0 < value <= 240):
                raise ValueError(f"{key}: usa un número mayor que 0 y hasta 240 minutos.")
            return float(value) * 60

        names = data.get("whitelist", DEFAULT_CONFIG["whitelist"])
        if not isinstance(names, list):
            raise ValueError("whitelist debe ser una lista de nombres de ejecutables.")
        allowed = set()
        for name in names:
            if (not isinstance(name, str) or not name.strip().lower().endswith(".exe")
                    or any(char in name for char in '/\\:*?"<>|\r\n')):
                raise ValueError("Usa nombres como code.exe, sin rutas ni comodines.")
            allowed.add(name.strip().casefold())
        strict = data.get("strict_web", True)
        if not isinstance(strict, bool):
            raise ValueError("strict_web debe ser true o false.")
        urls = data.get("urls", [])
        if not isinstance(urls, list) or len(urls) > 100:
            raise ValueError("urls debe ser una lista de hasta 100 URLs.")
        rules = tuple(dict.fromkeys(UrlRule.parse(item) for item in urls))
        return cls(duration("work_minutes"), duration("break_minutes"), frozenset(allowed), strict, rules)


class Pomodoro:
    """Acá llevo la cuenta del tiempo. Tk lo consulta, el otro hilo no."""

    def __init__(self, config: Config, clock: Callable[[], float] = time.monotonic):
        self.config = config
        self.clock = clock
        self.reset()

    def reset(self) -> None:
        self.phase = "work"
        self.running = False
        self.deadline = 0.0
        self.remaining = self.config.work_seconds
        self.completed = 0

    def start(self) -> None:
        if not self.running:
            self.deadline = self.clock() + self.remaining
            self.running = True

    def pause(self) -> None:
        self.tick()
        if self.running:
            self.remaining = max(0.0, self.deadline - self.clock())
            self.running = False

    def tick(self) -> bool:
        if not self.running:
            return False
        now = self.clock()
        self.remaining = max(0.0, self.deadline - now)
        if self.remaining > 0:
            return False
        if self.phase == "work":
            self.completed += 1
            self.phase = "break"
            self.remaining = self.config.break_seconds
        else:
            self.phase = "work"
            self.remaining = self.config.work_seconds
        # Si la ventana se quedó atrás, igual quiero un descanso completo.
        self.deadline = now + self.remaining
        return True


class Guardian(threading.Thread):
    """Este es el portero. Mira qué app estoy usando y avisa al tomate."""

    SHELL_SURFACES = frozenset({
        "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
    })

    def __init__(self, notices: queue.Queue):
        super().__init__(name="WhitelistGuardian", daemon=True)
        self.notices = notices
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.allowed: frozenset[str] = frozenset()
        self.active_until = 0.0
        self.own_pid = os.getpid()
        self.browser_process: tuple[int, float] | None = None
        self.last_notice = ("", 0.0)
        # No voy a quedarme esperando a que Discord tenga ganas de responder.
        self.show_window = ctypes.WinDLL("user32", use_last_error=True).ShowWindowAsync
        self.show_window.argtypes = [wintypes.HWND, ctypes.c_int]
        self.show_window.restype = wintypes.BOOL

    def configure(self, allowed: frozenset[str], active_until: float) -> None:
        with self.lock:
            self.allowed = allowed
            self.active_until = active_until

    def stop(self) -> None:
        with self.lock:
            self.active_until = 0.0
            self.stop_event.set()

    def trust_browser(self, process) -> None:
        with self.lock:
            self.browser_process = ((process.pid, psutil.Process(process.pid).create_time())
                                    if process is not None else None)

    def is_browser_process(self, pid: int) -> bool:
        with self.lock:
            identity = self.browser_process
        if identity is None:
            return False
        try:
            process = psutil.Process(pid)
            for ancestor in [process, *process.parents()]:
                if ancestor.pid == identity[0]:
                    return ancestor.create_time() == identity[1]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        return False

    def notify(self, message: str) -> None:
        now = time.monotonic()
        if message == self.last_notice[0] and now - self.last_notice[1] < 2.5:
            return
        self.last_notice = (message, now)
        try:
            self.notices.put_nowait(message)
        except queue.Full:
            pass

    def inspect_foreground(self) -> None:
        with self.lock:
            if self.stop_event.is_set() or time.monotonic() >= self.active_until:
                return
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd or not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
            return
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid == self.own_pid or not pid:
            return
        if self.is_browser_process(pid):
            return
        # Las carpetas sí cuentan como app; el escritorio y la barra no.
        if win32gui.GetClassName(hwnd) in self.SHELL_SURFACES:
            return
        try:
            name = psutil.Process(pid).name().casefold()
        except psutil.NoSuchProcess:
            return
        except psutil.AccessDenied:
            self.notify("Sin permiso para identificar la ventana activa")
            return
        with self.lock:
            # Miro otra vez por si justo apreté pausa. Sin esto había una carrera fea.
            if (self.stop_event.is_set() or time.monotonic() >= self.active_until
                    or name in self.allowed):
                return
            if win32gui.GetForegroundWindow() != hwnd or not win32gui.IsWindow(hwnd):
                return
            _, current_pid = win32process.GetWindowThreadProcessId(hwnd)
            if current_pid != pid:
                return
            submitted = self.show_window(hwnd, win32con.SW_MINIMIZE)
        if submitted:
            self.notify(f"Bloqueada: {name}")
        else:
            self.notify(f"No se pudo minimizar: {name}")

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.inspect_foreground()
            except (pywintypes.error, OSError) as error:
                self.notify(f"Guardián: error de Windows {getattr(error, 'winerror', '')}")
            except Exception:
                # Si se cae el portero, mejor avisar y dejar la sesión en pausa.
                self.notify("ERROR: guardián detenido; sesión pausada")
                return
            self.stop_event.wait(POLL_SECONDS)


def build_browser(config: Config, parent_identity: tuple[int, float] | None = None):
    """Qt y Tk no se llevan muy bien en el mismo loop, así que los separé."""
    from PySide6.QtCore import Qt, QUrl, QTimer, Signal, Slot
    from PySide6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import (QWebEnginePage, QWebEngineProfile, QWebEngineSettings,
                                        QWebEngineUrlRequestInterceptor, QWebEngineUrlRequestInfo,
                                        QWebEngineScript)

    policy = UrlPolicy(config.urls)

    class Interceptor(QWebEngineUrlRequestInterceptor):
        blocked = Signal(str)

        def interceptRequest(self, info):
            kind = info.resourceType()
            url = info.requestUrl().toString(QUrl.ComponentFormattingOption.FullyEncoded)
            # Las páginas pasan por el portero antes de cargar. Las imágenes y
            # estilos pueden venir de otro sitio o casi ninguna web se vería bien.
            documents = {QWebEngineUrlRequestInfo.ResourceType.ResourceTypeMainFrame,
                         QWebEngineUrlRequestInfo.ResourceType.ResourceTypeSubFrame}
            if kind in documents and not policy.allows(url):
                info.block(True)
                self.blocked.emit(url)

    class Page(QWebEnginePage):
        def acceptNavigationRequest(self, url, kind, main_frame):
            target = url.toString(QUrl.ComponentFormattingOption.FullyEncoded)
            if target == "about:blank":
                return True  # Documento vacío interno; no abre sitios ni archivos.
            if not policy.allows(target):
                window.block_notice(target)
                return False
            return True

        def createWindow(self, kind):
            window.block_notice("ventana emergente")
            return None

    class BrowserWindow(QMainWindow):
        @Slot(str)
        def block_notice(self, url):
            self.status.setText("Tomate dice: por aquí no. Enlace fuera de tu lista: " + url[:140])

    window = BrowserWindow()
    window.setWindowTitle("Monociclo · Web de enfoque")
    window.resize(1080, 760)
    window.setStyleSheet("""
        QMainWindow, QWidget { background: #151b2d; color: #f7f3e8; font: 11pt 'Segoe UI'; }
        QPushButton { background: #ff826c; color: #171c2b; border: none; border-radius: 8px; padding: 9px 16px; font-weight: 600; }
        QPushButton:hover { background: #ffa590; }
        QComboBox { background: #252e44; padding: 8px; border: 1px solid #43506a; border-radius: 6px; }
    """)
    central = QWidget(window)
    layout = QVBoxLayout(central)
    toolbar = QHBoxLayout()
    title = QLabel("●  WEB DE ENFOQUE")
    toolbar.addWidget(title)
    window.links = QComboBox()
    for rule in config.urls:
        window.links.addItem(("PÁGINA · " if rule.scope == "exact" else "SITIO · ") + rule.url)
    toolbar.addWidget(window.links, 1)
    visit = QPushButton("Abrir")
    home = QPushButton("Volver al enlace")
    toolbar.addWidget(visit)
    toolbar.addWidget(home)
    layout.addLayout(toolbar)
    note = QLabel("Sólo tus enlaces. Sin pestañas sorpresa, descargas ni ventanas emergentes.")
    layout.addWidget(note)
    window.view = QWebEngineView(central)
    window.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
    window.profile = QWebEngineProfile(window)  # off-the-record, sin datos persistentes
    window.profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
    window.profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
    window.interceptor = Interceptor(window.profile)
    window.profile.setUrlRequestInterceptor(window.interceptor)
    window.page = Page(window.profile, window.view)
    window.view.setPage(window.page)
    settings = window.page.settings()
    for attribute in (QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows,
                      QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls,
                      QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls,
                      QWebEngineSettings.WebAttribute.FullScreenSupportEnabled,
                      QWebEngineSettings.WebAttribute.PluginsEnabled,
                      QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard):
        settings.setAttribute(attribute, False)
    window.profile.downloadRequested.connect(lambda download: download.cancel())
    window.page.permissionRequested.connect(lambda permission: permission.deny())
    window.page.certificateError.connect(lambda error: error.rejectCertificate())
    layout.addWidget(window.view, 1)
    window.status = QLabel("Tu tomate está de portero. Lista de páginas activa.")
    window.status.setWordWrap(True)
    layout.addWidget(window.status)
    window.setCentralWidget(central)
    window.interceptor.blocked.connect(window.block_notice)

    # Algunas webs cambian la URL sin cargar otra página. Este pedazo
    # me llevó más rato del que esperaba; reviso history y también urlChanged.
    script = QWebEngineScript()
    script.setName("MonocicloHistory")
    script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
    script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    script.setRunsOnSubFrames(True)
    rules_json = json.dumps([{"url": rule.url, "scope": rule.scope} for rule in config.urls])
    script.setSourceCode("""(() => {
      const rules = RULES;
      const permits = value => {
        try {
          const u = new URL(value, location.href); u.hash = '';
          if (!['https:', 'http:'].includes(u.protocol)) return false;
          return rules.some(r => r.scope === 'site' ? new URL(r.url).origin === u.origin : r.url === u.href);
        } catch (_) { return false; }
      };
      for (const name of ['pushState', 'replaceState']) {
        const original = history[name].bind(history);
        const guarded = function(state, title, url) {
          if (url == null || permits(url)) return original(state, title, url);
          console.warn('Monociclo: URL fuera de la lista');
        };
        Object.defineProperty(history, name, {value: guarded, configurable: false, writable: false});
      }
    })();""".replace("RULES", rules_json))
    window.page.scripts().insert(script)

    def open_selected():
        index = window.links.currentIndex()
        if 0 <= index < len(config.urls):
            window.status.setText("Cargando tu enlace. El conejo de las distracciones se queda fuera.")
            window.view.setUrl(QUrl(config.urls[index].url))

    def check_url(url):
        value = url.toString(QUrl.ComponentFormattingOption.FullyEncoded)
        if value != "about:blank" and not policy.allows(value):
            window.view.stop()
            window.block_notice(value)
            QTimer.singleShot(0, open_selected)

    window.view.urlChanged.connect(check_url)
    window.view.loadFinished.connect(lambda ok: window.status.setText(
        "Lista blanca activa. Tu atención se queda aquí." if ok else
        "La página no cargó o intentó salir de la lista. Revisa el enlace y sus redirecciones."))
    visit.clicked.connect(open_selected)
    home.clicked.connect(open_selected)
    if parent_identity is not None:
        window.parent_timer = QTimer(window)

        def check_parent():
            try:
                if psutil.Process(parent_identity[0]).create_time() == parent_identity[1]:
                    return
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            window.close()

        window.parent_timer.timeout.connect(check_parent)
        window.parent_timer.start(750)
    window.open_selected = open_selected
    window.policy = policy
    QTimer.singleShot(0, open_selected)
    return window


def run_browser() -> None:
    from PySide6.QtWidgets import QApplication
    app = QApplication([sys.argv[0]])
    try:
        config = Config.load(Path(sys.argv[sys.argv.index("--policy-file") + 1]))
        if not config.urls:
            raise ValueError("Añade al menos una página permitida.")
        parent_pid = int(sys.argv[sys.argv.index("--parent-pid") + 1])
        created = float(sys.argv[sys.argv.index("--parent-created") + 1])
        window = build_browser(config, (parent_pid, created))
        window.show()
        app.exec()
        window.page.deleteLater()
        from PySide6.QtCore import QCoreApplication, QEvent
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        window.profile.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    except Exception as error:
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(None, "Web de enfoque", f"No se pudo abrir el navegador:\n{error}")


def discover_apps() -> dict[str, str]:
    """Busco las apps abiertas y unas conocidas, no todo el disco."""
    found = dict(APP_NAMES)
    seen = set()

    def collect(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd) or not win32gui.GetWindowText(hwnd):
                return
            if win32gui.GetClassName(hwnd) in Guardian.SHELL_SURFACES:
                return
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid == os.getpid() or pid in seen:
                return
            seen.add(pid)
            name = psutil.Process(pid).name().casefold()
            if name.endswith(".exe"):
                found.setdefault(name, name[:-4].replace("_", " ").title())
        except (pywintypes.error, psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    win32gui.EnumWindows(collect, None)
    return found


class FocusHUD:
    BG = "#151b2d"
    PANEL = "#252e44"
    TEXT = "#fff6e8"
    MUTED = "#b3bdd2"
    GREEN = "#8be0b0"
    BLUE = "#90cafa"
    RED = "#ffb09d"
    CORAL = "#ff826c"
    QUIPS = ("Tu futuro yo acaba de aplaudir.", "Una cosa a la vez. El tomate vigila.",
             "Las pestañas pueden esperar. Tu idea no.", "Modo tomate: menos drama, más progreso.")

    def __init__(self, root: tk.Tk, config: Config, path: Path):
        self.root = root
        self.path = path
        self.timer = Pomodoro(config)
        self.notices: queue.Queue[str] = queue.Queue(maxsize=20)
        self.guardian = Guardian(self.notices)
        self.notice_until = 0.0
        self.drag_offset = (0, 0)
        self.closing = False
        self.settings = None
        self.browser = None
        self.browser_snapshot = None
        self.quip = self.QUIPS[0]
        self.mood = "ready"
        self.party_until = 0.0
        self.glare_until = 0.0
        root.title("Monociclo")
        root.configure(bg=self.BG)
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<Escape>", lambda _event: self.stop())
        self.build_ui()
        root.update_idletasks()
        monitor = win32api.MonitorFromPoint((0, 0), win32con.MONITOR_DEFAULTTOPRIMARY)
        left, top, right, bottom = win32api.GetMonitorInfo(monitor)["Work"]
        width, height = max(380, root.winfo_reqwidth()), root.winfo_reqheight()
        root.geometry(f"{width}x{height}+{max(left, right-width-18)}+{max(top, bottom-height-18)}")
        self.guardian.start()
        self.refresh()
        self.keep_on_top()

    def label(self, parent, text: str, **kwargs) -> tk.Label:
        return tk.Label(parent, text=text, bg=self.BG, fg=self.TEXT,
                        font=("Segoe UI", 10), **kwargs)

    def button(self, parent, text: str, command, color: str = PANEL) -> tk.Button:
        dark_text = color == self.CORAL or color == self.GREEN
        return tk.Button(parent, text=text, command=command, bg=color, fg=self.BG if dark_text else self.TEXT,
                         activebackground="#ffa590" if dark_text else "#3b4762",
                         activeforeground=self.BG if dark_text else self.TEXT,
                         relief="flat", bd=0, padx=12, pady=7, cursor="hand2",
                         font=("Segoe UI", 10, "bold"), highlightthickness=0)

    def build_ui(self) -> None:
        header = tk.Frame(self.root, bg=self.BG)
        header.pack(fill="x", padx=18, pady=(12, 0))
        title = self.label(header, "●  MONOCICLO", anchor="w")
        title.configure(fg=self.CORAL, font=("Segoe UI", 11, "bold"))
        title.pack(side="left", fill="x", expand=True)
        for widget in (header, title):
            widget.bind("<ButtonPress-1>", self.begin_drag)
            widget.bind("<B1-Motion>", self.drag)
        self.button(header, "×", self.close).pack(side="right")
        subtitle = self.label(self.root, "Un solo foco. El resto, al banquillo.")
        subtitle.configure(fg=self.MUTED, font=("Segoe UI", 10))
        subtitle.pack(pady=(3, 12))
        hero = tk.Frame(self.root, bg=self.BG)
        hero.pack(padx=18, fill="x")
        self.mascot = tk.Canvas(hero, width=112, height=116, bg=self.BG, highlightthickness=0)
        self.mascot.pack(side="left", padx=(5, 14))
        right = tk.Frame(hero, bg=self.BG)
        right.pack(side="left", fill="x", expand=True)
        self.state_label = self.label(right, "ENFOCADO · LISTO", anchor="w")
        self.state_label.pack(fill="x")
        self.time_label = tk.Label(right, text="25:00", bg=self.BG, fg=self.TEXT,
                                   anchor="w", font=("Segoe UI", 36, "bold"))
        self.time_label.pack(fill="x")
        self.progress = tk.Canvas(self.root, width=332, height=10, bg=self.BG, highlightthickness=0)
        self.progress.pack(pady=(12, 6))
        self.counter_label = self.label(self.root, "0 tomates completados · a tu ritmo")
        self.counter_label.configure(fg=self.MUTED, font=("Segoe UI", 9))
        self.counter_label.pack(pady=(0, 12))
        controls = tk.Frame(self.root, bg=self.BG)
        controls.pack()
        self.start_button = self.button(controls, "¡A enfocarse!", self.start, self.CORAL)
        self.start_button.pack(side="left", padx=3)
        self.pause_button = self.button(controls, "Pausar", self.pause)
        self.pause_button.pack(side="left", padx=3)
        self.button(controls, "Terminar", self.stop).pack(side="left", padx=3)
        permissions = tk.Frame(self.root, bg=self.BG)
        permissions.pack(fill="x", padx=22, pady=(12, 0))
        self.button(permissions, "Mis permisos", self.open_settings).pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.button(permissions, "Web de enfoque", self.open_browser).pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.notice_label = self.label(self.root, "Guardián inactivo", wraplength=340, height=2)
        self.notice_label.pack(fill="x", padx=14, pady=(12, 4))
        self.summary_label = self.label(self.root, "")
        self.summary_label.configure(fg=self.MUTED, font=("Segoe UI", 8))
        self.summary_label.pack(pady=(0, 5))
        self.credit_label = self.label(self.root, "Hecho por makakas · para uso personal")
        self.credit_label.configure(fg=self.MUTED, font=("Segoe UI", 8))
        self.credit_label.pack(pady=(0, 12))

    def draw_mascot(self):
        # Lo dibujé con círculos porque no quería pelearme con archivos de imágenes.
        # Sí, es un tomate en un monociclo. No preguntes por la física.
        c = self.mascot
        c.delete("all")
        now = time.monotonic()
        happy = now < self.party_until
        angry = self.mood == "paused" or now < self.glare_until
        sleepy = self.timer.phase == "break" and not angry and not happy
        bounce = int(abs(math.sin(now * 9)) * -6) if happy else int(math.sin(now * 2) * 1.5)
        c.create_oval(30, 105, 85, 114, fill="#0f1422", outline="")
        # La ruedita gira sólo mientras corre el tiempo.
        c.create_line(56, 73+bounce, 56, 99, fill=self.GREEN, width=4)
        c.create_oval(43, 87, 69, 113, fill=self.PANEL, outline=self.MUTED, width=2)
        angle = now*3 if self.timer.running else 0.4
        for i in range(3):
            a = angle + i*math.pi/3
            dx, dy = math.cos(a)*10, math.sin(a)*10
            c.create_line(56-dx, 100-dy, 56+dx, 100+dy, fill=self.GREEN, width=2)
        c.create_oval(13, 17+bounce, 100, 84+bounce, fill=self.CORAL, outline="")
        c.create_oval(23, 23+bounce, 36, 35+bounce, fill="#ffb19b", outline="")
        c.create_polygon(52, 25+bounce, 34, 14+bounce, 53, 17+bounce, 58, 4+bounce,
                         64, 16+bounce, 82, 13+bounce, 66, 27+bounce, fill=self.GREEN, outline="")
        c.create_line(59, 16+bounce, 63, 2+bounce, fill=self.GREEN, width=3)
        # Cuando se alegra levanta los brazos; en pausa se queda mirando fijo.
        hand_y = 34+bounce+math.sin(now*12)*4 if happy else 75+bounce
        c.create_line(16, 53+bounce, 5, hand_y, fill=self.CORAL, width=5, capstyle="round")
        c.create_line(98, 53+bounce, 108, hand_y, fill=self.CORAL, width=5, capstyle="round")
        if happy:
            for x in (39, 70):
                c.create_arc(x-7, 43+bounce, x+7, 55+bounce, start=15, extent=150,
                             style="arc", outline=self.BG, width=3)
            c.create_arc(41, 49+bounce, 71, 74+bounce, start=180, extent=180,
                         style="pieslice", fill=self.BG, outline="")
            c.create_line(46, 62+bounce, 65, 62+bounce, fill=self.TEXT, width=3)
            for i, color in enumerate((self.GREEN, self.BLUE, "#ffe092", self.CORAL)):
                x = 5+i*30
                y = 5+int((now*23+i*13)%27)
                c.create_oval(x, y, x+4, y+4, fill=color, outline="")
        elif sleepy:
            for x in (39, 70):
                c.create_line(x-6, 50+bounce, x+6, 50+bounce, fill=self.BG, width=3)
            c.create_text(99, 13, text="z", fill=self.BLUE, font=("Segoe UI", 12, "bold"))
            c.create_oval(53, 65+bounce, 60, 71+bounce, fill=self.BG, outline="")
        else:
            blink = now%5 < 0.12 and not angry
            for x in (39, 70):
                if blink:
                    c.create_line(x-5, 50+bounce, x+5, 50+bounce, fill=self.BG, width=3)
                else:
                    c.create_oval(x-6, 43+bounce, x+6, 56+bounce, fill=self.TEXT, outline="")
                    look = 0 if angry else int(math.sin(now*.6)*1.5)
                    c.create_oval(x-2+look, 47+bounce, x+3+look, 54+bounce, fill=self.BG, outline="")
            if angry:
                c.create_line(30, 40+bounce, 45, 44+bounce, fill=self.BG, width=3)
                c.create_line(63, 44+bounce, 78, 40+bounce, fill=self.BG, width=3)
                c.create_arc(45, 64+bounce, 66, 79+bounce, start=20, extent=140,
                             style="arc", outline=self.BG, width=3)
            else:
                c.create_arc(43, 54+bounce, 69, 74+bounce, start=200, extent=140,
                             style="arc", outline=self.BG, width=3)
        c.create_oval(25, 58+bounce, 36, 64+bounce, fill="#ffb8a2", outline="")
        c.create_oval(74, 58+bounce, 85, 64+bounce, fill="#ffb8a2", outline="")

    def begin_drag(self, event) -> None:
        self.drag_offset = (event.x_root - self.root.winfo_x(),
                            event.y_root - self.root.winfo_y())

    def drag(self, event) -> None:
        x, y = event.x_root - self.drag_offset[0], event.y_root - self.drag_offset[1]
        self.root.geometry(f"{x:+d}{y:+d}")

    def sync_guardian(self) -> None:
        deadline = self.timer.deadline if self.timer.running and self.timer.phase == "work" else 0.0
        self.guardian.configure(self.timer.config.effective_whitelist, deadline)

    def start(self) -> None:
        if self.settings is not None:
            self.settings.root.lift()
            self.show_notice("Guarda o cierra tus permisos antes de empezar")
            return
        if not self.guardian.is_alive():
            self.show_notice("Guardián detenido: cierra y vuelve a abrir la app")
            return
        self.timer.start()
        self.mood = "focused"
        self.party_until = time.monotonic() + 1.6
        self.quip = random.choice(self.QUIPS)
        self.sync_guardian()

    def pause(self) -> None:
        self.guardian.configure(self.timer.config.effective_whitelist, 0.0)
        self.timer.pause()
        self.mood = "paused"
        self.party_until = 0.0
        self.sync_guardian()
        self.clear_notices()

    def stop(self) -> None:
        self.guardian.configure(self.timer.config.effective_whitelist, 0.0)
        self.timer.reset()
        self.mood = "ready"
        self.party_until = 0.0
        self.glare_until = 0.0
        self.clear_notices()
        self.show_notice("Se acabó el turno del tomate. PC libre.")

    def open_settings(self):
        if self.timer.running:
            self.show_notice("Pausa o termina antes de cambiar tus permisos")
            return
        if self.settings is not None:
            self.settings.root.lift()
        else:
            self.settings = SettingsDialog(self)

    def apply_config(self, config):
        self.close_browser()
        self.timer = Pomodoro(config)
        self.mood = "ready"
        self.party_until = 0.0
        self.sync_guardian()
        self.clear_notices()
        self.show_notice("Reglas guardadas. El tomate tomó nota.")

    def open_browser(self):
        if not self.timer.config.urls:
            self.show_notice("Añade un enlace en Mis permisos → Páginas web")
            if not self.timer.running:
                self.open_settings()
                self.settings.tabs.select(1)
            return
        if self.browser is not None and self.browser.poll() is None:
            self.show_notice("Tu Web de enfoque ya está abierta")
            return
        command = [sys.executable]
        if not getattr(sys, "frozen", False):
            command.append(str(Path(__file__).resolve()))
        try:
            self.browser_snapshot = tempfile.TemporaryDirectory(prefix="Monociclo-")
            snapshot = Path(self.browser_snapshot.name) / "policy.json"
            self.timer.config.save(snapshot)
            command.extend(["--browser", "--policy-file", str(snapshot),
                            "--parent-pid", str(os.getpid()),
                            "--parent-created", str(psutil.Process().create_time())])
            self.browser = subprocess.Popen(command, creationflags=subprocess.CREATE_NO_WINDOW)
            self.guardian.trust_browser(self.browser)
            self.show_notice("Abriendo la web. Distracciones: fuera de la lista.")
        except (OSError, psutil.Error) as error:
            self.close_browser()
            messagebox.showerror("El navegador no pudo arrancar", str(error), parent=self.root)

    def close_browser(self):
        self.guardian.trust_browser(None)
        if self.browser is not None and self.browser.poll() is None:
            try:
                children = psutil.Process(self.browser.pid).children(recursive=True)
                for child in reversed(children):
                    try:
                        child.terminate()
                    except psutil.Error:
                        pass
                self.browser.terminate()
            except (OSError, psutil.Error):
                pass
        self.browser = None
        if self.browser_snapshot is not None:
            self.browser_snapshot.cleanup()
            self.browser_snapshot = None

    def reload(self) -> None:
        if self.timer.running:
            self.show_notice("Pausa o finaliza antes de recargar")
            return
        try:
            config = Config.load(self.path)
        except (OSError, ValueError) as error:
            messagebox.showerror("Configuración inválida", str(error), parent=self.root)
            return
        self.apply_config(config)

    def show_notice(self, text: str) -> None:
        self.notice_until = time.monotonic() + 3.5
        self.notice_label.configure(text=text, fg=self.RED)

    def clear_notices(self) -> None:
        self.notice_until = 0.0
        while True:
            try:
                self.notices.get_nowait()
            except queue.Empty:
                return

    def refresh(self) -> None:
        if self.closing:
            return
        changed = self.timer.tick()
        if self.browser is not None and self.browser.poll() is not None:
            self.guardian.trust_browser(None)
            self.browser = None
            if self.browser_snapshot is not None:
                self.browser_snapshot.cleanup()
                self.browser_snapshot = None
        self.sync_guardian()
        if changed:
            self.mood = "break" if self.timer.phase == "break" else "focused"
            self.party_until = time.monotonic() + 3.0
            self.clear_notices()
            self.quip = random.choice(self.QUIPS)
            self.show_notice("Descanso: estira las patas, humano." if self.timer.phase == "break" else "Otro tomate. ¡Vamos con esa idea!")
            self.root.bell()
        if self.timer.running and not self.guardian.is_alive():
            self.pause()
            self.show_notice("Guardián detenido · sesión pausada")
        while True:
            try:
                notice = self.notices.get_nowait()
            except queue.Empty:
                break
            # En descanso no quiero que aparezca un regaño que quedó en la cola.
            if self.timer.running and self.timer.phase == "work":
                if notice.startswith("Bloqueada:"):
                    self.glare_until = time.monotonic() + 2.5
                    self.party_until = 0.0
                    notice = "¡Epa! " + notice.split(":", 1)[1].strip() + " se fue al banquillo."
                self.show_notice(notice)
        seconds = math.ceil(self.timer.remaining)
        minutes, seconds = divmod(seconds, 60)
        self.time_label.configure(text=f"{minutes:02d}:{seconds:02d}",
                                  fg=self.TEXT if self.timer.phase == "work" else self.BLUE)
        phase = "ENFOCADO" if self.timer.phase == "work" else "DESCANSO"
        status = "en marcha" if self.timer.running else "listo / pausa"
        self.state_label.configure(text=f"{phase} · {status}")
        self.counter_label.configure(text=f"{self.timer.completed} tomates completados · a tu ritmo")
        self.start_button.configure(state="disabled" if self.timer.running else "normal",
                                    text="¡A enfocarse!" if self.timer.remaining == self.timer.config.work_seconds
                                    and self.timer.phase == "work" else "Reanudar")
        self.pause_button.configure(state="normal" if self.timer.running else "disabled")
        if time.monotonic() >= self.notice_until:
            active = self.timer.running and self.timer.phase == "work"
            self.notice_label.configure(text=self.quip if active else "PC libre. El tomate está de recreo.",
                                        fg=self.MUTED)
        duration = self.timer.config.work_seconds if self.timer.phase == "work" else self.timer.config.break_seconds
        fraction = min(1.0, max(0.0, 1-self.timer.remaining/duration))
        self.progress.delete("all")
        self.progress.create_line(5, 5, 327, 5, fill=self.PANEL, width=8, capstyle="round")
        if fraction > 0:
            self.progress.create_line(5, 5, 5+322*fraction, 5, fill=self.CORAL if self.timer.phase == "work" else self.BLUE,
                                      width=8, capstyle="round")
        config = self.timer.config
        self.summary_label.configure(text=f"{len(config.effective_whitelist)} apps · {len(config.urls)} enlaces · "
                                          + ("web controlada" if config.strict_web else "apps permitidas"))
        self.draw_mascot()
        self.root.after(100, self.refresh)

    def keep_on_top(self) -> None:
        if not self.closing:
            self.root.attributes("-topmost", True)
            self.root.after(2000, self.keep_on_top)

    def close(self) -> None:
        if self.closing:
            return
        self.closing = True
        self.guardian.stop()
        self.close_browser()
        self.guardian.join(timeout=0.6)
        self.root.destroy()


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("Monociclo sólo funciona en Windows.")
    # Esto evita que Windows deje las letras borrosas en pantallas grandes.
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    root.withdraw()
    path = config_path()
    try:
        config = Config.load(path)
        app = FocusHUD(root, config, path)
    except (OSError, ValueError) as error:
        messagebox.showerror("Monociclo", f"No se pudo iniciar.\n\n{error}\n\nArchivo: {path}", parent=root)
        root.destroy()
        return

    def report_callback_error(kind, value, traceback) -> None:
        # Si metí la pata en un botón, libero el PC antes de mostrar el error.
        app.stop()
        messagebox.showerror("Monociclo", f"Sesión detenida por un error:\n{value}", parent=root)

    root.report_callback_exception = report_callback_error
    root.deiconify()
    try:
        root.mainloop()
    finally:
        app.guardian.stop()
        app.guardian.join(timeout=0.6)


# Dejé los permisos aquí abajo porque el HUD fue lo primero que armé.
class SettingsDialog:
    """La ventanita de casillas, enlaces y minutos. Todo termina en el JSON."""

    def __init__(self, hud):
        self.hud = hud
        self.root = tk.Toplevel(hud.root)
        self.root.title("Monociclo · Mis permisos")
        self.root.configure(bg=hud.BG)
        self.root.attributes("-topmost", True)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.config = hud.timer.config
        self.work = tk.StringVar(value=f"{self.config.work_seconds/60:g}")
        self.rest = tk.StringVar(value=f"{self.config.break_seconds/60:g}")
        self.strict = tk.BooleanVar(value=self.config.strict_web)
        self.rules = list(self.config.urls)
        self.apps = discover_apps()
        self.apps.update({name: APP_NAMES.get(name, name[:-4].title()) for name in self.config.whitelist})
        self.app_vars = {name: tk.BooleanVar(value=name in self.config.whitelist) for name in self.apps}
        self.search = tk.StringVar()
        self.url = tk.StringVar()
        self.scope = tk.StringVar(value="exact")
        self.build()
        self.root.update_idletasks()
        monitor = win32api.MonitorFromPoint((0, 0), win32con.MONITOR_DEFAULTTOPRIMARY)
        left, top, right, bottom = win32api.GetMonitorInfo(monitor)["Work"]
        width, height = min(780, right-left-40), min(710, bottom-top-40)
        self.root.geometry(f"{width}x{height}+{left+(right-left-width)//2}+{top+(bottom-top-height)//2}")
        self.root.minsize(min(660, width), min(540, height))
        self.root.lift()
        self.root.focus_set()

    def text(self, parent, text, **kwargs):
        options = dict(bg=self.hud.BG, fg=self.hud.TEXT, font=("Segoe UI", 10), anchor="w", justify="left")
        options.update(kwargs)
        return tk.Label(parent, text=text, **options)

    def entry(self, parent, variable):
        return tk.Entry(parent, textvariable=variable, bg=self.hud.PANEL, fg=self.hud.TEXT,
                        insertbackground=self.hud.TEXT, relief="flat", font=("Segoe UI", 11),
                        highlightthickness=1, highlightbackground="#45516b", highlightcolor=self.hud.CORAL)

    def check(self, parent, text, variable, **kwargs):
        return tk.Checkbutton(parent, text=text, variable=variable, bg=self.hud.BG, fg=self.hud.TEXT,
                              selectcolor=self.hud.PANEL, activebackground=self.hud.BG,
                              activeforeground=self.hud.TEXT, font=("Segoe UI", 10),
                              anchor="w", cursor="hand2", **kwargs)

    def build(self):
        outer = tk.Frame(self.root, bg=self.hud.BG)
        outer.pack(fill="both", expand=True, padx=22, pady=18)
        self.text(outer, "Tu foco, tus reglas.", font=("Segoe UI", 23, "bold")).pack(fill="x")
        self.text(outer, "El tomate pone límites. Tú eliges dónde. Sin editar archivos raros.",
                  fg=self.hud.MUTED).pack(fill="x", pady=(4, 15))
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Focus.TNotebook", background=self.hud.BG, borderwidth=0)
        style.configure("Focus.TNotebook.Tab", background=self.hud.PANEL, foreground=self.hud.TEXT,
                        padding=(15, 10), font=("Segoe UI", 10))
        style.map("Focus.TNotebook.Tab", background=[("selected", self.hud.CORAL)],
                  foreground=[("selected", self.hud.BG)])
        self.tabs = ttk.Notebook(outer, style="Focus.TNotebook")
        self.tabs.pack(fill="both", expand=True)
        apps = tk.Frame(self.tabs, bg=self.hud.BG, padx=12, pady=14)
        web = tk.Frame(self.tabs, bg=self.hud.BG, padx=12, pady=14)
        timer = tk.Frame(self.tabs, bg=self.hud.BG, padx=12, pady=14)
        self.tabs.add(apps, text="Aplicaciones")
        self.tabs.add(web, text="Páginas web")
        self.tabs.add(timer, text="Mi Pomodoro")

        self.text(apps, "¿Qué herramientas necesitas?", font=("Segoe UI", 14, "bold")).pack(fill="x")
        self.text(apps, "Marca las permitidas. Las demás se minimizan durante el foco.",
                  fg=self.hud.MUTED).pack(fill="x", pady=(3, 10))
        search_row = tk.Frame(apps, bg=self.hud.BG)
        search_row.pack(fill="x", pady=(0, 8))
        self.entry(search_row, self.search).pack(side="left", fill="x", expand=True, ipady=7)
        self.hud.button(search_row, "Actualizar abiertas", self.refresh_apps).pack(side="left", padx=(8, 0))
        self.search.trace_add("write", lambda *_: self.render_apps())
        listing = tk.Frame(apps, bg=self.hud.BG)
        listing.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(listing, bg=self.hud.BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(listing, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.app_list = tk.Frame(self.canvas, bg=self.hud.BG)
        self.canvas_window = self.canvas.create_window((0, 0), window=self.app_list, anchor="nw")
        self.app_list.bind("<Configure>", lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(self.canvas_window, width=event.width))
        self.hud.button(apps, "+ Elegir una aplicación (.exe)", self.choose_exe).pack(anchor="w", pady=(8, 4))
        self.text(apps, "Incluye apps habituales y con ventanas abiertas. Puedes añadir otras con su .exe.",
                  fg=self.hud.MUTED, wraplength=650).pack(fill="x")
        self.render_apps()

        self.check(web, "Usar navegación controlada", self.strict, command=self.render_apps).pack(fill="x")
        self.text(web, "Activado: los navegadores externos se bloquean durante el foco.\n"
                       "Abre tus páginas desde el botón «Web de enfoque» del tomate.",
                  fg=self.hud.MUTED, wraplength=650).pack(fill="x", pady=(4, 14))
        self.text(web, "Enlace permitido", font=("Segoe UI", 12, "bold")).pack(fill="x")
        self.entry(web, self.url).pack(fill="x", ipady=8, pady=(6, 4))
        self.text(web, "Ejemplo: https://docs.python.org/3/library/tkinter.html", fg=self.hud.MUTED).pack(fill="x")
        choice = tk.Frame(web, bg=self.hud.BG)
        choice.pack(fill="x", pady=8)
        for title, value in [("Sólo esta página", "exact"), ("Sitio completo", "site")]:
            tk.Radiobutton(choice, text=title, variable=self.scope, value=value, bg=self.hud.BG,
                           fg=self.hud.TEXT, selectcolor=self.hud.PANEL, activebackground=self.hud.BG,
                           activeforeground=self.hud.TEXT, font=("Segoe UI", 10)).pack(side="left", padx=(0, 14))
        self.hud.button(choice, "+ Añadir enlace", self.add_url, self.hud.CORAL).pack(side="right")
        self.text(web, "Página: ruta y parámetros exactos; las anclas # sí se permiten.\n"
                       "Sitio: mismo protocolo, dominio y puerto; no incluye subdominios.",
                  fg=self.hud.MUTED, wraplength=650).pack(fill="x", pady=(0, 10))
        url_container = tk.Frame(web, bg=self.hud.BG)
        url_container.pack(fill="both", expand=True)
        self.url_list = tk.Listbox(url_container, bg=self.hud.PANEL, fg=self.hud.TEXT,
                                  selectbackground="#465379", selectforeground=self.hud.TEXT,
                                  relief="flat", font=("Segoe UI", 10), height=5, exportselection=False)
        url_scroll = ttk.Scrollbar(url_container, command=self.url_list.yview)
        self.url_list.configure(yscrollcommand=url_scroll.set)
        url_scroll.pack(side="right", fill="y")
        self.url_list.pack(side="left", fill="both", expand=True)
        self.hud.button(web, "Quitar enlace seleccionado", self.remove_url).pack(anchor="w", pady=8)
        self.render_urls()

        self.text(timer, "Un sprint, un respiro.", font=("Segoe UI", 17, "bold")).pack(fill="x", pady=(5, 8))
        self.text(timer, "El tomate es intenso, pero también necesita vacaciones de 5 minutos.",
                  fg=self.hud.MUTED, wraplength=650).pack(fill="x", pady=(0, 22))
        for title, variable in [("Trabajo · minutos", self.work), ("Descanso · minutos", self.rest)]:
            self.text(timer, title).pack(fill="x", pady=(6, 6))
            self.entry(timer, variable).pack(anchor="w", ipady=8)
        self.text(timer, "Entre 0 y 240 minutos. Puedes usar decimales.\n"
                         "Trabajo → descanso → trabajo, hasta que tú lo detengas.",
                  fg=self.hud.MUTED).pack(fill="x", pady=18)
        footer = tk.Frame(outer, bg=self.hud.BG)
        footer.pack(fill="x", pady=(14, 0))
        self.text(footer, "Guardar reinicia el temporizador.", fg=self.hud.MUTED).pack(side="left")
        self.hud.button(footer, "Guardar mis reglas", self.save, self.hud.CORAL).pack(side="right")
        self.hud.button(footer, "Cancelar", self.close).pack(side="right", padx=8)

    def render_apps(self):
        for widget in self.app_list.winfo_children():
            widget.destroy()
        query = self.search.get().casefold()
        for name in sorted(self.apps, key=lambda item: self.apps[item].casefold()):
            title = self.apps[name]
            if query and query not in (title + name).casefold():
                continue
            browser = self.strict.get() and name in BROWSER_EXES
            self.check(self.app_list, f"{title}   ·   {name}" + ("   [usa Web de enfoque]" if browser else ""),
                       self.app_vars[name], state="disabled" if browser else "normal").pack(fill="x", pady=4)

    def refresh_apps(self):
        self.apps.update(discover_apps())
        for name in self.apps:
            self.app_vars.setdefault(name, tk.BooleanVar(value=False))
        self.render_apps()

    def choose_exe(self):
        path = filedialog.askopenfilename(parent=self.root, title="Elige la app que puede acompañarte",
                                          filetypes=[("Aplicación de Windows", "*.exe")])
        if path:
            name = Path(path).name.casefold()
            self.apps[name] = APP_NAMES.get(name, Path(path).stem)
            self.app_vars[name] = tk.BooleanVar(value=True)
            self.search.set("")
            self.render_apps()

    def render_urls(self):
        self.url_list.delete(0, "end")
        for rule in self.rules:
            self.url_list.insert("end", ("PÁGINA" if rule.scope == "exact" else "SITIO") + "  ·  " + rule.url)

    def add_url(self):
        try:
            rule = UrlRule.parse({"url": self.url.get(), "scope": self.scope.get()})
        except ValueError as error:
            messagebox.showerror("Ese enlace se tropezó", str(error), parent=self.root)
            return
        if rule not in self.rules:
            self.rules.append(rule)
        self.url.set("")
        self.render_urls()

    def remove_url(self):
        selected = self.url_list.curselection()
        if selected:
            del self.rules[selected[0]]
            self.render_urls()

    def save(self):
        try:
            data = {"work_minutes": float(self.work.get().replace(",", ".")),
                    "break_minutes": float(self.rest.get().replace(",", ".")),
                    "whitelist": [name for name, value in self.app_vars.items() if value.get()],
                    "strict_web": self.strict.get(),
                    "urls": [{"url": rule.url, "scope": rule.scope} for rule in self.rules]}
            config = Config.from_dict(data)
            config.save(self.hud.path)
        except (ValueError, OSError) as error:
            messagebox.showerror("Revisemos tus reglas", str(error), parent=self.root)
            return
        self.hud.apply_config(config)
        self.close()

    def close(self):
        self.hud.settings = None
        self.root.destroy()


if __name__ == "__main__":
    if "--browser" in sys.argv:
        run_browser()
    else:
        main()
