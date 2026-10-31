"""
Catalog of Teltonika events the emulator can send on demand.

An event is a normal AVL record with:
    - event IO ID = the IO element that caused it,
    - that IO element set to `value`,
    - a higher priority (1 = high, 2 = panic).

The `traccar` field is the alarm Traccar creates from it (checked against
traccar/src/main/java/org/traccar/protocol/TeltonikaProtocolDecoder.java).
Events with `needs_model` are only decoded as alarms when the device in
Traccar has a Teltonika model set, e.g. "FMB920" (any FMBxxx / FMCxxx / FMMxxx ...).
"""

EVENTS = {
    "crash": {
        "label": "Crash", "io_id": 247, "value": 1, "priority": 2,
        "traccar": "accident", "needs_model": True,
        "speed": 0, "movement": 0, "stop_after": True,   # vehicle stands still after the crash
    },
    "towing": {
        "label": "Towing", "io_id": 246, "value": 1, "priority": 1,
        "traccar": "tow", "needs_model": True,
        "ignition": 0,                                   # towed = moving with ignition off
    },
    "jamming": {
        "label": "GSM jamming", "io_id": 249, "value": 1, "priority": 1,
        "traccar": "jamming", "needs_model": True,
    },
    "power_cut": {
        "label": "Power cut (unplug)", "io_id": 252, "value": 1, "priority": 1,
        "traccar": "powerCut", "needs_model": True,
        "external_voltage": 0,
    },
    "harsh_acceleration": {
        "label": "Harsh acceleration", "io_id": 253, "value": 1, "priority": 1,
        "traccar": "hardAcceleration", "needs_model": False,
    },
    "harsh_braking": {
        "label": "Harsh braking", "io_id": 253, "value": 2, "priority": 1,
        "traccar": "hardBraking", "needs_model": False,
    },
    "harsh_cornering": {
        "label": "Harsh cornering", "io_id": 253, "value": 3, "priority": 1,
        "traccar": "hardCornering", "needs_model": False,
    },
    "alarm": {
        "label": "Alarm / SOS", "io_id": 236, "value": 1, "priority": 2,
        "traccar": "general", "needs_model": False,
    },
    "overspeed": {
        # Traccar has no alarm for IO 255 - it creates "deviceOverspeed" itself
        # when speed > the device's "speedLimit" attribute. So the vehicle really
        # speeds up to 130 km/h for a while; the event record (IO 255 = speed) is
        # sent once it is actually that fast.
        "label": "Overspeed", "io_id": 255, "value": None, "priority": 1,
        "traccar": "deviceOverspeed (needs speedLimit)", "needs_model": False,
        "overspeed_kmh": 130, "overspeed_s": 45,
    },
}


# OBD events (devices with OBD enabled, see core/obd.py). The record carries all OBD values;
# "obd_action" says what changes in the vehicle.
EVENTS.update({
    "dtc_set": {
        "label": "Set fault code", "io_id": 30, "value": None, "priority": 1, "obd_action": "dtc_set",
        "traccar": "faultCount, dtcs", "needs_model": True, "needs_obd": True,
    },
    "dtc_clear": {
        "label": "Clear fault codes", "io_id": 30, "value": None, "priority": 1, "obd_action": "dtc_clear",
        "traccar": "faultCount, dtcs", "needs_model": True, "needs_obd": True,
    },
    "battery_weak": {
        "label": "Weak battery", "io_id": 66, "value": None, "priority": 1, "obd_action": "battery_weak",
        "traccar": "power < 12 V while parked", "needs_model": False, "needs_obd": True,
    },
    "battery_ok": {
        "label": "Battery OK", "io_id": 66, "value": None, "priority": 0, "obd_action": "battery_ok",
        "traccar": "power", "needs_model": False, "needs_obd": True,
    },
    "service_due": {
        "label": "Service due", "io_id": 402, "value": None, "priority": 1, "obd_action": "service_due",
        "traccar": "io402 = 0", "needs_model": False, "needs_obd": True,
    },
})


def catalog():
    """Public list for the UI."""
    return [
        {"type": key, "label": e["label"], "traccar": e["traccar"], "needs_model": e["needs_model"],
         "needs_obd": e.get("needs_obd", False)}
        for key, e in EVENTS.items()
    ]


def name_for(io_id, value):
    """Event label for a decoded record (used by tools/fake_server.py)."""
    for e in EVENTS.values():
        if "obd_action" in e:
            continue                      # OBD events are shown by their IO values
        if e["io_id"] == io_id and e["value"] in (None, value):
            return e["label"]
    return None
