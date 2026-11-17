.pragma library

// Funções puras do painel: sem QML, para manter o Panel.qml só com interface.
// Os nomes (keyboard, static, ltr, eco…) são os mesmos da CLI `predator`.

var TARGETS = ["keyboard", "logo", "button"]
var CLAUDE_STATES = ["running", "waiting", "done", "limit"]
var DIRECTIONS = ["none", "ltr", "rtl"]
var SWATCHES = ["FF0000", "FF6000", "FFB000", "00FF40", "00C8FF", "0040FF", "8C00FF", "FF00A0", "FFFFFF"]
var ZONES = 4

function parseState(raw) {
  try {
    var d = JSON.parse(raw)
    return (d && typeof d === "object" && d.status) ? d : null
  } catch (e) {
    return null
  }
}

// "#ff0000", "ff0000" -> "FF0000"; inválido -> "".
function normalizeHex(text) {
  var t = String(text || "").trim().replace(/^#/, "").toUpperCase()
  return /^[0-9A-F]{6}$/.test(t) ? t : ""
}

function effectsFor(state, target) {
  return state && state.effects && state.effects[target] ? state.effects[target] : ["static"]
}

// Efeito salvo pode não existir para o alvo (ex.: "wave" no logo): cai no primeiro da lista.
function validEffect(state, target, effect) {
  var list = effectsFor(state, target)
  return list.indexOf(effect) >= 0 ? effect : list[0]
}

function zoneMaskFromList(list) {
  var mask = 0
  for (var i = 0; i < list.length; i++) if (list[i]) mask |= (1 << i)
  return mask
}

function zoneListFromMask(mask) {
  var list = []
  for (var i = 0; i < ZONES; i++) list.push(!mask || (mask & (1 << i)) !== 0)
  return list
}

// Configuração de iluminação para o editor (padrão salvo ou vermelho estático).
function lightingFor(state, target) {
  var saved = state && state.lighting ? state.lighting[target] : null
  var l = saved || { effect: "static", color: "FF0000", brightness: 100, speed: 5, direction: "none", zones: 0 }
  return {
    effect: validEffect(state, target, l.effect),
    color: normalizeHex(l.color) || "FF0000",
    brightness: l.brightness,
    speed: l.speed || 5,
    direction: DIRECTIONS.indexOf(l.direction) >= 0 ? l.direction : "none",
    zones: zoneListFromMask(l.zones)
  }
}

function lightArgs(target, l) {
  var args = ["light", target, "--effect", l.effect, "--color", l.color,
              "--brightness", String(Math.round(l.brightness)), "--speed", String(Math.round(l.speed))]
  if (target === "keyboard") {
    args.push("--direction", l.direction)
    if (l.effect === "static") args.push("--zones", zoneMaskFromList(l.zones).toString(16))
  }
  return args
}

function claudeLightFor(state, target, st) {
  var l = state && state.claude && state.claude.lights[target] ? state.claude.lights[target][st] : null
  l = l || { effect: "static", color: "FFFFFF", speed: 5 }
  return { effect: validEffect(state, target, l.effect), color: normalizeHex(l.color) || "FFFFFF", speed: l.speed }
}

function claudeLightArgs(target, st, l) {
  return ["claude-light", target, st, "--effect", l.effect, "--color", l.color, "--speed", String(Math.round(l.speed))]
}

// Som de um estado: "off" (ausente), "default" ("") ou o caminho.
function soundChoice(state, st) {
  var sounds = state && state.claude ? state.claude.sounds : {}
  if (!sounds || !(st in sounds)) return "off"
  return sounds[st] === "" ? "default" : "file"
}

function soundPath(state, st) {
  var sounds = state && state.claude ? state.claude.sounds : {}
  return sounds && sounds[st] ? String(sounds[st]) : ""
}

// Mensagem de erro da CLI ("predator: ...") sem o prefixo e limitada.
function errorText(raw) {
  var lines = String(raw || "").trim().split("\n")
  var last = lines[lines.length - 1] || ""
  return last.replace(/^predator[^:]*:\s*(error:\s*)?/, "").slice(0, 200)
}

function reading(value, unit) {
  return value === null || value === undefined ? "–" : String(value) + unit
}

// ------------------------------------------------------------------ temperaturas na barra

var TEMP_ALERT = 90

function parseBarTemps(raw) {
  try {
    var d = JSON.parse(raw)
    return (d && typeof d === "object" && "enabled" in d) ? d : null
  } catch (e) {
    return null
  }
}

// Conteúdo de um temp*_input do hwmon ("65000\n") em °C; inválido -> null.
function parseMilli(text) {
  var v = parseInt(String(text || "").trim(), 10)
  return isNaN(v) ? null : Math.round(v / 1000)
}

// Consumo total em W pelo uevent da bateria; só descarregando (na tomada o ACAD não informa).
// Mesma regra de hw.parse_battery_power.
function parseBatteryPower(text) {
  var d = {}
  String(text || "").split("\n").forEach(function(line) {
    var i = line.indexOf("=")
    if (i > 0) d[line.slice(0, i)] = line.slice(i + 1).trim()
  })
  if (d.POWER_SUPPLY_STATUS !== "Discharging") return null
  var uw = "POWER_SUPPLY_POWER_NOW" in d
    ? Math.abs(parseInt(d.POWER_SUPPLY_POWER_NOW, 10))
    : Math.abs(parseInt(d.POWER_SUPPLY_CURRENT_NOW, 10)) * parseInt(d.POWER_SUPPLY_VOLTAGE_NOW, 10) / 1e6
  return isNaN(uw) || uw === 0 ? null : Math.round(uw / 1e5) / 10
}

// "C 62° G 51° 18W"; omite o que não tiver leitura; nada -> "".
function barTempsText(cpu, gpu, power) {
  var parts = []
  if (cpu !== null && cpu !== undefined) parts.push("C " + cpu + "°")
  if (gpu !== null && gpu !== undefined) parts.push("G " + gpu + "°")
  if (power !== null && power !== undefined) parts.push(Math.round(power) + "W")
  return parts.join(" ")
}

function tempsHot(cpu, gpu) {
  return (cpu !== null && cpu >= TEMP_ALERT) || (gpu !== null && gpu >= TEMP_ALERT)
}
