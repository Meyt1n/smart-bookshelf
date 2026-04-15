"""
api/shelf.py
"""

from flask import Blueprint, jsonify, request

from db.shelf_ops import get_all_compartments, get_book_in_compartment
import services.shelf_service as shelf_service


shelf_bp = Blueprint("shelf", __name__)


def _payload_from_result(result):
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


@shelf_bp.route("/api/compartments")
def api_compartments():
    data = get_all_compartments()
    results = []
    for cid, x, y, status in data:
        results.append(
            {
                "cid": cid,
                "x": x,
                "y": y,
                "status": status,
                "book": get_book_in_compartment(cid),
            }
        )
    return jsonify(results)


@shelf_bp.route("/api/store", methods=["POST"])
def api_store():
    try:
        result = shelf_service.store_via_ocr()
        payload = _payload_from_result(result)
        if not payload["ok"]:
            return jsonify(payload), 400
        return jsonify(payload)
    except Exception as exc:
        return jsonify({"ok": False, "msg": f"store failed: {exc}"}), 500


@shelf_bp.route("/api/take", methods=["POST"])
def api_take():
    try:
        data = request.get_json(force=True) or {}
        cid = data.get("cid")
        title = data.get("title", "")
        if cid is None:
            return jsonify({"ok": False, "msg": "cid is required"}), 400

        result = shelf_service.take_by_cid(cid, title=title)
        payload = _payload_from_result(result)
        if not payload["ok"]:
            return jsonify(payload), 400
        return jsonify(payload)
    except Exception as exc:
        return jsonify({"ok": False, "msg": f"take failed: {exc}"}), 500


@shelf_bp.route("/api/take_by_text", methods=["POST"])
def api_take_by_text():
    try:
        data = request.get_json(force=True) or {}
        text = (data.get("text") or "").strip()
        result = shelf_service.take_by_text(text)
        payload = _payload_from_result(result)
        if not payload["ok"]:
            return jsonify(payload), 404
        return jsonify(payload)
    except Exception as exc:
        return jsonify({"ok": False, "msg": f"take failed: {exc}"}), 500


@shelf_bp.route("/api/motion/commit", methods=["POST"])
def api_motion_commit():
    try:
        data = request.get_json(force=True) or {}
        action = (data.get("action") or "").strip().lower()
        cid = data.get("cid")
        title = (data.get("title") or "").strip()
        book_id = data.get("book_id")
        if not action:
            return jsonify({"ok": False, "msg": "action is required"}), 400
        if cid is None:
            return jsonify({"ok": False, "msg": "cid is required"}), 400

        result = shelf_service.commit_prepared_action(action, cid=cid, title=title, book_id=book_id)
        payload = _payload_from_result(result)
        if not payload["ok"]:
            return jsonify(payload), 400
        return jsonify(payload)
    except Exception as exc:
        return jsonify({"ok": False, "msg": f"commit failed: {exc}"}), 500
