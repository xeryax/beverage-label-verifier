"""TTB label verifier constants — 27 CFR § 16.21 official warning text."""

GOVERNMENT_WARNING_TEXT = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women "
    "should not drink alcoholic beverages during pregnancy because of "
    "the risk of birth defects. (2) Consumption of alcoholic beverages "
    "impairs your ability to drive a car or operate machinery, and may "
    "cause health problems."
)

GOVERNMENT_WARNING_HEADER = "GOVERNMENT WARNING:"

STATUS_MATCH = "match"
STATUS_REVIEW = "review"
STATUS_MISMATCH = "mismatch"
STATUS_NOT_FOUND = "not_found"

VERDICT_APPROVE = "pass"
VERDICT_REVIEW = "review"
VERDICT_REJECT = "fail"

CORE_FIELD_NAMES = (
    "Brand Name",
    "Class/Type",
    "Alcohol Content",
    "Net Contents",
    "Producer",
    "Country of Origin",
    "Government Warning",
)

BEVERAGE_TYPES = ("Distilled Spirits", "Wine", "Beer / Malt Beverage")
