import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "Model.js" as Model

// Ícone na barra + painel do Predator. Toda leitura e gravação passa pela CLI `predator`
// (processos curtos); com o painel fechado não há nenhuma leitura de hardware.
Panel {
  id: root
  moduleName: "johwconst.predator"
  ipcTarget: "johwconst.predator"

  readonly property string predator: Quickshell.env("HOME") + "/.local/bin/predator"
  readonly property color fg: root.bar ? root.bar.foreground : Commons.Color.foreground
  readonly property string fontFamily: root.bar ? root.bar.fontFamily : Style.font.family

  property var info: null
  property bool stateFailed: false
  property string tab: "general"
  property string message: ""
  property bool messageIsError: false

  // Editor de iluminação (valores locais; só recarregam ao trocar de alvo).
  property string lTarget: "keyboard"
  property var light: Model.lightingFor(null, "keyboard")
  property bool lightLoaded: false

  // Editor dos LEDs do Claude.
  property string cTarget: "keyboard"
  property string cState: "running"
  property var claudeLight: Model.claudeLightFor(null, "keyboard", "running")
  property string cSound: "off"
  property string cSoundPath: ""
  property bool claudeLoaded: false

  Commons.Translations { id: strings; directory: Qt.resolvedUrl("i18n") }
  function t(key) { return strings.t(key) }

  readonly property var status: info ? info.status : ({})

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  function modeOptions(withNone) {
    var list = withNone ? [{ value: "none", label: t("autoNone") }] : []
    var modes = info ? info.modes : []
    for (var i = 0; i < modes.length; i++) list.push({ value: modes[i], label: t("mode." + modes[i]) })
    return list
  }

  function effectOptions(target) {
    var list = Model.effectsFor(info, target)
    return list.map(function(e) { return { value: e, label: t("effect." + e) } })
  }

  function prefixed(prefix, values) {
    return values.map(function(v) { return { value: v, label: t(prefix + v) } })
  }

  function sourceText() {
    if (status.on_ac === null || status.on_ac === undefined) return t("unknownSource")
    return status.on_ac ? t("onAc") : t("onBattery")
  }

  function flash(text, isError) {
    message = text
    messageIsError = isError
    messageTimer.restart()
  }

  // ------------------------------------------------------------------ leitura

  function refresh() {
    if (!stateProc.running) stateProc.running = true
  }

  onOpenedChanged: if (opened) refresh()

  Timer { interval: 3000; running: root.opened; repeat: true; onTriggered: root.refresh() }
  Timer { id: messageTimer; interval: 3500; onTriggered: root.message = "" }

  Process {
    id: stateProc
    command: [root.predator, "state", "--json"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var parsed = Model.parseState(text)
        root.stateFailed = parsed === null
        if (parsed === null) return
        root.info = parsed
        if (!root.lightLoaded) { root.loadLighting(); root.lightLoaded = true }
        if (!root.claudeLoaded) { root.loadClaude(); root.claudeLoaded = true }
      }
    }
  }

  // ------------------------------------------------------------------ gravação

  // Ações em fila (uma por vez, na ordem); ao esvaziar, relê o estado.
  property var queue: []

  function action(args, okText) {
    queue = queue.concat([{ args: args, ok: okText || "" }])
    pump()
  }

  function pump() {
    if (actionProc.running || queue.length === 0) return
    actionProc.job = queue[0]
    queue = queue.slice(1)
    actionProc.command = [root.predator].concat(actionProc.job.args)
    actionProc.running = true
  }

  Process {
    id: actionProc
    property var job: null
    stderr: StdioCollector { id: actionErr; waitForEnd: true }
    onExited: function(exitCode) {
      var job = actionProc.job
      Qt.callLater(function() {
        if (exitCode !== 0) root.flash(Model.errorText(actionErr.text), true)
        else if (job && job.ok) root.flash(job.ok, false)
        root.pump()
        if (!actionProc.running) root.refresh()
      })
    }
  }

  // Pré-visualização enquanto mexe: no máximo uma a cada 120 ms, e só a mais recente.
  property var pendingPreview: null

  function preview(args) {
    pendingPreview = args
    if (!previewTimer.running) previewTimer.start()
  }

  function runPreview() {
    if (!pendingPreview || previewProc.running) return
    previewProc.command = [root.predator].concat(pendingPreview)
    pendingPreview = null
    previewProc.running = true
  }

  Timer { id: previewTimer; interval: 120; onTriggered: root.runPreview() }

  Process {
    id: previewProc
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: if (text.trim() !== "") root.flash(Model.errorText(text), true)
    }
    onExited: if (root.pendingPreview) previewTimer.start()
  }

  // ------------------------------------------------------------------ iluminação

  function loadLighting() { light = Model.lightingFor(info, lTarget) }

  function setLight(key, value) {
    var next = Object.assign({}, light)
    next[key] = value
    light = next
    if (lTarget === "keyboard" && light.effect === "static" && Model.zoneMaskFromList(light.zones) === 0) {
      flash(t("selectZone"), true)
      return
    }
    preview(Model.lightArgs(lTarget, light))
  }

  function toggleZone(index) {
    var zones = light.zones.slice()
    zones[index] = !zones[index]
    setLight("zones", zones)
  }

  onLTargetChanged: loadLighting()

  // ------------------------------------------------------------------ Claude

  function loadClaude() {
    claudeLight = Model.claudeLightFor(info, cTarget, cState)
    cSound = Model.soundChoice(info, cState)
    cSoundPath = Model.soundPath(info, cState)
  }

  function setClaude(key, value) {
    var next = Object.assign({}, claudeLight)
    next[key] = value
    claudeLight = next
    preview(Model.claudeLightArgs(cTarget, cState, claudeLight))
  }

  function setSound(choice) {
    cSound = choice
    if (choice === "off") action(["claude-sound", cState, "off"])
    else if (choice === "default") action(["claude-sound", cState, "default", "--play"])
    // "file": espera o caminho no campo de texto.
  }

  onCTargetChanged: loadClaude()
  onCStateChanged: loadClaude()

  // ------------------------------------------------------------------ barra

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "󰌌"
    tooltipText: ""
    onPressed: function(b) { root.toggle() }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: panel.loadedContent
    contentWidth: panel.fittedContentWidth(Style.space(420))
    contentHeight: panel.fittedContentHeight(panel.loadedContentHeight)

    content: PanelKeyCatcher {
      anchors.fill: parent
      implicitHeight: column.implicitHeight
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      Column {
        id: column
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        spacing: Style.space(12)

        ButtonGroup {
          width: parent.width
          options: root.prefixed("tab.", ["general", "lighting", "claude"])
          value: root.tab
          foreground: root.fg
          fontFamily: root.fontFamily
          fontSize: Style.font.bodySmall
          onChanged: function(v) { root.tab = v }
        }

        Label {
          visible: root.info === null
          text: root.stateFailed ? root.t("unavailable") : root.t("loading")
          opacity: 0.6
        }

        // ---------------------------------------------------------- Geral
        Column {
          visible: root.tab === "general" && root.info !== null
          width: parent.width
          spacing: Style.space(10)

          PanelSectionHeader { text: root.t("monitor"); foreground: root.fg; fontFamily: root.fontFamily }
          InfoPair {
            label: root.t("cpu")
            value: Model.reading(root.status.cpu_temp, " °C") + " · " + Model.reading(root.status.cpu_fan_rpm, " RPM")
          }
          InfoPair {
            label: root.t("gpu")
            value: Model.reading(root.status.gpu_temp, " °C") + " · " + Model.reading(root.status.gpu_fan_rpm, " RPM")
                   + " · " + Model.reading(root.status.gpu_power_w, " W")
          }
          InfoPair {
            label: root.t("battery")
            value: Model.reading(root.status.battery_percent, "%") + " · " + root.sourceText()
          }
          InfoPair {
            label: root.t("mode")
            value: (root.status.mode ? root.t("mode." + root.status.mode) : "–")
                   + (root.status.power_profile ? " · " + root.status.power_profile : "")
          }

          PanelSeparator { foreground: root.fg }
          PanelSectionHeader { text: root.t("performance"); foreground: root.fg; fontFamily: root.fontFamily }

          ButtonGroup {
            width: parent.width
            options: root.modeOptions(false)
            value: root.status.mode || ""
            foreground: root.fg
            fontFamily: root.fontFamily
            fontSize: Style.font.bodySmall
            onChanged: function(v) { if (v !== root.status.mode) root.action(["mode", v]) }
          }

          Row {
            width: parent.width
            spacing: Style.space(10)
            Choice {
              width: (parent.width - parent.spacing) / 2
              label: root.t("autoAc")
              options: root.modeOptions(true)
              current: root.info && root.info.autoMode.ac ? root.info.autoMode.ac : "none"
              onChanged: function(v) { root.action(["auto-mode", "--ac", v]) }
            }
            Choice {
              width: (parent.width - parent.spacing) / 2
              label: root.t("autoBattery")
              options: root.modeOptions(true)
              current: root.info && root.info.autoMode.battery ? root.info.autoMode.battery : "none"
              onChanged: function(v) { root.action(["auto-mode", "--battery", v]) }
            }
          }
          Label { text: root.t("autoHint"); opacity: 0.6; font.pixelSize: Style.font.caption }

          Toggle {
            width: parent.width
            visible: root.status.keyboard_timeout !== null && root.status.keyboard_timeout !== undefined
            label: root.t("keyboardTimeout")
            checked: root.status.keyboard_timeout === true
            foreground: root.fg
            fontFamily: root.fontFamily
            onClicked: root.action(["timeout", checked ? "off" : "on"])
          }
          Toggle {
            width: parent.width
            visible: root.status.charge_limited !== null && root.status.charge_limited !== undefined
            label: root.t("chargeLimit")
            description: root.t("chargeLimitHint")
            checked: root.status.charge_limited === true
            foreground: root.fg
            fontFamily: root.fontFamily
            onClicked: root.action(["charge-limit", checked ? "off" : "on"])
          }
        }

        // ---------------------------------------------------------- Iluminação
        Column {
          visible: root.tab === "lighting" && root.info !== null
          width: parent.width
          spacing: Style.space(10)

          ButtonGroup {
            width: parent.width
            options: root.prefixed("target.", Model.TARGETS)
            value: root.lTarget
            foreground: root.fg
            fontFamily: root.fontFamily
            fontSize: Style.font.bodySmall
            onChanged: function(v) { root.lTarget = v }
          }

          Choice {
            width: parent.width
            label: root.t("effect")
            options: root.effectOptions(root.lTarget)
            current: root.light.effect
            onChanged: function(v) { root.setLight("effect", v) }
          }

          ColorPicker {
            visible: root.light.effect === "static"
            hex: root.light.color
            onPicked: function(h) { root.setLight("color", h) }
          }

          Column {
            visible: root.light.effect === "static" && root.lTarget === "keyboard"
            width: parent.width
            spacing: Style.space(6)
            Label { text: root.t("zones"); opacity: 0.6 }
            Row {
              spacing: Style.space(6)
              Repeater {
                model: Model.ZONES
                Button {
                  required property int index
                  text: String(index + 1)
                  bordered: true
                  active: root.light.zones[index] === true
                  foreground: root.fg
                  fontFamily: root.fontFamily
                  onClicked: root.toggleZone(index)
                }
              }
            }
          }

          SliderRow {
            label: root.t("brightness")
            maximum: 100
            value: root.light.brightness
            onMoved: function(v) { root.setLight("brightness", v) }
          }
          SliderRow {
            visible: root.light.effect !== "static"
            label: root.t("speed")
            maximum: 9
            value: root.light.speed
            onMoved: function(v) { root.setLight("speed", v) }
          }
          Choice {
            visible: root.light.effect !== "static" && root.lTarget === "keyboard"
            width: parent.width
            label: root.t("direction")
            options: root.prefixed("direction.", Model.DIRECTIONS)
            current: root.light.direction
            onChanged: function(v) { root.setLight("direction", v) }
          }

          Row {
            spacing: Style.space(8)
            Button {
              text: root.t("save")
              bordered: true
              foreground: root.fg
              fontFamily: root.fontFamily
              onClicked: root.action(Model.lightArgs(root.lTarget, root.light).concat(["--save"]), root.t("saved"))
            }
            Button {
              text: root.t("off")
              bordered: true
              foreground: root.fg
              fontFamily: root.fontFamily
              onClicked: root.action(["off"])
            }
            Button {
              text: root.t("restore")
              bordered: true
              foreground: root.fg
              fontFamily: root.fontFamily
              onClicked: root.action(["restore"])
            }
          }
        }

        // ---------------------------------------------------------- Claude Code
        Column {
          visible: root.tab === "claude" && root.info !== null
          width: parent.width
          spacing: Style.space(10)

          PanelSectionHeader { text: root.t("claudeLeds"); foreground: root.fg; fontFamily: root.fontFamily }
          Repeater {
            model: Model.TARGETS
            Toggle {
              required property string modelData
              width: parent.width
              label: root.t("target." + modelData)
              checked: root.info !== null && root.info.claude.targets.indexOf(modelData) >= 0
              foreground: root.fg
              fontFamily: root.fontFamily
              onClicked: root.action(["claude-target", modelData, checked ? "off" : "on"])
            }
          }

          PanelSeparator { foreground: root.fg }

          Row {
            width: parent.width
            spacing: Style.space(10)
            Choice {
              width: (parent.width - parent.spacing) / 2
              label: root.t("configure")
              options: root.prefixed("target.", Model.TARGETS)
              current: root.cTarget
              onChanged: function(v) { root.cTarget = v }
            }
            Choice {
              width: (parent.width - parent.spacing) / 2
              label: root.t("state")
              options: root.prefixed("state.", Model.CLAUDE_STATES)
              current: root.cState
              onChanged: function(v) { root.cState = v }
            }
          }

          Choice {
            width: parent.width
            label: root.t("effect")
            options: root.effectOptions(root.cTarget)
            current: root.claudeLight.effect
            onChanged: function(v) { root.setClaude("effect", v) }
          }
          ColorPicker {
            visible: root.claudeLight.effect === "static"
            hex: root.claudeLight.color
            onPicked: function(h) { root.setClaude("color", h) }
          }
          SliderRow {
            visible: root.claudeLight.effect !== "static"
            label: root.t("speed")
            maximum: 9
            value: root.claudeLight.speed
            onMoved: function(v) { root.setClaude("speed", v) }
          }

          Row {
            width: parent.width
            spacing: Style.space(8)
            Choice {
              width: parent.width - playButton.width - parent.spacing
              label: root.t("sound")
              options: root.prefixed("sound.", ["off", "default", "file"])
              current: root.cSound
              onChanged: function(v) { root.setSound(v) }
            }
            Button {
              id: playButton
              anchors.bottom: parent.bottom
              text: root.t("play")
              bordered: true
              enabled: root.cSound !== "off"
              foreground: root.fg
              fontFamily: root.fontFamily
              onClicked: root.action(["claude-sound", root.cState, "--play"])
            }
          }
          TextField {
            visible: root.cSound === "file"
            width: parent.width
            foreground: root.fg
            placeholderText: root.t("soundPath")
            text: root.cSoundPath
            onAccepted: {
              root.cSoundPath = text.trim()
              if (root.cSoundPath !== "") root.action(["claude-sound", root.cState, root.cSoundPath, "--play"])
            }
          }
        }

        Label {
          visible: root.message !== ""
          width: parent.width
          text: root.message
          color: root.messageIsError ? Commons.Color.urgent : root.fg
          opacity: root.messageIsError ? 1 : 0.7
          wrapMode: Text.Wrap
        }
      }
    }
  }

  // ------------------------------------------------------------------ componentes

  component Label: Text {
    textFormat: Text.PlainText
    color: root.fg
    font.family: root.fontFamily
    font.pixelSize: Style.font.bodySmall
  }

  component InfoPair: Item {
    property string label: ""
    property string value: ""
    width: parent.width
    implicitHeight: Math.max(pairLabel.implicitHeight, pairValue.implicitHeight)
    Label { id: pairLabel; text: parent.label; opacity: 0.6; anchors.left: parent.left }
    Label { id: pairValue; text: parent.value; anchors.right: parent.right }
  }

  // Dropdown que segue `current`: o Dropdown grava o próprio value ao escolher,
  // então a cada leitura de estado ele é realinhado com o valor real.
  component Choice: Dropdown {
    id: choice
    property string current: ""
    foreground: root.fg
    fontFamily: root.fontFamily
    onCurrentChanged: value = current
    Component.onCompleted: value = current
    Connections {
      target: root
      function onInfoChanged() { choice.value = choice.current }
    }
  }

  component SliderRow: Column {
    id: sliderRow
    property string label: ""
    property real maximum: 100
    property real value: 0
    signal moved(real value)
    width: parent.width
    spacing: Style.space(4)
    Item {
      width: parent.width
      implicitHeight: sliderLabel.implicitHeight
      Label { id: sliderLabel; text: sliderRow.label; opacity: 0.6 }
      Label { text: String(Math.round(slider.liveValue)); anchors.right: parent.right }
    }
    PanelSlider {
      id: slider
      bar: root.bar
      width: parent.width
      minimum: 0
      maximum: sliderRow.maximum
      step: 1
      integer: true
      value: sliderRow.value
      onMoved: function(v) { sliderRow.moved(Math.round(v)) }
    }
  }

  component ColorPicker: Column {
    id: picker
    property string hex: "FFFFFF"
    signal picked(string hex)
    width: parent.width
    spacing: Style.space(6)
    Label { text: root.t("color"); opacity: 0.6 }
    Row {
      spacing: Style.space(6)
      Repeater {
        model: Model.SWATCHES
        Rectangle {
          required property string modelData
          width: Style.space(22)
          height: width
          radius: width / 2
          color: "#" + modelData
          border.width: picker.hex === modelData ? Style.space(2) : 1
          border.color: picker.hex === modelData ? Commons.Color.accent : Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.3)
          MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: picker.picked(modelData)
          }
        }
      }
      Rectangle {
        width: Style.space(22)
        height: width
        radius: Style.space(4)
        color: "#" + picker.hex
        border.width: 1
        border.color: Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.3)
      }
    }
    TextField {
      width: Style.space(120)
      foreground: root.fg
      text: "#" + picker.hex
      onAccepted: {
        var h = Model.normalizeHex(text)
        if (h !== "") picker.picked(h)
        else text = "#" + picker.hex
      }
    }
  }
}
