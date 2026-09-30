from math import isfinite
from uuid import UUID


def text_field(data, key, limit=300, required=True):
    value = data.get(key, "")
    if not isinstance(value, str):
        raise ValueError(f"{key} must be text")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{key} is required")
    if len(value) > limit:
        raise ValueError(f"{key} is too long")
    return value


def number_field(data, key, default=None, nonnegative=False):
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{key} must be a finite number")
    if nonnegative and value < 0:
        raise ValueError(f"{key} cannot be negative")
    return float(value)


def list_field(data, key, allowed=None, max_items=50, required=True):
    value = data.get(key)
    if not isinstance(value, list) or len(value) > max_items or (required and not value):
        raise ValueError(f"{key} must be a list with 1 to {max_items} entries")
    cleaned = []
    for entry in value:
        if not isinstance(entry, str) or not entry.strip() or len(entry.strip()) > 100:
            raise ValueError(f"{key} entries must be nonempty text up to 100 characters")
        item = entry.strip()
        if allowed is not None and item not in allowed:
            raise ValueError(f"Unsupported {key} value: {item}")
        if item not in cleaned:
            cleaned.append(item)
    return cleaned


def property_exists(connection, property_id):
    try:
        property_id = UUID(str(property_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("property_id must be a valid UUID") from exc
    if connection.execute("SELECT id FROM properties WHERE id=?", (str(property_id),)).fetchone() is None:
        raise LookupError("Property not found")
    return property_id


def deal_exists(connection, deal_id):
    try:
        deal_id = UUID(str(deal_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("deal_id must be a valid UUID") from exc
    deal = connection.execute("SELECT * FROM deals WHERE id=?", (str(deal_id),)).fetchone()
    if deal is None:
        raise LookupError("Deal not found")
    return deal


