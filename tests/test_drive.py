import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "humble_integrator"))
import drive  # noqa: E402


class DriveScriptTest(unittest.TestCase):
    def test_serial_is_split_into_groups(self):
        self.assertEqual(drive.split_serial("abcd-EFGH-ijkl-MNOP"), ["abcd", "EFGH", "ijkl", "MNOP"])
        self.assertEqual(drive.split_serial(" ABCD EFGH "), ["ABCD", "EFGH"])
        self.assertEqual(drive.split_serial("NODASHES"), ["NODASHES"])

    def test_values_are_quoted_for_autohotkey(self):
        self.assertEqual(drive.ahk_string('a "b" 100% `x'), '"a ""b"" 100`% ``x"')

    def test_script_has_every_placeholder_filled(self):
        script = drive.installaware_script("ABCD-EFGH", name="N", company="C", accept_eula=True)
        self.assertIsNone(re.search(r"%[A-Z_]{4,}%", script), "unfilled placeholder")
        self.assertIn('serialParts := StrSplit("ABCD|EFGH", "|")', script)
        self.assertIn("acceptEula := 1", script)

    def test_eula_is_not_accepted_by_default(self):
        self.assertIn("acceptEula := 0", drive.installaware_script("X"))

    def test_driver_never_clicks_on_message_boxes(self):
        # The 7-Zip SFX progress window shares the message-box class; clicking
        # its first button would cancel the extraction.
        script = drive.installaware_script("X")
        self.assertNotIn("ControlClick, Button1, ahk_class", script)


if __name__ == "__main__":
    unittest.main()
