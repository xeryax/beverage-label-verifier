"""Beverage-type rules per 27 CFR parts 4, 5, 7, and 16."""

from __future__ import annotations


def normalize_beverage_type(value: str) -> str:
    v = (value or "").strip().lower()
    if v in ("spirits", "distilled spirits"):
        return "Distilled Spirits"
    if v == "wine":
        return "Wine"
    if v in ("beer", "malt", "malt beverage", "beer / malt beverage"):
        return "Beer / Malt Beverage"
    if value in ("Distilled Spirits", "Wine", "Beer / Malt Beverage"):
        return value
    return "Distilled Spirits"


def application_to_expected(application: dict) -> dict:
    """Map API/CSV application payload to matcher expected dict."""
    bt = normalize_beverage_type(
        application.get("beverageType") or application.get("beverage_type", "spirits")
    )
    return {
        "beverage_type": bt,
        "brand": (application.get("brandName") or application.get("brand") or "").strip(),
        "class_type": (application.get("classType") or application.get("class_type") or "").strip(),
        "abv": (application.get("alcoholContent") or application.get("abv") or "").strip(),
        "net_contents": (application.get("netContents") or application.get("net_contents") or "").strip(),
        "producer": (application.get("producer") or "").strip(),
        "country_of_origin": (
            application.get("countryOfOrigin") or application.get("country_of_origin") or ""
        ).strip(),
        "check_warning": True,
        "check_sulfite": False,
    }
