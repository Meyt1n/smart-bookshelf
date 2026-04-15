from __future__ import annotations

import importlib
import os
import shutil
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
SOURCE_DB_PATH = os.path.join(REPO_ROOT, "data", "bookshelf.db")
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


def install_service_stubs():
    def stub_module(name: str, **attrs):
        module = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        sys.modules[name] = module

    stub_module(
        "ai.book_match_ai",
        get_or_create_book_by_ai=lambda *_args, **_kwargs: {"id": 1, "title": "Stub Book"},
        trigger_action_chat=lambda action, title, **_kwargs: f"{action}:{title}",
    )
    stub_module(
        "ocr.paddle_ocr",
        ocr_image=lambda *_args, **_kwargs: [],
        stabilize_ocr_texts=lambda texts, **_kwargs: texts,
    )
    stub_module(
        "ocr.video_ocr",
        recognize_book_from_camera=lambda *_args, **_kwargs: None,
    )


class MotionPrepareCommitTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tempdir.name, "bookshelf.db")
        shutil.copyfile(SOURCE_DB_PATH, self.db_path)

        self.patchers = []
        for module_name, attr_name in [
            ("config", "DB_PATH"),
            ("extensions", "DB_PATH"),
            ("db.user_ops", "DB_PATH"),
            ("db.shelf_ops", "DB_PATH"),
            ("db.book_match", "DB_PATH"),
        ]:
            module = importlib.import_module(module_name)
            patcher = patch.object(module, attr_name, self.db_path)
            patcher.start()
            self.patchers.append(patcher)

        install_service_stubs()
        sys.modules.pop("services.shelf_service", None)
        self.shelf_service = importlib.import_module("services.shelf_service")
        self.shelf_ops = importlib.import_module("db.shelf_ops")

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.tempdir.cleanup()

    def _first_book_title(self):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT title FROM books ORDER BY id LIMIT 1")
        row = cur.fetchone()
        conn.close()
        if not row:
            raise AssertionError("Seed database has no books")
        return row[0]

    def test_store_prepare_then_commit(self):
        title = self._first_book_title()

        prepared = self.shelf_service.store_from_ocr_texts([title])
        self.assertTrue(prepared.ok)
        self.assertEqual(prepared.intent, "store")
        self.assertIsNotNone(prepared.dispatch_request)
        self.assertIsNotNone(prepared.commit_request)

        cid = prepared.commit_request["cid"]
        self.assertIsNone(self.shelf_ops.get_book_in_compartment(cid))

        committed = self.shelf_service.commit_prepared_action(
            prepared.commit_request["action"],
            cid=prepared.commit_request["cid"],
            title=prepared.commit_request["title"],
            book_id=prepared.commit_request["book_id"],
        )
        self.assertTrue(committed.ok)
        self.assertEqual(committed.intent, "store")
        self.assertEqual(self.shelf_ops.get_book_in_compartment(cid), title)

    def test_take_prepare_then_commit(self):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT c.compartment_id, b.title
            FROM compartments c
            JOIN stored_books s ON s.compartment_id = c.compartment_id
            JOIN books b ON b.id = s.book_id
            ORDER BY c.compartment_id
            LIMIT 1
            """
        )
        row = cur.fetchone()
        conn.close()
        if not row:
            raise AssertionError("Seed database has no stored books to take")

        cid, title = int(row[0]), row[1]
        prepared = self.shelf_service.take_by_cid(cid, title=title)
        self.assertTrue(prepared.ok)
        self.assertEqual(prepared.intent, "take")
        self.assertEqual(prepared.dispatch_request["cid"], cid)

        committed = self.shelf_service.commit_prepared_action(
            prepared.commit_request["action"],
            cid=prepared.commit_request["cid"],
            title=prepared.commit_request["title"],
        )
        self.assertTrue(committed.ok)
        self.assertEqual(committed.intent, "take")
        self.assertIsNone(self.shelf_ops.get_book_in_compartment(cid))


if __name__ == "__main__":
    unittest.main()
