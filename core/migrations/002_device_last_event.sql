-- Last event sent on demand (crash, towing, ...), shown in the device list.

ALTER TABLE devices ADD COLUMN last_event TEXT;
ALTER TABLE devices ADD COLUMN last_event_at TEXT;
