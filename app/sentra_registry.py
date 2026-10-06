"""Workspace-scoped registry snapshots; no mutation of the process-wide catalog."""
from hashlib import sha256
import json
from types import MappingProxyType

from .sentras import SentraDefinition


def execution_blockers(definition):
    blockers = []
    if definition.status != "active":
        blockers.append("source_not_active")
    if definition.source_type == "internal":
        blockers.append("internal_service_only")
    if definition.acquisition_mode not in {"official_api", "direct_http", "arcgis", "file_parser"}:
        blockers.append("adapter_contract_pending")
    if definition.credential_env:
        blockers.append("provider_specific_adapter_required")
    if not definition.rights_review_ref:
        blockers.append("rights_review_missing")
    if not definition.schema_fingerprint:
        blockers.append("successful_schema_probe_missing")
    if definition.estimated_cost_class != "free" or definition.max_cost_per_run_cents != 0:
        blockers.append("durable_metered_cost_accounting_pending")
    return blockers


def covers_market(definition, market):
    market = str(market or "").strip().casefold()
    if not market:
        return True
    # Country/global scope does not establish property coverage in every city.
    # Unknown coverage stays unknown until explicit aliases are reviewed.
    return market in {value.strip().casefold() for value in definition.coverage_markets}


class SentraRegistry:
    def __init__(self, definitions):
        items = {}
        for definition in definitions:
            if not isinstance(definition, SentraDefinition):
                raise ValueError("registry entries must be SentraDefinition instances")
            if definition.id in items:
                raise ValueError("duplicate Sentra id")
            items[definition.id] = definition
        self.definitions = MappingProxyType(items)

    def get(self, sentra_id):
        try:
            return self.definitions[sentra_id]
        except KeyError as exc:
            raise ValueError("unknown Sentra") from exc

    def catalog(self):
        return [self.definitions[key].catalog_record() for key in sorted(self.definitions)]

    @property
    def revision(self):
        return sha256(json.dumps(self.catalog(), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()

    def route(self, capability, *, market=None, verified_only=False):
        if not isinstance(capability, str) or not capability.strip() or len(capability) > 100:
            raise ValueError("capability must be bounded nonempty text")
        output = []
        for definition in self.definitions.values():
            capabilities = definition.verified_capabilities if verified_only else definition.capabilities
            if definition.status != "active" or capability not in capabilities or not covers_market(definition, market):
                continue
            output.append(definition)
        return sorted(output, key=lambda d: (
            d.max_cost_per_run_cents is None,
            d.max_cost_per_run_cents or 0,
            d.freshness_target_hours is None,
            d.freshness_target_hours or 0,
            d.id,
        ))
