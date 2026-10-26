# CLAUDE.md

Context for Claude Code working on this repository. User-facing documentation is in README.md.

## What this is

MB GPS Emulator emulates Teltonika GPS trackers. Virtual devices drive GraphHopper routes realistically and send
Teltonika Codec 8 AVL data over TCP to Traccar (or any Teltonika-compatible server). There are two UIs on one
shared core: a Flask web app (also deployed with Docker) and a PySide6 desktop app.

## Layout

```
core/       shared logic, no UI code (both UIs import only from here)
  config.py        settings from env vars / graphhopper_api.txt; TIMEZONE is the app-wide zone
  database.py      SQLite connect() + migration runner (core/migrations/NNN_*.sql, applied in order)
  models.py        SQL for servers / devices / routes, device dicts for the UIs, local_time()
  services.py      validation + create/update/delete; raises ServiceError(message, status)
  engine.py        Emulator (thread per device) and DeviceWorker (drive / park loop, events, offline buffer)
  motion.py        Trip (realistic driving, 1 s steps), Parked, GpsReceiver (noise, static navigation)
  recorder.py      RecordPolicy (when a record is saved, like Teltonika FMB) + RecordBuilder (AVL record + IO)
  scenarios.py     pure schedule logic: departures, next_departure, initial_position, preview
  history.py       HistorySimulator (scenario in virtual time, streamed) + HistoryJob (sends in batches)
  teltonika.py     Codec 8 encoder/decoder, CRC-16/IBM; tcp_client.py: IMEI handshake, send, ack
  graphhopper.py   routing incl. path details (average_speed, max_speed, road_class) = speed profile
  events.py        event catalog (crash, towing, ...) with IO ids and the Traccar alarm they map to
  imei.py          valid test IMEIs (prefix 00 + Luhn check digit)
web/        Flask: thin HTTP layer over core.services; static JS modules (no framework, no build step)
desktop/    PySide6: windows/dialogs; map = Leaflet in QWebEngineView (desktop/map/), QWebChannel bridge
tests/      unittest, no network
tools/fake_server.py   minimal Teltonika server that prints decoded records (port 5055)
```

## Commands

```bash
python -m unittest discover -s tests -t .   # all tests (fast, offline)
python -m web                               # web app on :5057 (EMULATOR_PORT)
python -m desktop                           # desktop app (needs requirements-desktop.txt)
python tools/fake_server.py                 # test server on :5055, no Traccar needed
python seed.py [--reset]                    # demo servers + devices around Saarbrücken
docker compose up -d --build                # web app in Docker (needs .env)
```

Use a scratch database for experiments: `EMULATOR_DB=/tmp/x.db python -m web`. Never touch the user's
`emulator.db` in tests; to try a migration on real data, copy it with SQLite's backup API first.

## Design decisions (keep them)

- **Realism is the point.** The data in Traccar is used for analytics. No time acceleration of live data; history
  uses real past timestamps. Realistic driving is on by default ("simple" mode only for protocol tests).
- `speed_kmh` on a device is the **max speed** in realistic mode (actual speed comes from the road profile).
- **Times:** stored in UTC in the database and sent in UTC in the protocol; shown in the app time zone
  (`models.local_time`). Scenario times are local times of `config.TIMEZONE` (one zone for the whole app).
- Scenario devices are **armed**: they resume after a restart (`scenario_enabled`); Stop disarms; app shutdown
  (`stop_all(disarm=False)`) keeps them armed. When armed mid-day the vehicle stands where the plan says.
- Return trips B -> A use their own GraphHopper route (`routes.return_*`), not the reversed A -> B route.
- Live emulation and history generation of the same device must never run at the same time.
- Docker/gunicorn must run exactly **one** worker: emulator threads live in the process.
- Both UIs must behave the same: put rules in `core/`, not in `web/` or `desktop/`.
- Teltonika encoder is verified against the wiki example (tests/test_protocol.py); keep that test green.

## Conventions

- Code, comments, UI texts and README in English. Simple, readable code; comments at key places.
- Schema changes only via a new migration file `core/migrations/00N_*.sql`.
- Web JS: ES modules, Bootstrap 5, Leaflet; a modal can't be stacked on another Bootstrap modal (the device
  dialog hides while the scenario dialog is open).
- Desktop: slow work (GraphHopper, stopping devices, saving) runs via `desktop/tasks.run_async`; worker
  threads talk to the GUI only through Qt signals.

## Working agreements with the user

- Make a backup of the project before larger changes (copy to `backup/<timestamp>/`, database via SQLite backup API).
- Before committing: check the list of files (`git status`) and stage files explicitly; scan staged files for the
  GraphHopper API key. Never commit `.env`, `graphhopper_api.txt`, `emulator.db`, `backup/`, `presentation/`.
- Commit / push only when the user asks. The repository is public.
- Verify changes for real (tests, fake server, browser screenshots) before reporting them as done.

## Open topics

- Not yet tested against a real Traccar (especially history uploads and alarms from events).
- `todo.md`: create the device in Traccar through the Traccar API when it is added in the emulator.
- Deployment on a small server (e.g. Hetzner): add access protection (VPN or reverse proxy with auth), the app has
  no login; check performance with many devices (list endpoint, browser rendering).
