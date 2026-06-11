from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ApplicationData(BaseModel):
    beverageType: str = "spirits"
    brandName: str = ""
    classType: str = ""
    alcoholContent: str = ""
    netContents: str = ""
    producer: str = ""
    countryOfOrigin: str = ""
    governmentWarning: str = ""


class FieldResultSchema(BaseModel):
    status: str
    detected: Optional[str] = None
    expected: Optional[str] = None
    score: Optional[float] = None
    notes: Optional[str] = None


class VerifyResponse(BaseModel):
    overall: str
    processingTimeMs: int
    ocrConfidence: float
    fields: dict[str, FieldResultSchema]
    error: Optional[str] = None


class BatchItemResult(BaseModel):
    itemId: Optional[str] = None
    filename: str
    overall: str
    processingTimeMs: int
    ocrConfidence: float
    fields: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None


class BatchResponse(BaseModel):
    results: list[BatchItemResult]
    summary: dict[str, int]
