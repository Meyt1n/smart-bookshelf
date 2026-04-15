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


def reset_auth_related_rows(db_path: str):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing_tables = {row[0] for row in cur.fetchall()}
    cur.execute("PRAGMA foreign_keys=OFF")
    for table_name in [
        "pair_codes",
        "cabinet_config",
        "account_user_rel",
        "user_sessions",
        "user_badges",
        "required_books",
        "reading_goals",
        "reading_events",
        "borrow_logs",
        "users",
        "families",
        "accounts",
        "recommendation_feedback",
        "recommendation_impressions",
    ]:
        if table_name in existing_tables:
            cur.execute(f"DELETE FROM {table_name}")
    conn.commit()
    conn.close()


def install_test_stubs():
    def stub_module(name: str, **attrs):
        module = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        sys.modules[name] = module

    stub_module(
        "ai.voice_module",
        speak=lambda *_args, **_kwargs: None,
        listen=lambda *_args, **_kwargs: "",
        listen_wake_only=lambda *_args, **_kwargs: False,
        transcribe_wav_bytes=lambda *_args, **_kwargs: "",
        tts_to_mp3_bytes=lambda *_args, **_kwargs: b"",
        tts_to_wav_bytes=lambda *_args, **_kwargs: b"",
        _has_wake=lambda *_args, **_kwargs: False,
    )
    stub_module(
        "ai.book_match_ai",
        chat_with_librarian=lambda *_args, **_kwargs: "stub reply",
        get_ai_reading_analysis=lambda *_args, **_kwargs: "stub insight",
        get_chat_history=lambda *_args, **_kwargs: [],
        clear_chat_history=lambda *_args, **_kwargs: None,
        _get_current_user_safe=lambda *_args, **_kwargs: None,
        get_or_create_book_by_ai=lambda *_args, **_kwargs: {"id": 1, "title": "Stub Book"},
        trigger_action_chat=lambda *_args, **_kwargs: "stub action",
        ollama_call=lambda *_args, **_kwargs: {},
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
    stub_module(
        "services.shelf_service",
        store_via_ocr=lambda *_args, **_kwargs: (True, "stored", "stub action"),
        take_by_text=lambda *_args, **_kwargs: (True, "taken", "stub action"),
        store_from_image_bytes=lambda *_args, **_kwargs: (True, "stored", "stub action"),
    )
    stub_module(
        "services.voice_service",
        push_voice_event=lambda *_args, **_kwargs: None,
        get_voice_events=lambda *_args, **_kwargs: [],
        route_text=lambda text, **_kwargs: {"ok": True, "text": text, "reply": "stub reply"},
        build_voice_hints=lambda: [],
        wake_loop=lambda: None,
    )
    stub_module(
        "services.ai_dispatch",
        dispatch_with_model=lambda *_args, **_kwargs: ("", [], ""),
        execute_model_commands=lambda *_args, **_kwargs: {"ok": True},
    )


class RecommendationApiTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tempdir.name, "bookshelf.db")
        shutil.copyfile(SOURCE_DB_PATH, self.db_path)
        reset_auth_related_rows(self.db_path)

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

        install_test_stubs()
        for module_name in [
            "app",
            "api.shelf",
            "api.voice",
            "api.chat",
            "api.recommendations",
            "services.recommendation_service",
        ]:
            sys.modules.pop(module_name, None)
        app_module = importlib.import_module("app")
        self.app = app_module.create_app()
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.tempdir.cleanup()

    def _bootstrap_user(self):
        issue_resp = self.client.post("/api/auth/pair/issue")
        self.assertEqual(issue_resp.status_code, 200)
        pair_code = issue_resp.get_json()["data"]["pair_code"]

        exchange_resp = self.client.post("/api/auth/pair/exchange", json={"pair_code": pair_code})
        self.assertEqual(exchange_resp.status_code, 200)
        pair_token = exchange_resp.get_json()["data"]["pair_token"]

        register_resp = self.client.post(
            "/api/auth/register",
            json={
                "pair_token": pair_token,
                "username": "reco-admin",
                "password": "p@ssw0rd123",
                "name": "Reco User",
                "family_name": "Reco Family",
            },
        )
        self.assertEqual(register_resp.status_code, 200)
        payload = register_resp.get_json()["data"]
        return payload["token"], payload["user"]["id"]

    def _seed_user_interactions(self, user_id: int):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT id FROM books ORDER BY id LIMIT 4")
        book_ids = [row[0] for row in cur.fetchall()]
        if len(book_ids) < 3:
            conn.close()
            raise AssertionError("Seed database does not contain enough books")

        cur.execute(
            "INSERT INTO borrow_logs (book_id, action, user_id, action_time, source) VALUES (?, 'take', ?, CURRENT_TIMESTAMP, 'test')",
            (book_ids[0], user_id),
        )
        cur.execute(
            "INSERT INTO borrow_logs (book_id, action, user_id, action_time, source) VALUES (?, 'take', ?, CURRENT_TIMESTAMP, 'test')",
            (book_ids[1], user_id),
        )
        cur.execute(
            """
            INSERT INTO reading_events (user_id, event_type, book_id, event_time, source, metadata_json)
            VALUES (?, 'view_book', ?, CURRENT_TIMESTAMP, 'test', NULL)
            """,
            (user_id, book_ids[2]),
        )
        conn.commit()
        conn.close()

    def test_recommendation_endpoint_requires_auth(self):
        response = self.client.get("/api/recommendations")
        self.assertEqual(response.status_code, 401)
        payload = response.get_json()
        self.assertFalse(payload["ok"])

    def test_recommendations_returns_request_id_and_items(self):
        token, user_id = self._bootstrap_user()
        self._seed_user_interactions(user_id)

        response = self.client.get(
            "/api/recommendations?limit=5",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        data = payload["data"]
        self.assertIn("request_id", data)
        self.assertIn("items", data)
        self.assertLessEqual(len(data["items"]), 5)

    def test_feedback_endpoint_persists_feedback_and_event(self):
        token, user_id = self._bootstrap_user()
        self._seed_user_interactions(user_id)

        reco_resp = self.client.get(
            "/api/recommendations?limit=3",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(reco_resp.status_code, 200)
        reco_data = reco_resp.get_json()["data"]
        self.assertGreater(len(reco_data["items"]), 0)
        first_item = reco_data["items"][0]

        feedback_resp = self.client.post(
            "/api/recommendations/feedback",
            json={
                "request_id": reco_data["request_id"],
                "book_id": first_item["book_id"],
                "feedback_type": "click",
                "source": "test_case",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(feedback_resp.status_code, 200)
        payload = feedback_resp.get_json()
        self.assertTrue(payload["ok"])

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM recommendation_feedback WHERE user_id = ?", (user_id,))
        feedback_count = cur.fetchone()[0]
        cur.execute(
            "SELECT COUNT(*) FROM reading_events WHERE user_id = ? AND event_type = 'recommend_click'",
            (user_id,),
        )
        click_events = cur.fetchone()[0]
        conn.close()

        self.assertGreaterEqual(feedback_count, 1)
        self.assertGreaterEqual(click_events, 1)


if __name__ == "__main__":
    unittest.main()
