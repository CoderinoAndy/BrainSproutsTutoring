import os
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

import server


class RenameTutorTests(unittest.TestCase):
    def setUp(self):
        handle, path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.db_path = Path(path)
        self.original_db_path = server.DB_PATH
        server.DB_PATH = self.db_path
        server.init_db()
        self.db = server.Database()
        server.create_user(
            server.UserBody(username="ayu", password="secret123", display_name="AndyYu"),
            self.db,
            {},
        )
        self.tutor_id = self.db.execute(
            "SELECT id FROM users WHERE username = ?", ("ayu",)
        ).fetchone()["id"]

    def tearDown(self):
        self.db.close()
        server.DB_PATH = self.original_db_path
        self.db_path.unlink()

    def rename(self, name, user_id=None):
        return server.update_display_name(
            self.tutor_id if user_id is None else user_id,
            server.DisplayNameBody(display_name=name),
            self.db,
            {},
        )

    def stored_name(self):
        return self.db.execute(
            "SELECT display_name FROM users WHERE id = ?", (self.tutor_id,)
        ).fetchone()["display_name"]

    def test_rename_updates_the_name_everywhere_it_is_listed(self):
        self.assertEqual(self.rename("Andy Yu"), {"display_name": "Andy Yu"})
        self.assertEqual(self.stored_name(), "Andy Yu")
        listed = {u["username"]: u["display_name"] for u in server.list_users(self.db, {})}
        self.assertEqual(listed["ayu"], "Andy Yu")

    def test_extra_spaces_are_tidied(self):
        self.assertEqual(self.rename("   Andy    Yu  ")["display_name"], "Andy Yu")

    def test_blank_or_overlong_names_are_rejected(self):
        for name in ("", "    ", "x" * 101):
            with self.assertRaises(HTTPException) as raised:
                self.rename(name)
            self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(self.stored_name(), "AndyYu")

    def test_unknown_tutor_is_a_404(self):
        with self.assertRaises(HTTPException) as raised:
            self.rename("Someone", user_id=999999)
        self.assertEqual(raised.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
