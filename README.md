# MB GPS Emulator

A web app that emulates **Teltonika GPS trackers**. Pick a start and end point on a map,
get a real road route from GraphHopper, and let virtual devices drive along it while
they stream positions to **Traccar** (or any Teltonika-compatible server) using the
**Codec 8 AVL** protocol over TCP.

Built to test GPS tracking servers, alarms and notifications without real hardware or driving around.

**Stack:** Python + Flask · SQLite · vanilla JavaScript + Bootstrap 5 · Leaflet + OpenStreetMap · Docker

![Main window: devices driving along their routes](docs/screenshots/main.jpg)

## Features

- **Devices:** add, edit and delete virtual trackers (name, IMEI, route, send interval, average speed, target server).
- **Routes:** click start and end on the map. The route comes from the GraphHopper Routing API and you can drag the markers to change it.
- **Emulation:** start or stop one device or all of them. Positions move along the route with realistic speed variation and heading.
- **Live map:** device markers update every 2 seconds. Works on desktop and iPhone.
- **Events:** send crash, towing, GSM jamming, power cut, harsh acceleration/braking/cornering, alarm/SOS or overspeed on demand, to test how your server reacts.
- **Servers:** manage a list of target servers (host + port).
- **Protocol:** real Teltonika Codec 8 over TCP, with IMEI handshake, CRC-16 and record acknowledgement. The encoder is verified byte-for-byte against the example in the Teltonika wiki.
- **Fake server:** a small built-in test server prints every decoded record, so you can try the emulator without Traccar.

## Screenshots

| Add a device: click start and end on the map | Send an event from a running device |
|---|---|
| ![Add device dialog with a generated route](docs/screenshots/add-device.jpg) | ![Event menu](docs/screenshots/events.jpg) |

<p align="center">
  <img src="docs/screenshots/iphone.jpg" alt="Emulator on an iPhone" width="300"><br>
  <em>On an iPhone</em>
</p>

## Quick start (Docker)

Requirements: Docker with the Compose plugin, and a free GraphHopper API key ([how to get one](#getting-a-free-graphhopper-api-key)).

```bash
git clone https://github.com/ffbmarcinbrodowski/mb_gps_emulator.git
cd mb_gps_emulator

cp .env.example .env
# edit .env and set GRAPHHOPPER_API_KEY=<your key>

docker compose up -d --build
```

Open `http://<docker-host-ip>:5057` in a browser on any device in your network.

Useful commands:

```bash
docker compose logs -f        # logs, including emulator output
docker compose down           # stop (data is kept)
git pull && docker compose up -d --build   # update
```

The database lives on the host in `./data/emulator.db`. To back it up, copy that file.

> **The server address `127.0.0.1` inside Docker means the container itself.**
> When you add a server in the emulator, use:
>
> | Traccar runs… | Host to enter |
> |---|---|
> | on the same machine as Docker | `host.docker.internal` |
> | on another machine | its IP or hostname, e.g. `192.168.1.20` |
> | the built-in fake server (see [below](#testing-without-traccar)) | `127.0.0.1`, port `5055` |

## Getting a free GraphHopper API key

1. Go to <https://www.graphhopper.com/> and click **Sign up**. An email and password are enough.
2. Confirm your email and log in to the dashboard at <https://graphhopper.com/dashboard/>.
3. Open **API Keys** and click **Add API Key**. Any description will do, e.g. `gps-emulator`.
4. Copy the key into `.env`:
   ```
   GRAPHHOPPER_API_KEY=your-key-here
   ```
5. Restart the container: `docker compose up -d`.

The free plan includes a limited number of requests per day. The emulator calls GraphHopper
only when you create a route, not while devices are driving, so the free plan is usually
enough for testing. Check GraphHopper's current pricing and terms for the exact limits and
allowed use.

## Usage

1. **Servers:** click **Servers** in the header and add your Traccar server. Teltonika's default port in Traccar is **5027**.
2. **Add a device:** click **+** in the *Devices* panel.
   - Enter a name and an IMEI (normally 15 digits).
   - Click the map once for the **start** point and once more for the **end** point. The route appears automatically.
   - Set the interval (seconds between positions), the average speed and the server, then click **Save**.
3. Click **Start** on a device, or **Start all**.
4. **Events:** select a running device and use **Event ▾** in the map header.

Device status: **Ready** (not started) · **Running** · **Paused** (stopped halfway; Start resumes) · **Finished** (Start drives the route again).

## Connecting to Traccar

1. In Traccar, add a device whose **Identifier** is the same IMEI as in the emulator.
   Traccar rejects unknown IMEIs, and the emulator then shows *"Server rejected IMEI"* under the device.
2. Point the emulator device at your Traccar server (port 5027) and click **Start**.

### Alarms from events

| Event | Teltonika IO = value | Traccar alarm |
|---|---|---|
| Crash \* | 247 = 1 | `accident` (the vehicle stops afterwards) |
| Towing \* | 246 = 1 | `tow` |
| GSM jamming \* | 249 = 1 | `jamming` |
| Power cut \* | 252 = 1 | `powerCut` |
| Harsh acceleration / braking / cornering | 253 = 1 / 2 / 3 | `hardAcceleration` / `hardBraking` / `hardCornering` |
| Alarm / SOS | 236 = 1 | `general` |
| Overspeed | 255 = speed | none from the IO. Traccar raises `deviceOverspeed` when the speed exceeds the device's `speedLimit` attribute. The emulator drives at 130 km/h for 3 ticks. |

\* Traccar decodes these only when the device's **Model** field (device settings → *Extra*) is set to a
Teltonika model such as `FMB920`. Without it, the value only appears as an attribute (`io247`, …).

To get notified, go to **Settings → Notifications → +**, choose type **Alarm**, select the alarms
(e.g. *Accident*) and a channel (e.g. *Web*), and tick **All Devices**.

## Running without Docker

Requirements: Python 3.10+.

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

export GRAPHHOPPER_API_KEY=your-key-here
python seed.py                      # optional: demo servers + 3 devices
python app.py                       # http://localhost:5057
```

Instead of the environment variable, you can put the key in a file named `graphhopper_api.txt`:

```
GRAPHHOPPER_API_KEY = 'your-key-here'
```

`seed.py` adds demo devices around Saarbrücken, Germany. It uses straight-line routes if GraphHopper is not configured.

## Testing without Traccar

`tools/fake_server.py` is a minimal Teltonika server. It accepts every IMEI, prints decoded records and sends proper acknowledgements:

```bash
python tools/fake_server.py                                  # local, port 5055
docker compose exec emulator python tools/fake_server.py     # inside the container
```

```
356307042441001 14:59:39  49.203869,6.948186    0 km/h  147°  alt 232 m  io {239: 1, 240: 0, 69: 1, 247: 1, 66: 12600, 16: 130}
    >>> EVENT: Crash (IO 247, priority 2)
```

## Configuration

| Variable | Default | Description |
|---|---|---|
| `GRAPHHOPPER_API_KEY` | – | GraphHopper API key (required for routing) |
| `GRAPHHOPPER_ROUTE_URL` | `https://graphhopper.com/api/1/route` | Routing endpoint; also works with a self-hosted GraphHopper |
| `EMULATOR_PORT` | `5057` | Web port |
| `EMULATOR_HOST` | `0.0.0.0` | Bind address |
| `EMULATOR_DB` | `./emulator.db` (Docker: `/data/emulator.db`) | SQLite database path |

More constants are in `config.py`: speed variation, TCP timeout and the routing profile.

## How it works

- Each running device has its own thread. Every *interval* seconds the thread:
  1. computes the position along the route (distance = speed × time, with ±15 % speed noise),
  2. computes the heading from the current road segment,
  3. sends one AVL record over a persistent TCP connection and waits for the server's acknowledgement.
- AVL records contain the timestamp, coordinates, altitude, heading, satellites and speed, plus these IO elements: ignition (239), movement (240), GNSS status (69), external voltage (66) and odometer (16).
- At the end of the route the device sends a final record with speed 0 and ignition off.
- If the server is unreachable, the device keeps driving, the error is shown in the UI and the next tick reconnects.
- The schema is created by SQL migrations in `migrations/`. They run automatically on startup.

Teltonika Codec 8 over TCP:

```
device → server : [2B length][IMEI]
server → device : 0x01 accepted / 0x00 rejected
device → server : [4B zero][4B length][0x08][N][AVL records][N][4B CRC-16/IBM]
server → device : [4B number of accepted records]
```

## REST API

| Method | Endpoint | Description |
|---|---|---|
| GET, POST | `/api/servers` | List / create `{name, host, port}` |
| GET, PUT, DELETE | `/api/servers/<id>` | Read / update / delete |
| GET, POST | `/api/devices` | List / create a device |
| GET, PUT, DELETE | `/api/devices/<id>` | Read (incl. route points) / update / delete |
| POST | `/api/routes` | `{start: [lat, lon], end: [lat, lon]}` → route preview |
| GET | `/api/routes/<id>` | Saved route |
| POST | `/api/emulator/start` | `{device_id: 1}` or `{all: true}` |
| POST | `/api/emulator/stop` | `{device_id: 1}` or `{all: true}` |
| GET | `/api/emulator/events` | Available events |
| POST | `/api/emulator/event` | `{device_id: 1, type: "crash"}` |
| GET | `/api/emulator/status` | IDs of running devices |

## Project structure

```
app.py                Flask app: REST API + page
config.py             settings (env variables / graphhopper_api.txt)
database.py           SQLite connection + migration runner
models.py             SQL for servers, devices, routes
seed.py               demo data
migrations/           SQL schema migrations
emulator/
  teltonika.py        Codec 8 encoder/decoder, CRC-16/IBM
  tcp_client.py       TCP client: IMEI handshake, send, acknowledgement
  geo.py              distance, bearing, interpolation along the route
  graphhopper.py      route generation
  events.py           event catalog (crash, towing, …)
  engine.py           one thread per device, sending every N seconds
templates/, static/   UI (HTML, CSS, JS)
tools/fake_server.py  test server
Dockerfile, docker-compose.yml
```

## Security

The app has **no authentication**. Run it only in a trusted local network or behind a VPN or reverse proxy with authentication. Anyone who can open it can control devices and use your GraphHopper quota.

## License

[MIT](LICENSE): free to use, modify and distribute for any purpose.
