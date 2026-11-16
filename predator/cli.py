"""predator: linha de comando."""
import os
import sys
import time
from types import SimpleNamespace

from . import hw
from .hw import HardwareError, LightDirection, LightEffect, LightSetting, LightTarget, PerformanceMode
from .settings import Settings

CLAUDE_STATES = ["running", "waiting", "done", "limit", "off"]
TARGET_ARGS = {"keyboard": LightTarget.Keyboard, "logo": LightTarget.Logo, "button": LightTarget.ModeButton}
EFFECT_ARGS = {"static": LightEffect.Static, "breath": LightEffect.Breath, "neon": LightEffect.Neon,
               "wave": LightEffect.Wave, "zoom": LightEffect.Zoom, "meteor": LightEffect.Meteor}
DIRECTION_ARGS = {"none": LightDirection.None_, "ltr": LightDirection.LeftToRight, "rtl": LightDirection.RightToLeft}
MODE_ARGS = {"silent": PerformanceMode.Silent, "balanced": PerformanceMode.Balanced,
             "performance": PerformanceMode.Performance, "turbo": PerformanceMode.Turbo, "eco": PerformanceMode.Eco}
ON_OFF = {"on": True, "off": False}


def _fmt(v, unit=""):
    return "–" if v is None else f"{v}{unit}"


def _yes_no(v):
    return "–" if v is None else ("sim" if v else "não")


def print_status(s: hw.Status) -> None:
    mode = hw.mode_text(PerformanceMode[s.mode]) if s.mode else "–"
    print(f"Modo de desempenho : {mode}" + (f"  (perfil de energia: {s.power_profile})" if s.power_profile else ""))
    print(f"CPU                : {_fmt(s.cpu_temp, ' °C')}   ventoinha {_fmt(s.cpu_fan_rpm, ' RPM')}")
    print(f"GPU                : {_fmt(s.gpu_temp, ' °C')}   ventoinha {_fmt(s.gpu_fan_rpm, ' RPM')}"
          f"   {_fmt(s.gpu_power_w, ' W')}, uso {_fmt(s.gpu_util, '%')}")
    print(f"Bateria            : {_fmt(s.battery_percent, '%')} ({s.battery_status or '–'}), "
          f"{hw.source_text(s.on_ac)}")
    print(f"Limite de carga 80%: {_yes_no(s.charge_limited)}")
    print(f"Apagar teclado 30 s: {_yes_no(s.keyboard_timeout)}")


def cmd_status(a):
    while True:
        s = hw.read_status()
        if a.json:
            print(s.to_json())
        else:
            print_status(s)
        if not a.watch:
            return
        time.sleep(a.watch)
        print()


def cmd_caps(a):
    caps = hw.read_capabilities()
    print("A1", caps["A1"].hex(" "))
    for target, c in caps["A3"].items():
        name = next((n for t, n in hw.TARGETS if t == target), "?")
        modes = int.from_bytes(c[5:9], "little")
        effects = [n for e, n in hw.EFFECTS if modes & (1 << (e - 1))]
        print(f"A3 {c.hex(' ')}  -> 0x{target:02X} {name}: {c[4]} zona(s), efeitos: {', '.join(effects)}")


def _parse_color(text: str):
    text = text.lstrip("#")
    try:
        if len(text) != 6:
            raise ValueError
        v = int(text, 16)
    except ValueError:
        raise ValueError("cor deve ser RRGGBB, ex.: FF0000") from None
    return v >> 16, (v >> 8) & 0xFF, v & 0xFF


def cmd_light(a):
    r, g, b = _parse_color(a.color)
    setting = LightSetting(TARGET_ARGS[a.target], EFFECT_ARGS[a.effect], r, g, b, a.brightness, a.speed,
                           DIRECTION_ARGS[a.direction], int(a.zones, 16))
    hw.set_lighting(setting)
    if a.save:
        Settings.save_lighting(setting)
        print("salvo como padrão.")


def lights_off() -> None:
    for target, _ in hw.TARGETS:
        hw.set_lighting(LightSetting(target, LightEffect.Static, 0, 0, 0, 0))


def cmd_off(a):
    lights_off()


def change_mode(mode) -> None:
    """Grava o modo e reaplica o estado do Claude: regravar o modo devolve a cor padrão do botão."""
    hw.set_mode(mode)
    from . import claude
    claude.refresh()


def apply_power_mode() -> None:
    """Aplica o modo configurado para a fonte atual (tomada ou bateria), se houver um."""
    on_ac = hw.on_ac()
    mode = Settings.load().mode_for(on_ac) if on_ac is not None else None
    if mode is None or hw.get_mode() == mode:
        return
    change_mode(mode)


def set_charge_limit(enabled: bool) -> None:
    """Grava no firmware e guarda como padrão para os próximos inícios."""
    hw.set_charge_limit(enabled)
    Settings.save_charge_limit(enabled)


def apply_charge_limit() -> None:
    limit = Settings.load().ChargeLimit
    current = hw.get_charge_limit()
    if current is not None and current != limit:  # None: módulo acer_wmi_battery ausente
        hw.set_charge_limit(limit)


def restore_saved() -> None:
    """Reaplica o limite de carga, o modo da fonte atual, o padrão salvo e depois o estado do Claude por cima.

    Cada passo roda mesmo se um anterior falhar (ex.: firmware não confirmou o modo); o primeiro erro sobe no fim.
    """
    from . import claude
    steps = [apply_charge_limit, apply_power_mode]
    steps += [lambda s=s: hw.set_lighting(s) for s in Settings.load().Lighting]
    steps.append(claude.refresh)
    errors = []
    for step in steps:
        try:
            step()
        except Exception as e:
            errors.append(e)
    if errors:
        raise errors[0]


def cmd_restore(a):
    restore_saved()


def cmd_mode(a):
    if a.mode is None:
        print(hw.mode_text(hw.get_mode()))
        return
    change_mode(MODE_ARGS[a.mode])
    print(hw.mode_text(hw.get_mode()))


def cmd_auto_mode(a):
    if a.apply:
        apply_power_mode()
        return
    for on_ac, value in ((True, a.ac), (False, a.battery)):
        if value is not None:
            Settings.save_power_mode(on_ac, None if value == "none" else MODE_ARGS[value])
    if a.ac is not None or a.battery is not None:
        apply_power_mode()
    s = Settings.load()
    for text, mode in (("na tomada ", s.ModeOnAC), ("na bateria", s.ModeOnBattery)):
        print(f"{text}: {hw.mode_text(mode) if mode is not None else 'não alterar'}")


def cmd_timeout(a):
    if a.value is not None:
        hw.set_keyboard_timeout(ON_OFF[a.value])
    print(f"apagar teclado após 30 s: {_yes_no(hw.get_keyboard_timeout())}")


def cmd_charge_limit(a):
    if a.value is not None:
        set_charge_limit(ON_OFF[a.value])
    print(f"limite de carga em 80%: {_yes_no(hw.get_charge_limit())}")


def _arg_name(table: dict, value):
    """Nome da CLI (ex.: "keyboard") para um enum, ou None."""
    return next((k for k, v in table.items() if v == value), None)


def _light_json(l) -> dict:
    return {"effect": _arg_name(EFFECT_ARGS, l.Effect), "color": "%02X%02X%02X" % (l.R, l.G, l.B),
            "brightness": l.Brightness, "speed": l.Speed, "direction": _arg_name(DIRECTION_ARGS, l.Direction),
            "zones": l.Zones}


def cmd_state(a):
    """Tudo o que o plugin do Omarchy mostra, num JSON só e com os nomes da própria CLI."""
    import json
    from dataclasses import asdict
    from .claude import STATES
    s = Settings.load()
    status = asdict(hw.read_status())
    status["mode"] = _arg_name(MODE_ARGS, hw.PerformanceMode[status["mode"]]) if status["mode"] else None
    targets = list(TARGET_ARGS.items())
    print(json.dumps({
        "status": status,
        "modes": [_arg_name(MODE_ARGS, m) for m, _ in hw.MODES if m in hw.supported_modes()],
        "autoMode": {"ac": _arg_name(MODE_ARGS, s.ModeOnAC), "battery": _arg_name(MODE_ARGS, s.ModeOnBattery)},
        "chargeLimit": s.ChargeLimit,
        "effects": {name: [_arg_name(EFFECT_ARGS, e) for e, _ in hw.EFFECTS
                           if t == LightTarget.Keyboard or e in hw.LOGO_EFFECTS] for name, t in targets},
        "lighting": {name: (_light_json(l) if (l := s.saved_lighting(t)) else None) for name, t in targets},
        "claude": {
            "targets": [_arg_name(TARGET_ARGS, t) for t in s.ClaudeTargets],
            "lights": {name: {st: {"effect": _arg_name(EFFECT_ARGS, (l := s.light_for(t, st)).Effect),
                                   "color": "%02X%02X%02X" % (l.R, l.G, l.B), "speed": l.Speed}
                              for st in STATES} for name, t in targets},
            "sounds": s.ClaudeSounds,
        },
    }, ensure_ascii=False))


def cmd_claude_target(a):
    from . import claude
    Settings.set_claude_target(TARGET_ARGS[a.target], ON_OFF[a.value])
    claude.refresh()


def cmd_claude_light(a):
    from . import claude
    from .settings import ClaudeLight
    r, g, b = _parse_color(a.color)
    if not 0 <= a.speed <= 9:
        raise ValueError("velocidade deve ficar entre 0 e 9.")
    Settings.save_claude_light(TARGET_ARGS[a.target], a.state, ClaudeLight(EFFECT_ARGS[a.effect], r, g, b, a.speed))
    claude.refresh()


def cmd_claude_sound(a):
    """off = sem som, default = som padrão do estado, ou um caminho de arquivo."""
    from . import claude
    if a.sound is not None:
        if a.sound not in ("off", "default") and not os.path.isfile(a.sound):
            raise ValueError(f"arquivo de som não encontrado: {a.sound}")
        Settings.save_claude_sound(a.state, None if a.sound == "off" else "" if a.sound == "default" else a.sound)
    if a.play:
        claude.play_sound(a.state)


def cmd_claude(a):
    from . import claude
    try:
        claude.set_state(a.state)
    except Exception as e:  # o hook nunca deve atrapalhar o Claude Code
        print(f"predator: {e}", file=sys.stderr)


def cmd_tray(a):
    from . import tray
    tray.main()


def cmd_panel(a):
    from . import panel
    panel.main()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    # Caminho rápido do hook (roda a cada ferramenta do Claude Code): sem montar o argparse.
    if len(argv) == 2 and argv[0] == "claude" and argv[1] in CLAUDE_STATES:
        return cmd_claude(SimpleNamespace(state=argv[1]))

    import argparse
    p = argparse.ArgumentParser(prog="predator", description="Acer Predator PHN16S-71: iluminação, modos e estado.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("status", help="temperaturas, ventoinhas, bateria e modo (só leitura)")
    s.add_argument("--json", action="store_true")
    s.add_argument("--watch", type=float, metavar="SEG")
    s.set_defaults(func=cmd_status)

    sub.add_parser("caps", help="lê os alvos e as capacidades do controlador (não grava)").set_defaults(func=cmd_caps)

    s = sub.add_parser("light", help="iluminação do teclado, do logo ou do botão de modo")
    s.add_argument("target", choices=TARGET_ARGS)
    s.add_argument("--effect", choices=EFFECT_ARGS, default="static")
    s.add_argument("--color", default="FFFFFF", help="RRGGBB (só vale no estático)")
    s.add_argument("--brightness", type=int, default=100, help="0-100")
    s.add_argument("--speed", type=int, default=5, help="0-9 (efeitos)")
    s.add_argument("--direction", choices=DIRECTION_ARGS, default="none", help="teclado, efeitos")
    s.add_argument("--zones", default="0", help="máscara hex das zonas do teclado no estático (0 = todas, ex.: 0F)")
    s.add_argument("--save", action="store_true", help="salvar como padrão")
    s.set_defaults(func=cmd_light)

    sub.add_parser("off", help="apaga teclado, logo e botão").set_defaults(func=cmd_off)
    sub.add_parser("restore", help="reaplica o modo da fonte atual e o padrão salvo").set_defaults(func=cmd_restore)

    s = sub.add_parser("mode", help="mostra ou troca o modo de desempenho")
    s.add_argument("mode", nargs="?", choices=MODE_ARGS)
    s.set_defaults(func=cmd_mode)

    s = sub.add_parser("auto-mode", help="modo aplicado ao iniciar, ao acordar e ao trocar tomada/bateria")
    s.add_argument("--ac", choices=[*MODE_ARGS, "none"], help="modo na tomada (none = não alterar)")
    s.add_argument("--battery", choices=[*MODE_ARGS, "none"], help="modo na bateria (none = não alterar)")
    s.add_argument("--apply", action="store_true", help="só aplica o modo configurado para a fonte atual")
    s.set_defaults(func=cmd_auto_mode)

    s = sub.add_parser("timeout", help="apagar o teclado após 30 s sem uso")
    s.add_argument("value", nargs="?", choices=ON_OFF)
    s.set_defaults(func=cmd_timeout)

    s = sub.add_parser("charge-limit", help="limitar a carga da bateria em 80%%")
    s.add_argument("value", nargs="?", choices=ON_OFF)
    s.set_defaults(func=cmd_charge_limit)

    s = sub.add_parser("claude", help="estado do Claude Code nos LEDs (usado pelos hooks)")
    s.add_argument("state", choices=CLAUDE_STATES)
    s.set_defaults(func=cmd_claude)

    s = sub.add_parser("state", help="estado e configurações em JSON (usado pelo plugin do Omarchy)")
    s.add_argument("--json", action="store_true", help="aceito por clareza; a saída é sempre JSON")
    s.set_defaults(func=cmd_state)

    s = sub.add_parser("claude-target", help="liga ou desliga um LED nos estados do Claude Code")
    s.add_argument("target", choices=TARGET_ARGS)
    s.add_argument("value", choices=ON_OFF)
    s.set_defaults(func=cmd_claude_target)

    s = sub.add_parser("claude-light", help="cor e efeito de um LED num estado do Claude Code")
    s.add_argument("target", choices=TARGET_ARGS)
    s.add_argument("state", choices=CLAUDE_STATES[:-1])
    s.add_argument("--effect", choices=EFFECT_ARGS, default="static")
    s.add_argument("--color", default="FFFFFF", help="RRGGBB")
    s.add_argument("--speed", type=int, default=5, help="0-9 (efeitos)")
    s.set_defaults(func=cmd_claude_light)

    s = sub.add_parser("claude-sound", help="som tocado ao entrar num estado do Claude Code")
    s.add_argument("state", choices=CLAUDE_STATES[:-1])
    s.add_argument("sound", nargs="?", help="off, default ou caminho de um arquivo de áudio")
    s.add_argument("--play", action="store_true", help="toca o som do estado")
    s.set_defaults(func=cmd_claude_sound)

    sub.add_parser("tray", help="ícone na bandeja").set_defaults(func=cmd_tray)
    sub.add_parser("panel", help="painel").set_defaults(func=cmd_panel)

    a = p.parse_args(argv)
    try:
        a.func(a)
    except (HardwareError, ValueError) as e:
        sys.exit(f"predator: {e}")
    except KeyboardInterrupt:
        pass
