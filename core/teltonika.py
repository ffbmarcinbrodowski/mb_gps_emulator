"""
Teltonika AVL data encoder (Codec 8 and Codec 8 Extended) - TCP variant.

Based on the Teltonika Telematics wiki ("Codec 8" / "Teltonika Data Sending Protocols").

TCP session:
    1. Device -> server : IMEI packet   [2 bytes length][IMEI as ASCII]
    2. Server -> device : 0x01 (accepted) or 0x00 (rejected)
    3. Device -> server : AVL data packet (below)
    4. Server -> device : 4 bytes = number of records accepted

AVL data packet:
    [4 bytes]  preamble        0x00000000
    [4 bytes]  data length     from codec ID to "number of data 2"
    [1 byte]   codec ID        0x08
    [1 byte]   number of data 1
    [N bytes]  AVL records
    [1 byte]   number of data 2 (same as number of data 1)
    [4 bytes]  CRC-16/IBM      computed from codec ID to number of data 2

AVL record:
    [8 bytes]  timestamp       ms since 1970-01-01 UTC
    [1 byte]   priority        0 low, 1 high, 2 panic
    [15 bytes] GPS element
        [4] longitude   int32, degrees * 10^7
        [4] latitude    int32, degrees * 10^7
        [2] altitude    meters
        [2] angle       degrees from north (0-360)
        [1] satellites
        [2] speed       km/h
    [IO element]
        [1] event IO ID (0 = periodic record)
        [1] total IO count
        [1] N1 count, then N1 x ([1] id, [1] value)
        [1] N2 count, then N2 x ([1] id, [2] value)
        [1] N4 count, then N4 x ([1] id, [4] value)
        [1] N8 count, then N8 x ([1] id, [8] value)

Codec 8 Extended (codec ID 0x8E) is the same, except:
    - event IO ID, total IO count, the N1/N2/N4/N8 counters and every IO id are 2 bytes,
    - after N8 comes an NX section for variable-length values (e.g. VIN, fault codes):
        [2] NX count, then NX x ([2] id, [2] length, [length] value)
    It is needed for IO ids above 255 (OBD OEM elements of the FMB003) and for NX values.

All integers are big-endian.
"""
import struct
from dataclasses import dataclass, field

CODEC_8 = 0x08
CODEC_8_EXT = 0x8E

# A few common Teltonika IO element IDs (understood by Traccar).
IO_IGNITION = 239       # 1 byte: 0/1
IO_MOVEMENT = 240       # 1 byte: 0/1
IO_GNSS_STATUS = 69     # 1 byte: 1 = GNSS on with fix
IO_EXTERNAL_VOLTAGE = 66  # 2 bytes: mV
IO_TOTAL_ODOMETER = 16  # 4 bytes: meters
IO_TRIP_ODOMETER = 199  # 4 bytes: meters since ignition on
IO_GSM_SIGNAL = 21      # 1 byte: 0-5
IO_PDOP = 181           # 2 bytes: PDOP * 10
IO_HDOP = 182           # 2 bytes: HDOP * 10


@dataclass
class AvlRecord:
    timestamp_ms: int
    lat: float
    lon: float
    altitude: float = 0
    angle: float = 0
    speed_kmh: float = 0
    satellites: int = 10
    priority: int = 0
    event_io_id: int = 0
    # IO values grouped by their byte size: {io_id: value}
    io_1b: dict = field(default_factory=dict)
    io_2b: dict = field(default_factory=dict)
    io_4b: dict = field(default_factory=dict)
    io_8b: dict = field(default_factory=dict)
    io_nx: dict = field(default_factory=dict)   # Codec 8 Extended only: {io_id: bytes}


def crc16_ibm(data: bytes) -> int:
    """CRC-16/IBM (a.k.a. CRC-16/ARC): poly 0xA001 (reflected 0x8005), init 0."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def encode_imei(imei: str) -> bytes:
    """First packet of a TCP session: 2-byte length + IMEI digits."""
    raw = imei.encode("ascii")
    return struct.pack(">H", len(raw)) + raw


def _int(value, size):
    """Pack an integer of `size` bytes; negative values are sent as two's complement."""
    return (value & ((1 << 8 * size) - 1)).to_bytes(size, "big")


def encode_record(r: AvlRecord, codec=CODEC_8) -> bytes:
    """Encode a single AVL record (timestamp + priority + GPS + IO) for Codec 8 or 8 Extended."""
    out = struct.pack(">QB", r.timestamp_ms, r.priority)

    # GPS element (15 bytes)
    out += struct.pack(
        ">iihHBH",
        round(r.lon * 10_000_000),
        round(r.lat * 10_000_000),
        max(-32768, min(32767, round(r.altitude))),
        round(r.angle) % 360,
        r.satellites,
        max(0, min(65535, round(r.speed_kmh))),
    )

    # IO element. Codec 8: ids and counters are 1 byte; Codec 8 Extended: 2 bytes + NX section.
    ext = codec == CODEC_8_EXT
    if not ext and (r.io_nx or max([*r.io_1b, *r.io_2b, *r.io_4b, *r.io_8b, r.event_io_id], default=0) > 255):
        raise ValueError("IO ids above 255 and variable-length IO need Codec 8 Extended")
    n = 2 if ext else 1
    groups = ((r.io_1b, 1), (r.io_2b, 2), (r.io_4b, 4), (r.io_8b, 8))
    total = sum(len(g) for g, _ in groups) + len(r.io_nx)
    out += _int(r.event_io_id, n) + _int(total, n)
    for values, size in groups:
        out += _int(len(values), n)
        for io_id, value in values.items():
            out += _int(io_id, n) + _int(value, size)
    if ext:
        out += _int(len(r.io_nx), 2)
        for io_id, value in r.io_nx.items():
            out += _int(io_id, 2) + _int(len(value), 2) + value
    return out


def encode_avl_packet(records: list[AvlRecord], codec=CODEC_8) -> bytes:
    """Build a complete Codec 8 / 8 Extended TCP packet containing `records` (1-255)."""
    if not 1 <= len(records) <= 255:
        raise ValueError("A packet must contain between 1 and 255 records")

    data = struct.pack(">BB", codec, len(records))
    data += b"".join(encode_record(r, codec) for r in records)
    data += struct.pack(">B", len(records))

    return (
        struct.pack(">II", 0, len(data))     # preamble + data field length
        + data
        + struct.pack(">I", crc16_ibm(data))  # CRC is 4 bytes, upper 2 are zero
    )


def decode_avl_packet(packet: bytes) -> list[dict]:
    """
    Minimal Codec 8 / 8 Extended decoder (used by tools/fake_server.py and for testing).
    Validates length + CRC and returns the records as dicts. Values are unsigned;
    variable-length (NX) values are returned as bytes.
    """
    preamble, length = struct.unpack_from(">II", packet, 0)
    if preamble != 0:
        raise ValueError("Bad preamble")
    data = packet[8:8 + length]
    (crc,) = struct.unpack_from(">I", packet, 8 + length)
    if crc != crc16_ibm(data):
        raise ValueError("CRC mismatch")

    codec, count = struct.unpack_from(">BB", data, 0)
    if codec not in (CODEC_8, CODEC_8_EXT):
        raise ValueError(f"Unsupported codec 0x{codec:02x}")
    n = 2 if codec == CODEC_8_EXT else 1

    pos = 2

    def read(size):
        nonlocal pos
        value = int.from_bytes(data[pos:pos + size], "big")
        pos += size
        return value

    records = []
    for _ in range(count):
        ts, prio = struct.unpack_from(">QB", data, pos)
        pos += 9
        lon, lat, alt, angle, sats, speed = struct.unpack_from(">iihHBH", data, pos)
        pos += 15
        event_id, _total = read(n), read(n)
        io = {}
        for size in (1, 2, 4, 8):
            for _ in range(read(n)):
                io_id = read(n)
                io[io_id] = read(size)
        if codec == CODEC_8_EXT:
            for _ in range(read(2)):
                io_id, size = read(2), read(2)
                io[io_id] = data[pos:pos + size]
                pos += size
        records.append({
            "codec": codec, "timestamp_ms": ts, "priority": prio,
            "lat": lat / 1e7, "lon": lon / 1e7, "altitude": alt,
            "angle": angle, "satellites": sats, "speed_kmh": speed,
            "event_io_id": event_id, "io": io,
        })

    if data[pos] != count:
        raise ValueError("Record count mismatch")
    return records
