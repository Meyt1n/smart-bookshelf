from __future__ import annotations

import json
import math
import re
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from extensions import db_conn, row_dict, rows_dict


MODEL_VERSION = "hybrid-v1"
_TOKEN_RE = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]+")
_MAX_CANDIDATES = 800
_FEEDBACK_TYPES = {"click", "dismiss", "like", "borrow"}


def ensure_recommendation_schema() -> None:
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS recommendation_impressions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            book_id INTEGER NOT NULL,
            rank_pos INTEGER NOT NULL,
            score REAL NOT NULL DEFAULT 0,
            reason TEXT,
            model_version TEXT NOT NULL DEFAULT 'hybrid-v1',
            clicked INTEGER NOT NULL DEFAULT 0,
            dismissed INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (request_id, book_id),
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (book_id) REFERENCES books(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS recommendation_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT,
            user_id INTEGER NOT NULL,
            book_id INTEGER NOT NULL,
            feedback_type TEXT NOT NULL,
            source TEXT,
            metadata_json TEXT,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (book_id) REFERENCES books(id)
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_reco_impr_user_time ON recommendation_impressions(user_id, created_at DESC)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_reco_impr_request ON recommendation_impressions(request_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_reco_feedback_user_time ON recommendation_feedback(user_id, created_at DESC)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_reco_feedback_request ON recommendation_feedback(request_id)"
    )
    conn.commit()
    conn.close()


def build_recommendations(
    user_id: int,
    *,
    limit: int = 10,
    include_read: bool = False,
    include_debug: bool = False,
) -> dict[str, Any]:
    ensure_recommendation_schema()
    safe_limit = max(1, min(int(limit), 50))

    conn = db_conn()
    cur = conn.cursor()
    user = _fetch_user(cur, user_id)
    if not user:
        conn.close()
        raise ValueError("user not found")

    books = _fetch_books(cur)
    if not books:
        conn.close()
        return {
            "request_id": uuid.uuid4().hex,
            "model_version": MODEL_VERSION,
            "user_id": user_id,
            "generated_at": _now_iso(),
            "items": [],
            "total_candidates": 0,
        }

    interaction_weights = _load_user_interactions(cur, user_id)
    seen_book_ids = set(interaction_weights.keys())
    book_lookup = {book["id"]: book for book in books}

    preference_tokens = _build_preference_tokens(user, interaction_weights, book_lookup)
    history_tokens = _build_history_tokens(interaction_weights, book_lookup)
    popularity = _load_global_popularity(cur)
    collaborative = _load_collaborative_signal(cur, user_id)

    max_popularity = max(popularity.values()) if popularity else 1.0
    max_collaborative = max(collaborative.values()) if collaborative else 1.0
    weight_plan = _pick_weight_plan(len(interaction_weights))

    scored_items: list[dict[str, Any]] = []
    for book in books[:_MAX_CANDIDATES]:
        book_id = book["id"]
        if not include_read and book_id in seen_book_ids:
            continue

        token_set = _book_tokens(book)
        content_score = _token_overlap_score(preference_tokens, token_set)
        history_score = _token_overlap_score(history_tokens, token_set)
        popularity_score = _log_normalize(popularity.get(book_id, 0.0), max_popularity)
        collaborative_score = collaborative.get(book_id, 0.0) / max_collaborative
        age_fit_score = _age_fit_score(user.get("age"), book.get("age_min"), book.get("age_max"))
        shelf_bonus = 0.08 if int(book.get("is_on_shelf") or 0) else 0.0
        publish_year_bonus = _publish_year_bonus(book.get("publish_year"))

        score = (
            weight_plan["content"] * content_score
            + weight_plan["history"] * history_score
            + weight_plan["popularity"] * popularity_score
            + weight_plan["collaborative"] * collaborative_score
            + weight_plan["age_fit"] * age_fit_score
            + shelf_bonus
            + publish_year_bonus
        )
        if include_read and book_id in seen_book_ids:
            score -= 0.25

        reason = _build_reason(
            book=book,
            content_score=content_score,
            history_score=history_score,
            popularity_score=popularity_score,
            collaborative_score=collaborative_score,
            age_fit_score=age_fit_score,
            interest_tokens=preference_tokens,
        )

        item = {
            "book_id": book_id,
            "title": book.get("title"),
            "author": book.get("author"),
            "category": book.get("category"),
            "tags": book.get("tags"),
            "cover_url": book.get("cover_url"),
            "is_on_shelf": bool(book.get("is_on_shelf")),
            "on_shelf_count": int(book.get("on_shelf_count") or 0),
            "score": round(score, 6),
            "reason": reason,
        }
        if include_debug:
            item["debug"] = {
                "content_score": round(content_score, 6),
                "history_score": round(history_score, 6),
                "popularity_score": round(popularity_score, 6),
                "collaborative_score": round(collaborative_score, 6),
                "age_fit_score": round(age_fit_score, 6),
                "weights": weight_plan,
            }
        scored_items.append(item)

    scored_items.sort(key=lambda row: (row["score"], row["on_shelf_count"], row["book_id"]), reverse=True)
    top_items = scored_items[:safe_limit]
    request_id = uuid.uuid4().hex
    _save_impressions(cur, user_id, request_id, top_items)
    conn.commit()
    conn.close()

    return {
        "request_id": request_id,
        "model_version": MODEL_VERSION,
        "user_id": user_id,
        "generated_at": _now_iso(),
        "items": top_items,
        "total_candidates": len(scored_items),
    }


def save_recommendation_feedback(
    *,
    user_id: int,
    book_id: int,
    feedback_type: str,
    request_id: str | None = None,
    source: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_recommendation_schema()
    normalized_feedback = (feedback_type or "").strip().lower()
    if normalized_feedback not in _FEEDBACK_TYPES:
        raise ValueError("feedback_type must be one of click/dismiss/like/borrow")

    conn = db_conn()
    cur = conn.cursor()
    if not _exists_row(cur, "users", user_id):
        conn.close()
        raise ValueError("user not found")
    if not _exists_row(cur, "books", book_id):
        conn.close()
        raise ValueError("book not found")

    payload_json = None
    if metadata is not None:
        payload_json = json.dumps(metadata, ensure_ascii=False)

    cur.execute(
        """
        INSERT INTO recommendation_feedback (request_id, user_id, book_id, feedback_type, source, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (request_id, user_id, book_id, normalized_feedback, source, payload_json),
    )
    feedback_id = cur.lastrowid

    if request_id:
        if normalized_feedback == "click":
            cur.execute(
                """
                UPDATE recommendation_impressions
                SET clicked = 1
                WHERE request_id = ? AND user_id = ? AND book_id = ?
                """,
                (request_id, user_id, book_id),
            )
        if normalized_feedback == "dismiss":
            cur.execute(
                """
                UPDATE recommendation_impressions
                SET dismissed = 1
                WHERE request_id = ? AND user_id = ? AND book_id = ?
                """,
                (request_id, user_id, book_id),
            )

    cur.execute(
        """
        INSERT INTO reading_events (user_id, event_type, book_id, source, metadata_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            user_id,
            _feedback_to_event_type(normalized_feedback),
            book_id,
            source or "recommendation_api",
            payload_json,
        ),
    )

    conn.commit()
    conn.close()
    return {
        "id": feedback_id,
        "feedback_type": normalized_feedback,
        "request_id": request_id,
    }


def _fetch_user(cur, user_id: int) -> dict[str, Any] | None:
    cur.execute(
        """
        SELECT id, name, age, reading_level, interests, family_id
        FROM users
        WHERE id = ?
        """,
        (user_id,),
    )
    return row_dict(cur.fetchone())


def _fetch_books(cur) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT
            b.id,
            b.title,
            b.author,
            b.category,
            b.keywords,
            b.description,
            b.age_min,
            b.age_max,
            b.difficulty_level,
            b.tags,
            b.cover_url,
            b.publish_year,
            COUNT(sb.compartment_id) AS on_shelf_count,
            CASE WHEN COUNT(sb.compartment_id) > 0 THEN 1 ELSE 0 END AS is_on_shelf
        FROM books b
        LEFT JOIN stored_books sb ON sb.book_id = b.id
        GROUP BY b.id
        ORDER BY b.id DESC
        """
    )
    return rows_dict(cur.fetchall())


def _load_user_interactions(cur, user_id: int) -> dict[int, float]:
    signals: defaultdict[int, float] = defaultdict(float)
    cur.execute(
        """
        SELECT book_id, action, action_time
        FROM borrow_logs
        WHERE user_id = ?
          AND book_id IS NOT NULL
          AND action_time >= datetime('now', '-365 days')
        """,
        (user_id,),
    )
    for row in cur.fetchall():
        book_id = row["book_id"]
        if not book_id:
            continue
        action = (row["action"] or "").strip().lower()
        action_weight = 3.0 if action == "take" else 1.25
        signals[int(book_id)] += action_weight * _time_decay(row["action_time"], half_life_days=45)

    event_weight_map = {
        "view_book": 0.40,
        "open_book_detail": 0.80,
        "scan_success": 0.80,
        "recommend_click": 1.30,
        "recommend_borrow": 1.80,
        "recommend_like": 1.00,
    }
    cur.execute(
        """
        SELECT book_id, event_type, event_time
        FROM reading_events
        WHERE user_id = ?
          AND book_id IS NOT NULL
          AND event_time >= datetime('now', '-365 days')
        """,
        (user_id,),
    )
    for row in cur.fetchall():
        book_id = row["book_id"]
        if not book_id:
            continue
        event_type = (row["event_type"] or "").strip().lower()
        base_weight = event_weight_map.get(event_type, 0.20)
        signals[int(book_id)] += base_weight * _time_decay(row["event_time"], half_life_days=30)

    return dict(signals)


def _build_preference_tokens(
    user: dict[str, Any],
    interactions: dict[int, float],
    book_lookup: dict[int, dict[str, Any]],
) -> Counter[str]:
    tokens: Counter[str] = Counter()
    for token in _tokenize(user.get("interests")):
        tokens[token] += 3
    for token in _tokenize(user.get("reading_level")):
        tokens[token] += 1

    if not interactions:
        return tokens

    for book_id, weight in interactions.items():
        book = book_lookup.get(book_id)
        if not book:
            continue
        for token in _tokenize(book.get("category")):
            tokens[token] += max(1, int(round(weight)))
        for token in _tokenize(book.get("author")):
            tokens[token] += max(1, int(round(weight * 0.6)))
        for token in _tokenize(book.get("tags")):
            tokens[token] += max(1, int(round(weight * 0.8)))
        for token in _tokenize(book.get("keywords")):
            tokens[token] += max(1, int(round(weight * 0.8)))
    return tokens


def _build_history_tokens(interactions: dict[int, float], book_lookup: dict[int, dict[str, Any]]) -> Counter[str]:
    tokens: Counter[str] = Counter()
    top_items = sorted(interactions.items(), key=lambda item: item[1], reverse=True)[:8]
    for book_id, weight in top_items:
        book = book_lookup.get(book_id)
        if not book:
            continue
        for token in _book_tokens(book):
            tokens[token] += max(1, int(round(weight)))
    return tokens


def _load_global_popularity(cur) -> dict[int, float]:
    popularity: defaultdict[int, float] = defaultdict(float)
    cur.execute(
        """
        SELECT book_id, COUNT(*) AS cnt
        FROM borrow_logs
        WHERE action = 'take'
          AND book_id IS NOT NULL
          AND action_time >= datetime('now', '-180 days')
        GROUP BY book_id
        """
    )
    for row in cur.fetchall():
        popularity[int(row["book_id"])] += float(row["cnt"]) * 1.2

    cur.execute(
        """
        SELECT book_id, COUNT(*) AS cnt
        FROM reading_events
        WHERE book_id IS NOT NULL
          AND event_time >= datetime('now', '-180 days')
          AND event_type IN ('view_book', 'open_book_detail', 'recommend_click', 'recommend_borrow')
        GROUP BY book_id
        """
    )
    for row in cur.fetchall():
        popularity[int(row["book_id"])] += float(row["cnt"])
    return dict(popularity)


def _load_collaborative_signal(cur, user_id: int) -> dict[int, float]:
    user_books: defaultdict[int, set[int]] = defaultdict(set)
    cur.execute(
        """
        SELECT user_id, book_id
        FROM borrow_logs
        WHERE action = 'take'
          AND book_id IS NOT NULL
        """
    )
    for row in cur.fetchall():
        user_books[int(row["user_id"])].add(int(row["book_id"]))

    cur.execute(
        """
        SELECT user_id, book_id
        FROM reading_events
        WHERE event_type IN ('recommend_click', 'recommend_borrow')
          AND book_id IS NOT NULL
        """
    )
    for row in cur.fetchall():
        user_books[int(row["user_id"])].add(int(row["book_id"]))

    target_books = user_books.get(user_id, set())
    if not target_books:
        return {}

    collaborative_scores: defaultdict[int, float] = defaultdict(float)
    for other_user_id, other_books in user_books.items():
        if other_user_id == user_id or not other_books:
            continue
        overlap = len(target_books & other_books)
        if overlap <= 0:
            continue
        weight = 1.0 + math.log1p(overlap)
        for book_id in other_books - target_books:
            collaborative_scores[book_id] += weight
    return dict(collaborative_scores)


def _pick_weight_plan(interaction_count: int) -> dict[str, float]:
    if interaction_count >= 5:
        return {
            "content": 0.35,
            "history": 0.25,
            "popularity": 0.15,
            "collaborative": 0.20,
            "age_fit": 0.05,
        }
    if interaction_count >= 2:
        return {
            "content": 0.40,
            "history": 0.25,
            "popularity": 0.20,
            "collaborative": 0.10,
            "age_fit": 0.05,
        }
    return {
        "content": 0.45,
        "history": 0.05,
        "popularity": 0.35,
        "collaborative": 0.00,
        "age_fit": 0.15,
    }


def _build_reason(
    *,
    book: dict[str, Any],
    content_score: float,
    history_score: float,
    popularity_score: float,
    collaborative_score: float,
    age_fit_score: float,
    interest_tokens: Counter[str],
) -> str:
    category = (book.get("category") or "").strip()
    matching_interest = None
    if interest_tokens:
        for token in _tokenize(" ".join([book.get("category") or "", book.get("tags") or "", book.get("keywords") or ""])):
            if token in interest_tokens:
                matching_interest = token
                break

    if matching_interest and content_score >= 0.15:
        return f"Aligned with your interests: {matching_interest}"
    if category and history_score >= 0.12:
        return f"Matches your recent reading in {category}"
    if collaborative_score >= 0.35:
        return "Readers with similar history also picked this"
    if popularity_score >= 0.50:
        return "Popular in recent borrowing"
    if age_fit_score >= 0.95:
        return "Fits your age range"
    if int(book.get("is_on_shelf") or 0):
        return "Available on shelf right now"
    return "Recommended as a balanced next read"


def _save_impressions(cur, user_id: int, request_id: str, items: list[dict[str, Any]]) -> None:
    for idx, item in enumerate(items, start=1):
        cur.execute(
            """
            INSERT OR REPLACE INTO recommendation_impressions
            (request_id, user_id, book_id, rank_pos, score, reason, model_version)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                user_id,
                item["book_id"],
                idx,
                item["score"],
                item.get("reason"),
                MODEL_VERSION,
            ),
        )


def _book_tokens(book: dict[str, Any]) -> set[str]:
    fields = [
        book.get("title"),
        book.get("author"),
        book.get("category"),
        book.get("keywords"),
        book.get("description"),
        book.get("tags"),
        book.get("difficulty_level"),
    ]
    tokens: set[str] = set()
    for value in fields:
        tokens.update(_tokenize(value))
    return tokens


def _token_overlap_score(reference: Counter[str], candidate_tokens: set[str]) -> float:
    if not reference or not candidate_tokens:
        return 0.0
    overlap_weight = 0.0
    for token, count in reference.items():
        if token in candidate_tokens:
            overlap_weight += float(count)
    norm = math.sqrt(sum(reference.values())) + 1e-6
    return min(1.0, overlap_weight / (norm * 3.0))


def _age_fit_score(user_age: Any, age_min: Any, age_max: Any) -> float:
    age = _safe_int(user_age)
    lower = _safe_int(age_min)
    upper = _safe_int(age_max)
    if age is None:
        return 0.50
    if lower is None and upper is None:
        return 0.60
    if lower is not None and age < lower:
        return max(0.0, 1.0 - (lower - age) / 8.0)
    if upper is not None and age > upper:
        return max(0.0, 1.0 - (age - upper) / 8.0)
    return 1.0


def _publish_year_bonus(publish_year: Any) -> float:
    year = _safe_int(publish_year)
    if year is None:
        return 0.0
    current_year = datetime.now(timezone.utc).year
    delta = max(0, current_year - year)
    if delta <= 3:
        return 0.04
    if delta <= 10:
        return 0.02
    return 0.0


def _log_normalize(value: float, max_value: float) -> float:
    if max_value <= 0:
        return 0.0
    return math.log1p(max(0.0, value)) / math.log1p(max_value)


def _time_decay(raw_time: str | None, *, half_life_days: int) -> float:
    if not raw_time:
        return 1.0
    parsed = _parse_sqlite_time(raw_time)
    if not parsed:
        return 1.0
    age_days = max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds() / 86400.0)
    return 0.5 ** (age_days / max(1.0, float(half_life_days)))


def _parse_sqlite_time(raw_time: str) -> datetime | None:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw_time[:19], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _feedback_to_event_type(feedback_type: str) -> str:
    mapping = {
        "click": "recommend_click",
        "dismiss": "recommend_dismiss",
        "like": "recommend_like",
        "borrow": "recommend_borrow",
    }
    return mapping.get(feedback_type, "recommend_click")


def _exists_row(cur, table_name: str, row_id: int) -> bool:
    cur.execute(f"SELECT 1 FROM {table_name} WHERE id = ? LIMIT 1", (row_id,))
    return cur.fetchone() is not None


def _tokenize(text: Any) -> list[str]:
    if not text:
        return []
    lowered = str(text).lower()
    return _TOKEN_RE.findall(lowered)


def _safe_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
