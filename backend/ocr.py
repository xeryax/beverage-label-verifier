"""
OCR pipeline: OpenCV preprocessing + Tesseract.

Tesseract uses ~50–150 MB RAM vs ~2 GB+ for EasyOCR/PyTorch — required for
16 GB hosts running alongside other Docker Swarm services.
"""

from __future__ import annotations

import os
import re
import time
from typing import Optional

import cv2
import numpy as np
import pytesseract
from PIL import Image

# Limit Tesseract/OpenCV thread fan-out (reduces memory spikes on small hosts).
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

_MIN_LONG_EDGE = 1600
_TARGET_LONG_EDGE = 2000


def _to_pil(image: Image.Image | np.ndarray | str) -> Image.Image:
    if isinstance(image, str):
        return Image.open(image).convert("RGB")
    if isinstance(image, Image.Image):
        return image.convert("RGB")
    if isinstance(image, np.ndarray):
        if image.ndim == 2:
            return Image.fromarray(image)
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        return Image.fromarray(rgb)
    raise TypeError(f"Unsupported image type: {type(image).__name__}")


def _to_cv2(image: Image.Image | np.ndarray | str) -> np.ndarray:
    pil = _to_pil(image)
    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)


def _upscale_if_small(bgr: np.ndarray) -> np.ndarray:
    h, w = bgr.shape[:2]
    long_edge = max(h, w)
    if long_edge >= _MIN_LONG_EDGE:
        return bgr
    scale = _TARGET_LONG_EDGE / float(long_edge)
    return cv2.resize(
        bgr,
        (int(round(w * scale)), int(round(h * scale))),
        interpolation=cv2.INTER_CUBIC,
    )


def preprocess(image: Image.Image | np.ndarray | str) -> np.ndarray:
    bgr = _to_cv2(image)
    bgr = _upscale_if_small(bgr)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    if float(gray.std()) < 60.0:
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
    return gray


def _is_flat_artwork(bgr: np.ndarray) -> bool:
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    return float(gray.std()) >= 48.0 and max(h, w) / max(min(h, w), 1) < 2.8


def _is_bottle_photo(bgr: np.ndarray) -> bool:
    """Landscape product shots — used for review routing only, not OCR path selection."""
    h, w = bgr.shape[:2]
    return w > h * 1.12


def _run_tesseract(gray: np.ndarray) -> tuple[list[str], list[float], list[dict]]:
    """Return lines, per-line confidence (0–1), and word bounding boxes."""
    data = pytesseract.image_to_data(
        gray,
        output_type=pytesseract.Output.DICT,
        config="--oem 1 --psm 6",
    )
    lines_map: dict[tuple[int, int], list[tuple[str, float]]] = {}
    words: list[dict] = []
    n = len(data["text"])
    for i in range(n):
        text = (data["text"][i] or "").strip()
        if not text:
            continue
        conf = float(data["conf"][i])
        if conf < 0:
            continue
        w = int(data["width"][i])
        h = int(data["height"][i])
        if w > 0 and h > 0:
            words.append({
                "text": text,
                "left": int(data["left"][i]),
                "top": int(data["top"][i]),
                "width": w,
                "height": h,
                "conf": conf / 100.0,
            })
        key = (data["block_num"][i], data["line_num"][i])
        lines_map.setdefault(key, []).append((text, conf / 100.0))

    lines: list[str] = []
    confs: list[float] = []
    for parts in lines_map.values():
        line = " ".join(t for t, _ in parts).strip()
        if len(line) < 2:
            continue
        avg = sum(c for _, c in parts) / len(parts)
        if avg < 0.25:
            continue
        lines.append(line)
        confs.append(avg)
    return lines, confs, words


def extract_text(
    image: Image.Image | np.ndarray | str,
    *,
    skip_preprocess: bool = False,
    multi_pass: bool = True,
) -> dict:
    t0 = time.perf_counter()
    bgr = _to_cv2(image)
    flat = _is_flat_artwork(bgr)
    photo = _is_bottle_photo(bgr)

    words: list[dict] = []
    if skip_preprocess:
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        lines, confs, words = _run_tesseract(gray)
    else:
        gray = preprocess(image)
        lines, confs, words = _run_tesseract(gray)
        if multi_pass:
            seen = {l.lower() for l in lines}
            if not flat:
                gray2 = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
                lines2, confs2, _ = _run_tesseract(gray2)
                for line, conf in zip(lines2, confs2):
                    if line.lower() not in seen:
                        lines.append(line)
                        confs.append(conf)
                        seen.add(line.lower())
            psm3 = pytesseract.image_to_string(gray, config="--oem 1 --psm 3")
            for line in psm3.splitlines():
                line = line.strip()
                if len(line) >= 2 and line.lower() not in seen:
                    lines.append(line)
                    confs.append(0.55)
                    seen.add(line.lower())

    # Normalize common OCR errors
    full_text = "\n".join(lines)
    full_text = re.sub(r"(?<=\d\s)FL\s*0Z", "FL OZ", full_text, flags=re.IGNORECASE)
    full_text = re.sub(r"\b0Z\b", "OZ", full_text)

    avg = float(sum(confs) / len(confs)) if confs else 0.0
    return {
        "full_text": full_text,
        "lines": lines,
        "confidences": confs,
        "avg_confidence": avg,
        "processing_time": time.perf_counter() - t0,
        "raw_results": list(zip(lines, confs)),
        "words": words,
        "flat_artwork": flat,
        "bottle_photo": photo,
        "gray": gray if flat else None,
    }


def warm_up() -> None:
    """Verify tesseract is available (no model download)."""
    arr = np.ones((40, 120), dtype=np.uint8) * 255
    _run_tesseract(arr)
