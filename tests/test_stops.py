import os
import random
import tempfile
import unittest
from unittest import mock

from core import config, database, geo, graphhopper, models, services
from core.motion import Trip
from tests.test_motion import PROFILE, straight_route

POINTS = straight_route()
CUM = geo.cumulative_distances(POINTS)


class TripStopTest(unittest.TestCase):
    def test_waits_at_the_stop_with_the_engine_running(self):
        stop_index = 20                                   # 1 km after the start
        trip = Trip(POINTS, PROFILE, 130, rng=random.Random(1), stops=[(stop_index, 300)])
        standing = []
        while not trip.finished:
            trip.step(1.0)
            st = trip.state()
            if st["phase"] == "stop":
                standing.append(st)
        self.assertAlmostEqual(len(standing), 300, delta=2)
        self.assertTrue(all(st["ignition"] and not st["moving"] for st in standing))
        self.assertAlmostEqual(standing[0]["distance_m"], CUM[stop_index], delta=0.01)

    def test_brakes_smoothly_for_the_stop(self):
        trip = Trip(POINTS, PROFILE, 130, rng=random.Random(2), stops=[(20, 60)])
        prev_v, max_dec = 0.0, 0.0
        while trip.phase != "stop":
            trip.step(1.0)
            max_dec = max(max_dec, prev_v - trip.v)
            prev_v = trip.v if trip.phase != "stop" else prev_v
        self.assertLessEqual(max_dec, 3.51)

    def test_no_traffic_stop_next_to_a_planned_stop(self):
        for seed in range(20):
            trip = Trip(POINTS, PROFILE, 130, rng=random.Random(seed), stops=[(20, 60)])
            self.assertTrue(all(abs(p - CUM[20]) > 80 for p in trip.stops))

    def test_zero_minutes_only_drives_through(self):
        trip = Trip(POINTS, PROFILE, 130, rng=random.Random(3), stops=[(20, 0)])
        self.assertEqual(trip.planned, [])

    def test_resumed_after_the_stop_does_not_stop_again(self):
        trip = Trip(POINTS, PROFILE, 130, rng=random.Random(4), start_m=CUM[25], stops=[(20, 300)])
        self.assertEqual(trip.planned, [])

    def test_simple_mode_stops_too(self):
        trip = Trip(POINTS, None, 60, realistic=False, stops=[(20, 30)])
        phases = []
        while not trip.finished:
            trip.step(1.0)
            phases.append(trip.phase)
        self.assertAlmostEqual(phases.count("stop"), 30, delta=1)


class StopIndicesTest(unittest.TestCase):
    def test_exact_vertices_in_order(self):
        points = [[49.0, 7.0, 0], [49.1, 7.1, 0], [49.2, 7.2, 0], [49.1, 7.1, 0], [49.3, 7.3, 0]]
        # The same coordinates appear twice: the second stop must be found after the first one.
        snapped = [[7.2, 49.2, 0], [7.1, 49.1, 0]]
        self.assertEqual(graphhopper._stop_indices(points, snapped), [2, 3])

    def test_nearest_point_as_fallback(self):
        points = [[49.0, 7.0, 0], [49.1, 7.1, 0], [49.2, 7.2, 0]]
        self.assertEqual(graphhopper._stop_indices(points, [[7.1001, 49.1001, 0]]), [1])

    def test_long_routes_are_requested_in_parts(self):
        """Plan limit 3 points: start, 3 stops, end -> 2 requests, joined into one route."""
        def fake_request(waypoints, profile):
            # Every waypoint becomes a route point, with one extra point after it (except the end).
            points = []
            for lat, lon in waypoints[:-1]:
                points += [[lat, lon, 0], [lat + 0.001, lon, 0]]
            points.append([*waypoints[-1], 0])
            n = len(points) - 1
            return {"points": points, "distance_m": 100.0 * n, "duration_s": 10.0 * n,
                    "profile": {"average_speed": [[0, n, 50]], "max_speed": None, "road_class": [[0, n, "primary"]]},
                    "stop_indices": list(range(2, n, 2))}

        waypoints = [[49.0, 7.0], [49.1, 7.0], [49.2, 7.0], [49.3, 7.0], [49.4, 7.0]]
        with mock.patch.object(config, "GRAPHHOPPER_API_KEY", "x"), \
                mock.patch.object(config, "GRAPHHOPPER_MAX_POINTS", 3), \
                mock.patch.object(graphhopper, "_request", side_effect=fake_request) as request:
            route = graphhopper.generate_route(waypoints[0], waypoints[-1], via=waypoints[1:-1])
        self.assertEqual([c.args[0] for c in request.call_args_list], [waypoints[:3], waypoints[2:]])
        self.assertEqual(len(route["points"]), 9)                       # 5 + 5 - the shared point
        self.assertEqual([route["points"][i][:2] for i in route["stop_indices"]], waypoints[1:-1])
        self.assertEqual(route["distance_m"], 800)
        self.assertEqual(route["profile"]["average_speed"], [[0, 4, 50], [4, 8, 50]])
        self.assertEqual(route["profile"]["max_speed"], [])


class GraphHopperServerTest(unittest.TestCase):
    """Own server (no key, no point limit, maybe no elevation) vs. the public API."""

    def setUp(self):
        graphhopper._no_elevation.clear()
        self.requests = []

    def _answer(self, url, params=None, **_kw):
        params = list(params)
        self.requests.append(params)
        answer = mock.Mock(headers={"content-type": "application/json"})
        if ("elevation", "true") in params and "graphhopper.com" not in url:
            answer.status_code, answer.json.return_value = 400, {"message": "Elevation not supported!"}
            return answer
        points = [p[1] for p in params if p[0] == "point"]
        coords = [[float(x.split(",")[1]), float(x.split(",")[0])] for x in points]
        answer.status_code = 200
        answer.json.return_value = {"paths": [{"points": {"coordinates": coords}, "distance": 100.0, "time": 10_000,
                                               "details": {}, "snapped_waypoints": {"coordinates": coords}}]}
        return answer

    def _route(self, url, key="", stops=3):
        waypoints = [[49.0 + i / 100, 7.0] for i in range(stops + 2)]
        with mock.patch.object(config, "GRAPHHOPPER_ROUTE_URL", url), \
                mock.patch.object(config, "GRAPHHOPPER_API_KEY", key), \
                mock.patch.object(config, "GRAPHHOPPER_MAX_POINTS", None), \
                mock.patch.object(graphhopper.requests, "get", side_effect=self._answer):
            return graphhopper.generate_route(waypoints[0], waypoints[-1], via=waypoints[1:-1])

    def test_own_server_without_key_limit_or_elevation(self):
        route = self._route("http://10.40.1.114:8989/route", stops=8)
        self.assertEqual(len(self.requests), 2)                       # with elevation, then without
        self.assertTrue(all(name != "key" for params in self.requests for name, _ in params))
        self.assertEqual(len(route["stop_indices"]), 8)               # all 10 points in one request
        self.assertEqual(route["points"][0][2], 0)                     # no elevation -> altitude 0
        self._route("http://10.40.1.114:8989/route")
        self.assertEqual(len(self.requests), 3)                       # the server is remembered

    def test_public_api_needs_a_key_and_is_split(self):
        with self.assertRaises(graphhopper.RoutingError):
            self._route(config.PUBLIC_ROUTE_URL)
        self._route(config.PUBLIC_ROUTE_URL, key="secret", stops=6)   # 8 points -> 2 requests of <= 5
        self.assertEqual(len(self.requests), 2)
        self.assertTrue(all(("key", "secret") in params for params in self.requests))


class ServicesStopsTest(unittest.TestCase):
    """Saving devices with stops - on a throwaway database, GraphHopper replaced by a fake."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = mock.patch.object(config, "DATABASE_PATH", os.path.join(self.tmp.name, "test.db"))
        self.db_path.start()
        database.run_migrations()
        self.db = database.connect()
        self.server_id = models.create_server(self.db, {"name": "s", "host": "127.0.0.1", "port": 5055})
        self.calls = []
        self.gh = mock.patch.object(graphhopper, "generate_route", side_effect=self._fake_route)
        self.gh.start()
        self.emulator = mock.Mock(is_running=mock.Mock(return_value=False))

    def tearDown(self):
        self.gh.stop()
        self.db.close()
        self.db_path.stop()
        self.tmp.cleanup()

    def _fake_route(self, start, end, profile=None, via=()):
        self.calls.append(list(via))
        points = [[*start, 0], *[[*p, 0] for p in via], [*end, 0]]
        return {"points": points, "distance_m": 1000.0, "duration_s": 100.0, "profile": PROFILE,
                "stop_indices": list(range(1, len(via) + 1))}

    def _payload(self, stops):
        return {"name": "Van", "imei": "001234567890123", "server_id": self.server_id, "interval_s": 5,
                "speed_kmh": 90, "start": [49.0, 7.0], "end": [49.3, 7.3], "stops": stops}

    def test_create_stores_stops_with_their_route_position(self):
        stops = [{"point": [49.1, 7.1], "dwell_min": 10}, {"point": [49.2, 7.2], "dwell_min": 0}]
        device = services.create_device(self.db, self._payload(stops))
        self.assertEqual(device["route"]["stops"], stops)
        self.assertEqual(self.calls, [[[49.1, 7.1], [49.2, 7.2]]])
        row = self.db.execute("SELECT * FROM routes WHERE device_id = ?", (device["id"],)).fetchone()
        _points, _profile, leg_stops = models.route_leg(row, "forward")
        self.assertEqual(leg_stops, [(1, 600)])            # 0 min = drive through, not a stop
        self.assertEqual(models.route_leg(row, "return")[2], [])
        self.assertEqual(models.stops_seconds(row), 600)

    def test_changing_only_the_waiting_time_keeps_the_route(self):
        stops = [{"point": [49.1, 7.1], "dwell_min": 10}]
        device = services.create_device(self.db, self._payload(stops))
        route_id = device["route"]["id"]
        updated = services.update_device(self.db, self.emulator, device["id"],
                                         self._payload([{"point": [49.1, 7.1], "dwell_min": 25}]))
        self.assertEqual(len(self.calls), 1)                # no new GraphHopper request
        self.assertEqual(updated["route"]["id"], route_id)
        self.assertEqual(updated["route"]["stops"][0]["dwell_min"], 25)

    def test_moving_a_stop_generates_a_new_route(self):
        device = services.create_device(self.db, self._payload([{"point": [49.1, 7.1], "dwell_min": 10}]))
        services.update_device(self.db, self.emulator, device["id"],
                               self._payload([{"point": [49.15, 7.15], "dwell_min": 10}]))
        self.assertEqual(self.calls[-1], [[49.15, 7.15]])

    def test_preview_for_other_stops_is_not_used(self):
        preview = self._fake_route([49.0, 7.0], [49.3, 7.3])      # made before a stop was added
        payload = {**self._payload([{"point": [49.1, 7.1], "dwell_min": 5}]), "route": preview}
        services.create_device(self.db, payload)
        self.assertEqual(self.calls[-1], [[49.1, 7.1]])

    def test_validation(self):
        with self.assertRaises(services.ServiceError):
            services.parse_stops([{"point": [49.1, 7.1], "dwell_min": -1}])
        with self.assertRaises(services.ServiceError):
            services.parse_stops([{"point": [149.1, 7.1], "dwell_min": 5}])
        with self.assertRaises(services.ServiceError):
            services.parse_stops([{"point": [49.1, 7.1], "dwell_min": 5}] * (services.MAX_STOPS + 1))
        self.assertEqual(services.parse_stops([{"point": ["49.1", "7.1"]}]),
                         [{"point": [49.1, 7.1], "dwell_min": 0}])

    def test_scenario_preview_counts_the_waiting_time(self):
        cfg = {"type": "interval", "every_min": 30, "from": "06:00", "to": "22:00", "round_trip": True,
               "dwell_min": 0}
        short = services.scenario_preview({"scenario": cfg, "duration_s": 300})
        long = services.scenario_preview({"scenario": cfg, "duration_s": 300, "stops_s": 1200})
        self.assertFalse(short["warnings"])
        self.assertTrue(any("longer than the interval" in w for w in long["warnings"]))


if __name__ == "__main__":
    unittest.main()
