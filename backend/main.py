"""TTB Label Verifier — FastAPI application."""

from __future__ import annotations

import csv
import io
import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ocr import warm_up
from schemas import ApplicationData, BatchResponse, VerifyResponse
from verify import verify_image

STATIC_DIR = Path(__file__).resolve().parent / "static"
_executor = ThreadPoolExecutor(max_workers=max(1, int(os.getenv("TTB_WORKERS", "1"))))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    warm_up()
    yield
    _executor.shutdown(wait=False)


app = FastAPI(title="TTB Label Verifier", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/verify", response_model=VerifyResponse)
async def api_verify(
    image: UploadFile = File(...),
    application: str = Form("{}"),
):
    try:
        app_data = json.loads(application) if application else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "Invalid application JSON") from exc
    data = await image.read()
    if not data:
        raise HTTPException(400, "Empty image")
    try:
        result = verify_image(data, app_data)
        return result
    except Exception as exc:
        return {
            "overall": "fail",
            "processingTimeMs": 0,
            "ocrConfidence": 0.0,
            "fields": {},
            "imageQualityNote": None,
            "error": str(exc),
        }


def _parse_manifest(csv_text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(csv_text))
    return [dict(row) for row in reader]


def _verify_one(filename: str, data: bytes, app_row: dict[str, str]) -> dict[str, Any]:
    application = {
        "beverageType": app_row.get("beverageType", "spirits"),
        "brandName": app_row.get("brandName", ""),
        "classType": app_row.get("classType", ""),
        "alcoholContent": app_row.get("alcoholContent", ""),
        "netContents": app_row.get("netContents", ""),
        "producer": app_row.get("producer", ""),
        "countryOfOrigin": app_row.get("countryOfOrigin", ""),
    }
    try:
        r = verify_image(data, application)
        r["filename"] = filename
        r["itemId"] = app_row.get("itemId")
        return r
    except Exception as exc:
        return {
            "itemId": app_row.get("itemId"),
            "filename": filename,
            "overall": "fail",
            "processingTimeMs": 0,
            "ocrConfidence": 0.0,
            "fields": {},
            "imageQualityNote": None,
            "error": str(exc),
        }


@app.post("/api/batch", response_model=BatchResponse)
async def api_batch(
    images: list[UploadFile] = File(...),
    manifest: UploadFile | None = File(None),
    application: str = Form("{}"),
):
    manifest_rows: list[dict[str, str]] = []
    if manifest:
        text = (await manifest.read()).decode("utf-8-sig")
        manifest_rows = _parse_manifest(text)
    by_file = {r["imageFile"]: r for r in manifest_rows if r.get("imageFile")}

    try:
        default_app = json.loads(application) if application else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "Invalid application JSON") from exc

    results: list[dict[str, Any]] = []
    processing_ms = 0
    for img in images:
        data = await img.read()
        filename = img.filename or "unknown"
        row = by_file.get(filename, default_app)
        future = _executor.submit(_verify_one, filename, data, row)
        result = future.result()
        results.append(result)
        processing_ms += int(result.get("processingTimeMs") or 0)
        del data

    severity = {"fail": 0, "review": 1, "pass": 2}
    results.sort(key=lambda r: (severity.get(r.get("overall", "fail"), 0), r.get("filename", "")))

    summary = {"total": len(results), "pass": 0, "review": 0, "fail": 0, "processingTimeMs": processing_ms}
    for r in results:
        summary[r.get("overall", "fail")] = summary.get(r.get("overall", "fail"), 0) + 1

    return {"results": results, "summary": summary}


if STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str):
        index = STATIC_DIR / "index.html"
        if full_path and (STATIC_DIR / full_path).is_file():
            return FileResponse(STATIC_DIR / full_path)
        if index.is_file():
            return FileResponse(index)
        raise HTTPException(404)
