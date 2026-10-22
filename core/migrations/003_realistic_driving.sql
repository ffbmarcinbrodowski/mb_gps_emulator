-- Realistic driving: per-device switch, lifetime odometer, road profile of the route.

ALTER TABLE devices ADD COLUMN realistic INTEGER NOT NULL DEFAULT 1;   -- 1 = realistic, 0 = simple constant speed
ALTER TABLE devices ADD COLUMN odometer_m REAL NOT NULL DEFAULT 0;     -- total distance ever driven (Teltonika IO 16)

-- GraphHopper path details (average_speed, max_speed, road_class) as JSON; NULL for old routes.
ALTER TABLE routes ADD COLUMN profile TEXT;
