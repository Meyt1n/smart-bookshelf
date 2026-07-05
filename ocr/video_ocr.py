from collections import Counter
from urllib.parse import urlparse

import cv2

import config
from ocr.paddle_ocr import ocr_image


def _configured_camera_sources():
    source = str(getattr(config, "CAMERA_SOURCE", "0") or "0").strip()
    if source.isdigit():
        return [int(source)]

    sources = []
    parsed = urlparse(source)
    if parsed.scheme in ("http", "https") and parsed.path in ("", "/"):
        # Many phone IP-camera apps expose the MJPEG stream at /video.
        sources.append(source.rstrip("/") + "/video")
    sources.append(source)
    return sources


def _open_camera():
    for source in _configured_camera_sources():
        cap = cv2.VideoCapture(source)
        if not cap.isOpened():
            cap.release()
            continue

        ok, frame = cap.read()
        if ok and frame is not None:
            print(f"[camera] opened source: {source}")
            return cap, frame

        cap.release()

    print(f"[camera] failed to open source: {getattr(config, 'CAMERA_SOURCE', '')}")
    return None, None


def open_camera_capture():
    return _open_camera()


def encode_frame_as_jpeg(frame, quality=85):
    encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)]
    ok, data = cv2.imencode(".jpg", frame, encode_params)
    if not ok:
        raise ValueError("failed to encode camera frame")
    return data.tobytes()


def recognize_book_from_camera(max_frames=30):
    cap, first_frame = _open_camera()
    if cap is None:
        return None

    all_texts = []
    frame_count = 0
    frame = first_frame

    try:
        while frame_count < max_frames:
            if frame is None:
                ret, frame = cap.read()
                if not ret or frame is None:
                    break

            texts = ocr_image(frame)
            if texts:
                all_texts.extend(texts)

            cv2.imshow("Camera", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

            frame_count += 1
            frame = None
    finally:
        cap.release()
        cv2.destroyAllWindows()

    if not all_texts:
        print("[camera OCR] no text detected")
        return None

    clean_texts = [t for t in all_texts if len(t) >= 2]

    if not clean_texts:
        print("[camera OCR] no valid text detected")
        return None

    common_texts = [
        text for text, _ in Counter(clean_texts).most_common(5)
    ]

    print("[camera OCR] stable texts:", common_texts)

    return {
        "ocr_texts": common_texts
    }
