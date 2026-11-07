import unittest

from core import models


def device(name, imei, vin="", server=None):
    """The fields of a device dict (core.models.device_to_dict) that the search looks at."""
    return {"name": name, "imei": imei, "obd": {"vin": vin}, "server": server}


class DeviceSearchTest(unittest.TestCase):
    D = device("SB MB 7825", "001637861043436", "VF3ERU5V0MV993280",
               {"name": "imit2", "host": "gps-soft.de", "port": 5027})

    def test_empty_text_matches_everything(self):
        self.assertTrue(models.device_matches(self.D, ""))
        self.assertTrue(models.device_matches(self.D, "   "))

    def test_fields_and_case(self):
        for text in ("sb mb 7825", "MB", "61043", "vf3eru", "IMIT2", "gps-soft"):
            self.assertTrue(models.device_matches(self.D, text), text)

    def test_every_word_must_match(self):
        self.assertTrue(models.device_matches(self.D, "mb 78"))
        self.assertFalse(models.device_matches(self.D, "mb 99999"))
        self.assertFalse(models.device_matches(self.D, "SB AC"))

    def test_device_without_server_or_vin(self):
        d = device("Test car", "000000000000000")
        self.assertTrue(models.device_matches(d, "test"))
        self.assertFalse(models.device_matches(d, "imit2"))


if __name__ == "__main__":
    unittest.main()
