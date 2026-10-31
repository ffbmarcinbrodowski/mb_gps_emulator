import unittest

from core import imei
from core.teltonika import CODEC_8_EXT, AvlRecord, decode_avl_packet, encode_avl_packet

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


# Codec 8 Extended example from the Teltonika wiki (CRC 0x2994).
WIKI_8E_PACKET = bytes.fromhex(
    "000000000000004A8E010000016B412CEE000100000000000000000000000000000000010005000100010100010011001D00"
    "010010015E2C880002000B000000003544C87A000E000000001DD7E06A00000100002994"
)


class Codec8ExtendedTest(unittest.TestCase):
    def test_encoder_matches_wiki_example(self):
        record = AvlRecord(timestamp_ms=1560166592000, lat=0, lon=0, priority=1, satellites=0, event_io_id=1,
                           io_1b={0x01: 1}, io_2b={0x11: 0x1D}, io_4b={0x10: 0x015E2C88},
                           io_8b={0x0B: 0x3544C87A, 0x0E: 0x1DD7E06A})
        self.assertEqual(encode_avl_packet([record], CODEC_8_EXT), WIKI_8E_PACKET)

    def test_obd_values_round_trip(self):
        record = AvlRecord(timestamp_ms=1_700_000_000_000, lat=49.2, lon=6.9, event_io_id=30,
                           io_1b={30: 2, 32: -5}, io_4b={389: 123456, 402: 15000},
                           io_nx={256: b"WVWZZZ1KZ9W000123", 281: b"P0420,P0171"})
        rec = decode_avl_packet(encode_avl_packet([record], CODEC_8_EXT))[0]
        self.assertEqual(rec["codec"], CODEC_8_EXT)
        self.assertEqual(rec["event_io_id"], 30)
        self.assertEqual(rec["io"][389], 123456)                        # id above 255
        self.assertEqual(rec["io"][32], 251)                            # -5 as a signed byte
        self.assertEqual(rec["io"][256], b"WVWZZZ1KZ9W000123")          # VIN in the NX section
        self.assertEqual(rec["io"][281], b"P0420,P0171")

    def test_codec_8_rejects_extended_values(self):
        with self.assertRaises(ValueError):
            encode_avl_packet([AvlRecord(timestamp_ms=0, lat=0, lon=0, io_4b={389: 1})])
        with self.assertRaises(ValueError):
            encode_avl_packet([AvlRecord(timestamp_ms=0, lat=0, lon=0, io_nx={256: b"VIN"})])


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
