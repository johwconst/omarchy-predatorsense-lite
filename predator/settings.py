"""Padrões de iluminação e LEDs do Claude Code, em ~/.config/predator-rgb/settings.json."""
import fcntl
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass, field

from .hw import LightEffect, LightSetting, LightTarget, PerformanceMode

FOLDER = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "predator-rgb")
FILE = os.path.join(FOLDER, "settings.json")
RUNTIME = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "predator-rgb")


@dataclass
class ClaudeLight:
    Effect: LightEffect
    R: int
    G: int
    B: int
    Speed: int = 5

    def to_json(self) -> dict:
        return {"Effect": self.Effect.name, "R": self.R, "G": self.G, "B": self.B, "Speed": self.Speed}

    @staticmethod
    def from_json(d: dict) -> "ClaudeLight":
        return ClaudeLight(LightEffect[d["Effect"]], d["R"], d["G"], d["B"], d.get("Speed", 5))


# Estado, texto e padrão de cada estado do Claude.
CLAUDE_STATES = [
    ("running", "Executando", ClaudeLight(LightEffect.Static, 0, 90, 255)),
    ("waiting", "Aguardando", ClaudeLight(LightEffect.Static, 255, 110, 0)),
    ("done", "Terminou", ClaudeLight(LightEffect.Static, 0, 255, 40)),
    ("limit", "Limite", ClaudeLight(LightEffect.Static, 255, 0, 0)),
]


@dataclass
class Settings:
    Lighting: list = field(default_factory=list)          # [LightSetting]
    ClaudeTargets: list = field(default_factory=list)     # [LightTarget]
    ClaudeLights: dict = field(default_factory=dict)      # "Alvo.estado" | "estado" -> ClaudeLight
    ClaudeSounds: dict = field(default_factory=dict)      # estado -> "" (padrão) | caminho; ausente = desligado
    # Modo aplicado ao iniciar, ao acordar e ao trocar a fonte de energia; None = não alterar.
    ModeOnAC: PerformanceMode = None
    ModeOnBattery: PerformanceMode = None
    # Limite de carga em 80%, reaplicado ao iniciar e ao acordar (o firmware pode ligá-lo sozinho).
    ChargeLimit: bool = False
    # Temperaturas de CPU/GPU na barra do Omarchy no lugar do ícone.
    BarTemps: bool = False

    def mode_for(self, on_ac: bool):
        return self.ModeOnAC if on_ac else self.ModeOnBattery

    def light_for(self, target: LightTarget, state: str) -> ClaudeLight:
        # A chave antiga só com o estado vale para todos os LEDs até ser personalizada.
        return (self.ClaudeLights.get(f"{LightTarget(target).name}.{state}")
                or self.ClaudeLights.get(state)
                or next(d for s, _, d in CLAUDE_STATES if s == state))

    def saved_lighting(self, target: LightTarget):
        return next((l for l in self.Lighting if l.Target == target), None)

    @staticmethod
    def load() -> "Settings":
        try:
            with open(FILE) as f:
                d = json.load(f)
        except (OSError, ValueError):
            return Settings()
        try:
            return Settings(
                Lighting=[LightSetting.from_json(x) for x in d.get("Lighting", [])],
                ClaudeTargets=[LightTarget[x] for x in d.get("ClaudeTargets", [])],
                ClaudeLights={k: ClaudeLight.from_json(v) for k, v in d.get("ClaudeLights", {}).items()},
                ClaudeSounds=dict(d.get("ClaudeSounds", {})),
                ModeOnAC=PerformanceMode[d["ModeOnAC"]] if d.get("ModeOnAC") else None,
                ModeOnBattery=PerformanceMode[d["ModeOnBattery"]] if d.get("ModeOnBattery") else None,
                ChargeLimit=bool(d.get("ChargeLimit", False)),
                BarTemps=bool(d.get("BarTemps", False)),
            )
        except (KeyError, TypeError, ValueError):
            return Settings()

    def save(self) -> None:
        os.makedirs(FOLDER, exist_ok=True)
        d = {
            "Lighting": [l.to_json() for l in self.Lighting],
            "ClaudeTargets": [t.name for t in self.ClaudeTargets],
            "ClaudeLights": {k: v.to_json() for k, v in self.ClaudeLights.items()},
            "ClaudeSounds": self.ClaudeSounds,
            "ModeOnAC": self.ModeOnAC.name if self.ModeOnAC is not None else None,
            "ModeOnBattery": self.ModeOnBattery.name if self.ModeOnBattery is not None else None,
            "ChargeLimit": self.ChargeLimit,
            "BarTemps": self.BarTemps,
        }
        tmp = FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        os.replace(tmp, FILE)

    @staticmethod
    @contextmanager
    def editing():
        """Lê, deixa alterar e grava, travado: bandeja, painel e CLI são processos diferentes no mesmo arquivo."""
        os.makedirs(FOLDER, exist_ok=True)
        with open(FILE + ".lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            s = Settings.load()
            yield s
            s.save()

    @staticmethod
    def save_lighting(setting: LightSetting) -> None:
        with Settings.editing() as s:
            s.Lighting = [l for l in s.Lighting if l.Target != setting.Target] + [setting]

    @staticmethod
    def set_claude_target(target: LightTarget, enabled: bool) -> None:
        with Settings.editing() as s:
            s.ClaudeTargets = [t for t in s.ClaudeTargets if t != target] + ([LightTarget(target)] if enabled else [])

    @staticmethod
    def save_claude_light(target: LightTarget, state: str, light: ClaudeLight) -> None:
        with Settings.editing() as s:
            s.ClaudeLights[f"{LightTarget(target).name}.{state}"] = light

    @staticmethod
    def save_power_mode(on_ac: bool, mode) -> None:
        mode = PerformanceMode(mode) if mode is not None else None
        with Settings.editing() as s:
            if on_ac:
                s.ModeOnAC = mode
            else:
                s.ModeOnBattery = mode

    @staticmethod
    def save_charge_limit(enabled: bool) -> None:
        with Settings.editing() as s:
            s.ChargeLimit = bool(enabled)

    @staticmethod
    def save_bar_temps(enabled: bool) -> None:
        with Settings.editing() as s:
            s.BarTemps = bool(enabled)

    @staticmethod
    def save_claude_sound(state: str, sound) -> None:
        with Settings.editing() as s:
            if sound is None:
                s.ClaudeSounds.pop(state, None)
            else:
                s.ClaudeSounds[state] = sound
