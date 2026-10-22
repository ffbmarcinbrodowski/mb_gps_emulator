-- Scenarios: when a device drives (see core/scenarios.py), and the return route B -> A.

ALTER TABLE devices ADD COLUMN scenario TEXT;                                 -- JSON, NULL = manual (drive once)
ALTER TABLE devices ADD COLUMN scenario_enabled INTEGER NOT NULL DEFAULT 0;   -- armed; resumed after a restart
ALTER TABLE devices ADD COLUMN direction TEXT NOT NULL DEFAULT 'forward';     -- current leg: forward A->B, return B->A
ALTER TABLE devices ADD COLUMN activity TEXT;                                 -- 'driving' / 'parked' while running
ALTER TABLE devices ADD COLUMN next_trip_at TEXT;                             -- next departure (local time)

-- Return route B -> A (generated separately: one-way streets, motorway exits differ).
ALTER TABLE routes ADD COLUMN return_points TEXT;
ALTER TABLE routes ADD COLUMN return_distance_m REAL;
ALTER TABLE routes ADD COLUMN return_duration_s REAL;
ALTER TABLE routes ADD COLUMN return_profile TEXT;
