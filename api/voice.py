"""
api/voice.py
语音相关路由：语音聊天、OCR 图像、语音输入、TTS、事件流。
"""

import base64
import json as _json
import sqlite3
import threading
import time

from flask import Blueprint, jsonify, request, Response, stream_with_context

from ai.voice_module import (
    listen,
    transcribe_wav_bytes,
    tts_to_mp3_bytes,
    tts_to_wav_bytes,
)
from ai.book_match_ai import chat_with_librarian, trigger_action_chat
from services.voice_intent import normalize_voice_text, has_wake_word, strip_wake_words
from services.voice_service import (
    push_voice_event,
    get_voice_events,
    route_text,
    build_voice_hints,
)
from services.shelf_service import store_from_image_bytes
from config import DB_PATH

voice_bp = Blueprint("voice", __name__)


def _shelf_result_payload(result):
    ok, msg, ai_reply = result
    payload = {
        "ok": ok,
        "msg": msg,
        "ai_reply": ai_reply,
    }
    for key in ("intent", "reply", "need_image", "picked", "dispatch_request", "commit_request"):
        value = getattr(result, key, None)
        if value not in (None, "", False):
            payload[key] = value
    return payload


def _build_tts_audio_payload(text):
    reply = (text or "").strip()
    if not reply:
        return "", ""
    try:
        mp3_bytes = tts_to_mp3_bytes(reply)
        if mp3_bytes:
            return base64.b64encode(mp3_bytes).decode("ascii"), "mp3"
        wav_bytes = tts_to_wav_bytes(reply)
        if wav_bytes:
            return base64.b64encode(wav_bytes).decode("ascii"), "wav"
    except Exception as exc:
        print("[voice tts payload error]", exc)
    return "", ""


def _attach_tts_audio(result):
    payload = dict(result or {})
    audio_b64, audio_format = _build_tts_audio_payload(payload.get("reply"))
    payload["audio_b64"] = audio_b64
    payload["audio_format"] = audio_format
    return payload


def _latest_borrow_log_id():
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT COALESCE(MAX(id), 0) FROM borrow_logs")
        latest = int(cur.fetchone()[0] or 0)
        conn.close()
        return latest
    except Exception:
        return 0


def _borrow_log_events_after(last_id):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """
            SELECT l.id, l.action, l.compartment_id, b.title
            FROM borrow_logs l
            JOIN books b ON b.id = l.book_id
            WHERE l.id > ?
            ORDER BY l.id ASC
            LIMIT 20
            """,
            (int(last_id or 0),),
        )
        rows = cur.fetchall()
        conn.close()
    except Exception:
        return [], int(last_id or 0)

    events = []
    latest = int(last_id or 0)
    for row in rows:
        latest = max(latest, int(row["id"]))
        action = row["action"]
        action_text = "已存入" if action == "store" else "已取出"
        title = row["title"] or "未知图书"
        cid = row["compartment_id"]
        suffix = f"（{cid}号格）" if cid is not None else ""
        op_text = f"{action_text}《{title}》{suffix}"
        try:
            ai_reply = trigger_action_chat(action, title, speak_out=False)
        except Exception as exc:
            print("[shelf watch ai reply error]", exc)
            ai_reply = op_text
        events.append(
            {
                "role": "assistant",
                "text": ai_reply or op_text,
                "ts": time.time(),
                "source": "shelf_watch",
                "intent": action,
                "action": action,
                "title": title,
                "cid": cid,
                "op_text": op_text,
                "log_id": int(row["id"]),
            }
        )
    return events, latest


@voice_bp.route("/api/camera/snapshot", methods=["GET"])
def api_camera_snapshot():
    try:
        from ocr.video_ocr import encode_frame_as_jpeg, open_camera_capture

        cap, frame = open_camera_capture()
        if cap is None or frame is None:
            return jsonify({"ok": False, "msg": "camera unavailable"}), 503
        try:
            image = encode_frame_as_jpeg(frame, quality=90)
        finally:
            cap.release()
        return Response(
            image,
            mimetype="image/jpeg",
            headers={"Cache-Control": "no-store"},
        )
    except Exception as exc:
        return jsonify({"ok": False, "msg": f"camera snapshot failed: {exc}"}), 500


@voice_bp.route("/api/camera/stream", methods=["GET"])
def api_camera_stream():
    fps = max(1, min(int(request.args.get("fps", 8)), 20))
    delay = 1.0 / fps

    def generate():
        from ocr.video_ocr import encode_frame_as_jpeg, open_camera_capture

        cap, frame = open_camera_capture()
        if cap is None:
            return

        try:
            while True:
                if frame is None:
                    ret, frame = cap.read()
                    if not ret or frame is None:
                        break

                image = encode_frame_as_jpeg(frame, quality=82)
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Cache-Control: no-store\r\n\r\n" + image + b"\r\n"
                )
                frame = None
                time.sleep(delay)
        finally:
            cap.release()

    return Response(
        stream_with_context(generate()),
        mimetype="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@voice_bp.route("/api/voice_chat", methods=["POST"])
def api_voice_chat():
    try:
        text = listen(timeout=8, phrase_time_limit=1.2, hints=build_voice_hints())
        if not text:
            return jsonify({"ok": False, "msg": "没听清，再说一次吧"}), 200

        from ai.voice_module import speak
        reply = chat_with_librarian(text)
        threading.Thread(target=speak, args=(reply,), daemon=True).start()
        return jsonify({"ok": True, "text": text, "reply": reply})
    except Exception as exc:
        return jsonify({"ok": False, "msg": str(exc)}), 500


@voice_bp.route("/api/ocr/ingest", methods=["POST"])
def api_ocr_ingest():
    try:
        image = None
        if "image" in request.files:
            image = request.files["image"].read()
        elif request.data:
            image = request.data

        if not image:
            return jsonify({"ok": False, "msg": "image is required"}), 400

        want_audio = request.args.get("audio") == "1" or request.form.get("audio") == "1"
        scan_spine = request.args.get("scan_spine") == "1" or request.form.get("scan_spine") == "1"
        source = (request.args.get("source") or request.form.get("source") or "").strip().lower()
        push_events = source not in ("ui", "web")

        shelf_result = store_from_image_bytes(image, speak_out=False, scan_spine=scan_spine)
        ok, msg, ai_reply = shelf_result
        if push_events:
            if msg:
                push_voice_event("log", msg)
            if ai_reply:
                push_voice_event("assistant", ai_reply)

        reply = getattr(shelf_result, "reply", None) or ai_reply or ""
        audio_b64 = ""
        audio_format = ""
        if want_audio and reply:
            audio_b64, audio_format = _build_tts_audio_payload(reply)

        return jsonify(
            {
                "ok": ok,
                "msg": msg,
                "ai_reply": ai_reply,
                "reply": reply,
                "audio_b64": audio_b64,
                "audio_format": audio_format,
                "intent": getattr(shelf_result, "intent", None),
                "picked": getattr(shelf_result, "picked", None),
                "dispatch_request": getattr(shelf_result, "dispatch_request", None),
                "commit_request": getattr(shelf_result, "commit_request", None),
            }
        )
    except Exception as exc:
        return jsonify({"ok": False, "msg": f"OCR ingest failed: {exc}"}), 500


@voice_bp.route("/api/ocr/rois", methods=["POST"])
def api_ocr_rois():
    try:
        image = None
        if "image" in request.files:
            image = request.files["image"].read()
        elif request.data:
            image = request.data

        if not image:
            return jsonify({"ok": False, "msg": "image is required"}), 400

        from ocr.yolo_roi import decode_image_bytes, detect_roi_boxes

        scan_spine = request.args.get("scan_spine") == "1" or request.form.get("scan_spine") == "1"
        mode = "spine" if scan_spine else "cover"
        frame = decode_image_bytes(image)
        height, width = frame.shape[:2]
        boxes = detect_roi_boxes(frame, mode=mode)
        return jsonify(
            {
                "ok": True,
                "mode": mode,
                "image_width": width,
                "image_height": height,
                "boxes": [
                    {
                        "x1": box.x1,
                        "y1": box.y1,
                        "x2": box.x2,
                        "y2": box.y2,
                        "conf": round(box.conf, 4),
                        "cls_id": box.cls_id,
                        "cls_name": box.cls_name,
                    }
                    for box in boxes
                ],
            }
        )
    except Exception as exc:
        return jsonify({"ok": False, "msg": f"YOLO ROI failed: {exc}"}), 500


@voice_bp.route("/api/voice/ingest", methods=["POST"])
def api_voice_ingest():
    audio = None
    if "audio" in request.files:
        audio = request.files["audio"].read()
    elif request.data:
        audio = request.data
    image = None
    if "image" in request.files:
        image = request.files["image"].read()

    if not audio:
        return jsonify({"ok": False, "msg": "audio is required"}), 400

    source = (request.args.get("source") or request.form.get("source") or "").strip().lower()
    mode = (request.args.get("mode") or request.form.get("mode") or "").strip().lower()
    push_events = source not in ("ui", "web")

    hints_extra = (request.form.get("hints_extra") or "").strip()
    extra_list = [h.strip() for h in hints_extra.split(",") if h.strip()] if hints_extra else []
    combined_hints = build_voice_hints() + extra_list
    try:
        raw_text = transcribe_wav_bytes(audio, hints=combined_hints, log_result=(mode == "command"))
    except ValueError as exc:
        return jsonify({"ok": False, "msg": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "msg": f"asr error: {exc}"}), 500

    if not raw_text:
        if mode != "command":
            return jsonify({"ok": True, "ignore": True}), 200
        return jsonify({"ok": False, "msg": "no speech detected"}), 200

    text = normalize_voice_text(raw_text)
    if not text:
        return jsonify({"ok": True, "ignore": True}), 200

    wake_hit = has_wake_word(text, push_event_fn=push_voice_event)
    if mode == "command" or wake_hit:
        print(f"[voice ingest] mode={mode or 'wake'} text={text} wake_hit={wake_hit}")

    if mode != "command":
        if not wake_hit:
            return jsonify({"ok": True, "ignore": True, "wake": False}), 200

        # Wake mode only arms the assistant. Commands are handled by the
        # next utterance inside the active wake window.
        reply_text = "\u6211\u5728"
        if push_events:
            push_voice_event("assistant", reply_text)
        result = {
            "ok": True,
            "wake": True,
            "intent": "wake",
            "text": "",
            "reply": reply_text,
        }
        return jsonify(_attach_tts_audio(result))
    else:
        stripped = strip_wake_words(text)
        if not stripped and wake_hit:
            reply_text = "\u6211\u5728"
            if push_events:
                push_voice_event("assistant", reply_text)
            result = {
                "ok": True,
                "wake": True,
                "intent": "wake",
                "text": text,
                "reply": reply_text,
            }
            return jsonify(_attach_tts_audio(result))
        if not stripped:
            stripped = text
        try:
            result = route_text(stripped, image_bytes=image, push_events=push_events)
        except Exception as exc:
            detail = str(exc).strip() or exc.__class__.__name__
            print("[voice ingest route error]", detail)
            return jsonify({"ok": False, "msg": f"voice route error: {detail}"}), 500
        result["wake"] = wake_hit
        result["text"] = stripped

    if result.get("need_image") and not result.get("reply"):
        result["ok"] = True
        result["reply"] = "\u597d\u7684\uff0c\u8bf7\u5bf9\u51c6\u4e66\u810a\uff0c\u6211\u6765\u626b\u63cf\u3002"

    return jsonify(_attach_tts_audio(result))


@voice_bp.route("/api/voice_events", methods=["GET"])
def api_voice_events():
    return jsonify({"events": get_voice_events()[-20:]})


@voice_bp.route("/api/voice_stream")
def api_voice_stream():
    """SSE 推送语音事件，替代轮询"""
    events = get_voice_events()

    def generate():
        last_idx = 0
        last_borrow_log_id = _latest_borrow_log_id()
        # 先推一条心跳，让浏览器确认连接成功
        yield 'data: {"type":"connected"}\n\n'
        while True:
            new_events = events[last_idx:]
            if new_events:
                last_idx = len(events)
                for ev in new_events:
                    yield f"data: {_json.dumps(ev, ensure_ascii=False)}\n\n"
            shelf_events, last_borrow_log_id = _borrow_log_events_after(last_borrow_log_id)
            for ev in shelf_events:
                yield f"data: {_json.dumps(ev, ensure_ascii=False)}\n\n"
            time.sleep(0.2)   # 200ms 检查一次

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )


@voice_bp.route("/api/tts_say", methods=["POST"])
def api_tts_say():
    data = request.get_json(force=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "msg": "empty text"}), 400
    audio_b64, audio_format = _build_tts_audio_payload(text)
    return jsonify({"ok": True, "audio_b64": audio_b64, "audio_format": audio_format})
