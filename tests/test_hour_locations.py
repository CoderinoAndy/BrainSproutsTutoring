import sqlite3
import unittest

from fastapi import HTTPException

import server


class HourLocationTests(unittest.TestCase):
    def setUp(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = server.dict_factory
        connection.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                display_name TEXT NOT NULL
            );
            CREATE TABLE hours (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                work_date TEXT NOT NULL,
                hours REAL NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                location TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            INSERT INTO users (display_name) VALUES ('Ada'), ('Grace');
            """
        )
        self.db = server.Database.__new__(server.Database)
        self.db.connection = connection

    def tearDown(self):
        self.db.close()

    def log(self, **overrides):
        body = {
            "user_ids": [1, 2],
            "work_date": "2026-11-04",
            "hours": 1.5,
            "description": " Tutoring session ",
            "location": "Hartman P.S.",
            **overrides,
        }
        server.create_hours(server.LogHoursBody(**body), self.db, {})

    def test_the_hour_locations_are_offered(self):
        self.assertEqual(
            server.hour_locations({}),
            ["Aurora Public Library", "Hartman P.S.", "Northern Lights P.S.", "CICS"],
        )

    def test_logged_hours_keep_location_and_description(self):
        self.log()
        entries = server.all_hours(self.db, {})
        self.assertEqual(len(entries), 2)
        for entry in entries:
            self.assertEqual(entry["location"], "Hartman P.S.")
            self.assertEqual(entry["description"], "Tutoring session")

        mine = server.my_hours(self.db, {"user_id": 1})
        self.assertEqual(mine[0]["location"], "Hartman P.S.")

    def test_logging_requires_a_known_location(self):
        for location in (None, "", "   ", "Somewhere Else"):
            with self.assertRaises(HTTPException) as raised:
                self.log(location=location)
            self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(server.all_hours(self.db, {}), [])

    def test_editing_can_change_or_leave_location_unassigned(self):
        self.db.execute(
            "INSERT INTO hours (user_id, work_date, hours) VALUES (1, '2026-10-01', 2)"
        )
        entry_id = self.db.execute("SELECT id FROM hours").fetchone()["id"]

        server.update_hours(
            entry_id,
            server.HoursBody(work_date="2026-10-01", hours=2, location=None),
            self.db,
            {},
        )
        self.assertIsNone(self.db.execute("SELECT location FROM hours").fetchone()["location"])

        server.update_hours(
            entry_id,
            server.HoursBody(
                work_date="2026-10-01", hours=2, location="Northern Lights P.S."
            ),
            self.db,
            {},
        )
        self.assertEqual(
            self.db.execute("SELECT location FROM hours").fetchone()["location"],
            "Northern Lights P.S.",
        )

        with self.assertRaises(HTTPException):
            server.update_hours(
                entry_id,
                server.HoursBody(work_date="2026-10-01", hours=2, location="Nowhere"),
                self.db,
                {},
            )


if __name__ == "__main__":
    unittest.main()
