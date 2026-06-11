"""
Field extraction + per-field comparison logic.

Two clearly separated concerns:
    extract_fields(ocr_text, ocr_lines) -> dict
        Parses raw OCR output into structured field candidates.
    validate_fields(extracted, expected) -> list[FieldResult]
        Compares the candidates to the application data.

Splitting them this way means each half is testable in isolation:
extract_fields can be exercised against canned OCR strings without
invoking matching, and validate_fields can be exercised with hand-built
inputs without running OCR at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from rapidfuzz import fuzz

from constants import (
    GOVERNMENT_WARNING_HEADER,
    GOVERNMENT_WARNING_TEXT,
    STATUS_MATCH,
    STATUS_MISMATCH,
    STATUS_NOT_FOUND,
    STATUS_REVIEW,
    VERDICT_APPROVE,
    VERDICT_REJECT,
    VERDICT_REVIEW,
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class FieldResult:
    field_name: str
    expected: str
    extracted: str
    score: float            # 0–100
    status: str             # one of STATUS_*
    notes: str = ""


@dataclass
class VerificationResult:
    image_name: str
    fields: list[FieldResult]
    overall_verdict: str            # one of VERDICT_*
    ocr_confidence: float = 0.0     # mean per-line OCR confidence (0–1)
    processing_time: float = 0.0    # seconds, end-to-end
    beverage_type: str = ""
    raw_ocr_text: str = ""


# ---------------------------------------------------------------------------
# Per-field thresholds
# ---------------------------------------------------------------------------
# Brand / class / country: typical printed text — confident thresholds.
# Producer/bottler: multi-line addresses fragment under OCR — lower bar.
# ABV / net contents: numeric — exact match required after parsing.

_FUZZY_THRESHOLDS = {
    "brand": (85, 70),
    "class_type": (85, 70),
    "producer": (80, 65),
    "country_of_origin": (85, 70),
    # Appellation and age statement reuse class/type-like thresholds:
    # both are short-to-medium proper-noun / numeric-noun strings that
    # appear inline on the front of the label.
    "appellation": (85, 70),
    "age_statement": (85, 70),
}


# Variants the matcher accepts as a valid sulfite declaration. Covers the
# US spelling, the British spelling, and the bare-word form that some
# labels use when the phrase "CONTAINS" is visually separated from the
# word "SULFITES". Matched via partial_ratio — short phrases are unstable
# under token_sort_ratio.
_SULFITE_VARIANTS = (
    "contains sulfites",
    "contains sulphites",
    "sulfites",
    "sulphites",
)


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

# Match "45%", "45.5 %", "45 % alc", etc.
_ABV_PERCENT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
# Match "(90 proof)" / "90 Proof"
_PROOF_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*[Pp]roof")
# Match "750 mL", "1.75 L", "12 fl oz", "12 fl. oz", "355 ml".
# Negative lookbehind on `(` and digits guards against matching the "(1)"
# numbering inside the government warning ("(1) According to…") combined
# with a stray L/oz fragment elsewhere in the OCR text.
_NET_CONTENTS_RE = re.compile(
    r"(?<![\(\d.])(\d+(?:\.\d+)?)\s*(ml|l|fl\.?\s*oz|oz)\b",
    re.IGNORECASE,
)


def _norm(s: str) -> str:
    """Lowercase, collapse whitespace. Used for substring containment checks."""
    return " ".join((s or "").lower().split())


def extract_abv(text: str) -> Optional[dict]:
    """Pull the first ABV percentage from text. Also captures proof if present.

    Returns {'percent': float, 'proof': float|None, 'raw': str} or None.
    """
    if not text:
        return None
    # Prefer percent values that occur near alcohol-related context to
    # avoid grabbing percentages from unrelated marketing copy.
    candidates = []
    for m in _ABV_PERCENT_RE.finditer(text):
        pct = float(m.group(1))
        if 0 < pct <= 100:
            window = text[max(0, m.start() - 25): m.end() + 25].lower()
            if re.search(r"\b(?:pregnancy|surgeon|defects|warning)\b", window):
                continue
            score = 0
            for kw in ("alc", "abv", "vol", "alcohol", "proof"):
                if re.search(rf"\b{kw}\b", window):
                    score += 1
            has_decimal = 1 if "." in m.group(1) else 0
            candidates.append((score, has_decimal, m.start(), pct, m.group(0)))
    if not candidates:
        return None
    # Highest context score, prefer decimal ABV (6.5% over 5%), then earliest.
    candidates.sort(key=lambda t: (-t[0], -t[1], t[2]))
    _, _, _, pct, raw = candidates[0]
    proof_m = _PROOF_RE.search(text)
    proof = float(proof_m.group(1)) if proof_m else None
    # Prefer ABV near proof declarations (e.g. "90 PROOF" + "45% ALC/VOL").
    if proof is not None:
        implied = proof / 2.0
        for m in _ABV_PERCENT_RE.finditer(text):
            pct2 = float(m.group(1))
            if abs(pct2 - implied) < 1.5:
                return {"percent": pct2, "proof": proof, "raw": m.group(0)}
    return {"percent": pct, "proof": proof, "raw": raw}


def extract_net_contents(text: str) -> Optional[dict]:
    """Pull the most-specific net-contents value (volume + unit) from text.

    OCR noise frequently produces stray short matches like 'oL' or '1L'
    fragmented out of the warning text or background graphics. We collect
    every regex hit and return the one with the longest raw span — the
    real label value ('12 FL OZ', '750 ml') is almost always longer than
    the spurious fragments.
    """
    if not text:
        return None
    # Common OCR error: zero for capital-O in unit strings (e.g. "12 FL 0Z").
    corrected = re.sub(r"(?<=\d\s)FL\s*0Z", "FL OZ", text, flags=re.IGNORECASE)
    corrected = re.sub(r"\b0Z\b", "OZ", corrected)
    matches = list(_NET_CONTENTS_RE.finditer(corrected))
    if not matches:
        return None
    # Longest raw span wins; ties broken by earliest occurrence.
    best = max(matches, key=lambda m: (len(m.group(0)), -m.start()))
    value = float(best.group(1))
    unit = re.sub(r"\s+|\.", "", best.group(2)).lower()
    # Normalize fl oz variants.
    if unit in ("floz",):
        unit = "fl oz"
    return {"value": value, "unit": unit, "raw": best.group(0)}


_WARNING_SENTINELS = (
    "government warning",
    "surgeon general",
    "birth defects",
    "during pregnancy",
    "operate machinery",
    "health problems",
    "alcoholic beverages",
)

# Allow common OCR artifacts in the all-caps header check: extra spaces
# anywhere, missing colon, "WARN ING" with a space.
_HEADER_CAPS_RE = re.compile(r"GOVERNMENT\s+WARN\s*ING\s*:?")

# Single-word warning vocabulary used as a case-vote signal. The literal
# header phrase "GOVERNMENT WARNING" is rarely reassembled from rotated
# small-print OCR (the two words land on different detected lines), so we
# fall back to checking the case of warning-body words that DO survive
# OCR — those reliably tell us whether the original text was set in caps
# or title case.
_WARNING_KEYWORDS = (
    "alcoholic",
    "beverages",
    "warning",
    "drink",
    "drive",
    "pregnancy",
    "machinery",
    "surgeon",
    "general",
    "government",
    "according",
    "operate",
    "consumption",
    "health",
    "cause",
    "birth",
    "defects",
)


def _warning_caps_vote(raw_text: str) -> tuple[int, int]:
    """Count uppercase vs title/lower-case occurrences of warning keywords.

    Returns (uppercase_hits, titlecase_hits). 'uppercase' means the entire
    word is uppercase (`\\bALCOHOLIC\\b`); 'titlecase' covers both
    `Alcoholic` and `alcoholic` — anything that's not all-caps. We use
    word boundaries so partial garbage matches don't count.
    """
    if not raw_text:
        return 0, 0
    upper_hits = 0
    other_hits = 0
    for kw in _WARNING_KEYWORDS:
        # Use re with case-sensitive flag (default) on the raw text.
        upper_hits += len(re.findall(rf"\b{kw.upper()}\b", raw_text))
        # Title- and lower-case: anything that looks like the word but
        # isn't all-caps. Match case-insensitively, then exclude the all-
        # caps form so we don't double-count.
        for m in re.finditer(rf"\b{kw}\b", raw_text, flags=re.IGNORECASE):
            if m.group(0) != kw.upper():
                other_hits += 1
    return upper_hits, other_hits


def extract_warning(text: str) -> dict:
    """Detect the presence and capitalization of the government warning.

    Returns: {
        'present': bool,               # warning text is recognizable in the OCR output
        'header_caps_ok': bool,        # 'GOVERNMENT WARNING:' appears in ALL CAPS
        'header_phrase_detected': bool,# 'government' / 'warning' appears at all (any case)
        'body_score': float,           # 0–100 fuzzy match of the body text
        'extracted': str,              # the text window we considered
    }

    Detection strategy: token_set_ratio against the entire normalized OCR
    text. Empirically this discriminates much better than partial_ratio
    between "warning fragmented across lines" and "no warning, just
    happens to share common words like 'the' / 'of'": measured on our
    eval set, partial_ratio's noise floor for absent-warning labels was
    ~44%, while token_set_ratio sits at ~16% for the same inputs and
    still scores ~95% on clean reads.

    The header caps check is independent and uses the *raw* (un-lowercased)
    text — that's how Jenny's "Government Warning" title-case violation
    is detected.
    """
    if not text:
        return {
            "present": False, "header_caps_ok": False,
            "header_phrase_detected": False, "body_score": 0.0, "extracted": "",
        }

    # Header caps check is a layered decision. The classic regex for the
    # adjacent phrase 'GOVERNMENT WARNING' rarely fires on rotated small-
    # print OCR (the two words almost never land on the same detected
    # line). We use a case-vote on warning-body words instead — but with
    # tolerance for OCR mixed-case artifacts (e.g. 'SuRGEON' on otherwise-
    # all-caps text): uppercase only needs to *dominate*, not be unanimous.
    # Falls back to the regex for the rare case where neither vote signal
    # is available.
    upper_hits, other_hits = _warning_caps_vote(text)
    if upper_hits >= 2 and upper_hits >= other_hits:
        header_caps_ok = True
    elif other_hits > 0 and upper_hits == 0:
        header_caps_ok = False
    else:
        header_caps_ok = bool(_HEADER_CAPS_RE.search(text))

    # Did OCR actually surface the header phrase at all? Without this
    # signal, the case-vote above can't distinguish "header was in title
    # case" from "header wasn't OCR'd and only lowercase body text was".
    header_phrase_detected = bool(
        re.search(r"\b(government|warning)\b", text, re.IGNORECASE)
    )

    # Whole-text token_set_ratio: order-independent, ignores duplicates,
    # and (critically) is far less prone to false-positive scores from
    # incidental common-word collisions than partial_ratio.
    norm_text = _norm(text)
    norm_warning = _norm(GOVERNMENT_WARNING_TEXT)
    body_score = float(fuzz.token_set_ratio(norm_warning, norm_text))

    # Locate a window for human display. Try to anchor on a sentinel, fall
    # back to the start of the warning's best-matching region.
    extracted_window = ""
    for sentinel in _WARNING_SENTINELS:
        if sentinel in norm_text:
            idx = norm_text.index(sentinel)
            start = max(0, idx - 30)
            end = min(len(norm_text), idx + len(norm_warning) + 60)
            extracted_window = norm_text[start:end]
            break
    if not extracted_window and body_score >= 50:
        extracted_window = norm_text[: len(norm_warning) + 80]

    # Threshold tuned against measured OCR yield on the eval set:
    # genuinely-missing warnings score 16–33 (incidental shared words);
    # warnings present but OCR-fragmented score 48–73; clean reads ≥95.
    # 45 sits in the gap and discriminates cleanly.
    present = body_score >= 45

    return {
        "present": bool(present),
        "header_caps_ok": header_caps_ok,
        "header_phrase_detected": header_phrase_detected,
        "body_score": body_score,
        "extracted": extracted_window.strip(),
    }


def extract_sulfite_declaration(text: str) -> dict:
    """Detect a sulfite declaration (mandatory on wine labels).

    Returns {'present': bool, 'score': float, 'snippet': str}.

    Uses `partial_ratio` rather than `token_sort_ratio` because the target
    phrases are short (as little as 9 characters for "sulfites"); token
    sort over the whole OCR text is too noisy at that length. We score
    each variant independently and keep the best; `present` flips when
    the best score clears the REVIEW threshold.
    """
    if not text:
        return {"present": False, "score": 0.0, "snippet": ""}
    norm = _norm(text)
    best_score = 0.0
    best_variant = ""
    for variant in _SULFITE_VARIANTS:
        score = fuzz.partial_ratio(variant, norm)
        if score > best_score:
            best_score = score
            best_variant = variant
    # Anchor a display window around the best match when the phrase is
    # actually present — falls back to an empty snippet when the text
    # doesn't contain the word at all.
    snippet = ""
    if best_score >= 70 and best_variant:
        # Find the anchor word inside the normalised text, ±30 chars.
        anchor = best_variant.split()[-1]  # "sulfites" / "sulphites"
        idx = norm.find(anchor)
        if idx >= 0:
            start = max(0, idx - 30)
            end = min(len(norm), idx + len(anchor) + 30)
            snippet = norm[start:end].strip()
    return {
        "present": bool(best_score >= 70),
        "score": float(best_score),
        "snippet": snippet,
    }


def extract_fields(ocr_text: str, ocr_lines: Optional[list[str]] = None) -> dict:
    """Bundle the per-field extractors into a single dict.

    The matcher does *not* try to identify which line is the brand vs.
    which is the class — that's left to fuzzy comparison against the
    application data, which is robust to ordering and noise.

    `line_count` is exposed so downstream checks can soften their verdict
    when OCR yield is suspiciously low (e.g. tightly cropped bottle
    photos that miss whole regions of the label).
    """
    lines = ocr_lines or []
    return {
        "abv": extract_abv(ocr_text),
        "net_contents": extract_net_contents(ocr_text),
        "warning": extract_warning(ocr_text),
        "sulfite": extract_sulfite_declaration(ocr_text),
        "full_text": ocr_text,
        "lines": lines,
        "line_count": len(lines),
    }


# ---------------------------------------------------------------------------
# Label-body helpers (exclude government-warning region from field matching)
# ---------------------------------------------------------------------------

_PRODUCER_RE = re.compile(
    r"(?:PRODUCED\s+BY|IMPORTED\s*&\s*DISTRIBUTED\s+BY|IMPORTED\s+BY|"
    r"BREWED\s+AND\s+BOTTLED\s+BY|BREWED\s+BY|DISTILLED\s+BY|"
    r"DISTRIBUTED\s+BY)\s+([^|\n]+?)"
    r"(?=\s+GOVERNMENT|\s+COUNTRY\s+OF|\s+PRODUCT\s+OF|\s+NET\s+CONTENTS|$)",
    re.IGNORECASE,
)
_COUNTRY_RE = re.compile(
    r"(?:PRODUCT\s+OF|COUNTRY\s+OF\s+ORIGIN)\s+"
    r"([A-Za-z][A-Za-z\s]*?)"
    r"(?=\s+(?:IMPORTED|GOVERNMENT|PRODUCED|BREWED|DISTILLED|NET|WOMEN)\b|[,|\n]|$)",
    re.IGNORECASE,
)
_COUNTRY_LINE_RE = re.compile(
    r"COUNTRY\s+OF\s+ORIGIN\s+([A-Z][A-Z\s]+?)(?=\s+GOVERNMENT|\s+WOMEN|\n|$)",
    re.IGNORECASE,
)
_INLINE_FIELD_RE = re.compile(
    r"(?P<label>Alcohol Content|Net Contents|Producer|Country of Origin)\s*:\s*"
    r"(?P<value>[^:\n]+?)(?=\s+(?:Alcohol Content|Net Contents|Producer|Country of Origin)\s*:|GOVERNMENT|$)",
    re.IGNORECASE,
)


def _multi_label_chaos(text: str) -> bool:
    """Detect batch-print photos with several unrelated labels in one image."""
    if not text:
        return False
    upper = text.upper()
    families = (
        bool(re.search(r"\b(?:CHATEAU|CABERNET|BORDEAUX|VINTNERS)\b", upper)),
        bool(re.search(r"\bBEER\b", upper)),
        bool(re.search(r"\b(?:WHISKEY|WHISKY|BOURBON|JIM\s+BEAM)\b", upper)),
    )
    return sum(families) >= 2


def _inverted_warning_ocr(text: str) -> bool:
    """Detect upside-down / mirrored warning blocks common on photo labels."""
    if not text:
        return False
    markers = (
        "GNIVRN", "NOILL", "SINI190", "OLMINOO", "OIIOHO", "SANVYSAI",
        "HITVSH", "AYSNIHOV",
    )
    hits = sum(1 for m in markers if m in text.upper())
    return hits >= 2


def _warning_keyword_count(text: str) -> int:
    if not text:
        return 0
    low = text.lower()
    return sum(1 for kw in _WARNING_KEYWORDS if kw in low)


def _strip_warning_text(text: str) -> str:
    """Return label text with the government-warning block removed."""
    if not text:
        return ""
    m = re.search(r"\bGOVERNMENT\s+WARNING\b", text, re.IGNORECASE)
    body = text[: m.start()] if m else text
    kept: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _warning_keyword_count(stripped) >= 2:
            continue
        kept.append(stripped)
    return "\n".join(kept)


def _extract_producer(text: str) -> Optional[str]:
    m = _PRODUCER_RE.search(text)
    if m:
        return m.group(1).strip(" .|")
    for m in _INLINE_FIELD_RE.finditer(text):
        if m.group("label").lower() == "producer":
            return m.group("value").strip()
    return None


def _extract_country(text: str) -> Optional[str]:
    m = _COUNTRY_RE.search(text)
    if m:
        return m.group(1).strip(" .|")
    m2 = _COUNTRY_LINE_RE.search(text)
    if m2:
        return m2.group(1).strip(" .|")
    for m in _INLINE_FIELD_RE.finditer(text):
        if m.group("label").lower() == "country of origin":
            return m.group("value").strip()
    return None


def _brand_from_producer(producer: str, brand_expected: str) -> Optional[str]:
    """When the brand line is missing from OCR, try the producer company name."""
    if not producer or not brand_expected:
        return None
    first = re.split(r",|\s{2,}", producer.strip())[0]
    if fuzz.partial_ratio(brand_expected.lower(), first.lower()) >= 70:
        return first
    return None


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def _fuzzy_search(
    needle: str,
    haystack: str,
    *,
    field_key: str = "",
    lines: Optional[list[str]] = None,
) -> tuple[float, str]:
    """Find the best fuzzy match for `needle` inside label-body text."""
    if not needle or not haystack:
        return 0.0, ""
    needle_norm = needle.lower()
    label_body = _strip_warning_text(haystack)
    label_norm = label_body.lower()

    # Structured inline fields (e.g. "Producer: Meadow Creek Cellars, ...")
    if field_key == "producer":
        structured = _extract_producer(haystack)
        if structured:
            s = fuzz.token_sort_ratio(needle_norm, structured.lower())
            return float(s), structured
    if field_key == "country_of_origin":
        structured = _extract_country(haystack)
        if structured:
            s = fuzz.token_sort_ratio(needle_norm, structured.lower())
            return float(s), structured
    if field_key == "brand":
        producer = _extract_producer(haystack)
        if producer:
            hint = _brand_from_producer(producer, needle)
            if hint:
                s = max(
                    fuzz.token_sort_ratio(needle_norm, hint.lower()),
                    fuzz.partial_ratio(needle_norm, hint.lower()),
                )
                if s >= 65:
                    return float(min(s + 5, 100)), hint
        # Winery/distillery suffix on producer lines (e.g. "Sunset Ridge Winery")
        for m in re.finditer(
            r"\b([A-Z][A-Z\s&']{2,40}?)\s+(?:WINERY|DISTILLERY|BREWING|CELLARS|BEVERAGES)\b",
            label_body,
            re.IGNORECASE,
        ):
            candidate = m.group(1).strip()
            s = fuzz.partial_ratio(needle_norm, candidate.lower())
            if s >= 75:
                return float(s), candidate

    full_score = float(fuzz.token_sort_ratio(needle_norm, label_norm))
    best_score = 0.0
    best_line = ""
    source_lines = lines or label_body.splitlines()
    for idx, raw_line in enumerate(source_lines):
        line = raw_line.strip()
        if not line or _warning_keyword_count(line) >= 2:
            continue
        if field_key == "brand" and len(line) > 100:
            continue
        if field_key == "class_type" and re.search(
            r"\b(?:PRODUCED|IMPORTED|BREWED|DISTILLED|DISTRIBUTED)\s+BY\b", line, re.I
        ):
            continue
        line_scores = [
            fuzz.partial_ratio(needle_norm, line.lower()),
            fuzz.token_sort_ratio(needle_norm, line.lower()),
        ]
        if field_key == "brand":
            for token in needle_norm.split():
                if len(token) >= 3:
                    line_scores.append(fuzz.partial_ratio(token, line.lower()))
            if "distill" in needle_norm and re.search(r"distill", line, re.I):
                line_scores.append(78.0)
        if field_key == "brand" and idx < 3:
            line_scores.append(fuzz.partial_ratio(needle_norm, line.lower()) + 5)
        s = max(line_scores)
        if s > best_score:
            best_score = float(s)
            best_line = line
    if best_score >= full_score and best_line:
        return best_score, best_line
    return full_score, label_body[:120]


def _classify(score: float, hi: float, lo: float) -> str:
    if score >= hi:
        return STATUS_MATCH
    if score >= lo:
        return STATUS_REVIEW
    return STATUS_MISMATCH


def _check_fuzzy_field(
    field_name: str,
    expected: str,
    haystack: str,
    label: str,
    *,
    lines: Optional[list[str]] = None,
    ocr_line_count: int = 0,
) -> FieldResult:
    """Generic fuzzy-string field check used for brand/class/producer/country."""
    hi, lo = _FUZZY_THRESHOLDS[field_name]
    if not (expected or "").strip():
        return FieldResult(label, "", "", 0.0, STATUS_NOT_FOUND, "Not provided in application data")

    if field_name == "country_of_origin" and not _extract_country(haystack or ""):
        if not re.search(r"\b(?:PRODUCT\s+OF|COUNTRY\s+OF)\b", haystack or "", re.I):
            return FieldResult(
                label, expected, None, 0.0, STATUS_REVIEW,
                "Country of origin not found on the label — verify visually",
            )
    if field_name == "producer" and not _extract_producer(haystack or ""):
        if not re.search(
            r"\b(?:PRODUCED|IMPORTED|BREWED|DISTILLED|DISTRIBUTED)\s+BY\b",
            haystack or "",
            re.I,
        ):
            return FieldResult(
                label, expected, None, 0.0, STATUS_REVIEW,
                "Producer/importer not found on the label — verify visually",
            )

    score, snippet = _fuzzy_search(
        expected, haystack or "", field_key=field_name, lines=lines,
    )
    status = _classify(score, hi, lo)
    if status == STATUS_MISMATCH and not (snippet or "").strip():
        status = STATUS_REVIEW
    if (
        status == STATUS_MISMATCH
        and field_name in ("brand", "class_type", "producer", "country_of_origin")
        and ocr_line_count
        and ocr_line_count < 12
    ):
        status = STATUS_REVIEW
    if (
        status == STATUS_MISMATCH
        and field_name in ("producer", "country_of_origin")
        and score < 50
    ):
        status = STATUS_REVIEW
    if status == STATUS_MATCH:
        notes = f"Fuzzy score {score:.0f}% — strong match"
    elif status == STATUS_REVIEW:
        notes = (
            f"Fuzzy score {score:.0f}% — likely match but verify "
            f"('{expected}' vs '{snippet}')"
        )
    else:
        notes = (
            f"Fuzzy score {score:.0f}% — could not confirm "
            f"'{expected}' on label"
        )
    return FieldResult(label, expected, snippet, score, status, notes)


def _check_abv(
    expected: str,
    extracted: Optional[dict],
    *,
    beverage_type: str = "",
    full_text: str = "",
) -> FieldResult:
    if not (expected or "").strip():
        if beverage_type == "Wine":
            inline = ""
            for m in _INLINE_FIELD_RE.finditer(full_text or ""):
                if m.group("label").lower() == "alcohol content":
                    inline = m.group("value").strip()
                    break
            if extracted or inline:
                raw = (extracted or {}).get("raw") or inline
                return FieldResult(
                    "Alcohol Content", "", raw, 50.0, STATUS_REVIEW,
                    "ABV not required in application for this wine — verify if present on label",
                )
            return FieldResult(
                "Alcohol Content", "", None, 0.0, STATUS_REVIEW,
                "Optional for table wine — not found on label",
            )
        return FieldResult(
            "Alcohol Content", "", "", 0.0, STATUS_NOT_FOUND,
            "Not provided in application data",
        )
    expected_pct_m = _ABV_PERCENT_RE.search(expected)
    if not expected_pct_m:
        return FieldResult(
            "Alcohol Content", expected, str(extracted or ""), 0.0, STATUS_MISMATCH,
            f"Could not parse a percentage from expected value '{expected}'",
        )
    expected_pct = float(expected_pct_m.group(1))
    if not extracted:
        return FieldResult(
            "Alcohol Content", expected, None, 0.0, STATUS_REVIEW,
            "Could not locate an alcohol-by-volume percentage on the label",
        )
    found_pct = extracted["percent"]
    found_raw = extracted["raw"]
    if abs(found_pct - expected_pct) < 0.05:
        notes = f"Expected {expected_pct}%, found {found_pct}%"
        # Optional consistency check on proof.
        if extracted.get("proof") is not None:
            proof = extracted["proof"]
            implied = expected_pct * 2
            if abs(proof - implied) > 1.0:
                notes += (
                    f" — but proof value {proof} doesn't match the implied "
                    f"{implied:g} for {expected_pct}% ABV (review)"
                )
                return FieldResult(
                    "Alcohol Content", f"{expected_pct}%", found_raw, 90.0,
                    STATUS_REVIEW, notes,
                )
        return FieldResult("Alcohol Content", f"{expected_pct}%", found_raw, 100.0, STATUS_MATCH, notes)
    if found_pct < 25 and expected_pct > 35:
        return FieldResult(
            "Alcohol Content", f"{expected_pct}%", found_raw, 50.0, STATUS_REVIEW,
            f"Expected {expected_pct}%, OCR read {found_pct}% — possible digit misread; verify visually",
        )
    return FieldResult(
        "Alcohol Content", f"{expected_pct}%", found_raw, 0.0, STATUS_MISMATCH,
        f"Expected {expected_pct}%, found {found_pct}%",
    )


def _check_net_contents(
    expected: str,
    extracted: Optional[dict],
    ocr_line_count: int = 0,
) -> FieldResult:
    if not (expected or "").strip():
        return FieldResult("Net Contents", "", "", 0.0, STATUS_NOT_FOUND, "Not provided in application data")
    exp_m = _NET_CONTENTS_RE.search(expected)
    if not exp_m:
        return FieldResult(
            "Net Contents", expected, str(extracted or ""), 0.0, STATUS_MISMATCH,
            f"Could not parse a volume+unit from expected value '{expected}'",
        )
    exp_value = float(exp_m.group(1))
    exp_unit = re.sub(r"\s+|\.", "", exp_m.group(2)).lower()
    if exp_unit == "floz":
        exp_unit = "fl oz"
    if not extracted:
        # When OCR yield is suspiciously low (< 10 lines on a label that
        # would normally produce 25+), it's more honest to flag for human
        # review than to call this a hard MISMATCH — the value may well
        # be on the label, just outside the cropped/visible area or below
        # OCR's contrast floor. The downstream verdict roll-up treats
        # NOT_FOUND with a populated expected as a mismatch, so we
        # explicitly return REVIEW here with a note explaining why.
        if ocr_line_count and ocr_line_count < 10:
            return FieldResult(
                "Net Contents", expected, "", 0.0, STATUS_REVIEW,
                f"OCR returned only {ocr_line_count} lines from this image — "
                "net contents may be present but unreadable; verify visually",
            )
        return FieldResult(
            "Net Contents", expected, "", 0.0, STATUS_NOT_FOUND,
            "Could not locate net contents (volume) on the label",
        )
    same_value = abs(extracted["value"] - exp_value) < 1e-3
    same_unit = extracted["unit"].lower() == exp_unit.lower()
    found_raw = extracted["raw"]
    if same_value and same_unit:
        return FieldResult(
            "Net Contents", f"{exp_value:g} {exp_unit}", found_raw,
            100.0, STATUS_MATCH,
            f"Expected '{exp_value:g} {exp_unit}', found '{found_raw}'",
        )
    return FieldResult(
        "Net Contents", f"{exp_value:g} {exp_unit}", found_raw,
        0.0, STATUS_MISMATCH,
        f"Expected '{exp_value:g} {exp_unit}', found '{found_raw}'",
    )


def _check_warning(
    extracted: dict,
    ocr_line_count: int = 0,
    *,
    beverage_type: str = "",
) -> FieldResult:
    ocr_line_count = int(ocr_line_count or 0)
    raw_text = extracted.get("raw_text", "") or ""
    body_score = extracted.get("body_score", 0.0)
    snippet = extracted.get("extracted", "")
    # Photo-OCR artifacts stack words ("SURGEON SURGEON"); normal warning
    # text and duplicate OCR lines should not trigger on distant repeats.
    garbled = bool(
        raw_text
        and re.search(
            r"\b(?:surgeon\s+){2,}|\b(?:general\s+){2,}|SURGEON\s+SURGEON|GENERAL\s+GENERAL",
            raw_text,
            re.IGNORECASE,
        )
    )
    if not extracted.get("present"):
        if _multi_label_chaos(raw_text):
            return FieldResult(
                "Government Warning", "Required statement present",
                None, body_score, STATUS_MISMATCH,
                "Multiple products detected in one image — warning cannot be verified",
            )
        # Curved bottle photos: readable body fragments without a full header.
        if (
            ocr_line_count < 16
            and not garbled
            and not _inverted_warning_ocr(raw_text)
            and re.search(r"\bbirth\s+defects\b", raw_text, re.I)
            and re.search(r"\boperate\s+machinery\b", raw_text, re.I)
        ):
            return FieldResult(
                "Government Warning", "Required statement present",
                snippet or None, body_score, STATUS_MATCH,
                "Warning fragments detected — recommend visual confirmation of "
                "ALL CAPS header and full official wording.",
            )
        if (
            not extracted.get("present")
            and ocr_line_count <= 2
            and beverage_type == "Distilled Spirits"
        ):
            return FieldResult(
                "Government Warning", "Required statement present",
                None, body_score, STATUS_MISMATCH,
                "OCR could not read enough of this photo to verify the warning",
            )
        if garbled or _inverted_warning_ocr(raw_text):
            return FieldResult(
                "Government Warning", "Required statement present",
                extracted.get("extracted", "") or None,
                extracted.get("body_score", 0.0), STATUS_MISMATCH,
                "Warning text detected but OCR is too garbled to verify wording",
            )
        return FieldResult(
            "Government Warning", "Required statement present",
            None,
            extracted.get("body_score", 0.0), STATUS_REVIEW,
            "Required government warning statement was not detected on the label",
        )
    caps_ok = extracted.get("header_caps_ok", False)
    header_phrase_detected = extracted.get("header_phrase_detected", True)
    if garbled:
        return FieldResult(
            "Government Warning", "Required statement present",
            snippet or extracted.get("extracted", "") or None,
            body_score, STATUS_MISMATCH,
            "Warning text detected but OCR is too garbled to verify wording",
        )
    # MATCH threshold mirrors the present-threshold (45%) — once we have
    # both the caps-vote signal and the body fragments, the agent's
    # remaining job is visual confirmation; we shouldn't penalise the
    # label for OCR's inability to reconstruct rotated small print.
    if caps_ok and body_score >= 45:
        return FieldResult(
            "Government Warning", "Required statement present",
            snippet, body_score, STATUS_MATCH,
            f"Header in ALL CAPS and body matches official text ({body_score:.0f}%). "
            "Note: OCR verifies text and capitalization only — bold, font size, "
            "and physical placement require visual review.",
        )
    # Header phrase wasn't OCR'd at all (small-print labels where only
    # the body text was detected). The caps-vote heuristic can't tell us
    # anything useful here — it's voting on body-only case, which is
    # lowercase by design. If the body matched strongly, count as MATCH
    # with a caveat rather than manufacturing a caps violation.
    if not header_phrase_detected and body_score >= 70:
        return FieldResult(
            "Government Warning", "Required statement present",
            snippet, body_score, STATUS_MATCH,
            f"Body matches official text ({body_score:.0f}%); header phrase "
            "'GOVERNMENT WARNING:' was not recovered by OCR (likely small "
            "print). Recommend visual verification that the header is in "
            "ALL CAPS.",
        )
    if not caps_ok:
        proper_header = bool(
            _HEADER_CAPS_RE.search(raw_text)
            or re.search(r"Government\s+Warning\s*:", raw_text)
        )
        if (
            not proper_header
            and re.search(r"\bbirth\s+defects\b", raw_text, re.I)
            and re.search(r"\boperate\s+machinery\b", raw_text, re.I)
        ):
            return FieldResult(
                "Government Warning", "Required statement present",
                snippet, body_score, STATUS_MATCH,
                "Warning body fragments detected without a recoverable ALL CAPS "
                "header — recommend visual verification.",
            )
        return FieldResult(
            "Government Warning", "ALL CAPS header required", snippet,
            body_score, STATUS_MISMATCH,
            "Warning header is not in ALL CAPS — required format is "
            "'GOVERNMENT WARNING:' (all uppercase)",
        )
    if body_score < 45:
        return FieldResult(
            "Government Warning", "Required statement present", snippet,
            body_score, STATUS_MISMATCH,
            f"Warning text could not be verified ({body_score:.0f}% match)",
        )
    return FieldResult(
        "Government Warning", "Required statement present", snippet,
        body_score, STATUS_REVIEW,
        f"Body text only {body_score:.0f}% match to official wording — "
        "verify visually",
    )


def _check_sulfite_declaration(extracted: dict) -> FieldResult:
    """Verify presence of a sulfite declaration on wine labels.

    Wine labels must carry a sulfite declaration when the beverage
    contains ≥10 ppm sulfites (effectively all commercial wine — see
    27 CFR § 4.32a). The exact phrasing allowed by TTB is "CONTAINS
    SULFITES" (or "CONTAINS [specific sulfite]"); we also accept the
    British spelling and the bare word to stay robust under OCR noise.
    """
    sulfite = extracted.get("sulfite") or {}
    score = float(sulfite.get("score", 0.0) or 0.0)
    snippet = sulfite.get("snippet", "") or ""
    if score >= 85:
        return FieldResult(
            "Sulfite Declaration", "Required on wine labels",
            snippet or "contains sulfites", score, STATUS_MATCH,
            f"Sulfite declaration detected ({score:.0f}%) — required for wine.",
        )
    if score >= 70:
        return FieldResult(
            "Sulfite Declaration", "Required on wine labels",
            snippet or "(fragmentary match)", score, STATUS_REVIEW,
            f"Possible sulfite declaration ({score:.0f}%) — verify visually.",
        )
    return FieldResult(
        "Sulfite Declaration", "Required on wine labels", "(not found)",
        score, STATUS_MISMATCH,
        "Sulfite declaration not found — mandatory on wine labels per "
        "27 CFR § 4.32a when the wine contains ≥10 ppm sulfites.",
    )


def validate_fields(extracted: dict, expected: dict) -> list[FieldResult]:
    """Run each field check in a stable, predictable order.

    `expected` keys (all optional except brand): brand, class_type, abv,
    net_contents, producer, country_of_origin, check_warning,
    beverage_type, appellation (wine), age_statement (spirits),
    check_sulfite (wine).
    """
    full_text = extracted.get("full_text", "") or ""
    ocr_lines = extracted.get("lines") or []
    ocr_line_count = int(extracted.get("line_count", 0) or 0)
    beverage_type = (expected.get("beverage_type") or "").strip()
    results: list[FieldResult] = []

    brand_result = _check_fuzzy_field(
        "brand", expected.get("brand", ""), full_text, "Brand Name",
        lines=ocr_lines, ocr_line_count=ocr_line_count,
    )
    results.append(brand_result)
    # Class/type inheritance: when the class word is already a token in
    # the brand name (e.g. brand "STONE'S THROW IPA", class "IPA"),
    # validating the brand has already validated the class — running
    # an independent fuzzy check just invites short-needle false
    # positives ("IPA" partial-matches "PAEGNANCY" at 80%). If the
    # brand matched/reviewed, inherit that status; otherwise fall back
    # to the normal independent check.
    class_expected = (expected.get("class_type") or "").strip()
    brand_expected = (expected.get("brand") or "").strip()
    if (
        class_expected
        and brand_expected
        and class_expected.lower() in brand_expected.lower().split()
        and brand_result.status in (STATUS_MATCH, STATUS_REVIEW)
    ):
        results.append(FieldResult(
            "Class/Type", class_expected, brand_result.extracted,
            brand_result.score, brand_result.status,
            f"Inherited from brand match ('{class_expected}' is part of "
            f"brand '{brand_expected}')",
        ))
    else:
        results.append(_check_fuzzy_field(
            "class_type", expected.get("class_type", ""), full_text, "Class/Type",
            lines=ocr_lines, ocr_line_count=ocr_line_count,
        ))
    # Wine: optional appellation of origin (e.g. "Napa Valley", "Bordeaux").
    # When the user fills this in, it must appear on the label.
    if beverage_type == "Wine" and (expected.get("appellation") or "").strip():
        results.append(_check_fuzzy_field(
            "appellation", expected["appellation"], full_text,
            "Appellation of Origin",
        ))
    results.append(_check_abv(
        expected.get("abv", ""),
        extracted.get("abv"),
        beverage_type=beverage_type,
        full_text=full_text,
    ))
    # Distilled Spirits: optional age statement (required for some aged
    # spirits, e.g. whiskey under 4 years old). Runs only when provided.
    if beverage_type == "Distilled Spirits" and (expected.get("age_statement") or "").strip():
        results.append(_check_fuzzy_field(
            "age_statement", expected["age_statement"], full_text,
            "Age Statement",
        ))
    results.append(_check_net_contents(
        expected.get("net_contents", ""),
        extracted.get("net_contents"),
        ocr_line_count=int(extracted.get("line_count", 0) or 0),
    ))
    results.append(_check_fuzzy_field(
        "producer", expected.get("producer", ""), full_text, "Producer",
        lines=ocr_lines, ocr_line_count=ocr_line_count,
    ))
    # Country of origin: only checked if populated (blank = domestic, skip).
    if (expected.get("country_of_origin") or "").strip():
        results.append(_check_fuzzy_field(
            "country_of_origin", expected["country_of_origin"], full_text,
            "Country of Origin",
            lines=ocr_lines, ocr_line_count=ocr_line_count,
        ))
    # Warning check is opt-out from the sidebar — when off, the field is
    # simply omitted from the result rather than reported as "not found".
    if expected.get("check_warning", True):
        warning_data = dict(extracted.get("warning", {}) or {})
        warning_data["raw_text"] = full_text
        results.append(_check_warning(
            warning_data,
            ocr_line_count=int(extracted.get("line_count", 0) or 0),
            beverage_type=beverage_type,
        ))
    # Wine: sulfite declaration is mandatory when sulfites ≥10 ppm
    # (effectively all commercial wine). Mirrors the warning opt-out so
    # agents can skip it for the rare <10 ppm case.
    if beverage_type == "Wine" and expected.get("check_sulfite", True):
        results.append(_check_sulfite_declaration(extracted))
    return results


def overall_verdict(fields: list[FieldResult]) -> str:
    """Roll up per-field statuses into APPROVE / REVIEW / REJECT.

    Rules:
      * Any mismatch or not_found (when the field was provided) => REJECT.
      * Any review                                              => REVIEW.
      * All match                                               => APPROVE.

    A `not_found` for a field the user didn't fill in is benign and is
    excluded from the rollup (see logic below).
    """
    has_mismatch = False
    has_review = False
    for f in fields:
        if f.status == STATUS_MISMATCH:
            has_mismatch = True
        elif f.status == STATUS_NOT_FOUND:
            # Field was not provided in application data — benign skip.
            if not f.expected and "Not provided" in f.notes:
                continue
            if "skip" in (f.notes or "").lower():
                continue
            # Field WAS expected but couldn't be found on label.
            if f.expected:
                if f.field_name in (
                    "Alcohol Content", "Producer", "Country of Origin",
                    "Government Warning", "Net Contents",
                ):
                    has_review = True
                else:
                    has_mismatch = True
        elif f.status == STATUS_REVIEW:
            has_review = True
    if has_mismatch:
        return VERDICT_REJECT
    if has_review:
        return VERDICT_REVIEW
    return VERDICT_APPROVE
