import unittest

from core import imei
from core.teltonika import AvlRecord, decode_avl_packet, encode_avl_packet

# Codec 8 example 1 from the Teltonika wiki (CRC 0xC7CF).
WIKI_PACKET = bytes.fromhex(
    "000000000000003608010000016B40D8EA30010000000000000000000000000000000105021503010101425E0F01F10000"
    "601A014E0000000000000000010000C7CF"
)


class TeltonikaTest(unittest.TestCase):
    def test_encoder_matches_wiki_example(self):
        record = AvlRecord(timestamp_ms=1560161086000, lat=0, lon=0, priority=1, satellites=0, event_io_id=1,
                           io_1b={0x15: 3, 0x01: 1}, io_2b={0x42: 0x5E0F}, io_4b={0xF1: 0x601A}, io_8b={0x4E: 0})
        self.assertEqual(encode_avl_packet([record]), WIKI_PACKET)

    def test_round_trip(self):
        records = [AvlRecord(timestamp_ms=1_700_000_000_000 + i, lat=49.2 + i / 1000, lon=6.9, altitude=230,
                             angle=90, speed_kmh=50, io_1b={239: 1}) for i in range(3)]
        decoded = decode_avl_packet(encode_avl_packet(records))
        self.assertEqual(len(decoded), 3)
        self.assertAlmostEqual(decoded[2]["lat"], 49.202, places=6)
        self.assertEqual(decoded[0]["io"][239], 1)


class ImeiTest(unittest.TestCase):
    def test_luhn(self):
        self.assertTrue(imei.is_valid_imei("490154203237518"))
        self.assertFalse(imei.is_valid_imei("490154203237519"))

    def test_generated_imei_is_valid_unique_test_imei(self):
        taken = {imei.generate_imei() for _ in range(20)}
        new = imei.generate_imei(taken)
        self.assertTrue(imei.is_valid_imei(new))
        self.assertTrue(new.startswith("00"))
        self.assertNotIn(new, taken)


if __name__ == "__main__":
    unittest.main()
