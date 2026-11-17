"""Painel (GTK4 + libadwaita). Criado sob demanda pela bandeja; só lê o hardware enquanto está aberto."""
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import claude, hw  # noqa: E402
from .cli import apply_power_mode, change_mode, set_charge_limit  # noqa: E402
from .hw import LightDirection, LightEffect, LightSetting, LightTarget  # noqa: E402
from .settings import CLAUDE_STATES, ClaudeLight, Settings  # noqa: E402

SWATCHES = [("Vermelho", (255, 0, 0)), ("Laranja", (255, 96, 0)), ("Âmbar", (255, 176, 0)), ("Verde", (0, 255, 64)),
            ("Ciano", (0, 200, 255)), ("Azul", (0, 64, 255)), ("Roxo", (140, 0, 255)), ("Rosa", (255, 0, 160)),
            ("Branco", (255, 255, 255))]
SOUNDS = ["Desligado", "Padrão", "Arquivo…"]

CSS = "".join(f".swatch-{i} {{ background: rgb{c}; min-width: 22px; min-height: 22px; border-radius: 99px; padding: 0; }}"
              for i, (_, c) in enumerate(SWATCHES))
CSS += ".swatch-selected { outline: 2px solid @accent_color; outline-offset: 2px; }"


def combo(title, items, subtitle=None):
    row = Adw.ComboRow(title=title, model=Gtk.StringList.new(items))
    if subtitle:
        row.set_subtitle(subtitle)
    return row


def scale_row(title, lo, hi, value):
    row = Adw.ActionRow(title=title)
    scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, 1)
    scale.set_value(value)
    scale.set_hexpand(True)
    scale.set_size_request(220, -1)
    scale.set_draw_value(True)
    scale.set_value_pos(Gtk.PositionType.LEFT)
    scale.set_digits(0)
    row.add_suffix(scale)
    return row, scale


class ColorRow(Adw.ActionRow):
    """Amostras, seletor de cor e HEX. `on_change(r, g, b)` a cada mudança."""

    def __init__(self, on_change):
        super().__init__(title="Cor")
        self.on_change = on_change
        self.rgb = (255, 0, 0)
        self.updating = False
        box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        self.swatches = []
        for i, (name, c) in enumerate(SWATCHES):
            b = Gtk.Button(tooltip_text=name, css_classes=[f"swatch-{i}"], valign=Gtk.Align.CENTER)
            b.connect("clicked", lambda _b, c=c: self.set_rgb(c, notify=True))
            box.append(b)
            self.swatches.append((c, b))
        self.dialog = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), valign=Gtk.Align.CENTER)
        self.dialog.connect("notify::rgba", self.on_dialog)
        box.append(self.dialog)
        self.hex = Gtk.Entry(max_length=7, width_chars=8, valign=Gtk.Align.CENTER)
        self.hex.connect("activate", self.on_hex)
        box.append(self.hex)
        self.add_suffix(box)

    def set_rgb(self, rgb, notify=False):
        self.rgb = tuple(int(x) for x in rgb)
        self.updating = True
        rgba = Gdk.RGBA()
        rgba.parse("#%02X%02X%02X" % self.rgb)
        self.dialog.set_rgba(rgba)
        self.hex.set_text("#%02X%02X%02X" % self.rgb)
        for c, b in self.swatches:
            (b.add_css_class if c == self.rgb else b.remove_css_class)("swatch-selected")
        self.updating = False
        if notify:
            self.on_change(*self.rgb)

    def on_dialog(self, *_):
        if self.updating:
            return
        c = self.dialog.get_rgba()
        self.set_rgb((round(c.red * 255), round(c.green * 255), round(c.blue * 255)), notify=True)

    def on_hex(self, *_):
        text = self.hex.get_text().strip().lstrip("#")
        try:
            v = int(text, 16)
            if len(text) != 6:
                raise ValueError
        except ValueError:
            self.set_rgb(self.rgb)
            return
        self.set_rgb((v >> 16, (v >> 8) & 0xFF, v & 0xFF), notify=True)


class Panel(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Predator", default_width=640, default_height=820)
        self.toasts = Adw.ToastOverlay()
        page = Adw.PreferencesPage()
        self.toasts.set_child(page)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(self.toasts)
        self.set_content(view)
        self.loading = False
        self.pending = None
        self.reading = False

        page.add(self.build_monitor())
        page.add(self.build_modes())
        page.add(self.extras)
        page.add(self.build_lighting())
        page.add(self.build_claude())

        self.refresh()
        GLib.timeout_add_seconds(3, self.refresh)

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=2))

    def run(self, action, ok=None):
        try:
            action()
            if ok:
                self.toast(ok)
            return True
        except Exception as e:
            self.toast(str(e))
            return False

    # ------------------------------------------------------------------ monitor

    def build_monitor(self):
        g = Adw.PreferencesGroup(title="Monitor")
        self.mon = {}
        for key, title in [("cpu", "CPU"), ("gpu", "GPU"), ("bat", "Bateria"), ("mode", "Modo")]:
            row = Adw.ActionRow(title=title)
            label = Gtk.Label(css_classes=["dim-label", "numeric"])
            row.add_suffix(label)
            g.add(row)
            self.mon[key] = label
        return g

    def refresh(self):
        # nvidia-smi e powerprofilesctl podem demorar: lê numa thread e aplica na thread do GTK.
        if not self.reading:
            self.reading = True
            threading.Thread(target=self.read_status, daemon=True).start()
        return True

    def read_status(self):
        try:
            s = hw.read_status()
        except Exception:
            s = None
        GLib.idle_add(self.show_status, s)

    def show_status(self, s):
        self.reading = False
        if s is None:
            return False

        def v(x, unit):
            return "–" if x is None else f"{x}{unit}"
        self.mon["cpu"].set_text(f"{v(s.cpu_temp, ' °C')} · {v(s.cpu_fan_rpm, ' RPM')}")
        self.mon["gpu"].set_text(f"{v(s.gpu_temp, ' °C')} · {v(s.gpu_fan_rpm, ' RPM')} · {v(s.gpu_power_w, ' W')}")
        self.mon["bat"].set_text(f"{v(s.battery_percent, '%')} · {hw.source_text(s.on_ac)}"
                                 + (f" · {s.system_power_w} W" if s.system_power_w is not None else ""))
        mode = hw.PerformanceMode[s.mode] if s.mode else None
        self.mon["mode"].set_text(hw.mode_text(mode) + (f" · {s.power_profile}" if s.power_profile else ""))
        self.loading = True
        if mode in self.mode_buttons:
            self.mode_buttons[mode].set_active(True)
        if s.keyboard_timeout is not None:
            self.timeout_row.set_active(s.keyboard_timeout)
        if s.charge_limited is not None:
            self.charge_row.set_active(s.charge_limited)
        self.loading = False
        return False  # idle_add: roda uma vez

    # ------------------------------------------------------------------ modos

    def build_modes(self):
        g = Adw.PreferencesGroup(title="Desempenho")
        box = Gtk.Box(css_classes=["linked"], homogeneous=True)
        self.mode_buttons = {}
        group = None
        supported = hw.supported_modes()
        for mode, text in hw.MODES:
            if mode not in supported:
                continue
            b = Gtk.ToggleButton(label=text, group=group)
            group = group or b
            b.connect("toggled", self.on_mode, mode, text)
            box.append(b)
            self.mode_buttons[mode] = b
        g.add(box)

        # Modo automático por fonte: None = não alterar.
        auto_modes = [None] + [m for m, _ in hw.MODES if m in supported]
        names = ["Não alterar"] + [hw.mode_text(m) for m in auto_modes[1:]]
        settings = Settings.load()
        for on_ac, title in ((True, "Na tomada"), (False, "Na bateria")):
            row = combo(title, names, "Aplicado ao iniciar, ao acordar e ao trocar a fonte")
            current = settings.mode_for(on_ac)
            row.set_selected(auto_modes.index(current) if current in auto_modes else 0)
            row.connect("notify::selected", lambda r, _, a=on_ac: self.on_auto_mode(a, auto_modes[r.get_selected()]))
            g.add(row)
        self.extras = Adw.PreferencesGroup()

        self.timeout_row = Adw.SwitchRow(title="Apagar teclado após 30 s sem uso")
        self.timeout_row.connect("notify::active", lambda r, _: self.loading or self.run(
            lambda: hw.set_keyboard_timeout(r.get_active())))
        self.extras.add(self.timeout_row)
        self.charge_row = Adw.SwitchRow(title="Limitar carga em 80%", subtitle="Modo de saúde da bateria")
        self.charge_row.connect("notify::active", lambda r, _: self.loading or self.run(
            lambda: set_charge_limit(r.get_active())))
        self.extras.add(self.charge_row)
        return g

    def on_mode(self, button, mode, text):
        if self.loading or not button.get_active() or hw.get_mode() == mode:
            return
        if not self.run(lambda: change_mode(mode), f"Modo {text} aplicado"):
            self.refresh()

    def on_auto_mode(self, on_ac, mode):
        Settings.save_power_mode(on_ac, mode)
        if hw.on_ac() == on_ac:
            self.run(apply_power_mode)
            self.refresh()

    # ------------------------------------------------------------------ iluminação

    def build_lighting(self):
        g = Adw.PreferencesGroup(title="Iluminação",
                                 description="O padrão salvo de cada alvo é aplicado ao iniciar e ao acordar.")
        save = Gtk.Button(label="Salvar como padrão", css_classes=["suggested-action"], valign=Gtk.Align.CENTER)
        save.connect("clicked", self.on_save)
        g.set_header_suffix(save)

        self.l_target = combo("Alvo", [t for _, t in hw.TARGETS])
        self.l_effect = combo("Efeito", [e for _, e in hw.EFFECTS])
        self.l_color = ColorRow(lambda *_: self.preview())
        row, self.l_brightness = scale_row("Brilho", 0, 100, 100)
        g_row_brightness = row
        self.l_speed_row, self.l_speed = scale_row("Velocidade", 0, 9, 5)
        self.l_direction = combo("Direção", [d for _, d in hw.DIRECTIONS])
        self.l_zones_row = Adw.ActionRow(title="Zonas")
        zbox = Gtk.Box(css_classes=["linked"], valign=Gtk.Align.CENTER)
        self.l_zones = []
        for i in range(4):
            b = Gtk.ToggleButton(label=str(i + 1), active=True)
            b.connect("toggled", lambda *_: self.preview())
            zbox.append(b)
            self.l_zones.append(b)
        self.l_zones_row.add_suffix(zbox)

        for w in (self.l_target, self.l_effect, self.l_color, g_row_brightness, self.l_speed_row,
                  self.l_direction, self.l_zones_row):
            g.add(w)

        self.l_target.connect("notify::selected", lambda *_: self.load_lighting())
        for w in (self.l_effect, self.l_direction):
            w.connect("notify::selected", lambda *_: self.preview())
        for s in (self.l_brightness, self.l_speed):
            s.connect("value-changed", lambda *_: self.preview())
        self.load_lighting()
        return g

    def current_target(self):
        return hw.TARGETS[self.l_target.get_selected()][0]

    def effects_for(self, target):
        return [e for e, _ in hw.EFFECTS if target == LightTarget.Keyboard or e in hw.LOGO_EFFECTS]

    def load_lighting(self):
        target = self.current_target()
        saved = Settings.load().saved_lighting(target) or LightSetting(target, LightEffect.Static, 255, 0, 0)
        self.loading = True
        self.l_effect.set_model(Gtk.StringList.new([dict(hw.EFFECTS)[e] for e in self.effects_for(target)]))
        effects = self.effects_for(target)
        self.l_effect.set_selected(effects.index(saved.Effect) if saved.Effect in effects else 0)
        self.l_color.set_rgb((saved.R, saved.G, saved.B))
        self.l_brightness.set_value(saved.Brightness)
        self.l_speed.set_value(saved.Speed or 5)
        self.l_direction.set_selected([d for d, _ in hw.DIRECTIONS].index(saved.Direction))
        for i, b in enumerate(self.l_zones):
            b.set_active(not saved.Zones or bool(saved.Zones & (1 << i)))
        self.loading = False
        self.update_visibility()

    def update_visibility(self):
        # Cor e zonas só no Estático; velocidade e direção só nos efeitos (PROTOCOLO.md).
        target = self.current_target()
        is_static = self.current_effect() == LightEffect.Static
        self.l_color.set_visible(is_static)
        self.l_zones_row.set_visible(is_static and target == LightTarget.Keyboard)
        self.l_speed_row.set_visible(not is_static)
        self.l_direction.set_visible(not is_static and target == LightTarget.Keyboard)

    def current_effect(self):
        effects = self.effects_for(self.current_target())
        return effects[min(self.l_effect.get_selected(), len(effects) - 1)]

    def current_setting(self):
        zones = sum(1 << i for i, b in enumerate(self.l_zones) if b.get_active())
        r, g, b = self.l_color.rgb
        return LightSetting(self.current_target(), self.current_effect(), r, g, b,
                            int(self.l_brightness.get_value()), int(self.l_speed.get_value()),
                            hw.DIRECTIONS[self.l_direction.get_selected()][0],
                            zones if self.current_target() == LightTarget.Keyboard else 0)

    def preview(self):
        if self.loading:
            return
        self.update_visibility()
        # Envia ao controlador enquanto mexe, no máximo a cada 80 ms.
        if self.pending is None:
            self.pending = GLib.timeout_add(80, self.send_preview)

    def send_preview(self):
        self.pending = None
        setting = self.current_setting()
        if setting.Effect == LightEffect.Static and setting.Target == LightTarget.Keyboard and not setting.Zones:
            self.toast("Selecione ao menos uma zona")
            return False
        self.run(lambda: hw.set_lighting(setting))
        return False

    def on_save(self, *_):
        setting = self.current_setting()

        def save():
            hw.set_lighting(setting)
            Settings.save_lighting(setting)
        self.run(save, "Padrão salvo")

    # ------------------------------------------------------------------ Claude Code

    def build_claude(self):
        g = Adw.PreferencesGroup(title="LEDs do Claude Code",
                                 description="Azul executando, laranja aguardando, verde ao terminar, vermelho no limite.")
        targets = Settings.load().ClaudeTargets
        for target, text in hw.TARGETS:
            row = Adw.SwitchRow(title=text, active=target in targets)
            row.connect("notify::active", lambda r, _, t=target: self.on_claude_target(t, r.get_active()))
            g.add(row)

        self.c_target = combo("Configurar", [t for _, t in hw.TARGETS])
        self.c_state = combo("Estado", [text for _, text, _ in CLAUDE_STATES])
        self.c_effect = combo("Efeito", [e for _, e in hw.EFFECTS])
        self.c_color = ColorRow(lambda *_: self.save_claude())
        self.c_speed_row, self.c_speed = scale_row("Velocidade", 0, 9, 5)
        self.c_sound = combo("Som", SOUNDS)
        for w in (self.c_target, self.c_state, self.c_effect, self.c_color, self.c_speed_row, self.c_sound):
            g.add(w)

        for w in (self.c_target, self.c_state):
            w.connect("notify::selected", lambda *_: self.load_claude())
        self.c_effect.connect("notify::selected", lambda *_: self.save_claude())
        self.c_speed.connect("value-changed", lambda *_: self.save_claude())
        self.c_sound.connect("notify::selected", self.on_sound)
        self.load_claude()
        return g

    def claude_key(self):
        return hw.TARGETS[self.c_target.get_selected()][0], CLAUDE_STATES[self.c_state.get_selected()][0]

    def load_claude(self):
        target, state = self.claude_key()
        settings = Settings.load()
        light = settings.light_for(target, state)
        effects = self.effects_for(target)
        self.loading = True
        self.c_effect.set_model(Gtk.StringList.new([dict(hw.EFFECTS)[e] for e in effects]))
        self.c_effect.set_selected(effects.index(light.Effect) if light.Effect in effects else 0)
        self.c_color.set_rgb((light.R, light.G, light.B))
        self.c_speed.set_value(light.Speed)
        sound = settings.ClaudeSounds.get(state)
        self.c_sound.set_selected(0 if sound is None else 1 if sound == "" else 2)
        self.c_sound.set_subtitle(sound or "")
        self.loading = False
        self.update_claude_visibility()

    def update_claude_visibility(self):
        effects = self.effects_for(self.claude_key()[0])
        is_static = effects[min(self.c_effect.get_selected(), len(effects) - 1)] == LightEffect.Static
        self.c_color.set_visible(is_static)
        self.c_speed_row.set_visible(not is_static)

    def save_claude(self):
        if self.loading:
            return
        self.update_claude_visibility()
        target, state = self.claude_key()
        effects = self.effects_for(target)
        r, g, b = self.c_color.rgb
        Settings.save_claude_light(target, state, ClaudeLight(
            effects[min(self.c_effect.get_selected(), len(effects) - 1)], r, g, b, int(self.c_speed.get_value())))
        self.run(claude.refresh)

    def on_claude_target(self, target, enabled):
        Settings.set_claude_target(target, enabled)
        self.run(claude.refresh)

    def on_sound(self, *_):
        if self.loading:
            return
        state = self.claude_key()[1]
        choice = self.c_sound.get_selected()
        if choice == 2:
            dialog = Gtk.FileDialog(title="Som")
            audio = Gtk.FileFilter(name="Áudio")
            audio.add_mime_type("audio/*")
            filters = Gio.ListStore.new(Gtk.FileFilter)
            filters.append(audio)
            dialog.set_filters(filters)
            dialog.open(self, None, self.on_sound_file, state)
            return
        Settings.save_claude_sound(state, None if choice == 0 else "")
        self.c_sound.set_subtitle("")
        claude.play_sound(state)

    def on_sound_file(self, dialog, result, state):
        try:
            path = dialog.open_finish(result).get_path()
        except GLib.Error:
            self.load_claude()  # cancelado
            return
        Settings.save_claude_sound(state, path)
        self.c_sound.set_subtitle(path)
        claude.play_sound(state)


def main():
    app = Adw.Application(application_id="dev.johwconst.PredatorRGB")
    app.set_flags(Gio.ApplicationFlags.DEFAULT_FLAGS)

    def activate(app):
        win = app.get_active_window()
        if win is None:
            provider = Gtk.CssProvider()
            provider.load_from_string(CSS)
            Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                      Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            win = Panel(app)
        win.present()

    app.connect("activate", activate)
    app.run([sys.argv[0]])
