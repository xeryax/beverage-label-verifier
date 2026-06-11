# API Reference

Base URL: `http://localhost:8000` (or deployed host)

## `GET /health`

Returns `{"status": "ok"}`.

## `POST /api/verify`

Verify a single label image.

**Multipart form:**

| Field | Type | Description |
|---|---|---|
| `image` | file | Label image (PNG/JPG) |
| `application` | JSON string | Application form data |

**Application JSON fields:**

```json
{
  "beverageType": "spirits | wine | beer",
  "brandName": "OLD TOM DISTILLERY",
  "classType": "Kentucky Straight Bourbon Whiskey",
  "alcoholContent": "45% Alc./Vol. (90 Proof)",
  "netContents": "750 mL",
  "producer": "Old Tom Distillery, Louisville, KY",
  "countryOfOrigin": "United States"
}
```

**Response:**

```json
{
  "overall": "pass | review | fail",
  "processingTimeMs": 3200,
  "ocrConfidence": 0.91,
  "fields": {
    "Brand Name": { "status": "pass", "detected": "OLD TOM", "expected": "...", "score": 92, "notes": "..." }
  },
  "imageQualityNote": null,
  "error": null
}
```

When the image looks like a bottle photo and OCR confidence is low, `imageQualityNote` explains that automated verification is unreliable and flat COLA artwork is preferred. The overall verdict may be elevated to `review` when the only failures are unreadable fields (no confident mismatches).

**Verdict rollup:**

- Any field `fail` → overall `fail`
- Else any field `review` → overall `review`
- Else → overall `pass`

## `POST /api/batch`

Verify multiple images. Optional CSV manifest maps `imageFile` column to per-row application data.

**Multipart form:**

| Field | Type | Description |
|---|---|---|
| `images` | files[] | Label images |
| `manifest` | file | Optional CSV (same columns as `sample-data/manifest.csv`) |
| `application` | JSON string | Default application when no manifest row matches |

**Response:**

```json
{
  "results": [ { "filename": "...", "overall": "pass", "fields": {}, ... } ],
  "summary": { "total": 10, "pass": 8, "review": 1, "fail": 1, "processingTimeMs": 28500 }
}
```

Results sorted with failures first.

**Behavior notes:**

- **Synchronous:** the HTTP connection stays open until every image is processed; there is no streaming or job queue.
- **Sequential OCR:** with `TTB_WORKERS=1` (default), images are verified one at a time to stay within the 1.5 GB memory cap.
- **Large batches:** 300 flat labels take ~15 minutes at ~3s/label. Use a client timeout of at least 20 minutes (see `scripts/batch_load_test.py --timeout 1200`).
- **Browser UI:** recommend ≤50 images per upload; larger jobs should use the API directly or chunked requests (`--chunk-size 50` in the load-test script).
- **Per-image mapping:** each result includes `filename` (and optional `itemId` from the manifest row).
