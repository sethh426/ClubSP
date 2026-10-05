"""Explain comparable suitability without inferring an exit price or appraisal."""
from datetime import date
import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class Measurements(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore", allow_inf_nan=False)
    living_area: float | None = Field(default=None, gt=0, le=1000000000)
    year_built: float | None = Field(default=None, ge=1700, le=9999)
    bath: float | None = Field(default=None, ge=0, le=1000)
    acreage: float | None = Field(default=None, ge=0, le=1000000000)


def parcel_identity(value):
    return re.sub(r"[-\s]", "", str(value)).casefold()


def numeric(value, field):
    try:
        return getattr(Measurements.model_validate({field: value}), field)
    except ValidationError:
        return None


def comparable_screen(prop, evidence, snapshot, *, today=None, evidence_max_age_days=30):
    from .opportunities import canonical, recent
    from core.memory.models import utc_now
    now = utc_now()
    today = today or now.date()
    items = []
    values = evidence["values"]
    thresholds = {"living_area": 0.25, "year_built": 20, "bath": 1, "acreage": 0.50}
    for record in snapshot["items"]:
        sale = record["sale"]
        exclusions, gaps, comparisons = [], [], {}
        if evidence["conflicts"]:
            gaps.append("Resolve conflicting subject facts: " + ", ".join(evidence["conflicts"]))
        for field in ("parcel_id", "property_class", "neighborhood_code", *thresholds):
            facts = [f for f in evidence["facts"] if canonical(f["attribute"]) == field]
            if any(not recent(f["observed_at"], evidence_max_age_days, now) for f in facts):
                gaps.append("Refresh subject " + field + " evidence")
        subject_parcel = values.get("parcel_id")
        if not subject_parcel:
            gaps.append("Confirm subject parcel identity")
        elif parcel_identity(subject_parcel) == parcel_identity(sale.get("parcel_id", "")):
            exclusions.append("Subject parcel cannot be its own comparable")
        if any(canonical(prop[k]) != canonical(sale.get(k, "")) for k in ("city", "state")):
            exclusions.append("Sale market differs from the subject market")
        try:
            age = (today - date.fromisoformat(sale["sale_date"])).days
            if not 0 <= age <= 365:
                exclusions.append("Sale is future-dated or older than the 365-day screen")
        except (ValueError, TypeError, KeyError):
            age = None
            exclusions.append("Sale date is invalid")
        for field in ("property_class", "neighborhood_code"):
            subject, comp = values.get(field), sale.get(field)
            if not isinstance(subject, str) or not subject.strip() or not isinstance(comp, str) or not comp.strip():
                gaps.append("Confirm subject and sale " + field)
            elif canonical(subject) != canonical(comp):
                exclusions.append(field + " differs from the subject")
        for field, threshold in thresholds.items():
            subject, comp = numeric(values.get(field), field), numeric(sale.get(field), field)
            if field == "year_built":
                if subject is not None and (subject > today.year or subject != int(subject)):
                    subject = None
                if comp is not None and (comp > today.year or comp != int(comp)):
                    comp = None
            if subject is None or comp is None:
                gaps.append("Confirm valid subject and sale " + field)
                continue
            difference = abs(comp - subject)
            if field in {"living_area", "acreage"}:
                difference = difference / subject if subject else (0 if comp == 0 else None)
            comparisons[field] = {"subject": subject, "sale": comp, "difference": difference,
                                  "limit": threshold, "unit": "ratio" if field in {"living_area", "acreage"} else "absolute"}
            if difference is None or difference > threshold:
                exclusions.append(field + " difference exceeds the screen limit")
        if snapshot["conflicts"]:
            gaps.append("Resolve conflicting prices in the accepted sale set")
        price, area = numeric(sale.get("sale_price"), "living_area"), numeric(sale.get("living_area"), "living_area")
        items.append({"sale_id": record["id"], "address": sale["address"],
                      "decision": "excluded_by_screen" if exclusions else "needs_review" if gaps else "screening_fit",
                      "reasons": exclusions + gaps, "comparisons": comparisons, "sale_age_days": age,
                      "historical_price_per_sqft": round(price / area, 2) if price and area else None,
                      "source_url": record["source"]["source_url"], "source_date": record["source"]["source_date"]})
    order = {"screening_fit": 0, "needs_review": 1, "excluded_by_screen": 2}
    items.sort(key=lambda x: (order[x["decision"]], len(x["reasons"]), x["sale_age_days"] if x["sale_age_days"] is not None else 999999, x["sale_id"]))
    return {"property_id": prop["id"], "address": prop["address"], "items": items,
            "subject_evidence_digest": evidence["digest"], "sale_evidence_digest": snapshot["digest"],
            "valuation_available": False, "execution_authorized": False,
            "scope": "Advisory screening of accepted sales; condition, concessions and sale validity still require review"}
