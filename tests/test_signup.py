import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

import server


class SignupApprovalTests(unittest.TestCase):
    def setUp(self):
        handle, path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.db_path = Path(path)
        self.original_db_path = server.DB_PATH
        server.DB_PATH = self.db_path
        server.init_db()
        self.db = server.Database()

    def tearDown(self):
        self.db.close()
        server.DB_PATH = self.original_db_path
        self.db_path.unlink()

    def signup(self, username="AdaLovelace", password="secret123", confirm=None):
        body = server.SignupBody(
            username=username,
            password=password,
            confirm_password=password if confirm is None else confirm,
        )
        return server.signup(body, self.db)

    def login(self, username="AdaLovelace", password="secret123"):
        return server.login(server.LoginBody(username=username, password=password), self.db)

    def pending_usernames(self):
        return [row["username"] for row in server.list_pending_users(self.db, {})]

    def approved_usernames(self):
        return [row["username"] for row in server.list_users(self.db, {})]

    def test_new_account_cannot_sign_in_until_approved(self):
        self.signup()
        self.assertEqual(self.pending_usernames(), ["AdaLovelace"])
        self.assertNotIn("AdaLovelace", self.approved_usernames())

        with self.assertRaises(HTTPException) as raised:
            self.login()
        self.assertEqual(raised.exception.status_code, 403)

        pending_id = server.list_pending_users(self.db, {})[0]["id"]
        server.approve_pending_user(pending_id, self.db, {})

        self.assertEqual(self.pending_usernames(), [])
        self.assertIn("AdaLovelace", self.approved_usernames())
        result = self.login()
        self.assertEqual(result["user"]["username"], "AdaLovelace")
        self.assertFalse(result["user"]["is_admin"])

    def test_pending_account_with_wrong_password_is_not_revealed(self):
        self.signup()
        with self.assertRaises(HTTPException) as raised:
            self.login(password="wrong-password")
        self.assertEqual(raised.exception.status_code, 401)

    def test_denying_deletes_the_request_and_frees_the_username(self):
        self.signup()
        pending_id = server.list_pending_users(self.db, {})[0]["id"]
        server.deny_pending_user(pending_id, self.db, {})

        self.assertEqual(self.pending_usernames(), [])
        with self.assertRaises(HTTPException) as raised:
            self.login()
        self.assertEqual(raised.exception.status_code, 401)
        self.signup()
        self.assertEqual(self.pending_usernames(), ["AdaLovelace"])

    def test_signup_rejects_bad_input(self):
        cases = [
            ({"confirm": "different1"}, 400),
            ({"password": "short"}, 400),
            ({"password": "x" * 73}, 400),
            ({"username": "  ab "}, 400),
            ({"username": "a" * 41}, 400),
            ({"username": "andyalbert"}, 409),
        ]
        for overrides, status in cases:
            with self.assertRaises(HTTPException) as raised:
                self.signup(**overrides)
            self.assertEqual(raised.exception.status_code, status, overrides)
        self.assertEqual(self.pending_usernames(), [])

    def test_duplicate_signup_is_rejected(self):
        self.signup()
        with self.assertRaises(HTTPException) as raised:
            self.signup(username=" adalovelace ")
        self.assertEqual(raised.exception.status_code, 409)

    def test_approved_accounts_cannot_be_approved_or_denied_again(self):
        admin_id = self.db.execute(
            "SELECT id FROM users WHERE username = 'AndyAlbert'"
        ).fetchone()["id"]
        for action in (server.approve_pending_user, server.deny_pending_user):
            with self.assertRaises(HTTPException) as raised:
                action(admin_id, self.db, {})
            self.assertEqual(raised.exception.status_code, 404)
        self.assertIn("AndyAlbert", self.approved_usernames())

    def test_accounts_created_by_an_admin_are_approved_immediately(self):
        server.create_user(
            server.UserBody(username="grace", password="secret123", display_name="Grace"),
            self.db,
            {},
        )
        self.assertEqual(self.login("grace")["user"]["display_name"], "Grace")

    def test_existing_databases_keep_their_accounts_approved(self):
        self.db.close()
        self.db_path.unlink()
        legacy = sqlite3.connect(self.db_path)
        legacy.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                display_name TEXT NOT NULL,
                is_admin INTEGER DEFAULT 0,
                executive_title TEXT NOT NULL DEFAULT 'Jr. Tutor',
                created_at TEXT DEFAULT (datetime('now'))
            );
            INSERT INTO users (username, password_hash, display_name)
            VALUES ('legacy', 'not-a-real-hash', 'Legacy Tutor');
            """
        )
        legacy.commit()
        legacy.close()

        server.init_db()
        self.db = server.Database()
        self.assertIn("legacy", self.approved_usernames())
        self.assertEqual(self.pending_usernames(), [])


if __name__ == "__main__":
    unittest.main()
