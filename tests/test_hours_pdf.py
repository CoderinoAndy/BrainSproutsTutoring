import sqlite3
import unittest
from datetime import date

import server


class HoursPdfTests(unittest.TestCase):
    def setUp(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = server.dict_factory
        connection.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                display_name TEXT NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0,
                is_approved INTEGER NOT NULL DEFAULT 1,
                executive_title TEXT,
                created_at TEXT DEFAULT (datetime('now'))
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
            INSERT INTO users (username, display_name, is_admin, is_approved) VALUES
                ('grace', 'grace', 0, 1),
                ('ada', 'Ada', 0, 1),
                ('boss', 'Boss', 1, 1),
                ('newbie', 'Newbie', 0, 0),
                ('idle', 'Idle', 0, 1);
            INSERT INTO hours (user_id, work_date, hours, location) VALUES
                (1, '2026-11-01', 1.5, 'Hartman P.S.'),
                (1, '2026-11-02', 2.25, 'Hartman P.S.'),
                (1, '2026-11-03', 1, 'CICS'),
                (2, '2026-11-01', 3, 'Aurora Public Library');
            """
        )
        self.db = server.Database.__new__(server.Database)
        self.db.connection = connection

    def tearDown(self):
        self.db.close()

    def test_totals_list_every_tutor_alphabetically(self):
        columns, rows = server.hours_totals(self.db)
        self.assertEqual(columns, server.HOUR_LOCATIONS)
        # Admins and unapproved accounts are left out; tutors without hours stay in.
        self.assertEqual([row["name"] for row in rows], ["Ada", "grace", "Idle"])

    def test_totals_add_up_hours_per_location(self):
        _columns, rows = server.hours_totals(self.db)
        grace = rows[1]
        self.assertEqual(grace["by_location"], {"Hartman P.S.": 3.75, "CICS": 1.0})
        self.assertEqual(grace["total"], 4.75)
        self.assertEqual(rows[2]["total"], 0)

    def test_unassigned_hours_get_their_own_column(self):
        self.db.execute("INSERT INTO hours (user_id, work_date, hours) VALUES (2, '2025-01-01', 2)")
        self.db.execute("INSERT INTO hours (user_id, work_date, hours, location) VALUES (2, '2025-01-02', 1, 'Old Site')")
        columns, rows = server.hours_totals(self.db)
        self.assertEqual(columns, [*server.HOUR_LOCATIONS, server.NO_HOUR_LOCATION])
        self.assertEqual(rows[0]["by_location"][server.NO_HOUR_LOCATION], 3)
        self.assertEqual(rows[0]["total"], 6)

    def test_admin_with_logged_hours_is_included(self):
        self.db.execute("INSERT INTO hours (user_id, work_date, hours, location) VALUES (3, '2026-11-01', 2, 'CICS')")
        _columns, rows = server.hours_totals(self.db)
        self.assertEqual([row["name"] for row in rows], ["Ada", "Boss", "grace", "Idle"])

    def test_download_is_a_pdf_attachment(self):
        response = server.download_hours_totals(self.db, {})
        self.assertEqual(response.media_type, "application/pdf")
        self.assertRegex(
            response.headers["content-disposition"],
            r'^attachment; filename="brainsprouts-hours-by-tutor-\d{4}-\d{2}-\d{2}\.pdf"$',
        )
        self.assertTrue(response.body.startswith(b"%PDF-"))

    def test_pdf_is_built_without_tutors_and_with_many(self):
        self.assertTrue(server.hours_totals_pdf(server.HOUR_LOCATIONS, [], date(2026, 11, 5)).startswith(b"%PDF-"))
        rows = [{"name": f"Tutor {number}", "by_location": {"CICS": 1.5}, "total": 1.5} for number in range(120)]
        self.assertTrue(server.hours_totals_pdf(server.HOUR_LOCATIONS, rows, date(2026, 11, 5)).startswith(b"%PDF-"))

    def test_names_outside_latin1_do_not_break_the_pdf(self):
        self.assertEqual(server.pdf_text("Zoë Nuñez"), "Zoë Nuñez")
        self.assertEqual(server.pdf_text("Łukasz Dvořák"), "?ukasz Dvorák")
        rows = [{"name": "李雷 Dvořák", "by_location": {}, "total": 0.0}]
        self.assertTrue(server.hours_totals_pdf(server.HOUR_LOCATIONS, rows, date(2026, 11, 5)).startswith(b"%PDF-"))


if __name__ == "__main__":
    unittest.main()
