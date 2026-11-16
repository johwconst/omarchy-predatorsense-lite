"""Ícone na bandeja (GTK3 + AyatanaAppIndicator3). O painel GTK4 roda em outro processo, aberto sob demanda."""
import fcntl
import os
import subprocess
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3 as AppIndicator, Gio, GLib, Gtk  # noqa: E402

from . import claude, hw  # noqa: E402
from .cli import apply_power_mode, change_mode, lights_off, restore_saved, set_charge_limit  # noqa: E402
from .settings import RUNTIME, Settings  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREDATOR = os.path.join(ROOT, "bin", "predator")
AUTOSTART = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                         "autostart", "predator-tray.desktop")
ICON = os.path.join(ROOT, "icons", "predatorsense-icon.svg")


def notify(text: str) -> None:
    subprocess.Popen(["notify-send", "-a", "Predator", "Predator", text],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run_safely(action) -> None:
    try:
        action()
    except Exception as e:
        notify(str(e))


def set_autostart(enabled: bool) -> None:
    if not enabled:
        if os.path.exists(AUTOSTART):
            os.remove(AUTOSTART)
        return
    os.makedirs(os.path.dirname(AUTOSTART), exist_ok=True)
    with open(AUTOSTART, "w") as f:
        f.write("[Desktop Entry]\nType=Application\nName=Predator\nComment=Iluminação e modos do Acer Predator\n"
                f"Exec={PREDATOR} tray\nIcon={ICON}\nX-GNOME-Autostart-Delay=10\nTerminal=false\n")


class Tray:
    def __init__(self):
        self.indicator = AppIndicator.Indicator.new("predator-rgb", ICON, AppIndicator.IndicatorCategory.HARDWARE)
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.indicator.set_title("Predator")
        self.on_ac = hw.on_ac()
        self.build_menu()
        # Modo e fonte de energia chegam por evento; o timer só atualiza temperatura e ventoinha.
        self.mode_watched = self.watch_mode()
        self.update_info()
        self.update_mode()
        GLib.timeout_add_seconds(10, self.update_info)
        # Reaplica o padrão salvo ao iniciar e ao voltar da suspensão.
        GLib.timeout_add_seconds(2, self.restore_once)
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM)
        bus.signal_subscribe("org.freedesktop.login1", "org.freedesktop.login1.Manager", "PrepareForSleep",
                             "/org/freedesktop/login1", None, Gio.DBusSignalFlags.NONE, self.on_sleep)
        bus.signal_subscribe("org.freedesktop.UPower", "org.freedesktop.DBus.Properties", "PropertiesChanged",
                             "/org/freedesktop/UPower", None, Gio.DBusSignalFlags.NONE, self.on_upower)

    def watch_mode(self) -> bool:
        """O kernel faz sysfs_notify no platform_profile a cada troca (painel, CLI, botão de modo, daemon).

        Ler o modo custa ~12 ms de ACPI; assim só lemos quando muda. O facer não avisa: fica no timer.
        """
        if hw._uses_facer() or not os.path.exists(hw.PLATFORM_PROFILE):
            return False
        fd = os.open(hw.PLATFORM_PROFILE, os.O_RDONLY)
        os.read(fd, 64)  # a notificação só dispara depois de uma leitura
        GLib.io_add_watch(fd, GLib.PRIORITY_DEFAULT, GLib.IOCondition.PRI | GLib.IOCondition.ERR, self.on_mode_file)
        return True

    def on_mode_file(self, fd, _cond):
        os.lseek(fd, 0, os.SEEK_SET)
        os.read(fd, 64)  # rearma
        self.update_mode()
        return True

    def on_upower(self, _conn, _sender, _path, _iface, _signal, params):
        if "OnBattery" in params.unpack()[1]:
            self.check_power_source()

    def restore_once(self, attempt=1):
        # Logo após o login ou a suspensão o HID e o firmware podem ainda não responder: tenta 3 vezes.
        try:
            restore_saved()
        except Exception as e:
            if attempt < 3:
                GLib.timeout_add_seconds(3, self.restore_once, attempt + 1)
            else:
                notify(str(e))
        return False

    def on_sleep(self, _conn, _sender, _path, _iface, _signal, params):
        if not params.unpack()[0]:  # False = acordou
            GLib.timeout_add_seconds(2, self.restore_once)

    # ------------------------------------------------------------------ menu

    def item(self, menu, text, callback=None):
        it = Gtk.MenuItem(label=text)
        if callback:
            it.connect("activate", lambda *_: callback())
        else:
            it.set_sensitive(False)
        menu.append(it)
        return it

    def check(self, menu, text, active, callback):
        it = Gtk.CheckMenuItem(label=text)
        it.set_active(bool(active))
        it.connect("toggled", lambda w: callback(w.get_active()))
        menu.append(it)
        return it

    def submenu(self, menu, text):
        sub = Gtk.Menu()
        it = Gtk.MenuItem(label=text)
        it.set_submenu(sub)
        menu.append(it)
        return sub

    def build_menu(self):
        menu = Gtk.Menu()
        self.info = self.item(menu, "…")
        self.info_mode = self.item(menu, "…")
        menu.append(Gtk.SeparatorMenuItem())
        self.item(menu, "Abrir painel", self.open_panel)

        modes = self.submenu(menu, "Modo de desempenho")
        self.mode_items = {}
        group = None
        current = hw.get_mode()
        supported = hw.supported_modes()
        for mode, text in hw.MODES:
            if mode not in supported:
                continue
            it = Gtk.RadioMenuItem.new_with_label_from_widget(group, text)
            group = it
            it.set_active(mode == current)
            it.connect("toggled", self.on_mode, mode)
            modes.append(it)
            self.mode_items[mode] = it

        light = self.submenu(menu, "Iluminação")
        self.item(light, "Aplicar padrão salvo", lambda: run_safely(restore_saved))
        self.item(light, "Desligar", lambda: run_safely(lights_off))
        self.check(light, "Apagar teclado após 30 s sem uso", hw.get_keyboard_timeout(),
                   lambda on: run_safely(lambda: hw.set_keyboard_timeout(on)))

        leds = self.submenu(menu, "LEDs do Claude Code")
        targets = Settings.load().ClaudeTargets
        for target, text in hw.TARGETS:
            self.check(leds, text, target in targets, lambda on, t=target: self.on_claude_target(t, on))

        self.check(menu, "Limitar carga em 80%", hw.get_charge_limit(),
                   lambda on: run_safely(lambda: set_charge_limit(on)))
        self.check(menu, "Iniciar com a sessão", os.path.exists(AUTOSTART), set_autostart)
        menu.append(Gtk.SeparatorMenuItem())
        self.item(menu, "Sair", Gtk.main_quit)
        menu.show_all()
        self.indicator.set_menu(menu)

    def check_power_source(self):
        # Ligou ou tirou o carregador: aplica o modo configurado para a nova fonte.
        on_ac = hw.on_ac()
        if on_ac != self.on_ac:
            self.on_ac = on_ac
            run_safely(apply_power_mode)

    def update_info(self):
        # Fonte também aqui (sysfs, barato) caso o UPower não esteja rodando.
        self.check_power_source()
        # Só sysfs (barato): o nvidia-smi fica para o painel.
        cpu, rpm = hw.cpu_temp(), hw.fan_rpms()[0]
        self.info.set_label(f"CPU {cpu if cpu is not None else '–'} °C · ventoinha {rpm if rpm is not None else '–'} RPM")
        if not self.mode_watched:
            self.update_mode()
        return True

    def update_mode(self):
        mode = hw.get_mode()
        self.info_mode.set_label(f"Modo: {hw.mode_text(mode)}")
        if mode in self.mode_items and not self.mode_items[mode].get_active():
            self.mode_items[mode].handler_block_by_func(self.on_mode)
            self.mode_items[mode].set_active(True)
            self.mode_items[mode].handler_unblock_by_func(self.on_mode)

    # ------------------------------------------------------------------ ações

    def on_mode(self, item, mode):
        if not item.get_active() or hw.get_mode() == mode:
            return
        run_safely(lambda: change_mode(mode))
        self.update_mode()

    def on_claude_target(self, target, enabled):
        Settings.set_claude_target(target, enabled)
        run_safely(claude.refresh)

    def open_panel(self):
        # Se já estiver aberto, o Gtk.Application do painel só traz a janela para a frente.
        subprocess.Popen([sys.executable, PREDATOR, "panel"])


def main():
    os.makedirs(RUNTIME, exist_ok=True)
    lock = open(os.path.join(RUNTIME, "tray.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit("predator: a bandeja já está aberta.")
    Tray()
    Gtk.main()
