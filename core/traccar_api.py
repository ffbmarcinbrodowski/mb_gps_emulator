"""
Traccar REST API client: creates an emulator device in Traccar, so the IMEI and the model
don't have to be entered there by hand.

The emulator only CREATES devices (or finds an existing one with the same IMEI) - it never
changes or deletes anything in Traccar.

Docs: https://www.traccar.org/api-reference/  (login with HTTP Basic auth: e-mail + password)
"""
import requests

TIMEOUT_S = 10

# Traccar decodes the OBD values only for model FMB003, and the alarms of the crash, towing,
# jamming and power cut events only when a model like FMB920 is set (see README).
MODEL_OBD = "FMB003"
MODEL_DEFAULT = "FMB920"


class TraccarError(Exception):
    pass


def device_model(obd_enabled):
    return MODEL_OBD if obd_enabled else MODEL_DEFAULT


class TraccarApi:
    def __init__(self, url, user, password):
        self.url = url.rstrip("/")
        self.auth = (user, password)

    def _call(self, method, path, **kwargs):
        try:
            resp = requests.request(method, f"{self.url}/api/{path}", auth=self.auth, timeout=TIMEOUT_S, **kwargs)
        except requests.RequestException as exc:
            raise TraccarError(f"Traccar not reachable ({self.url}): {exc}") from exc
        if resp.status_code == 401:
            raise TraccarError("Traccar login failed - check e-mail and password")
        if resp.status_code >= 400:
            # Traccar answers errors as plain text (e.g. a database message for a duplicate IMEI).
            text = resp.text.strip().splitlines()[0][:200] if resp.text.strip() else f"HTTP {resp.status_code}"
            raise TraccarError(f"Traccar: {text}")
        return resp.json() if resp.content else None

    def version(self):
        """Server version - also checks that the address is a Traccar and the login works."""
        version = (self._call("GET", "server") or {}).get("version")
        self._call("GET", "devices", params={"id": 0})     # /server works without login, this doesn't
        return version

    def find_device(self, imei):
        """The device with this IMEI ("identifier" in Traccar) the user can see, or None."""
        devices = self._call("GET", "devices", params={"uniqueId": imei}) or []
        return next((d for d in devices if d.get("uniqueId") == imei), None)

    def create_device(self, name, imei, model):
        return self._call("POST", "devices", json={"name": name, "uniqueId": imei, "model": model,
                                                   "category": "car"})

    def ensure_device(self, name, imei, model):
        """
        Create the device unless one with this IMEI exists already.
        Returns (Traccar device, created: bool).
        """
        existing = self.find_device(imei)
        if existing:
            return existing, False
        return self.create_device(name, imei, model), True
