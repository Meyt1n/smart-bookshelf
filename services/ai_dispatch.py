from __future__ import annotations

import json
import re

from ai.book_match_ai import (
    _build_user_persona,
    _get_current_user_safe,
    _get_tone_guide,
    ollama_call,
)
from db.shelf_ops import get_all_compartments, get_book_in_compartment
from services.shelf_service import store_from_image_bytes, take_by_cid, take_by_text


def build_bookshelf_status_for_prompt():
    items = []
    try:
        for cid, _x, _y, status in get_all_compartments():
            if status != "occupied":
                continue
            title = get_book_in_compartment(cid)
            if title:
                items.append(f"{cid}:{title}")
    except Exception:
        pass

    if not items:
        return "bookshelf is empty"
    return "stored books (cid:title) = " + ", ".join(items[:40])


def parse_model_response(model_output):
    if isinstance(model_output, str):
        model_output = re.sub(r"```json\s*([\s\S]*?)\s*```", r"\1", model_output).strip()

    try:
        data = json.loads(model_output)
    except Exception:
        return "", []

    response_text = data.get("response", "")
    commands = data.get("commands", [])
    if not isinstance(commands, list):
        commands = []
    return response_text, commands


def dispatch_with_model(user_text: str):
    user = _get_current_user_safe()
    uname = (user or {}).get("name", "reader")
    persona = _build_user_persona()
    tone = _get_tone_guide(user)

    prompt = f"""
You are the smart bookshelf assistant.
Return JSON only.

Shape:
{{
  "response": "short user-facing text",
  "commands": [
    {{"device":"bookshelf","action":"take|store|status","book":"optional title","cid":optional integer}}
  ]
}}

Rules:
- take/borrow/get a book -> action=take
- store/return a book -> action=store
- shelf status question -> action=status
- if no device action is needed, return an empty commands list

User name: {uname}
Persona: {persona}
Tone guide: {tone}
Current shelf state: {build_bookshelf_status_for_prompt()}
User text: {user_text}
"""
    raw_reply = ollama_call(prompt)
    response_text, commands = parse_model_response(raw_reply)
    return response_text, commands, raw_reply


def execute_model_commands(commands, image_bytes=None, push_event_fn=None):
    result = {
        "ok": True,
        "intent": "chat",
        "need_image": False,
        "msg": "",
        "ai_reply": "",
        "reply": "",
        "picked": None,
        "dispatch_request": None,
        "commit_request": None,
    }

    for cmd in commands:
        device = (cmd.get("device") or "").lower()
        action = (cmd.get("action") or "").lower()
        if device and device != "bookshelf":
            continue

        if action == "store":
            result["intent"] = "store"
            if image_bytes:
                shelf_result = store_from_image_bytes(image_bytes, speak_out=False)
                ok, msg, ai_reply = shelf_result
                result.update(
                    {
                        "ok": ok,
                        "msg": msg,
                        "ai_reply": ai_reply or "",
                        "reply": getattr(shelf_result, "reply", None) or ai_reply or "",
                        "picked": getattr(shelf_result, "picked", None),
                        "dispatch_request": getattr(shelf_result, "dispatch_request", None),
                        "commit_request": getattr(shelf_result, "commit_request", None),
                    }
                )
            else:
                result["need_image"] = True
                result["msg"] = "image required for store"
            break

        if action == "take":
            result["intent"] = "take"
            book = (cmd.get("book") or "").strip()
            cid = cmd.get("cid")
            if book:
                shelf_result = take_by_text(book, speak_out=False)
            elif cid is not None:
                shelf_result = take_by_cid(cid, speak_out=False)
            else:
                shelf_result = None

            if shelf_result is None:
                result["ok"] = False
                result["msg"] = "missing book title or cid"
            else:
                ok, msg, ai_reply = shelf_result
                result.update(
                    {
                        "ok": ok,
                        "msg": msg,
                        "ai_reply": ai_reply or "",
                        "reply": getattr(shelf_result, "reply", None) or ai_reply or "",
                        "picked": getattr(shelf_result, "picked", None),
                        "dispatch_request": getattr(shelf_result, "dispatch_request", None),
                        "commit_request": getattr(shelf_result, "commit_request", None),
                    }
                )
            break

        if action == "status":
            result["intent"] = "status"
            result["msg"] = build_bookshelf_status_for_prompt()
            result["reply"] = result["msg"]
            break

    if push_event_fn:
        if result["msg"]:
            push_event_fn("log", result["msg"])
        if result["ai_reply"]:
            push_event_fn("assistant", result["ai_reply"])

    return result
