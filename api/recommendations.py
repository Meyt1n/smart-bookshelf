from __future__ import annotations

from flask import Blueprint, jsonify, request

from auth_utils import current_user, ensure_user_in_current_family, is_admin
from extensions import error_envelope, json_body, normalize_int
from services.recommendation_service import (
    build_recommendations,
    save_recommendation_feedback,
)


recommendations_bp = Blueprint("recommendations", __name__)


def _to_bool(raw_value: str | None) -> bool:
    return (raw_value or "").strip().lower() in {"1", "true", "yes", "on"}


def _resolve_target_user_id(requested_user_id: int | None) -> tuple[int | None, tuple | None]:
    viewer = current_user()
    if requested_user_id is None:
        return int(viewer["id"]), None
    if not is_admin() and int(viewer["id"]) != int(requested_user_id):
        return None, (jsonify(error_envelope("user_forbidden", code="user_forbidden")), 403)
    try:
        ensure_user_in_current_family(int(requested_user_id))
    except Exception:
        return None, (jsonify(error_envelope("user not found", code="user_not_found")), 404)
    return int(requested_user_id), None


@recommendations_bp.route("/api/recommendations", methods=["GET"])
def api_get_recommendations():
    limit = min(max(int(request.args.get("limit", 10)), 1), 50)
    include_read = _to_bool(request.args.get("include_read"))
    include_debug = _to_bool(request.args.get("debug"))
    requested_user_id = normalize_int(request.args.get("user_id"))
    target_user_id, denied = _resolve_target_user_id(requested_user_id)
    if denied:
        return denied
    try:
        result = build_recommendations(
            target_user_id,
            limit=limit,
            include_read=include_read,
            include_debug=include_debug,
        )
        return jsonify(result)
    except ValueError as exc:
        return jsonify(error_envelope(str(exc), code="invalid_request")), 400
    except Exception as exc:
        return jsonify(error_envelope(str(exc), code="recommendation_failed")), 500


@recommendations_bp.route("/api/recommendations/feedback", methods=["POST"])
def api_post_recommendation_feedback():
    viewer = current_user()
    data = json_body()

    requested_user_id = normalize_int(data.get("user_id"))
    target_user_id, denied = _resolve_target_user_id(requested_user_id)
    if denied:
        return denied

    book_id = normalize_int(data.get("book_id"))
    feedback_type = (data.get("feedback_type") or "").strip().lower()
    request_id = (data.get("request_id") or "").strip() or None
    source = (data.get("source") or "").strip() or "web"
    metadata = data.get("metadata")

    if not book_id:
        return jsonify(error_envelope("book_id is required", code="book_id_required")), 400
    if not feedback_type:
        return jsonify(error_envelope("feedback_type is required", code="feedback_required")), 400

    if target_user_id is None:
        target_user_id = int(viewer["id"])

    try:
        result = save_recommendation_feedback(
            user_id=int(target_user_id),
            book_id=int(book_id),
            feedback_type=feedback_type,
            request_id=request_id,
            source=source,
            metadata=metadata if isinstance(metadata, dict) else None,
        )
        return jsonify(result)
    except ValueError as exc:
        return jsonify(error_envelope(str(exc), code="invalid_request")), 400
    except Exception as exc:
        return jsonify(error_envelope(str(exc), code="feedback_failed")), 500
