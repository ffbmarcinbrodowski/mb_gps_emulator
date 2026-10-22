-- Initial schema: servers, devices, routes.

CREATE TABLE servers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    host        TEXT    NOT NULL,
    port        INTEGER NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE devices (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    imei            TEXT    NOT NULL UNIQUE,
    server_id       INTEGER REFERENCES servers(id) ON DELETE RESTRICT,
    interval_s      INTEGER NOT NULL DEFAULT 5,      -- send frequency (seconds)
    speed_kmh       REAL    NOT NULL DEFAULT 50,     -- average speed
    status          TEXT    NOT NULL DEFAULT 'stopped'
                    CHECK (status IN ('stopped', 'running', 'finished')),

    -- Live emulation state (updated by the emulator on every tick)
    progress_m      REAL    NOT NULL DEFAULT 0,      -- distance travelled on the route
    current_lat     REAL,
    current_lon     REAL,
    current_speed   REAL,
    current_bearing REAL,
    last_sent_at    TEXT,
    last_error      TEXT,

    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- One route per device. Points are stored as JSON: [[lat, lon, ele], ...]
CREATE TABLE routes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   INTEGER NOT NULL UNIQUE REFERENCES devices(id) ON DELETE CASCADE,
    start_lat   REAL    NOT NULL,
    start_lon   REAL    NOT NULL,
    end_lat     REAL    NOT NULL,
    end_lon     REAL    NOT NULL,
    points      TEXT    NOT NULL,
    distance_m  REAL    NOT NULL,
    duration_s  REAL    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'planned'
                CHECK (status IN ('planned', 'running', 'completed')),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
