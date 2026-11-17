"""Testes sem hardware: hook do Claude Code e leitura/gravação do settings.json.

Rodar: python3 -m unittest discover -s tests
O HID e o modo de desempenho são trocados por funções que só registram as chamadas.
"""
import json
import os
import sys
import tempfile
import unittest

# Antes de importar o pacote: settings.py e claude.py fixam os caminhos ao carregar.
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from predator import claude, hw, settings  # noqa: E402
from predator.hw import HardwareError, LightEffect, LightSetting, LightTarget  # noqa: E402
from predator.settings import Settings  # noqa: E402


class Fixture(unittest.TestCase):
    def setUp(self):
        for path in (settings.FILE, claude.STATE_FILE):
            if os.path.exists(path):
                os.remove(path)
        self.calls = []
        self.fail_targets = set()
        self._orig = hw.set_lighting, hw.get_mode, claude.play_sound
        hw.set_lighting = self.fake_set_lighting
        hw.get_mode = lambda: None
        claude.play_sound = lambda *a, **k: None

    def tearDown(self):
        hw.set_lighting, hw.get_mode, claude.play_sound = self._orig

    def fake_set_lighting(self, s: LightSetting):
        if s.Target in self.fail_targets:
            raise HardwareError("simulado")
        self.calls.append((LightTarget(s.Target).name, s.R, s.G, s.B))

    def state(self):
        with open(claude.STATE_FILE) as f:
            return json.load(f)


class ClaudeHookTest(Fixture):
    def setUp(self):
        super().setUp()
        with Settings.editing() as s:
            s.ClaudeTargets = [LightTarget.Keyboard, LightTarget.Logo]
            s.Lighting = [LightSetting(LightTarget.Logo, LightEffect.Static, 1, 2, 3)]

    def test_sequence(self):
        claude.set_state("running")
        self.assertEqual({c[0] for c in self.calls}, {"Keyboard", "Logo"})

        self.calls.clear()
        claude.set_state("running")  # repetido: nenhuma gravação no HID
        self.assertEqual(self.calls, [])

        claude.set_state("waiting")
        self.assertEqual(len(self.calls), 2)

        self.calls.clear()
        self.fail_targets = {LightTarget.Logo}
        with self.assertRaises(HardwareError):
            claude.set_state("done")
        self.assertTrue(self.state()["retry"])

        self.calls.clear()
        with self.assertRaises(HardwareError):  # o logo ainda falha
            claude.set_state("done")  # mesmo estado, mas com retry: grava de novo
        self.assertEqual(len(self.calls), 1)  # o teclado foi regravado

        self.fail_targets = set()
        self.calls.clear()
        claude.set_state("off")  # restaura o padrão salvo do logo
        self.assertIn(("Logo", 1, 2, 3), self.calls)
        self.assertEqual(self.state(), {"state": "off", "lit": []})

    def test_failed_restore_stays_lit(self):
        claude.set_state("running")
        with Settings.editing() as s:
            s.ClaudeTargets = [LightTarget.Keyboard]  # usuário desmarca o logo
        self.fail_targets = {LightTarget.Logo}
        with self.assertRaises(HardwareError):
            claude.refresh()
        self.assertIn("Logo", self.state()["lit"])  # não pode ser esquecido

        self.fail_targets = set()
        self.calls.clear()
        claude.set_state("running")  # retry: agora restaura o logo
        self.assertIn(("Logo", 1, 2, 3), self.calls)
        self.assertNotIn("Logo", self.state()["lit"])


class SettingsTest(Fixture):
    def test_round_trip(self):
        with Settings.editing() as s:
            s.BarTemps = True
            s.ClaudeSounds = {"done": ""}
        loaded = Settings.load()
        self.assertTrue(loaded.BarTemps)
        self.assertEqual(loaded.ClaudeSounds, {"done": ""})

    def test_restore_auto_respects_option(self):
        from types import SimpleNamespace
        from predator import cli
        calls = []
        orig = cli.restore_saved
        cli.restore_saved = lambda: calls.append("restore")
        try:
            self.assertTrue(Settings.load().RestoreOnStart)  # padrão: ligado
            cli.cmd_restore(SimpleNamespace(auto=True))
            Settings.save_restore_on_start(False)
            cli.cmd_restore(SimpleNamespace(auto=True))   # login/acordar: pula
            cli.cmd_restore(SimpleNamespace(auto=False))  # botão "Restaurar": sempre roda
        finally:
            cli.restore_saved = orig
        self.assertEqual(calls, ["restore", "restore"])

    def test_invalid_file_is_not_overwritten(self):
        os.makedirs(settings.FOLDER, exist_ok=True)
        bad = '{"Lighting": [{"Target": "Keyboard", "Effect": "Rainbow", "R": 1, "G": 2, "B": 3}]}'
        with open(settings.FILE, "w") as f:
            f.write(bad)
        self.assertEqual(Settings.load().Lighting, [])  # leitura: padrões
        with self.assertRaises(ValueError):
            Settings.save_bar_temps(True)  # gravação: recusa
        with open(settings.FILE) as f:
            self.assertEqual(f.read(), bad)


if __name__ == "__main__":
    unittest.main()
