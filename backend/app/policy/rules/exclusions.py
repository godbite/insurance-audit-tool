"""
Exclusions rule checker.

Handles:
- Full claim exclusions (TC012: bariatric/obesity → REJECTED)
- Per-line-item exclusions (TC006: teeth whitening excluded → PARTIAL)
- Category-specific exclusions (dental, vision)
"""
from __future__ import annotations

from typing import Optional

from app.models.decision import CheckResult, LineItemDecision
from app.models.domain import PolicyTerms
from app.models.extraction_schemas import LineItem

# ─── Condition exclusion keywords ─────────────────────────────────────────────
# Maps substring → policy exclusion category label
_EXCLUDED_CONDITION_KEYWORDS: list[tuple[str, str]] = [
    ("self-inflicted", "Self-inflicted injuries"),
    ("war", "War or nuclear hazard"),
    ("nuclear", "War or nuclear hazard"),
    ("substance abuse", "Substance abuse treatment"),
    ("alcohol", "Substance abuse treatment"),
    ("drug abuse", "Substance abuse treatment"),
    ("experimental", "Experimental treatments"),
    ("infertility", "Infertility and assisted reproduction"),
    ("ivf", "Infertility and assisted reproduction"),
    ("assisted reproduction", "Infertility and assisted reproduction"),
    ("obesity", "Obesity and weight loss programs"),
    ("weight loss", "Obesity and weight loss programs"),
    ("morbid obesity", "Obesity and weight loss programs"),
    ("bariatric", "Bariatric surgery"),
    ("cosmetic", "Cosmetic or aesthetic procedures"),
    ("aesthetic", "Cosmetic or aesthetic procedures"),
    ("vaccination", "Vaccination (non-medically necessary)"),
    ("supplement", "Health supplements and tonics"),
    ("tonic", "Health supplements and tonics"),
    ("diet plan", "Obesity and weight loss programs"),
    ("nutrition program", "Obesity and weight loss programs"),
]

# Dental procedure exclusion keywords
_DENTAL_EXCLUDED_KEYWORDS: list[str] = [
    "teeth whitening",
    "whitening",
    "veneer",
    "orthodontic",
    "braces",
    "implant",
    "bleaching",
]

# Vision item exclusion keywords
_VISION_EXCLUDED_KEYWORDS: list[str] = [
    "lasik",
    "refractive surgery",
    "refractive",
    "cosmetic eye",
]


def _matches_any(text: str, keywords: list[str]) -> Optional[str]:
    """Return the first keyword found in text (case-insensitive), else None."""
    lower = text.lower()
    for kw in keywords:
        if kw.lower() in lower:
            return kw
    return None


def check_diagnosis_exclusion(
    diagnosis: str,
    treatment: Optional[str],
    policy: PolicyTerms,
) -> CheckResult:
    """
    Check whether the overall diagnosis/treatment is a policy exclusion.
    Returns a failing CheckResult if excluded (causes REJECTED, not PARTIAL).
    """
    combined = f"{diagnosis or ''} {treatment or ''}".strip()

    for keyword, exclusion_label in _EXCLUDED_CONDITION_KEYWORDS:
        if keyword.lower() in combined.lower():
            return CheckResult(
                check_name="exclusion.diagnosis",
                passed=False,
                detail=(
                    f"The diagnosis/treatment '{combined}' matches the excluded condition "
                    f"'{exclusion_label}' in the policy. This category is not covered under "
                    f"the group health insurance plan."
                ),
                policy_reference="exclusions.conditions",
                data={
                    "matched_keyword": keyword,
                    "exclusion_label": exclusion_label,
                    "diagnosis": diagnosis,
                    "treatment": treatment,
                },
            )

    return CheckResult(
        check_name="exclusion.diagnosis",
        passed=True,
        detail=f"Diagnosis '{diagnosis}' does not match any policy exclusion.",
        policy_reference="exclusions.conditions",
    )


def check_line_item_exclusions(
    claim_category: str,
    line_items: list[LineItem],
    policy: PolicyTerms,
) -> tuple[list[LineItemDecision], CheckResult]:
    """
    Check each line item against category-specific exclusions.

    Returns:
        - list of LineItemDecision (one per line item, with status)
        - overall CheckResult (passed=True if at least one item eligible,
          passed=False if ALL items excluded)
    """
    if not line_items:
        return [], CheckResult(
            check_name="exclusion.line_items",
            passed=True,
            detail="No line items to check.",
            policy_reference="exclusions",
        )

    decisions: list[LineItemDecision] = []
    any_approved = False

    for item in line_items:
        excluded_reason: Optional[str] = None

        if claim_category == "DENTAL":
            matched = _matches_any(item.description, _DENTAL_EXCLUDED_KEYWORDS)
            if matched:
                # Also check against policy's explicit excluded_procedures list
                excluded_reason = (
                    f"'{item.description}' is a cosmetic dental procedure excluded "
                    f"by policy (matched: '{matched}')."
                )
                # Use the actual model
                dental_cat = policy.opd_categories.get("dental")
                if dental_cat and dental_cat.excluded_procedures:
                    excluded_reason = (
                        f"'{item.description}' is a cosmetic dental procedure excluded "
                        f"by policy (matched keyword: '{matched}'). "
                        f"Covered procedures are: {', '.join(dental_cat.covered_procedures)}."
                    )

        elif claim_category == "VISION":
            matched = _matches_any(item.description, _VISION_EXCLUDED_KEYWORDS)
            if matched:
                vision_cat = policy.opd_categories.get("vision")
                excluded_reason = (
                    f"'{item.description}' is a vision exclusion (matched: '{matched}'). "
                    f"Excluded vision items under policy: {', '.join(policy.exclusions.vision_exclusions)}."
                )

        else:
            # General exclusion check on line items for non-dental/vision
            for keyword, label in _EXCLUDED_CONDITION_KEYWORDS:
                if keyword.lower() in item.description.lower():
                    excluded_reason = (
                        f"'{item.description}' matches excluded condition '{label}'. "
                        f"This item is not covered under the policy."
                    )
                    break

        if excluded_reason:
            decisions.append(LineItemDecision(
                description=item.description,
                claimed_amount=item.amount,
                approved_amount=0.0,
                status="EXCLUDED",
                reason=excluded_reason,
            ))
        else:
            any_approved = True
            decisions.append(LineItemDecision(
                description=item.description,
                claimed_amount=item.amount,
                approved_amount=item.amount,
                status="APPROVED",
                reason="Eligible line item",
            ))

    excluded_items = [d for d in decisions if d.status == "EXCLUDED"]
    approved_items = [d for d in decisions if d.status == "APPROVED"]

    if excluded_items and not approved_items:
        # All items excluded → full rejection
        detail = (
            f"All {len(line_items)} line item(s) are excluded: "
            + "; ".join(f"{d.description} — {d.reason}" for d in excluded_items)
        )
        passed = False
    elif excluded_items:
        # Partial — some excluded, some approved
        detail = (
            f"{len(excluded_items)} of {len(line_items)} line item(s) excluded. "
            f"Excluded: {', '.join(d.description for d in excluded_items)}. "
            f"Approved: {', '.join(d.description for d in approved_items)}."
        )
        passed = True  # partial approval still "passes" (no hard failure)
    else:
        detail = f"All {len(line_items)} line item(s) are eligible."
        passed = True

    return decisions, CheckResult(
        check_name="exclusion.line_items",
        passed=passed,
        detail=detail,
        policy_reference="exclusions",
        data={
            "total_items": len(line_items),
            "excluded_count": len(excluded_items),
            "approved_count": len(approved_items),
        },
    )
