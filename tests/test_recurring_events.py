import sqlite3
import unittest

from fastapi import HTTPException

import server


class RecurringEventCreationTests(unittest.TestCase):
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
            """
        )
        self.db = server.Database.__new__(server.Database)
        self.db.connection = connection

    def tearDown(self):
        self.db.close()

    def event_body(self, **overrides):
        values = {
            "title": "Tutoring Session",
            "event_date": "2026-09-09",
            "start_time": "16:00",
            "end_time": "17:00",
            "max_capacity": 15,
            "location_tag": "Aurora Public Library",
            "repeat_count": 1,
        }
        values.update(overrides)
        return server.CreateEventBody(**values)

    def test_creates_independent_weekly_events(self):
        result = server.create_event(
            self.event_body(repeat_count=4), self.db, {}
        )

        events = self.db.execute(
            "SELECT * FROM events ORDER BY event_date"
        ).fetchall()
        expected_dates = [
            "2026-09-09",
            "2026-09-16",
            "2026-09-23",
            "2026-09-30",
        ]
        self.assertEqual(result, {"created_count": 4, "dates": expected_dates})
        self.assertEqual([event["event_date"] for event in events], expected_dates)
        self.assertTrue(
            all(event["location_tag"] == "Aurora Public Library" for event in events)
        )

        server.update_event(
            events[1]["id"],
            server.EventBody(
                title="Tutoring Session",
                start_time="16:30",
                end_time="17:30",
                max_capacity=15,
                location_tag="Aurora Public Library",
            ),
            self.db,
            {},
        )
        updated_events = self.db.execute(
            "SELECT * FROM events ORDER BY event_date"
        ).fetchall()
        self.assertEqual(updated_events[0]["start_time"], "16:00")
        self.assertEqual(updated_events[1]["start_time"], "16:30")

    def test_conflicting_date_rejects_entire_batch(self):
        self.db.execute(
            "INSERT INTO events "
            "(title, event_date, start_time, end_time, max_capacity, location_tag) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("Existing", "2026-09-16", "16:00", "17:00", 15, None),
        )
        self.db.commit()

        with self.assertRaises(HTTPException) as raised:
            server.create_event(
                self.event_body(repeat_count=3), self.db, {}
            )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("2026-09-16", raised.exception.detail)
        events = self.db.execute("SELECT * FROM events").fetchall()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "Existing")

    def test_rejects_end_time_before_start_time(self):
        with self.assertRaises(HTTPException) as raised:
            server.create_event(
                self.event_body(start_time="17:00", end_time="16:00"),
                self.db,
                {},
            )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(raised.exception.detail, "End time must be after start time")
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) AS count FROM events").fetchone()["count"],
            0,
        )


if __name__ == "__main__":
    unittest.main()
