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
  "error": null
}
```

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
  "summary": { "total": 10, "pass": 8, "review": 1, "fail": 1 }
}
```

Results sorted with failures first.
