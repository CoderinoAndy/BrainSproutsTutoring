import sqlite3
import unittest

import server


class FixedDistanceTests(unittest.TestCase):
    def test_matrix_contains_all_twenty_five_routes(self):
        school_names = [school for school, _address in server.HIGH_SCHOOLS]
        self.assertEqual(set(server.FIXED_ROUTE_MATRIX), set(server.EVENT_LOCATIONS))

        for location_tag in server.EVENT_LOCATIONS:
            self.assertEqual(
                set(server.FIXED_ROUTE_MATRIX[location_tag]), set(school_names)
            )
            distances = server.fixed_driving_distances(location_tag)
            self.assertEqual(
                [distance["school"] for distance in distances], school_names
            )
            self.assertEqual(len(distances), 5)
            for distance in distances:
                self.assertGreater(distance["duration_seconds"], 0)
                self.assertGreater(distance["distance_meters"], 0)
                self.assertIn("google.com/maps/dir/", distance["google_maps_url"])

    def test_known_fixed_route_is_formatted_for_display(self):
        distances = server.fixed_driving_distances("Aurora Public Library")
        denison_route = distances[0]
        self.assertEqual(denison_route["school"], "Denison High School")
        self.assertEqual(denison_route["duration_text"], "14 min")
        self.assertEqual(denison_route["distance_text"], "8.8 km")


class EventLocationPersistenceTests(unittest.TestCase):
    def setUp(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = server.dict_factory
        connection.executescript(
            """
            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                event_date TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                max_capacity INTEGER NOT NULL,
                location_tag TEXT
            );
            CREATE TABLE rsvps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                event_date TEXT NOT NULL,
                status TEXT NOT NULL,
                UNIQUE(user_id, event_date)
            );
            """
        )
        self.db = server.Database.__new__(server.Database)
        self.db.connection = connection

    def tearDown(self):
        self.db.close()

    def test_create_change_and_clear_location_assignment(self):
        server.create_event(
            server.CreateEventBody(
                title="Tutoring Session",
                event_date="2026-10-07",
                start_time="16:00",
                end_time="17:00",
                max_capacity=15,
                location_tag="Aurora Public Library",
            ),
            self.db,
            {},
        )

        event = self.db.execute("SELECT * FROM events").fetchone()
        self.assertEqual(event["location_tag"], "Aurora Public Library")
        location_payload = server.event_location_payload(event)
        self.assertEqual(location_payload["location_tag"], "Aurora Public Library")
        self.assertEqual(len(location_payload["distances"]), 5)

        server.update_event(
            event["id"],
            server.EventBody(
                title="Updated Session",
                start_time="16:30",
                end_time="17:30",
                max_capacity=20,
                location_tag="Denne Public School",
            ),
            self.db,
            {},
        )
        updated_event = self.db.execute("SELECT * FROM events").fetchone()
        self.assertEqual(updated_event["location_tag"], "Denne Public School")

        server.update_event(
            event["id"],
            server.EventBody(
                title="Updated Session",
                start_time="16:30",
                end_time="17:30",
                max_capacity=20,
                location_tag=None,
            ),
            self.db,
            {},
        )
        cleared_event = self.db.execute("SELECT * FROM events").fetchone()
        self.assertIsNone(cleared_event["location_tag"])
        self.assertEqual(server.event_location_payload(cleared_event)["distances"], [])


if __name__ == "__main__":
    unittest.main()
