# AGENTS.md

Guidance for AI coding agents (and humans) working on this repository.

## What this is

A PredatorSense replacement for the **Acer Predator PHN16S-71** on Linux, shipped as a native **Omarchy shell plugin**. Two layers:

1. **Python CLI** (`predator/`, entry point `bin/predator`): the only code that touches hardware. Every invocation is a short process that exits.
2. **Omarchy plugin** (repository root: `manifest.json`, `Service.qml`, `Panel.qml`, `Model.js`, `i18n/`, `icon.svg`): QML running inside `omarchy-shell` (Quickshell). It never touches hardware directly; it spawns `~/.local/bin/predator <command>` and parses the output.

The GTK3 tray (`predator/tray.py`) and GTK4 panel (`predator/panel.py`) are the fallback UI for non-Omarchy desktops. Keep them working, but new UI work goes into the plugin first.

## Layout

```
bin/predator                 entry point (adds the repo to sys.path, calls predator.cli.main)
predator/hw.py               hardware: ENE K5130 HID (ioctl), sysfs (mode, timeout, charge limit), status readers
predator/settings.py         ~/.config/predator-rgb/settings.json (dataclass, locked read-modify-write)
predator/claude.py           Claude Code LED states (hook entry point: `predator claude <state>`)
predator/cli.py              argparse CLI; also the JSON contract consumed by the plugin (`state --json`)
predator/tray.py, panel.py   GTK fallback UI (non-Omarchy)
manifest.json, Service.qml,  Omarchy plugin (id io.github.johwconst.predatorsense), at the repository root as the
Panel.qml, Model.js, i18n/   marketplace requires; icon.svg is the bar icon (derived from icons/predatorsense-icon.svg)
icons/                       predatorsense-icon.svg: full icon, used by the GTK tray
system/                      udev rule, tmpfiles.d, modprobe.d, modules-load.d (installed by install.sh)
install.sh                   installer/uninstaller (uses sudo for system/)
PROTOCOLO.md                 ENE K5130 protocol and sysfs paths, with real readings (Portuguese)
```

## Hardware model (read PROTOCOLO.md before touching hw.py)

- Lighting goes through the **ENE K5130** at `/dev/hidraw*` (matched by `HID_ID=0018:00000CF2:00005130`), 11-byte feature reports: `0xA1` targets, `0xA2`/`0xA3` capabilities, `0xA4` set lighting. Targets: keyboard `0x21`, logo `0x83`, mode button `0x65`. Acer's WMI color methods are wired to nothing on this model: do not "fix" lighting through WMI.
- `set_lighting` checks the target's capabilities before writing; the logo and mode button only support Static, Breath and Neon.
- Performance mode: `platform_profile` (kernel `acer_wmi predator_v4=1`) or `thermal_profile` (`facer`). Reading `platform_profile` costs ~12 ms of ACPI: never poll it. The kernel calls `sysfs_notify` on it, so watch it with `POLLPRI` (see `Tray.watch_mode`).
- The mode button color is owned by the firmware: rewriting the current mode restores it (`claude.restore`).
- All HID access is serialized with `$XDG_RUNTIME_DIR/predator-rgb.lock` because the hook, the plugin and the CLI are separate processes. Settings writes go through `Settings.editing()` (file lock). Keep both.

## Performance rules

This tool runs all day next to the user's work. Changes must not add background cost.

- No polling where an event exists: UPower `OnBattery`, logind `PrepareForSleep`, `platform_profile` notify.
- The plugin panel reads `predator state --json` only while open. Do not add timers that run while it is closed.
- Single exception, opt-in: with "temperatures in the bar" on (`predator bar-temps on`), the widget re-reads two sysfs files (coretemp `Package id 0` and the EC GPU sensor, hwmon `acer` `temp2_input`) through `FileView` every 3 s. The paths come from `predator bar-temps` once at load. No process spawn and never `nvidia-smi` there; with the option off no timer runs.
- `nvidia-smi` wakes a suspended GPU: always guard it with `_nvidia_suspended()`.
- Do not spawn helper processes for things readable directly (the power profile is read over D-Bus, not `powerprofilesctl`).
- The Claude hook runs after **every** tool call: keep `predator claude <state>` on the argparse-free fast path in `cli.main`, and keep the "unchanged state = no HID write" short-circuit (with its `retry` flag after failures).

## Plugin rules (Omarchy / Quickshell)

- Marketplace layout (`omacom/omarchy-plugin-marketplace`, `SUBMISSION.md`): one plugin with `manifest.json` at the repository root, a root README with install and removal instructions, a root `LICENSE`, and documented external dependencies. The plugin ID `io.github.johwconst.predatorsense` is permanent once listed: do not change it.
- No symlinks anywhere in the repository (`.git` excluded): the whole repository is the plugin folder and `omarchy plugin validate` rejects them.
- The marketplace security baseline scans `install.sh` statically: never pipe downloads into a shell, never add `NOPASSWD` sudoers rules, keep runtime state under `$XDG_RUNTIME_DIR` (not `/tmp`).
- Follow the Omarchy plugin contract: `manifest.json` (`schemaVersion: 1`), validate with `omarchy plugin validate .`.
- Reuse the `qs.Ui` kit (`Panel`, `BarIconButton`, `KeyboardPanel`, `ButtonGroup`, `Dropdown`, `Toggle`, `PanelSlider`, `TextField`, `Button`). Read the built-in plugins in `/usr/share/omarchy/shell/plugins/` (e.g. `panels/power/Panel.qml`) for patterns. **Never edit `/usr/share/omarchy/`.**
- Every `Text` showing dynamic data uses `textFormat: Text.PlainText` (the shell is unsandboxed).
- Animations: `Style.duration(ms)`, ease-out, under 300 ms.
- Strings live in `i18n/en.json` and `i18n/pt-BR.json` with identical keys; read them with `Commons.Translations`. Add a key to both files.
- `Dropdown` writes its own `value` on selection; use the local `Choice` wrapper in `Panel.qml`, which resyncs from the real state.
- `Item` already has a `state` property: do not name plugin properties `state` (the parsed JSON is `info`).
- The widget root must set `implicitWidth`/`implicitHeight` from the button or it renders with zero size.
- Pure logic (argument building, parsing, defaults) goes in `Model.js` (`.pragma library`), not in QML.

### CLI ↔ plugin contract

The plugin only uses CLI names (`keyboard`, `static`, `ltr`, `eco`…), never Python enum names. If you change `cmd_state` output or any command's arguments in `cli.py`, update `Model.js`/`Panel.qml` in the same change. Errors are reported on stderr as `predator: <message>` with a non-zero exit; the plugin shows the last stderr line.

## Language conventions

- **Docs** (`README.md`, `AGENTS.md`, `ROADMAP.md`): English.
- **Code comments, CLI messages and `PROTOCOLO.md`**: Brazilian Portuguese (pt-BR) for now, with correct accents. Keep new comments and user-facing CLI messages consistent with the file you are editing.
- Identifiers: English.

## Testing

There is no automated test suite yet. Before committing:

```bash
python3 -m py_compile predator/*.py
bash -n install.sh
omarchy plugin validate .
bin/predator state --json | jq .        # read-only, safe
```

- **Do not write to the real hardware or the user's settings while testing logic.** Run against a throwaway config and mock the HID:
  ```bash
  export XDG_CONFIG_HOME=$(mktemp -d) XDG_RUNTIME_DIR=$(mktemp -d)
  ```
  and replace `hw.set_lighting` with a function that records calls (see how the Claude hook sequence was tested: running → running → waiting → new target → failure → retry → off).
- Changing the performance mode, charge limit or LEDs changes the user's machine: ask first, and restore the previous value afterwards.
- Plugin: files under `~/.config/omarchy/plugins/` hot-reload on save (`omarchy-shell shell rescanPlugins` forces it). Read errors with `quickshell log -p /usr/share/omarchy/shell`. Open the panel with `quickshell ipc -p /usr/share/omarchy/shell call io.github.johwconst.predatorsense open`. If a layout change does not show up, `omarchy restart shell`.
- Screenshots of the panel: capture only the bar/panel region (`grim -g`), never the whole screen.

## Commits

Conventional commits with a scope, e.g. `feat(plugin): ...`, `fix(cli): ...`, `perf(hook): ...`, `docs: ...`. Keep hardware, CLI contract and plugin changes that depend on each other in the same commit.

## Out of scope

- Fans Auto/Max and PL1/PL2 (no working sysfs interface on this model; see `ROADMAP.md`).
- Windows support (removed; this repository is Linux-only).
