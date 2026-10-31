-- Stops between start and end of the A -> B route (see core/motion.py: the vehicle waits there
-- with the engine running). JSON: [{"lat": ..., "lon": ..., "dwell_min": 5, "index": 151}, ...]
-- "index" is the route point the stop lies on; dwell_min 0 = the route only passes through.
-- NULL = no stops. The return route B -> A always goes straight back.

ALTER TABLE routes ADD COLUMN stops TEXT;
