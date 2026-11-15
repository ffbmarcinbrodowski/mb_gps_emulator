-- Stops on the return route B -> A, same JSON format as routes.stops:
-- [{"lat": ..., "lon": ..., "dwell_min": 5, "index": 151}, ...] ("index" = point of return_points).
-- Needed for school/day-care runs: the afternoon trip drops people off in its own order.
-- NULL = the return route goes straight back (as before).

ALTER TABLE routes ADD COLUMN return_stops TEXT;
