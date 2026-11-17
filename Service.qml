import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Services.UPower

// Serviço de fundo do Predator dentro do omarchy-shell (substitui a bandeja GTK no Omarchy):
// reaplica o padrão salvo ao iniciar e ao acordar (se a opção estiver ligada: `restore --auto`), e o modo de cada fonte ao ligar/tirar o carregador.
// Não faz polling: tudo vem de eventos (UPower e o sinal PrepareForSleep do logind).
Item {
  id: root

  property var shell: null
  readonly property string predator: Quickshell.env("HOME") + "/.local/bin/predator"
  property int restoreAttempt: 0
  property string restoreError: ""

  function notify(text) {
    Quickshell.execDetached(["notify-send", "-a", "Predator", "Predator", text])
  }

  // Logo após o login ou a suspensão o HID e o firmware podem ainda não responder: tenta 3 vezes.
  function scheduleRestore() {
    restoreAttempt = 1
    restoreTimer.interval = 2000
    restoreTimer.restart()
  }

  Component.onCompleted: scheduleRestore()

  Timer {
    id: restoreTimer
    repeat: false
    onTriggered: if (!restoreProc.running) restoreProc.running = true
  }

  Process {
    id: restoreProc
    command: [root.predator, "restore", "--auto"]
    stderr: StdioCollector { waitForEnd: true; onStreamFinished: root.restoreError = text.trim() }
    onExited: function(exitCode) {
      if (exitCode === 0) return
      if (root.restoreAttempt < 3) {
        root.restoreAttempt++
        restoreTimer.interval = 3000
        restoreTimer.restart()
      } else {
        // O stderr termina de chegar junto com a saída; espera o próximo ciclo para lê-lo.
        Qt.callLater(function() { root.notify(root.restoreError || "falha ao restaurar") })
      }
    }
  }

  // Tomada ou bateria: aplica o modo configurado para a nova fonte.
  Connections {
    target: UPower
    function onOnBatteryChanged() {
      if (!powerProc.running) powerProc.running = true
    }
  }

  Process {
    id: powerProc
    command: [root.predator, "auto-mode", "--apply"]
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: if (text.trim() !== "") root.notify(text.trim())
    }
  }

  // Quickshell não tem D-Bus genérico em QML: o gdbus (glib2) escuta o logind e
  // imprime uma linha por sinal. "PrepareForSleep (false,)" = acabou de acordar.
  Process {
    id: sleepMonitor
    running: true
    command: ["gdbus", "monitor", "--system", "--dest", "org.freedesktop.login1",
              "--object-path", "/org/freedesktop/login1"]
    stdout: SplitParser {
      onRead: function(line) {
        if (line.indexOf("PrepareForSleep (false") >= 0) root.scheduleRestore()
      }
    }
    // Se o gdbus cair (ex.: reinício do dbus), volta a escutar.
    onExited: monitorRetry.restart()
  }

  Timer {
    id: monitorRetry
    interval: 5000
    repeat: false
    onTriggered: sleepMonitor.running = true
  }
}
