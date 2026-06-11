"""Verification orchestration: OCR → extract → match → API response."""

from __future__ import annotations

import io
import re
import time
from typing import Any, Optional

from PIL import Image

from constants import CORE_FIELD_NAMES, GOVERNMENT_WARNING_TEXT, STATUS_MATCH, STATUS_MISMATCH, STATUS_NOT_FOUND, STATUS_REVIEW
from matcher import FieldResult, extract_fields, overall_verdict, validate_fields
from ocr import extract_text
from rules import application_to_expected, normalize_beverage_type
from warning_style import analyze_warning_style, apply_warning_style


def _status_to_api(status: str) -> str:
    if status == STATUS_MATCH:
        return "pass"
    if status in (STATUS_REVIEW, STATUS_NOT_FOUND):
        return "review"
    return "fail"


def _warning_snippet(full_text: str) -> Optional[str]:
    if not full_text:
        return None
    m = re.search(
        r"(GOVERNMENT\s+WARNING:?.{80,})",
        full_text,
        re.IGNORECASE | re.DOTALL,
    )
    if m:
        return m.group(1).strip()[:500]
    m2 = re.search(r"(Government Warning:.{80,})", full_text, re.IGNORECASE | re.DOTALL)
    if m2:
        return m2.group(1).strip()[:500]
    return None


def _field_to_api(fr: FieldResult, full_text: str) -> dict[str, Any]:
    detected = fr.extracted
    if fr.field_name == "Government Warning" and fr.status == STATUS_MATCH:
        detected = _warning_snippet(full_text) or GOVERNMENT_WARNING_TEXT.upper()
    elif detected in ("", "(not found)", None):
        detected = None
    return {
        "status": _status_to_api(fr.status),
        "detected": detected,
        "expected": fr.expected or None,
        "score": round(fr.score, 1) if fr.score else None,
        "notes": fr.notes or None,
    }


def _pad_beer_optional_fields(
    fields: list[FieldResult],
    extracted: dict,
    expected: dict,
) -> list[FieldResult]:
    """Beer labels: surface ABV/producer/country as review when absent (27 CFR 7)."""
    if normalize_beverage_type(expected.get("beverage_type", "")) != "Beer / Malt Beverage":
        return fields
    by_name = {f.field_name: f for f in fields}
    if "Class/Type" in by_name and not (expected.get("class_type") or "").strip():
        fr = by_name["Class/Type"]
        if fr.status == STATUS_MISMATCH:
            out = [x for x in fields if x.field_name != "Class/Type"]
            out.append(FieldResult(
                "Class/Type", "", None, fr.score, STATUS_REVIEW,
                "Class/type not provided in application",
            ))
            fields = out
            by_name = {f.field_name: f for f in fields}
    optional = ("Alcohol Content", "Producer", "Country of Origin")
    out = list(fields)
    for fr in list(out):
        if fr.field_name == "Producer" and fr.status == STATUS_MISMATCH and not (fr.expected or "").strip():
            out = [x for x in out if x.field_name != "Producer"]
            out.append(FieldResult(
                "Producer", "", None, 0.0, STATUS_REVIEW,
                "Optional — not provided in application",
            ))
    for name in optional:
        if name in by_name:
            fr = by_name[name]
            if fr.status == STATUS_NOT_FOUND and (
                "Not provided" in fr.notes or "Could not locate" in fr.notes
            ):
                out = [x for x in out if x.field_name != name]
                out.append(FieldResult(name, "", None, 0.0, STATUS_REVIEW, "Optional for malt beverages — not found on label"))
            elif fr.status == STATUS_MISMATCH and name == "Alcohol Content":
                out = [x for x in out if x.field_name != name]
                out.append(FieldResult(name, fr.expected, fr.extracted, fr.score, STATUS_REVIEW, "Optional for malt beverages — verify ABV"))
            continue
    if "Class/Type" in by_name:
        fr = by_name["Class/Type"]
        if fr.status == STATUS_MISMATCH:
            out = [x for x in out if x.field_name != "Class/Type"]
            out.append(FieldResult(
                "Class/Type", fr.expected, None, fr.score, STATUS_REVIEW,
                "Class/type unclear for malt beverage — verify visually",
            ))
        if name not in by_name:
            out.append(FieldResult(name, expected.get({
                "Alcohol Content": "abv",
                "Producer": "producer",
                "Country of Origin": "country_of_origin",
            }[name], ""), None, 0.0, STATUS_REVIEW, "Optional for malt beverages — not found on label"))
    return out


def _apply_warning_style_fields(
    fields: list[FieldResult],
    ocr: dict,
    expected: dict,
) -> list[FieldResult]:
    if not ocr.get("flat_artwork") or ocr.get("gray") is None:
        return fields
    style = analyze_warning_style(
        ocr["gray"],
        ocr.get("words") or [],
        net_contents=expected.get("net_contents") or "",
    )
    return [
        apply_warning_style(fr, style) if fr.field_name == "Government Warning" else fr
        for fr in fields
    ]


def _ensure_core_fields(fields: list[FieldResult], expected: dict) -> list[FieldResult]:
    by_name = {f.field_name: f for f in fields}
    out = []
    for name in CORE_FIELD_NAMES:
        if name in by_name:
            out.append(by_name[name])
        elif name == "Government Warning" and expected.get("check_warning", True):
            out.append(FieldResult(name, "Required", None, 0.0, STATUS_REVIEW, "Not evaluated"))
    return out


def verify_image(image_bytes: bytes, application: dict) -> dict[str, Any]:
    t0 = time.perf_counter()
    image = Image.open(io.BytesIO(image_bytes))
    ocr = extract_text(image)
    expected = application_to_expected(application)
    extracted = extract_fields(ocr["full_text"], ocr["lines"])
    fields = validate_fields(extracted, expected)
    fields = _pad_beer_optional_fields(fields, extracted, expected)
    fields = _ensure_core_fields(fields, expected)
    fields = _apply_warning_style_fields(fields, ocr, expected)
    fields = [f for f in fields if f.field_name in CORE_FIELD_NAMES]

    overall = overall_verdict(fields)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    return {
        "overall": overall,
        "processingTimeMs": elapsed_ms,
        "ocrConfidence": round(ocr["avg_confidence"], 2),
        "fields": {fr.field_name: _field_to_api(fr, ocr["full_text"]) for fr in fields},
        "error": None,
    }
