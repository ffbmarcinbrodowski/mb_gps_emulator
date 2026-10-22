"""
Minimal Teltonika (Codec 8) TCP server for testing without Traccar.

It accepts every IMEI, decodes AVL packets, prints the records and sends the
correct acknowledgement - exactly what Traccar does on port 5027.

    python tools/fake_server.py            # listens on 0.0.0.0:5055
    python tools/fake_server.py 6000       # custom port
"""
import os
import socketserver
import struct
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.events import name_for  # noqa: E402
from core.teltonika import decode_avl_packet  # noqa: E402


class Handler(socketserver.BaseRequestHandler):
    def recv_exact(self, size):
        buf = b""
        while len(buf) < size:
            chunk = self.request.recv(size - len(buf))
            if not chunk:
                raise ConnectionError("closed")
            buf += chunk
        return buf

    def handle(self):
        try:
            # 1. IMEI handshake
            (length,) = struct.unpack(">H", self.recv_exact(2))
            imei = self.recv_exact(length).decode("ascii")
            print(f"[+] {self.client_address[0]} connected, IMEI {imei}")
            self.request.sendall(b"\x01")

            # 2. AVL packets
            while True:
                header = self.recv_exact(8)
                (data_len,) = struct.unpack(">I", header[4:])
                packet = header + self.recv_exact(data_len + 4)
                records = decode_avl_packet(packet)
                for r in records:
                    ts = datetime.fromtimestamp(r["timestamp_ms"] / 1000, timezone.utc).strftime("%H:%M:%S")
                    print(f"    {imei} {ts}  {r['lat']:.6f},{r['lon']:.6f}  "
                          f"{r['speed_kmh']:>3} km/h  {r['angle']:>3}°  alt {r['altitude']} m  io {r['io']}")
                    if r["event_io_id"]:
                        if r["event_io_id"] == 239:
                            name = "Ignition " + ("on" if r["io"].get(239) else "off")
                        else:
                            name = name_for(r["event_io_id"], r["io"].get(r["event_io_id"]))
                        print(f"    >>> EVENT: {name or 'unknown'} (IO {r['event_io_id']}, priority {r['priority']})")
                self.request.sendall(struct.pack(">I", len(records)))
        except (ConnectionError, OSError):
            print(f"[-] {self.client_address[0]} disconnected")
        except ValueError as exc:
            print(f"[!] invalid packet: {exc}")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5055
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("0.0.0.0", port), Handler) as server:
        print(f"Fake Teltonika server listening on port {port}")
        server.serve_forever()
