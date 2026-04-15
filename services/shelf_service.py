from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass

import config
from ai.book_match_ai import get_or_create_book_by_ai, trigger_action_chat
from db.book_match import match_book
from db.shelf_ops import (
    find_free_compartment,
    get_all_compartments,
    get_book_in_compartment,
    store_book as db_store_book,
    take_book_by_cid as db_take_book_by_cid,
)
from ocr.yolo_roi import ocr_image_rois
from services.voice_intent import extract_title_from_take_text
from thefuzz import fuzz


MOTION_CMD_FETCH = 0x01
MOTION_CMD_STORE = 0x02


@dataclass
class ShelfActionResult:
    ok: bool
    msg: str
    ai_reply: str | None = None
    intent: str | None = None
    reply: str | None = None
    need_image: bool = False
    picked: dict | None = None
    dispatch_request: dict | None = None
    commit_request: dict | None = None

    def __iter__(self):
        yield self.ok
        yield self.msg
        yield self.ai_reply


def _result(
    ok: bool,
    msg: str,
    ai_reply: str | None = None,
    *,
    intent: str | None = None,
    reply: str | None = None,
    need_image: bool = False,
    picked: dict | None = None,
    dispatch_request: dict | None = None,
    commit_request: dict | None = None,
) -> ShelfActionResult:
    return ShelfActionResult(
        ok=bool(ok),
        msg=msg,
        ai_reply=ai_reply,
        intent=intent,
        reply=reply,
        need_image=need_image,
        picked=picked,
        dispatch_request=dispatch_request,
        commit_request=commit_request,
    )


def _log_ocr_texts(tag: str, texts) -> None:
    items = [str(item).strip() for item in (texts or []) if str(item).strip()]
    if items:
        print(f"[{tag}] " + " | ".join(items))
    else:
        print(f"[{tag}] <empty>")


def _current_user_id():
    try:
        from db.user_ops import get_current_user

        current_user = get_current_user()
        return current_user["id"] if current_user else None
    except Exception:
        return None


def _location_from_cid(cid: int) -> tuple[int, int, int]:
    target_cid = int(cid)
    for raw_cid, floor_hint, cell_hint, _status in get_all_compartments():
        if int(raw_cid) != target_cid:
            continue

        floor_id = int(floor_hint)
        shelf_cell_no = int(cell_hint)
        cell_id = shelf_cell_no - 1 + int(getattr(config, "STM32_CELL_ID_BASE", 0))
        if cell_id < 0:
            raise ValueError("invalid cell mapping")
        return floor_id, cell_id, shelf_cell_no

    raise ValueError(f"compartment not found: {target_cid}")


def _build_motion_payload(action: str, cid: int, title: str, book_id=None):
    floor_id, cell_id, shelf_cell_no = _location_from_cid(cid)
    cmd = MOTION_CMD_STORE if action == "store" else MOTION_CMD_FETCH
    location_text = f"{floor_id}层 {shelf_cell_no}号格"

    dispatch_request = {
        "cmd": cmd,
        "floor_id": floor_id,
        "cell_id": cell_id,
        "cid": int(cid),
        "title": title,
    }
    commit_request = {
        "action": action,
        "cid": int(cid),
        "title": title,
        "book_id": book_id,
    }
    picked = {
        "cid": int(cid),
        "title": title,
        "floor_id": floor_id,
        "cell_id": cell_id,
        "shelf_cell_no": shelf_cell_no,
        "location_text": location_text,
    }
    return dispatch_request, commit_request, picked


def _coerce_ai_book(book):
    if not book:
        return None, None

    if isinstance(book, dict):
        return book.get("id"), book.get("title")

    if isinstance(book, (list, tuple)) and len(book) >= 2:
        return book[0], book[1]

    return None, None


def _resolve_book_from_ocr_texts(ocr_texts):
    local = match_book(ocr_texts)
    if local and isinstance(local, (list, tuple)) and len(local) >= 2:
        book_id, title = local[0], local[1]
        print(f"[store match] local title={title}")
        return book_id, title

    book_id, title = _coerce_ai_book(get_or_create_book_by_ai(ocr_texts))
    if book_id and title:
        print(f"[store match] ai title={title}")
    return book_id, title


def _prepare_store(book_id, title, cid):
    dispatch_request, commit_request, picked = _build_motion_payload(
        "store",
        cid=cid,
        title=title,
        book_id=book_id,
    )
    msg = f"已准备存书命令，目标位置：{picked['location_text']}（{picked['cid']}号格）"
    reply = f"我已经帮你选好位置了，请把《{title}》放到 {picked['location_text']}。"
    return _result(
        True,
        msg,
        None,
        intent="store",
        reply=reply,
        picked=picked,
        dispatch_request=dispatch_request,
        commit_request=commit_request,
    )


def _prepare_take(cid, title):
    dispatch_request, commit_request, picked = _build_motion_payload(
        "take",
        cid=cid,
        title=title,
    )
    msg = f"已准备取书命令，目标位置：{picked['location_text']}（{picked['cid']}号格）"
    reply = f"我来帮你取《{title}》，位置在 {picked['location_text']}。"
    return _result(
        True,
        msg,
        None,
        intent="take",
        reply=reply,
        picked=picked,
        dispatch_request=dispatch_request,
        commit_request=commit_request,
    )


def store_from_ocr_texts(ocr_texts, speak_out=True):
    del speak_out
    if not ocr_texts:
        return _result(False, "未识别到书名文本", None, intent="store")

    _log_ocr_texts("OCR stable", ocr_texts)
    book_id, title = _resolve_book_from_ocr_texts(ocr_texts)
    if not book_id or not title:
        return _result(False, "没有匹配到图书信息", None, intent="store")

    free = find_free_compartment()
    if not free:
        return _result(False, "书架已满，没有空余格口", None, intent="store")

    cid = int(free[0])
    print(f"[store prepare] title={title} slot={cid}")
    return _prepare_store(book_id, title, cid)


def store_from_image_bytes(image_bytes: bytes, speak_out=True, scan_spine=False):
    del speak_out
    if not image_bytes:
        return _result(False, "没有收到图像数据", None, intent="store")

    texts = []
    ocr_texts = []
    roi_count = 0
    ocr_mode = "spine" if scan_spine else "cover"
    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as fp:
        img_path = fp.name
        fp.write(image_bytes)

    try:
        roi_result = ocr_image_rois(img_path, mode=ocr_mode)
        from ocr.paddle_ocr import stabilize_ocr_texts

        texts = roi_result.texts
        roi_count = roi_result.roi_count
        ocr_texts = stabilize_ocr_texts(texts, top_k=5)
    except Exception as exc:
        label = "书脊" if scan_spine else "封面文字"
        return _result(False, f"{label} ROI 识别失败：{exc}", None, intent="store")
    finally:
        try:
            os.remove(img_path)
        except Exception:
            pass

    _log_ocr_texts(f"OCR {ocr_mode} ROI raw", texts)
    _log_ocr_texts(f"OCR {ocr_mode} ROI stabilized", ocr_texts)
    if not ocr_texts:
        if roi_count <= 0:
            msg = "未检测到书脊区域" if scan_spine else "未检测到封面标题/作者区域"
        else:
            msg = "ROI 区域内没有识别到文字"
        return _result(False, msg, None, intent="store")
    return store_from_ocr_texts(ocr_texts)


def store_via_ocr(speak_out=True):
    del speak_out
    from ocr.video_ocr import recognize_book_from_camera

    result = recognize_book_from_camera()
    if not result:
        return _result(False, "摄像头识别失败", None, intent="store")

    if isinstance(result, dict) and (result.get("book_id") or result.get("id")):
        book_id = result.get("book_id") or result.get("id")
        title = result.get("title", "Unknown Book")
        free = find_free_compartment()
        if not free:
            return _result(False, "书架已满，没有空余格口", None, intent="store")
        return _prepare_store(book_id, title, int(free[0]))

    ocr_texts = result.get("ocr_texts") if isinstance(result, dict) else result
    _log_ocr_texts("OCR camera", ocr_texts)
    return store_from_ocr_texts(ocr_texts)


def pick_best_book_on_shelf(query: str, min_score: int = 68):
    query = (query or "").strip()
    if not query:
        return None

    candidates = []
    for cid, _x, _y, status in get_all_compartments():
        if status != "occupied":
            continue

        title = get_book_in_compartment(cid)
        if not title:
            continue

        score = max(fuzz.partial_ratio(query, title), fuzz.ratio(query, title))
        if query in title:
            score += 20
        score += len(set(query) & set(title)) * 4
        candidates.append({"cid": int(cid), "title": title, "score": score})

    if not candidates:
        return None

    candidates.sort(key=lambda item: (-item["score"], item["cid"]))
    best = candidates[0]
    if best["score"] < min_score:
        return None
    return best


def take_by_cid(cid, title="", speak_out=True):
    del speak_out
    target_cid = int(cid)
    resolved_title = (title or "").strip() or get_book_in_compartment(target_cid)
    if not resolved_title:
        return _result(False, "这个格口里没有书", None, intent="take")

    print(f"[take prepare] title={resolved_title} slot={target_cid}")
    return _prepare_take(target_cid, resolved_title)


def take_by_text(text: str, speak_out=True):
    del speak_out
    title = extract_title_from_take_text(text)
    if not title:
        return _result(False, "没有识别出要取的书名", None, intent="take")

    target = pick_best_book_on_shelf(title)
    if not target:
        return _result(False, f"书架上没有找到《{title}》", None, intent="take")

    return take_by_cid(target["cid"], title=target["title"])


def commit_prepared_action(action: str, cid: int, title: str = "", book_id=None):
    action = (action or "").strip().lower()
    target_cid = int(cid)
    user_id = _current_user_id()

    if action == "store":
        if not book_id:
            return _result(False, "存书提交缺少 book_id", None, intent="store")

        db_store_book(book_id, target_cid, user_id=user_id)
        ai_reply = trigger_action_chat("store", title, speak_out=False)
        return _result(
            True,
            f"已完成存书：{title} -> {target_cid}号格",
            ai_reply,
            intent="store",
            reply=ai_reply,
        )

    if action == "take":
        actual_title = get_book_in_compartment(target_cid) or title
        if not actual_title:
            return _result(False, "这个格口已经是空的", None, intent="take")

        ok = db_take_book_by_cid(target_cid, user_id=user_id)
        if not ok:
            return _result(False, "取书提交失败", None, intent="take")

        ai_reply = trigger_action_chat("take", actual_title, speak_out=False)
        return _result(
            True,
            f"已完成取书：{actual_title} <- {target_cid}号格",
            ai_reply,
            intent="take",
            reply=ai_reply,
        )

    return _result(False, f"unsupported action: {action}", None)
