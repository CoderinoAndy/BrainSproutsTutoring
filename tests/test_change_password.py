import sqlite3
import unittest

import bcrypt
from fastapi import HTTPException

import server


class ChangePasswordTests(unittest.TestCase):
    def setUp(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = server.dict_factory
        connection.execute(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                password_hash TEXT NOT NULL
            )
            """
        )
        old_hash = bcrypt.hashpw(b"old-password", bcrypt.gensalt()).decode()
        connection.execute(
            "INSERT INTO users (id, password_hash) VALUES (?, ?)", (7, old_hash)
        )
        connection.commit()
        self.db = server.Database.__new__(server.Database)
        self.db.connection = connection
        self.auth = {"user_id": 7, "is_admin": False}

    def tearDown(self):
        self.db.close()

    def stored_hash(self):
        return self.db.execute(
            "SELECT password_hash FROM users WHERE id = ?", (7,)
        ).fetchone()["password_hash"]

    def test_current_password_must_be_correct(self):
        original_hash = self.stored_hash()
        with self.assertRaises(HTTPException) as context:
            server.change_password(
                server.ChangePasswordBody(
                    current_password="wrong-password",
                    new_password="new-password",
                    confirm_password="new-password",
                ),
                self.db,
                self.auth,
            )

        self.assertEqual(context.exception.status_code, 400)
        self.assertEqual(context.exception.detail, "Current password is incorrect")
        self.assertEqual(self.stored_hash(), original_hash)

    def test_confirmation_must_match(self):
        original_hash = self.stored_hash()
        with self.assertRaises(HTTPException) as context:
            server.change_password(
                server.ChangePasswordBody(
                    current_password="old-password",
                    new_password="new-password",
                    confirm_password="different-password",
                ),
                self.db,
                self.auth,
            )

        self.assertEqual(context.exception.detail, "New passwords do not match")
        self.assertEqual(self.stored_hash(), original_hash)

    def test_valid_change_replaces_the_universal_login_hash(self):
        response = server.change_password(
            server.ChangePasswordBody(
                current_password="old-password",
                new_password="new-password",
                confirm_password="new-password",
            ),
            self.db,
            self.auth,
        )

        updated_hash = self.stored_hash()
        self.assertEqual(response["message"], "Password changed successfully")
        self.assertTrue(server.password_matches("new-password", updated_hash))
        self.assertFalse(server.password_matches("old-password", updated_hash))

    def test_verification_does_not_change_the_hash(self):
        original_hash = self.stored_hash()
        response = server.verify_current_password(
            server.PasswordVerificationBody(current_password="old-password"),
            self.db,
            self.auth,
        )

        self.assertEqual(response, {"verified": True})
        self.assertEqual(self.stored_hash(), original_hash)


if __name__ == "__main__":
    unittest.main()
