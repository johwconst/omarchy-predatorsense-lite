"""Mostra nos LEDs escolhidos o estado do Claude Code.

No Linux o HID não exige root, então o próprio hook (`predator claude <estado>`) grava os LEDs.
O último estado e os LEDs acesos ficam em $XDG_RUNTIME_DIR/predator-rgb/claude.json, porque cada hook é um processo novo.
"""
import json
import os
import shutil
import subprocess

from . import hw
from .hw import LightEffect, LightSetting, LightTarget, LightDirection
from .settings import CLAUDE_STATES, RUNTIME, Settings

STATE_FILE = os.path.join(RUNTIME, "claude.json")
STATES = [s for s, _, _ in CLAUDE_STATES]
DEFAULT_SOUND = {"waiting": "/usr/share/sounds/freedesktop/stereo/dialog-warning.oga",
                 "limit": "/usr/share/sounds/freedesktop/stereo/dialog-error.oga"}
FALLBACK_SOUND = "/usr/share/sounds/freedesktop/stereo/complete.oga"


def _load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_state(state: str, lit: list, retry: bool = False) -> None:
    os.makedirs(RUNTIME, exist_ok=True)
    d = {"state": state, "lit": [LightTarget(t).name for t in lit]}
    if retry:
        d["retry"] = True  # algum LED falhou: o próximo evento grava de novo, mesmo repetido
    with open(STATE_FILE, "w") as f:
        json.dump(d, f)


def play_sound(state: str, settings: Settings = None) -> None:
    sound = (settings or Settings.load()).ClaudeSounds.get(state)
    if sound is None:
        return
    path = sound or DEFAULT_SOUND.get(state, FALLBACK_SOUND)
    player = shutil.which("pw-play") or shutil.which("paplay")
    if player and os.path.exists(path):
        # Sem esperar: o hook precisa voltar rápido.
        subprocess.Popen([player, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def restore(target: LightTarget, settings: Settings = None) -> None:
    if target == LightTarget.ModeButton:
        # A cor do botão é do firmware: regravar o modo atual devolve a cor padrão (PROTOCOLO.md).
        mode = hw.get_mode()
        if mode is not None:
            hw.set_mode(mode)
        return
    saved = (settings or Settings.load()).saved_lighting(target)
    if saved is not None:
        hw.set_lighting(saved)  # ponytail: sem padrão salvo o LED fica na última cor do Claude


def set_state(state: str) -> None:
    """Chamado pelos hooks. `off` (ou qualquer estado desconhecido) restaura os LEDs."""
    previous = _load_state()
    settings = Settings.load()
    lit = [t.name for t in settings.ClaudeTargets] if state in STATES else []
    if (previous.get("state") == state and set(previous.get("lit", [])) == set(lit)
            and not previous.get("retry")):
        return  # PostToolUse repete "running" a cada ferramenta: os LEDs já estão assim, nada a gravar
    if state != previous.get("state") and previous:
        play_sound(state, settings)  # só quando muda; nada na primeira vez
    apply(state, [LightTarget[t] for t in previous.get("lit", [])], settings)


def refresh() -> None:
    """Reaplica o estado atual depois de algo sobrescrever os LEDs (padrão salvo, troca de modo, nova cor)."""
    previous = _load_state()
    if previous:
        apply(previous.get("state", "off"), [LightTarget[t] for t in previous.get("lit", [])])


def apply(state: str, lit: list, settings: Settings = None) -> None:
    settings = settings or Settings.load()
    targets = settings.ClaudeTargets
    active = state in STATES
    errors = []

    for target in lit:
        if not active or target not in targets:
            try:
                restore(target, settings)
            except Exception as e:  # LED indisponível agora: o próximo evento tenta de novo
                errors.append(e)

    for target in targets if active else []:
        light = settings.light_for(target, state)
        # Logo e botão de modo só têm Estático, Respiração e Neon: nos outros efeitos ficam na cor estática.
        effect = light.Effect if target == LightTarget.Keyboard or light.Effect in hw.LOGO_EFFECTS else LightEffect.Static
        is_static = effect == LightEffect.Static
        try:
            hw.set_lighting(LightSetting(target, effect, light.R, light.G, light.B, 100,
                                         0 if is_static else max(0, min(9, light.Speed)), LightDirection.None_, 0))
        except Exception as e:
            errors.append(e)

    _save_state(state, list(targets) if active else [], retry=bool(errors))
    if errors:
        raise errors[0]
