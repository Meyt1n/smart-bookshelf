from __future__ import annotations

import importlib
import io
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
        transcribe_wav_bytes=lambda *_args, **_kwargs: "帮我取书",
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


class VoiceApiTests(unittest.TestCase):
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
        for module_name in ["app", "api.shelf", "api.voice", "api.chat"]:
            sys.modules.pop(module_name, None)
        app_module = importlib.import_module("app")
        self.app = app_module.create_app()
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()
        self._bootstrap_user()

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
                "username": "voice-admin",
                "password": "p@ssw0rd123",
                "name": "Voice User",
                "family_name": "Voice Family",
            },
        )
        self.assertEqual(register_resp.status_code, 200)

        login_resp = self.client.post(
            "/api/auth/login",
            json={"username": "voice-admin", "password": "p@ssw0rd123"},
        )
        self.assertEqual(login_resp.status_code, 200)

    def _post_voice_ingest(self, query: str):
        return self.client.post(
            f"/api/voice/ingest{query}",
            data={"audio": (io.BytesIO(b"RIFF...."), "audio.wav")},
            content_type="multipart/form-data",
        )

    def test_voice_ingest_returns_json_envelope_when_route_fails(self):
        with patch("api.voice.route_text", side_effect=RuntimeError("route exploded")):
            response = self._post_voice_ingest("?source=web&mode=command")

        self.assertEqual(response.status_code, 500)
        payload = response.get_json()
        self.assertFalse(payload["ok"])
        self.assertIsNone(payload["data"])
        self.assertIn("route exploded", payload["message"])

    def test_voice_ingest_keeps_success_response_when_tts_payload_fails(self):
        with patch("api.voice.transcribe_wav_bytes", return_value="小燕"), patch(
            "api.voice.has_wake_word", return_value=True
        ), patch("api.voice.tts_to_mp3_bytes", side_effect=RuntimeError("tts exploded")):
            response = self._post_voice_ingest("?source=web&mode=wake")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["intent"], "wake")
        self.assertEqual(payload["data"]["reply"], "我在")
        self.assertEqual(payload["data"]["audio_b64"], "")
        self.assertEqual(payload["data"]["audio_format"], "")


if __name__ == "__main__":
    unittest.main()
