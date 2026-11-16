# omarchy-predatorsense-lite

A lightweight PredatorSense alternative for the **Acer Predator Helios Neo 16S AI (PHN16S-71)** on Linux, built as a native [Omarchy](https://omarchy.org/) shell plugin.

- RGB lighting for the keyboard (4 zones), lid logo and mode button
- Performance modes (Eco, Silent, Balanced, Performance, Turbo), with a mode per power source
- 80% battery charge limit
- Temperatures, fan RPM, GPU power and battery at a glance
- LEDs (and optional sounds) that show what **Claude Code** is doing

On Omarchy everything runs inside `omarchy-shell`, the Quickshell process that already draws the bar: there is no resident Python process. A small Python CLI (`predator`) does the hardware work and exits. Outside Omarchy, a GTK tray icon and a GTK4 panel are available instead.

> Only tested on the PHN16S-71 (BIOS V1.28, kernel 7.2, Omarchy 4.5). Other Predator/Nitro models with the ENE K5130 controller may work, but are untested.

## How it works

On this model the keyboard color is **not** controlled by Acer's WMI: the WMI color methods accept writes and report success, but are wired to nothing. Lighting is driven by a separate chip:

| Item | Value |
|---|---|
| Controller | **ENE K5130**, over I2C HID |
| VID / PID | `0CF2` / `5130` |
| Usage page | `0xFF5A` (vendor) |
| Reports | 11-byte feature reports |

| Feature | Linux interface | Root? |
|---|---|---|
| Lighting (keyboard `0x21`, logo `0x83`, mode button `0x65`) | ENE K5130 HID at `/dev/hidraw*` (`HIDIOCGFEATURE` / `HIDIOCSFEATURE`) | no (`input` group) |
| Performance mode | `/sys/firmware/acpi/platform_profile` (kernel `acer_wmi` with `predator_v4=1`) or `/sys/devices/platform/acer-wmi/thermal_profile` (`facer` module) | write access granted by `install.sh` |
| Keyboard off after 30 s idle (`facer` only) | `/sys/devices/platform/acer-wmi/backlight_timeout` | same |
| 80% charge limit | `/sys/bus/wmi/drivers/acer-wmi-battery/health_mode` (AUR `acer-wmi-battery-dkms`) | same |
| Fan RPM | hwmon `acer` (`fan1_input` CPU, `fan2_input` GPU) | no |
| Temperatures | hwmon `coretemp` (`Package id 0`), `nvidia-smi` (skipped while the GPU sleeps) | no |

The full protocol, with the readings and tests behind it, is in [`PROTOCOLO.md`](PROTOCOLO.md) (Portuguese).

## Install

```bash
git clone https://github.com/johwconst/omarchy-predatorsense-lite.git
cd omarchy-predatorsense-lite
./install.sh              # asks before optional steps
./install.sh --uninstall
```

`install.sh`:

1. links `~/.local/bin/predator` to `bin/predator`;
2. installs a udev rule and a tmpfiles.d config that give the `input` group write access to the ENE hidraw node and the sysfs files above (uses `sudo`);
3. without `facer`, enables `predator_v4=1` for the kernel `acer_wmi` (modprobe.d) so `platform_profile` exposes all five modes; offers to install `acer-wmi-battery-dkms` for the charge limit;
4. on Omarchy, links the plugin into `~/.config/omarchy/plugins/johwconst.predator` and enables it; elsewhere, offers to autostart the tray;
5. offers to add the Claude Code hooks to `~/.claude/settings.json`, keeping your existing hooks (backup in `settings.json.bak.predator`).

Your user must be in the `input` group (`sudo usermod -aG input $USER`, then log in again). Dependencies ship with Omarchy: `python`, `python-gobject`, `jq`, `glib2` (`gdbus`); the GTK fallback also needs `gtk4`, `libadwaita` and `libayatana-appindicator`.

If the bar icon does not show up right after installing, run `omarchy restart shell`.

## Omarchy plugin

The plugin (`omarchy/johwconst.predator/`) is a `service` + `bar-widget`:

- **Bar icon** (right section) opening a panel with three tabs:
  - **General**: monitor, performance mode, mode when plugged in / on battery, charge limit, keyboard timeout;
  - **Lighting**: target, effect, color, brightness, speed, direction and zones, with live preview and **Save as default**;
  - **Claude Code**: which LEDs show Claude's state, and effect, color, speed and sound per state.
- The panel reads the hardware (`predator state --json`) **only while it is open**, every 3 s. When closed it reads nothing and never wakes the GPU.
- **Background service**: reapplies the saved defaults when the shell starts and on resume (logind `PrepareForSleep`, via `gdbus monitor`), and applies the per-source mode when the charger is plugged or unplugged (UPower). Errors become notifications.
- Strings follow Omarchy's language (`i18n/en.json`, `i18n/pt-BR.json`).

```bash
omarchy bar move johwconst.predator --section right
omarchy plugin disable johwconst.predator
```

## CLI

```bash
predator status                  # mode, °C, RPM, battery, GPU (--json, --watch 2)
predator state --json            # status + settings in one JSON (used by the plugin)
predator caps                    # read-only: controller targets and capabilities

predator light keyboard --color FF0000                      # static red
predator light keyboard --color 00FF00 --zones 05           # zones 1 and 3 only
predator light keyboard --effect wave --speed 5 --direction ltr
predator light logo --color 0000FF --brightness 30 --save   # save as default
predator light button --effect breath
predator off                     # turn all three off
predator restore                 # reapply charge limit, per-source mode and saved lighting

predator mode                    # show the mode
predator mode turbo              # silent | balanced | performance | turbo | eco
predator auto-mode --ac turbo --battery balanced            # none = don't change
predator auto-mode --apply       # apply the mode for the current power source
predator timeout on|off          # keyboard off after 30 s idle
predator charge-limit on|off     # 80% charge limit

predator claude-target logo on   # LED that shows Claude's state
predator claude-light logo waiting --effect breath --color FF6E00 --speed 5
predator claude-sound waiting default --play   # off | default | path to a file

predator tray                    # tray icon (outside Omarchy)
predator panel                   # GTK4 panel (outside Omarchy)
```

Effects: `static`, `breath`, `neon`, `wave`, `zoom`, `meteor`. The logo and mode button only support `static`, `breath` and `neon`. Animated effects use the hardware's own colors, so color and zones only apply to `static`.

## Claude Code LEDs

**Blue** while running, **orange** while waiting for you, **green** when done and **red** on a usage limit. On `off` (session end) each LED returns to its saved default, and the mode button returns to the performance-mode color.

| Claude Code event | State |
|---|---|
| `UserPromptSubmit`, `PostToolUse` | running |
| `PreToolUse` (`AskUserQuestion`), `Notification` (`permission_prompt`) | waiting |
| `Stop` | done |
| `StopFailure` (`rate_limit\|billing_error`) | limit |
| `SessionEnd` | off |

The hook writes the LEDs directly (the HID needs no root) and skips the write when the state did not change, so the `running` sent after every tool call costs no HID I/O. Sounds play only when the state changes (`pw-play` / `paplay`).

## Files

| Path | Contents |
|---|---|
| `~/.config/predator-rgb/settings.json` | saved lighting, per-source modes, charge limit, Claude LEDs and sounds |
| `$XDG_RUNTIME_DIR/predator-rgb/claude.json` | last Claude state and which LEDs are lit |
| `$XDG_RUNTIME_DIR/predator-rgb.lock` | serializes HID access (hook, plugin, CLI) |

## Limitations

- **Fans Auto/Max** are not implemented: `facer` only exposes `pwm*_enable` with the `pwm` quirk, which does not apply to the PHN16S-71 (see [`ROADMAP.md`](ROADMAP.md)).
- No sysfs equivalent for PL1/PL2, display overdrive, boot animation or USB charging while off.
- With `facer`, changing the mode also sets `powerprofilesctl`: Silent/Eco → `power-saver`, Balanced → `balanced`, Performance/Turbo → `performance`.

## References

- [cleyton1986/predator-sense](https://github.com/cleyton1986/predator-sense), `predator-sense-gui/src/hardware/hid_rgb.rs`: first documented ENE K5130 protocol for the PHN16S-71.
- [Issue #268 (AN16S-61): ENE K5130 protocol, WMI "wired to nothing"](https://github.com/JafarAkhondali/acer-predator-turbo-and-rgb-keyboard-linux-module/issues/268)
- [Issue #287 (ANV16S-41): same symptom](https://github.com/JafarAkhondali/acer-predator-turbo-and-rgb-keyboard-linux-module/issues/287)

## License

[MIT](LICENSE). Not affiliated with Acer. "Predator" and "PredatorSense" are trademarks of Acer Inc.
