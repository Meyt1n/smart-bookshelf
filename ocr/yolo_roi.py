from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

import cv2
import numpy as np

import config


@dataclass(frozen=True)
class RoiBox:
    x1: int
    y1: int
    x2: int
    y2: int
    conf: float
    cls_id: int
    cls_name: str


@dataclass(frozen=True)
class RoiOcrResult:
    mode: str
    roi_count: int
    texts: list[str]
    boxes: list[RoiBox]


def _model_path(mode: str) -> str:
    if mode == "spine":
        return config.BOOK_SPINE_YOLO_MODEL
    return config.BOOK_COVER_YOLO_MODEL


@lru_cache(maxsize=2)
def _load_model(model_path: str):
    if not model_path or not os.path.exists(model_path):
        raise FileNotFoundError(f"YOLO model not found: {model_path}")

    from ultralytics import YOLO

    return YOLO(model_path)


def _read_image(img):
    if isinstance(img, str):
        frame = cv2.imread(img)
        if frame is None:
            raise ValueError(f"failed to read image: {img}")
        return frame
    return img


def decode_image_bytes(image_bytes: bytes):
    data = np.frombuffer(image_bytes, dtype=np.uint8)
    frame = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("failed to decode image bytes")
    return frame


def _class_priority(mode: str, cls_name: str) -> int:
    name = (cls_name or "").strip().lower()
    if mode == "cover":
        if "title" in name or "book_name" in name or "书名" in name or "标题" in name or "name" == name:
            return 0
        if "author" in name or "writer" in name or "作者" in name:
            return 1
    return 2


def _sort_boxes(mode: str, boxes: list[RoiBox]) -> list[RoiBox]:
    if mode == "spine":
        return sorted(boxes, key=lambda box: (box.x1, box.y1, -box.conf))
    return sorted(
        boxes,
        key=lambda box: (
            _class_priority(mode, box.cls_name),
            box.y1,
            box.x1,
            -box.conf,
        ),
    )


def _expand_box(box: RoiBox, width: int, height: int) -> tuple[int, int, int, int]:
    box_w = max(1, box.x2 - box.x1)
    box_h = max(1, box.y2 - box.y1)
    ratio = float(getattr(config, "YOLO_OCR_PADDING_RATIO", 0.04))
    pad_x = max(2, int(box_w * ratio))
    pad_y = max(2, int(box_h * ratio))
    x1 = max(0, box.x1 - pad_x)
    y1 = max(0, box.y1 - pad_y)
    x2 = min(width, box.x2 + pad_x)
    y2 = min(height, box.y2 + pad_y)
    return x1, y1, x2, y2


def _dedupe_texts(texts: list[str]) -> list[str]:
    seen = set()
    clean = []
    for text in texts:
        item = str(text).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        clean.append(item)
    return clean


def detect_roi_boxes(img, mode: str = "cover") -> list[RoiBox]:
    mode = "spine" if mode == "spine" else "cover"
    frame = _read_image(img)
    model = _load_model(_model_path(mode))
    results = model.predict(
        source=frame,
        conf=float(getattr(config, "YOLO_OCR_CONF", 0.25)),
        iou=float(getattr(config, "YOLO_OCR_IOU", 0.7)),
        max_det=int(getattr(config, "YOLO_OCR_MAX_DET", 20)),
        verbose=False,
    )
    if not results:
        return []

    result = results[0]
    raw_boxes = getattr(result, "boxes", None)
    if raw_boxes is None or len(raw_boxes) == 0:
        return []

    names = getattr(result, "names", None) or getattr(model, "names", {}) or {}
    xyxy = raw_boxes.xyxy.detach().cpu().numpy()
    confs = raw_boxes.conf.detach().cpu().numpy()
    classes = raw_boxes.cls.detach().cpu().numpy()

    boxes = []
    for coords, conf, cls_id in zip(xyxy, confs, classes):
        x1, y1, x2, y2 = [int(round(float(value))) for value in coords]
        class_id = int(cls_id)
        boxes.append(
            RoiBox(
                x1=x1,
                y1=y1,
                x2=x2,
                y2=y2,
                conf=float(conf),
                cls_id=class_id,
                cls_name=str(names.get(class_id, class_id)),
            )
        )
    return _sort_boxes(mode, boxes)


def ocr_image_rois(img, mode: str = "cover") -> RoiOcrResult:
    mode = "spine" if mode == "spine" else "cover"
    frame = _read_image(img)
    height, width = frame.shape[:2]
    boxes = detect_roi_boxes(frame, mode=mode)
    if not boxes:
        return RoiOcrResult(mode=mode, roi_count=0, texts=[], boxes=[])

    from ocr.paddle_ocr import ocr_image

    texts = []
    for box in boxes:
        x1, y1, x2, y2 = _expand_box(box, width=width, height=height)
        if x2 <= x1 or y2 <= y1:
            continue
        crop = frame[y1:y2, x1:x2]
        texts.extend(ocr_image(crop))

    return RoiOcrResult(
        mode=mode,
        roi_count=len(boxes),
        texts=_dedupe_texts(texts),
        boxes=boxes,
    )
