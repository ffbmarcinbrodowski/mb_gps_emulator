"""
Teltonika TCP client: keeps one connection per device open and sends AVL packets.
"""
import socket
import struct

from . import teltonika


class TeltonikaError(Exception):
    pass


class TeltonikaClient:
    def __init__(self, host, port, imei, timeout=5):
        self.host = host
        self.port = int(port)
        self.imei = imei
        self.timeout = timeout
        self.sock = None

    def connect(self):
        """Open the TCP connection and do the IMEI handshake."""
        self.close()
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        self.sock.sendall(teltonika.encode_imei(self.imei))

        reply = self._recv_exact(1)
        if reply != b"\x01":
            self.close()
            # Traccar answers 0x00 when the IMEI is not registered as a device.
            raise TeltonikaError(f"Server rejected IMEI {self.imei} (is the device added on the server?)")

    def send(self, records):
        """Send records; (re)connects if needed. Raises on any failure."""
        if self.sock is None:
            self.connect()
        try:
            self.sock.sendall(teltonika.encode_avl_packet(records))
            (accepted,) = struct.unpack(">I", self._recv_exact(4))
        except Exception:
            self.close()  # force a fresh connection on the next tick
            raise
        if accepted != len(records):
            raise TeltonikaError(f"Server accepted {accepted} of {len(records)} records")
        return accepted

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def _recv_exact(self, size):
        buf = b""
        while len(buf) < size:
            chunk = self.sock.recv(size - len(buf))
            if not chunk:
                raise TeltonikaError("Connection closed by server")
            buf += chunk
        return buf
