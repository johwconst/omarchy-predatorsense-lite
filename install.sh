#!/usr/bin/env bash
# Instala o omarchy-predatorsense-lite: comando `predator`, permissões (udev/tmpfiles), plugin do Omarchy (ou autostart
# da bandeja fora dele) e hooks do Claude Code.
# Uso: ./install.sh            instala (pergunta antes do autostart e dos hooks)
#      ./install.sh --uninstall
set -euo pipefail

DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
BIN="$HOME/.local/bin/predator"
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/predator-tray.desktop"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
PLUGIN_ID=io.github.johwconst.predatorsense
PLUGINS_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins"
PLUGIN_LINK="$PLUGINS_DIR/$PLUGIN_ID"
OLD_PLUGIN_ID=johwconst.predator   # ID antigo (plugin numa subpasta omarchy/): removido ao instalar ou desinstalar
# Instalado com `omarchy plugin add`, o repositório já é a pasta do plugin: não há link a criar.
[[ "$DIR" == "$(readlink -f "$PLUGIN_LINK" 2>/dev/null)" && ! -L "$PLUGIN_LINK" ]] && FROM_OMARCHY=1 || FROM_OMARCHY=0

remove_old_plugin() {
  [[ -L "$PLUGINS_DIR/$OLD_PLUGIN_ID" ]] || return 0
  omarchy plugin disable "$OLD_PLUGIN_ID" >/dev/null 2>&1 || true
  rm -f "$PLUGINS_DIR/$OLD_PLUGIN_ID"
}
UDEV=/etc/udev/rules.d/99-predator-rgb.rules
TMPFILES=/etc/tmpfiles.d/predator-rgb.conf
MODPROBE=/etc/modprobe.d/predator-rgb.conf
MODULES=/etc/modules-load.d/predator-rgb.conf

ask() { read -r -p "$1 [S/n] " r; [[ -z "$r" || "$r" =~ ^[sSyY] ]]; }

# Remove os hooks deste projeto (comando contém "predator claude") e os eventos que ficarem vazios.
JQ_REMOVE='
  if .hooks then
    .hooks |= (with_entries(.value |= (map(.hooks |= map(select(.command // "" | contains("predator claude") | not)))
                                       | map(select(.hooks | length > 0))))
               | with_entries(select(.value | length > 0)))
  else . end'

remove_hooks() {
  [[ -f "$CLAUDE_SETTINGS" ]] || return 0
  tmp=$(mktemp)
  jq "$JQ_REMOVE" "$CLAUDE_SETTINGS" >"$tmp" && mv "$tmp" "$CLAUDE_SETTINGS"
}

install_hooks() {
  mkdir -p "$(dirname "$CLAUDE_SETTINGS")"
  [[ -f "$CLAUDE_SETTINGS" ]] || echo '{}' >"$CLAUDE_SETTINGS"
  cp "$CLAUDE_SETTINGS" "$CLAUDE_SETTINGS.bak.predator"
  tmp=$(mktemp)
  # Acrescenta sem mexer nos hooks de outros apps (reinstalar não duplica).
  jq --arg bin "$BIN" "$JQ_REMOVE"' |
    def hook(event; matcher; state):
      .hooks[event] = ((.hooks[event] // []) + [{matcher: matcher,
        hooks: [{type: "command", command: ($bin + " claude " + state), timeout: 5}]}]);
    hook("UserPromptSubmit"; ""; "running")
    | hook("PostToolUse"; ""; "running")
    | hook("PreToolUse"; "AskUserQuestion"; "waiting")
    | hook("Notification"; "permission_prompt"; "waiting")
    | hook("Stop"; ""; "done")
    | hook("StopFailure"; "rate_limit|billing_error"; "limit")
    | hook("SessionEnd"; ""; "off")' "$CLAUDE_SETTINGS" >"$tmp" && mv "$tmp" "$CLAUDE_SETTINGS"
  echo "hooks do Claude Code instalados (backup em $CLAUDE_SETTINGS.bak.predator)."
}

if [[ "${1:-}" == "--uninstall" ]]; then
  if [[ -L "$PLUGIN_LINK" ]]; then
    omarchy plugin disable "$PLUGIN_ID" >/dev/null 2>&1 || true
    rm -f "$PLUGIN_LINK"
  fi
  remove_old_plugin
  rm -f "$BIN" "$AUTOSTART"
  remove_hooks
  sudo rm -f "$UDEV" "$TMPFILES" "$MODPROBE" "$MODULES"
  sudo udevadm control --reload
  echo "desinstalado. As permissões do sysfs voltam ao padrão no próximo boot."
  if (( FROM_OMARCHY )); then
    echo "para remover o plugin (esta pasta): omarchy plugin remove $PLUGIN_ID"
  fi
  exit 0
fi

command -v jq >/dev/null || { echo "precisa do jq (sudo pacman -S jq)"; exit 1; }
python3 -c 'import gi; gi.require_version("Gtk","4.0"); gi.require_version("Adw","1"); gi.require_version("AyatanaAppIndicator3","0.1")' 2>/dev/null \
  || echo "aviso: para a bandeja e o painel: sudo pacman -S python-gobject gtk4 libadwaita libayatana-appindicator"

# 1. comando
mkdir -p "$(dirname "$BIN")"
ln -sf "$DIR/bin/predator" "$BIN"
echo "comando: $BIN"

# 2. permissões (grupo input)
id -nG | grep -qw input || echo "aviso: seu usuário não está no grupo input (sudo usermod -aG input $USER e entre de novo)."
sudo install -m 644 "$DIR/system/99-predator-rgb.rules" "$UDEV"
sudo install -m 644 "$DIR/system/predator-rgb.conf" "$TMPFILES"
sudo udevadm control --reload
sudo udevadm trigger --subsystem-match=hidraw
echo "permissões instaladas."

# 3. módulos do kernel
# Modo de desempenho: sem o módulo facer, usa o acer_wmi do kernel com predator_v4=1 (platform_profile).
if [[ ! -e /sys/devices/platform/acer-wmi/thermal_profile ]]; then
  sudo install -m 644 "$DIR/system/predator-rgb-modprobe.conf" "$MODPROBE"
  if [[ "$(cat /sys/module/acer_wmi/parameters/predator_v4 2>/dev/null)" != "Y" ]]; then
    sudo modprobe -r acer_wmi && sudo modprobe acer_wmi || echo "aviso: não deu para recarregar o acer_wmi agora; vale após reiniciar."
  fi
fi
# Limite de carga: módulo acer-wmi-battery (AUR).
if ! modinfo acer_wmi_battery >/dev/null 2>&1 && ask "Instalar o módulo do limite de carga (AUR acer-wmi-battery-dkms)?"; then
  if command -v yay >/dev/null; then yay -S --needed acer-wmi-battery-dkms
  elif command -v paru >/dev/null; then paru -S --needed acer-wmi-battery-dkms
  else echo "instale o pacote AUR acer-wmi-battery-dkms e rode este script de novo."; fi
fi
if modinfo acer_wmi_battery >/dev/null 2>&1; then
  sudo install -m 644 "$DIR/system/predator-rgb-modules.conf" "$MODULES"
  sudo modprobe acer_wmi_battery || true
fi
sudo systemd-tmpfiles --create "$TMPFILES"

# 4. interface: plugin nativo no Omarchy (roda dentro do omarchy-shell); fora dele, a bandeja GTK
if command -v omarchy >/dev/null; then
  remove_old_plugin
  if (( FROM_OMARCHY )); then
    :   # `omarchy plugin add` já colocou o repositório na pasta de plugins
  elif [[ -e "$PLUGIN_LINK" && ! -L "$PLUGIN_LINK" ]]; then
    echo "aviso: $PLUGIN_LINK já existe (instalado com omarchy plugin add?); mantido sem alterar."
  else
    mkdir -p "$PLUGINS_DIR"
    ln -sfn "$DIR" "$PLUGIN_LINK"
  fi
  omarchy-shell shell rescanPlugins >/dev/null 2>&1 || true
  sleep 1
  omarchy plugin enable "$PLUGIN_ID" || echo "aviso: ative depois com: omarchy plugin enable $PLUGIN_ID"
  # O plugin faz o papel da bandeja (restaurar ao iniciar/acordar, modo por fonte): não rodam os dois.
  rm -f "$AUTOSTART"
  echo "plugin do Omarchy: $PLUGIN_LINK"
elif ask "Abrir a bandeja ao iniciar a sessão?"; then
  python3 -c "import sys; sys.path.insert(0, '$DIR'); from predator.tray import set_autostart; set_autostart(True)"
  echo "autostart: $AUTOSTART"
fi

# 5. hooks do Claude Code
if ask "Instalar os hooks do Claude Code (LEDs por estado)?"; then
  install_hooks
fi
