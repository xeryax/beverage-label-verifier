"""27 CFR § 16.22 style heuristics for flat artwork (bold header, relative type size).

Uses Tesseract word bounding boxes from the primary OCR pass — no extra
Tesseract invocation. Skipped on bottle photos (unreliable perspective/glare).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

import cv2
import numpy as np

from matcher import FieldResult, STATUS_MATCH, STATUS_REVIEW

_HEADER_WORDS = frozenset({"government", "warning"})
_BODY_WORDS = frozenset({
    "according", "surgeon", "general", "women", "pregnancy", "defects",
    "consumption", "impairs", "machinery", "problems",
})
_NET_WORDS = frozenset({"ml", "ml.", "liter", "litre", "l", "oz", "fl"})

# Bold "likely" when header strokes clearly heavier than body; fail only when clearly thinner.
_BOLD_LIKELY_RATIO = 1.08
_BOLD_FAIL_RATIO = 0.92
# Warning body height vs net-contents height (same image scale).
_SIZE_RATIO_MIN = 0.50


@dataclass
class WordBox:
    text: str
    left: int
    top: int
    width: int
    height: int
    conf: float


def word_boxes_from_ocr(words: list[dict[str, Any]]) -> list[WordBox]:
    out: list[WordBox] = []
    for w in words or []:
        text = (w.get("text") or "").strip()
        if not text:
            continue
        out.append(
            WordBox(
                text=text,
                left=int(w.get("left") or 0),
                top=int(w.get("top") or 0),
                width=int(w.get("width") or 0),
                height=int(w.get("height") or 0),
                conf=float(w.get("conf") or 0),
            )
        )
    return out


def _crop_word(gray: np.ndarray, box: WordBox, pad: int = 2) -> Optional[np.ndarray]:
    if box.width < 2 or box.height < 2:
        return None
    h, w = gray.shape[:2]
    x0 = max(0, box.left - pad)
    y0 = max(0, box.top - pad)
    x1 = min(w, box.left + box.width + pad)
    y1 = min(h, box.top + box.height + pad)
    if x1 <= x0 or y1 <= y0:
        return None
    return gray[y0:y1, x0:x1]


def _stroke_metrics(crop: np.ndarray) -> tuple[float, float]:
    """Return (ink_density, median_horizontal_run) for a word crop."""
    if crop is None or crop.size == 0:
        return 0.0, 0.0
    blur = cv2.GaussianBlur(crop, (3, 3), 0)
    _, binary = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    ink = float(np.count_nonzero(binary))
    density = ink / float(binary.size)
    runs: list[int] = []
    for row in binary:
        in_run = False
        length = 0
        for val in row:
            if val:
                length += 1
                in_run = True
            elif in_run:
                if length > 0:
                    runs.append(length)
                length = 0
                in_run = False
        if length > 0:
            runs.append(length)
    median_run = float(np.median(runs)) if runs else 0.0
    return density, median_run


def _classify_words(boxes: list[WordBox]) -> tuple[list[WordBox], list[WordBox], list[WordBox]]:
    header: list[WordBox] = []
    body: list[WordBox] = []
    net: list[WordBox] = []
    for box in boxes:
        token = re.sub(r"[^a-z0-9]", "", box.text.lower())
        if token in _HEADER_WORDS:
            header.append(box)
        elif token in _BODY_WORDS:
            body.append(box)
        elif token in _NET_WORDS or re.fullmatch(r"\d{2,4}", token):
            net.append(box)
        elif re.search(r"\d+\s*ml", box.text, re.I):
            net.append(box)
    return header, body, net


def _median(values: list[float]) -> float:
    return float(np.median(values)) if values else 0.0


def _parse_volume_ml(net_contents: str) -> Optional[float]:
    if not net_contents:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*(ml|mL|ML)\b", net_contents, re.I)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:fl\.?\s*oz|floz)\b", net_contents, re.I)
    if m:
        return float(m.group(1)) * 29.5735
    m = re.search(r"(\d+(?:\.\d+)?)\s*L\b", net_contents, re.I)
    if m:
        return float(m.group(1)) * 1000.0
    return None


def _min_type_height_mm(volume_ml: Optional[float]) -> float:
    """27 CFR § 16.22 minimum character height by container size."""
    if volume_ml is None:
        return 2.0
    if volume_ml <= 237:
        return 1.0
    if volume_ml <= 3785:
        return 2.0
    return 3.0


def analyze_warning_style(
    gray: np.ndarray,
    words: list[dict[str, Any]],
    *,
    net_contents: str = "",
) -> dict[str, Any]:
    """CV heuristics on flat artwork. Returns diagnostic dict for merging into field notes."""
    boxes = word_boxes_from_ocr(words)
    header, body, net = _classify_words(boxes)
    result: dict[str, Any] = {
        "checked": False,
        "header_bold_likely": None,
        "header_bold_fail": None,
        "header_taller_than_body": None,
        "size_ratio_ok": None,
        "style_notes": [],
    }
    if gray is None or gray.size == 0 or len(boxes) < 4:
        result["style_notes"].append("Style check skipped — insufficient OCR boxes")
        return result
    if not header or len(body) < 2:
        result["style_notes"].append(
            "Style check inconclusive — could not locate enough warning word boxes"
        )
        return result

    result["checked"] = True
    header_scores: list[float] = []
    body_scores: list[float] = []
    header_heights: list[float] = []
    body_heights: list[float] = []
    net_heights: list[float] = []

    for box in header:
        crop = _crop_word(gray, box)
        density, median_run = _stroke_metrics(crop) if crop is not None else (0.0, 0.0)
        header_scores.append(density * 40.0 + median_run)
        header_heights.append(float(box.height))

    for box in body:
        crop = _crop_word(gray, box)
        density, median_run = _stroke_metrics(crop) if crop is not None else (0.0, 0.0)
        body_scores.append(density * 40.0 + median_run)
        body_heights.append(float(box.height))

    for box in net:
        net_heights.append(float(box.height))

    h_score = _median(header_scores)
    b_score = _median(body_scores)
    h_height = _median(header_heights)
    b_height = _median(body_heights)
    n_height = _median(net_heights)

    if b_score > 0:
        bold_ratio = h_score / b_score
        result["header_bold_likely"] = bold_ratio >= _BOLD_LIKELY_RATIO
        result["header_bold_fail"] = bold_ratio < _BOLD_FAIL_RATIO
        if result["header_bold_likely"]:
            result["style_notes"].append(
                f"Header stroke weight ~{bold_ratio:.2f}× body (flat-artwork CV) — bold header likely"
            )
        elif result["header_bold_fail"]:
            result["style_notes"].append(
                f"Header stroke weight only ~{bold_ratio:.2f}× body — verify bold on "
                "'GOVERNMENT WARNING:' (27 CFR § 16.22)"
            )
        else:
            result["style_notes"].append(
                f"Header stroke weight ~{bold_ratio:.2f}× body — bold inconclusive; verify visually"
            )

    if b_height > 0:
        result["header_taller_than_body"] = h_height >= b_height * 0.95
        if not result["header_taller_than_body"]:
            result["style_notes"].append(
                "Header type height is smaller than body — verify warning formatting"
            )

    if n_height > 0 and b_height > 0:
        size_ratio = b_height / n_height
        result["size_ratio_ok"] = size_ratio >= _SIZE_RATIO_MIN
        vol = _parse_volume_ml(net_contents)
        min_mm = _min_type_height_mm(vol)
        if result["size_ratio_ok"]:
            result["style_notes"].append(
                f"Warning type height ~{size_ratio:.0%} of net-contents text — "
                f"plausible for {min_mm:g} mm minimum at stated volume"
            )
        else:
            result["style_notes"].append(
                f"Warning type height only ~{size_ratio:.0%} of net-contents text — "
                f"verify ≥{min_mm:g} mm minimum (27 CFR § 16.22)"
            )
    elif b_height > 0 and b_height < 10:
        result["size_ratio_ok"] = False
        result["style_notes"].append(
            "Warning text OCR boxes are very small — verify minimum type size visually"
        )

    return result


def apply_warning_style(fr: FieldResult, style: dict[str, Any]) -> FieldResult:
    """Merge flat-artwork style heuristics into Government Warning field result."""
    if fr.field_name != "Government Warning" or not style.get("checked"):
        return fr

    extra = style.get("style_notes") or []
    notes = fr.notes or ""
    if extra:
        suffix = " | ".join(extra)
        notes = f"{notes} {suffix}".strip() if notes else suffix

    status = fr.status
    if status == STATUS_MATCH and style.get("header_bold_fail"):
        status = STATUS_REVIEW

    return FieldResult(
        fr.field_name,
        fr.expected,
        fr.extracted,
        fr.score,
        status,
        notes,
    )
