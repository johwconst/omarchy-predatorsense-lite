"""Consumo total pelo uevent da bateria (hw.parse_battery_power; Model.js segue a mesma regra)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from predator.hw import parse_battery_power  # noqa: E402

BASE = "POWER_SUPPLY_NAME=BAT1\nPOWER_SUPPLY_VOLTAGE_NOW=16000000\n"


class BatteryPowerTest(unittest.TestCase):
    def test_discharging_current_times_voltage(self):
        text = BASE + "POWER_SUPPLY_STATUS=Discharging\nPOWER_SUPPLY_CURRENT_NOW=1250000\n"
        self.assertEqual(parse_battery_power(text), 20.0)

    def test_power_now_wins_and_sign_ignored(self):
        text = BASE + "POWER_SUPPLY_STATUS=Discharging\nPOWER_SUPPLY_POWER_NOW=-18340000\nPOWER_SUPPLY_CURRENT_NOW=1\n"
        self.assertEqual(parse_battery_power(text), 18.3)

    def test_not_discharging_is_none(self):
        for status in ("Full", "Charging", "Not charging"):
            text = BASE + f"POWER_SUPPLY_STATUS={status}\nPOWER_SUPPLY_CURRENT_NOW=1250000\n"
            self.assertIsNone(parse_battery_power(text))

    def test_zero_or_missing_is_none(self):
        self.assertIsNone(parse_battery_power(BASE + "POWER_SUPPLY_STATUS=Discharging\nPOWER_SUPPLY_CURRENT_NOW=0\n"))
        self.assertIsNone(parse_battery_power("POWER_SUPPLY_STATUS=Discharging\n"))
        self.assertIsNone(parse_battery_power(""))


if __name__ == "__main__":
    unittest.main()
