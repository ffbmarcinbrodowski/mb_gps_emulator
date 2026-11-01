import base64
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock
from urllib.parse import parse_qs, urlparse

from core import config, database, graphhopper, models, services

USER, PASSWORD = "admin@example.com", "secret"


class FakeTraccar(BaseHTTPRequestHandler):
    """The few Traccar API calls the emulator uses: GET /api/server, GET/POST /api/devices."""
    devices = []

    def log_message(self, *_args):
        pass

    def _send(self, status, body):
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain" if isinstance(body, str) else "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _logged_in(self):
        expected = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
        return self.headers.get("Authorization") == expected

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/api/server":
            return self._send(200, {"version": "6.15.3"})
        if not self._logged_in():
            return self._send(401, "jakarta.ws.rs.WebApplicationException: HTTP 401 Unauthorized\n\tat ...")
        query = parse_qs(url.query)
        found = [d for d in self.devices
                 if ("uniqueId" not in query or d["uniqueId"] in query["uniqueId"])
                 and ("id" not in query or str(d["id"]) in query["id"])]
        return self._send(200, found)

    def do_POST(self):
        if not self._logged_in():
            return self._send(401, "HTTP 401 Unauthorized")
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if any(d["uniqueId"] == body["uniqueId"] for d in self.devices):
            return self._send(400, "Duplicate entry for key uniqueId\n\tat ...")
        device = {**body, "id": 100 + len(self.devices)}
        self.devices.append(device)
        return self._send(200, device)


class TraccarApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeTraccar)
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FakeTraccar.devices = [{"id": 7, "name": "Real car", "uniqueId": "356307042441999", "model": "FMB001"}]
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = mock.patch.object(config, "DATABASE_PATH", os.path.join(self.tmp.name, "test.db"))
        self.db_path.start()
        database.run_migrations()
        self.db = database.connect()
        fake_route = {"points": [[49.0, 7.0, 0], [49.1, 7.1, 0]], "distance_m": 1000.0, "duration_s": 100.0,
                      "profile": {"average_speed": [[0, 1, 50]]}, "stop_indices": []}
        self.gh = mock.patch.object(graphhopper, "generate_route", return_value=fake_route)
        self.gh.start()

    def tearDown(self):
        self.gh.stop()
        self.db.close()
        self.db_path.stop()
        self.tmp.cleanup()

    def _server(self, password=PASSWORD, url=None):
        return services.create_server(self.db, {"name": "Traccar", "host": "10.0.0.1", "port": 5027,
                                                "api_url": url or self.url, "api_user": USER,
                                                "api_password": password})

    def _device(self, server_id, imei="001234567890128", obd=None, traccar=False):
        data = {"name": "Van 1", "imei": imei, "server_id": server_id, "interval_s": 5, "speed_kmh": 90,
                "start": [49.0, 7.0], "end": [49.1, 7.1], "traccar": traccar}
        if obd:
            data["obd"] = obd
        return services.create_device(self.db, data)

    # --- servers ------------------------------------------------------------

    def test_password_is_never_returned_and_kept_when_empty(self):
        server = self._server()
        self.assertNotIn("api_password", server)
        self.assertTrue(server["api_password_set"])
        services.update_server(self.db, server["id"], {**server, "name": "Renamed", "api_password": ""})
        self.assertEqual(models.server_api_access(self.db, server["id"]), (self.url, USER, PASSWORD))
        # Removing the address removes the whole access.
        services.update_server(self.db, server["id"], {**server, "api_url": ""})
        self.assertIsNone(models.server_api_access(self.db, server["id"]))

    def test_validation(self):
        with self.assertRaises(services.ServiceError):
            self._server(url="10.40.1.114:8082")            # no http://
        with self.assertRaises(services.ServiceError):
            self._server(password="")                        # new access without password

    def test_connection_test(self):
        self.assertEqual(services.test_traccar_api(self.db, {"api_url": self.url, "api_user": USER,
                                                             "api_password": PASSWORD})["version"], "6.15.3")
        with self.assertRaises(services.ServiceError) as err:
            services.test_traccar_api(self.db, {"api_url": self.url, "api_user": USER, "api_password": "wrong"})
        self.assertIn("login failed", err.exception.message)
        # Saved server, password field left empty -> the stored password is used.
        server = self._server()
        services.test_traccar_api(self.db, {"api_url": self.url, "api_user": USER, "server_id": server["id"]})

    def test_unreachable_traccar(self):
        with self.assertRaises(services.ServiceError) as err:
            services.test_traccar_api(self.db, {"api_url": "http://127.0.0.1:1", "api_user": USER,
                                                "api_password": PASSWORD})
        self.assertIn("not reachable", err.exception.message)

    # --- devices ------------------------------------------------------------

    def test_create_with_model(self):
        server = self._server()
        device = self._device(server["id"], traccar=True)
        self.assertEqual(device["traccar_result"]["result"], "created")
        self.assertEqual(FakeTraccar.devices[-1], {"id": 101, "name": "Van 1", "uniqueId": "001234567890128",
                                                   "model": "FMB920", "category": "car"})
        self.assertEqual(device["traccar_id"], 101)

        obd = {"enabled": True, "codec": "8E", "vin": "WVWZZZ1KZ9W000123", "odometer_km": 1000,
               "service_km": 1000, "fuel_pct": 50}
        self._device(server["id"], imei="001234567890136", obd=obd, traccar=True)
        self.assertEqual(FakeTraccar.devices[-1]["model"], "FMB003")

    def test_existing_imei_is_linked_not_changed(self):
        server = self._server()
        device = self._device(server["id"], imei="356307042441999")
        result = services.add_to_traccar(self.db, device["id"])
        self.assertEqual((result["result"], result["traccar_id"]), ("exists", 7))
        self.assertEqual(FakeTraccar.devices, [{"id": 7, "name": "Real car", "uniqueId": "356307042441999",
                                                "model": "FMB001"}])

    def test_traccar_error_still_saves_the_device(self):
        server = self._server(url="http://127.0.0.1:1")
        device = self._device(server["id"], traccar=True)
        self.assertEqual(device["traccar_result"]["result"], "error")
        self.assertIsNone(device["traccar_id"])
        self.assertIsNotNone(models.get_device(self.db, device["id"]))

    def test_without_api_access(self):
        server = services.create_server(self.db, {"name": "Fake", "host": "127.0.0.1", "port": 5055})
        device = self._device(server["id"], traccar=True)
        self.assertEqual(device["traccar_result"]["result"], "error")
        with self.assertRaises(services.ServiceError):
            services.add_to_traccar(self.db, device["id"])

    def test_link_is_reset_when_server_or_imei_changes(self):
        server = self._server()
        other = self._server()
        device = self._device(server["id"], traccar=True)
        emulator = mock.Mock(is_running=mock.Mock(return_value=False))
        payload = {**device, "server_id": server["id"], "start": device["route"]["start"],
                   "end": device["route"]["end"], "name": "Van renamed"}
        self.assertEqual(services.update_device(self.db, emulator, device["id"], payload)["traccar_id"], 101)
        moved = services.update_device(self.db, emulator, device["id"], {**payload, "server_id": other["id"]})
        self.assertIsNone(moved["traccar_id"])


if __name__ == "__main__":
    unittest.main()
