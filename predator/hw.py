"""Acesso direto ao hardware do Acer Predator PHN16S-71 no Linux (protocolos em PROTOCOLO.md).

Iluminação: HID do controlador ENE K5130 (/dev/hidraw*, grupo input, sem root).
Modo, timeout do teclado e limite de carga: sysfs do módulo facer / acer-wmi-battery.
"""
import enum
import fcntl
import glob
import json
import os
import shutil
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass, asdict


class PerformanceMode(enum.IntEnum):
    Silent = 0x00
    Balanced = 0x01
    Performance = 0x04
    Turbo = 0x05
    Eco = 0x06


class LightTarget(enum.IntEnum):
    Keyboard = 0x21
    Logo = 0x83
    ModeButton = 0x65


class LightEffect(enum.IntEnum):
    Static = 0x02
    Breath = 0x04
    Neon = 0x05
    Wave = 0x07
    Zoom = 0x09
    Meteor = 0x0A


class LightDirection(enum.IntEnum):
    None_ = 0
    LeftToRight = 1
    RightToLeft = 2


# No settings.json a direção neutra é gravada como "None".
def direction_name(d: LightDirection) -> str:
    return "None" if d == LightDirection.None_ else d.name


def parse_direction(name: str) -> LightDirection:
    return LightDirection.None_ if name in ("None", "None_") else LightDirection[name]


MODES = [(PerformanceMode.Eco, "Eco"), (PerformanceMode.Silent, "Silencioso"), (PerformanceMode.Balanced, "Equilibrado"),
         (PerformanceMode.Performance, "Desempenho"), (PerformanceMode.Turbo, "Turbo")]
EFFECTS = [(LightEffect.Static, "Estático"), (LightEffect.Breath, "Respiração"), (LightEffect.Neon, "Neon"),
           (LightEffect.Wave, "Onda"), (LightEffect.Zoom, "Zoom"), (LightEffect.Meteor, "Meteoro")]
LOGO_EFFECTS = [LightEffect.Static, LightEffect.Breath, LightEffect.Neon]
TARGETS = [(LightTarget.Keyboard, "Teclado"), (LightTarget.Logo, "Logo da tampa"), (LightTarget.ModeButton, "Botão de modo")]
DIRECTIONS = [(LightDirection.None_, "Neutra"), (LightDirection.LeftToRight, "Esquerda → direita"),
              (LightDirection.RightToLeft, "Direita → esquerda")]


def mode_text(mode) -> str:
    return dict(MODES).get(mode, "–")


def source_text(on_ac) -> str:
    """None = sem fonte Mains conhecida: não dá para afirmar que está na bateria."""
    return "fonte desconhecida" if on_ac is None else "na tomada" if on_ac else "na bateria"


@dataclass
class LightSetting:
    Target: LightTarget
    Effect: LightEffect
    R: int
    G: int
    B: int
    Brightness: int = 100
    Speed: int = 0
    Direction: LightDirection = LightDirection.None_
    Zones: int = 0

    def to_json(self) -> dict:
        return {"Target": self.Target.name, "Effect": self.Effect.name, "R": self.R, "G": self.G, "B": self.B,
                "Brightness": self.Brightness, "Speed": self.Speed, "Direction": direction_name(self.Direction),
                "Zones": self.Zones}

    @staticmethod
    def from_json(d: dict) -> "LightSetting":
        return LightSetting(LightTarget[d["Target"]], LightEffect[d["Effect"]], d["R"], d["G"], d["B"],
                            d.get("Brightness", 100), d.get("Speed", 0), parse_direction(d.get("Direction", "None")),
                            d.get("Zones", 0))


class HardwareError(Exception):
    pass


# Estado de execução e travas: nunca em /tmp (caminho fixo e compartilhado entre usuários).
RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"

NO_PERMISSION = "sem permissão para gravar {}. Rode o install.sh para instalar a regra udev."

# ------------------------------------------------------------------ HID (ENE K5130)

ENE_HID_ID = "HID_ID=0018:00000CF2:00005130"
REPORT_LEN = 11


def _ioc(direction: int, nr: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord("H") << 8) | nr


HIDIOCSFEATURE = _ioc(3, 0x06, REPORT_LEN)
HIDIOCGFEATURE = _ioc(3, 0x07, REPORT_LEN)


def find_controller() -> str:
    for dev in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            with open(f"{dev}/device/uevent") as f:
                if ENE_HID_ID in f.read().split("\n"):
                    return "/dev/" + os.path.basename(dev)
        except OSError:
            continue
    raise HardwareError("controlador de iluminação ENE K5130 não encontrado.")


@contextmanager
def _locked():
    # Hook do Claude, bandeja e painel podem gravar ao mesmo tempo.
    with open(os.path.join(RUNTIME_DIR, "predator-rgb.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


@contextmanager
def _controller():
    path = find_controller()
    try:
        fd = os.open(path, os.O_RDWR)
    except PermissionError:
        raise HardwareError(NO_PERMISSION.format(path))
    except OSError as e:
        raise HardwareError(f"falha ao abrir {path}: {e.strerror or e}") from None
    try:
        yield fd
    finally:
        os.close(fd)


def _ioctl(fd: int, request: int, buf: bytearray) -> None:
    # Ex.: logo após acordar o controlador I2C pode responder EIO/ETIMEDOUT; vira mensagem de uma linha.
    try:
        fcntl.ioctl(fd, request, buf)
    except OSError as e:
        raise HardwareError(f"falha no controlador de iluminação: {e.strerror or e}") from None


def _get(fd: int, report: int) -> bytes:
    buf = bytearray(REPORT_LEN)
    buf[0] = report
    _ioctl(fd, HIDIOCGFEATURE, buf)
    return bytes(buf)


def _set(fd: int, packet) -> None:
    buf = bytearray(packet)
    assert len(buf) == REPORT_LEN
    _ioctl(fd, HIDIOCSFEATURE, buf)


def _caps(fd: int, target: int) -> bytes:
    # 0xA2 seleciona o alvo; 0xA3 devolve [A3, alvo, ?, classe, zonas, bitmap de modos (u32 LE)].
    _set(fd, [0xA2, target] + [0] * 9)
    caps = _get(fd, 0xA3)
    if caps[1] != target:
        raise HardwareError(f"não foi possível ler as capacidades do alvo 0x{target:02X}.")
    return caps


def read_capabilities() -> dict:
    """Só leitura: alvos (A1) e capacidades (A3) de cada um."""
    with _locked(), _controller() as fd:
        a1 = _get(fd, 0xA1)
        return {"A1": a1, "A3": {t: _caps(fd, t) for t in a1[2:2 + a1[1]]}}


def set_lighting(s: LightSetting) -> None:
    target, effect, direction = LightTarget(s.Target), LightEffect(s.Effect), LightDirection(s.Direction)
    if not 0 <= s.Brightness <= 100:
        raise ValueError("brilho deve ficar entre 0 e 100.")
    if not 0 <= s.Speed <= 9:
        raise ValueError("velocidade deve ficar entre 0 e 9.")
    if not all(0 <= c <= 255 for c in (s.R, s.G, s.B)):
        raise ValueError("cor inválida.")

    with _locked(), _controller() as fd:
        caps = _caps(fd, target)
        supported = int.from_bytes(caps[5:9], "little")
        if not supported & (1 << (effect - 1)):
            raise HardwareError(f"{target.name} não suporta o efeito {effect.name}.")

        all_zones = (1 << max(1, caps[4])) - 1
        is_static = effect == LightEffect.Static
        zones = s.Zones & all_zones if is_static and s.Zones else all_zones
        if not zones:
            raise ValueError("nenhuma zona válida selecionada.")

        # Byte 5: direção no teclado; no logo e no botão, flag 1 = estático / 2 = efeito.
        if target == LightTarget.Keyboard:
            flag = 0 if is_static else direction
        else:
            flag = 1 if is_static else 2

        _set(fd, [0xA4, target, effect, s.Brightness, 0 if is_static else s.Speed, flag,
                  s.R, s.G, s.B, zones & 0xFF, zones >> 8])


# ------------------------------------------------------------------ sysfs

ACER_WMI = "/sys/devices/platform/acer-wmi"
THERMAL_PROFILE = f"{ACER_WMI}/thermal_profile"
BACKLIGHT_TIMEOUT = f"{ACER_WMI}/backlight_timeout"
HEALTH_MODE = "/sys/bus/wmi/drivers/acer-wmi-battery/health_mode"


def _read(path: str):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_int(path: str):
    v = _read(path)
    try:
        return int(v, 0) if v is not None else None
    except ValueError:
        return None


def _write(path: str, value) -> None:
    try:
        with open(path, "w") as f:
            f.write(str(value))
    except PermissionError:
        raise HardwareError(NO_PERMISSION.format(path))
    except OSError as e:
        raise HardwareError(f"falha ao gravar {path}: {e}")


def _hwmon(name: str):
    for h in glob.glob("/sys/class/hwmon/hwmon*"):
        if _read(f"{h}/name") == name:
            return h
    return None


# Dois caminhos para o modo: o módulo facer (thermal_profile, código numérico) ou o acer_wmi do kernel
# com predator_v4=1 (platform_profile, por nome). Os códigos do firmware são os mesmos.
PLATFORM_PROFILE = "/sys/firmware/acpi/platform_profile"
PLATFORM_NAMES = {PerformanceMode.Eco: "low-power", PerformanceMode.Silent: "quiet",
                  PerformanceMode.Balanced: "balanced", PerformanceMode.Performance: "balanced-performance",
                  PerformanceMode.Turbo: "performance"}


def _uses_facer() -> bool:
    return os.path.exists(THERMAL_PROFILE)


def get_mode():
    if _uses_facer():
        try:
            return PerformanceMode(_read_int(THERMAL_PROFILE))
        except ValueError:
            return None
    name = _read(PLATFORM_PROFILE)
    return next((m for m, n in PLATFORM_NAMES.items() if n == name), None)


def supported_modes() -> list:
    if _uses_facer():
        mask = _read_int(f"{ACER_WMI}/thermal_profile_supported") or 0
        return [m for m, _ in MODES if mask & (1 << m)]
    choices = (_read(PLATFORM_PROFILE + "_choices") or "").split()
    return [m for m, _ in MODES if PLATFORM_NAMES[m] in choices]


# Perfil do power-profiles-daemon que acompanha cada modo (só com o facer).
POWER_PROFILE = {PerformanceMode.Silent: "power-saver", PerformanceMode.Eco: "power-saver",
                 PerformanceMode.Balanced: "balanced",
                 PerformanceMode.Performance: "performance", PerformanceMode.Turbo: "performance"}


def set_mode(mode: PerformanceMode) -> None:
    mode = PerformanceMode(mode)
    if _uses_facer():
        _write(THERMAL_PROFILE, int(mode))
    elif os.path.exists(PLATFORM_PROFILE):
        _write(PLATFORM_PROFILE, PLATFORM_NAMES[mode])
    else:
        raise HardwareError("modo de desempenho indisponível: carregue o acer_wmi com predator_v4=1 "
                            "(install.sh) ou o módulo facer.")
    # Lê de volta até 4 vezes com 80 ms de intervalo, como o PreySense.
    for _ in range(4):
        if get_mode() == mode:
            break
        time.sleep(0.08)
    else:
        raise HardwareError(f"o firmware não confirmou o modo {mode.name}.")
    # Com o platform_profile, o power-profiles-daemon já acompanha o modo (e regravar por ele trocaria o modo).
    if _uses_facer() and shutil.which("powerprofilesctl"):
        subprocess.run(["powerprofilesctl", "set", POWER_PROFILE[mode]], capture_output=True)


def get_power_profile():
    # Direto no D-Bus do power-profiles-daemon: o powerprofilesctl abre um Python novo a cada leitura (~80 ms).
    # Properties.Get sem proxy em cache, porque o painel lê numa thread sem main loop (o cache ficaria velho).
    try:
        from gi.repository import Gio, GLib
        r = Gio.bus_get_sync(Gio.BusType.SYSTEM).call_sync(
            "net.hadess.PowerProfiles", "/net/hadess/PowerProfiles", "org.freedesktop.DBus.Properties", "Get",
            GLib.Variant("(ss)", ("net.hadess.PowerProfiles", "ActiveProfile")), GLib.VariantType("(v)"),
            Gio.DBusCallFlags.NONE, 1000, None)
        return r.unpack()[0] or None
    except Exception:  # sem gi, sem barramento ou daemon parado
        return None


def get_keyboard_timeout():
    v = _read_int(BACKLIGHT_TIMEOUT)
    return None if v is None else v != 0


def set_keyboard_timeout(enabled: bool) -> None:
    _write(BACKLIGHT_TIMEOUT, 1 if enabled else 0)


def get_charge_limit():
    v = _read_int(HEALTH_MODE)
    return None if v is None else v != 0


def set_charge_limit(enabled: bool) -> None:
    _write(HEALTH_MODE, 1 if enabled else 0)


# ------------------------------------------------------------------ status


@dataclass
class Status:
    cpu_temp: int = None
    gpu_temp: int = None
    cpu_fan_rpm: int = None
    gpu_fan_rpm: int = None
    mode: str = None
    battery_percent: int = None
    battery_status: str = None
    on_ac: bool = None
    charge_limited: bool = None
    keyboard_timeout: bool = None
    power_profile: str = None
    gpu_power_w: float = None
    gpu_util: int = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


def cpu_temp_path():
    """Arquivo do sensor "Package id 0" do coretemp (o índice hwmonN muda entre boots)."""
    h = _hwmon("coretemp")
    if not h:
        return None
    for label in glob.glob(f"{h}/temp*_label"):
        if _read(label) == "Package id 0":
            return label.replace("_label", "_input")
    return None


def gpu_temp_path():
    """Sensor da GPU no EC (hwmon acer, temp2): ler não acorda a GPU, ao contrário do nvidia-smi."""
    h = _hwmon("acer")
    path = f"{h}/temp2_input" if h else None
    return path if path and os.path.exists(path) else None


def cpu_temp():
    path = cpu_temp_path()
    v = _read_int(path) if path else None
    return None if v is None else v // 1000


def _nvidia_suspended() -> bool:
    """True se a GPU NVIDIA está dormindo (runtime PM). Chamar o nvidia-smi nesse estado acordaria a GPU."""
    for dev in glob.glob("/sys/bus/pci/devices/*"):
        if _read(f"{dev}/vendor") == "0x10de" and (_read(f"{dev}/class") or "").startswith("0x03"):
            return _read(f"{dev}/power/runtime_status") == "suspended"
    return False


def _gpu():
    if not shutil.which("nvidia-smi") or _nvidia_suspended():
        return None, None, None
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=temperature.gpu,power.draw,utilization.gpu",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=3)
        temp, power, util = (x.strip() for x in r.stdout.splitlines()[0].split(","))
        return int(temp), float(power), int(util)
    except (subprocess.SubprocessError, ValueError, IndexError):
        return None, None, None


def fan_rpms():
    fans = _hwmon("acer")
    if not fans:
        return None, None
    return _read_int(f"{fans}/fan1_input"), _read_int(f"{fans}/fan2_input")


def on_ac():
    """True na tomada, False na bateria, None se não houver fonte Mains (ACAD neste modelo)."""
    for supply in glob.glob("/sys/class/power_supply/*"):
        if _read(f"{supply}/type") == "Mains":
            return _read_int(f"{supply}/online") == 1
    return None


def read_status() -> Status:
    cpu_fan, gpu_fan = fan_rpms()
    gpu_temp, gpu_power, gpu_util = _gpu()
    mode = get_mode()
    return Status(
        cpu_temp=cpu_temp(),
        gpu_temp=gpu_temp,
        cpu_fan_rpm=cpu_fan,
        gpu_fan_rpm=gpu_fan,
        mode=mode.name if mode is not None else None,
        battery_percent=_read_int("/sys/class/power_supply/BAT1/capacity"),
        battery_status=_read("/sys/class/power_supply/BAT1/status"),
        on_ac=on_ac(),
        charge_limited=get_charge_limit(),
        keyboard_timeout=get_keyboard_timeout(),
        power_profile=get_power_profile(),
        gpu_power_w=gpu_power,
        gpu_util=gpu_util,
    )
