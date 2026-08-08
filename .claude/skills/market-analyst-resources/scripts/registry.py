"""Load and validate `Knowledge-Base/taxonomy/market-indicators.yml`.

The registry is hand-curated, owner-authored configuration -- this module
reads it, never writes it, mirroring how `evaluate-stock-decision/scripts/
rubric.py` treats `decision-rubric.yml`. Nothing here fetches data; it only
enforces structure so a typo in the YAML fails loudly at load time instead of
silently at fetch time.

Two-tier shape, not a flat list: `indicators` are leaves (fetched from a
named source), `derived` are composites (`spread`/`ratio` over two leaf
indicators). They are kept separate because they are genuinely different
entities -- a `derived` entry has no `source`/`series_ref`, which is exactly
the signal that it isn't a transform of one series but a combination of two.
Evaluation order is always "all leaves, then all derived" (depth capped at
1 -- a `derived` may not reference another `derived`), so no topological
sort is needed.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from config import KNOWLEDGE_BASE_FOLDER  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from transforms import UNARY_TRANSFORM_NAMES, parse_transform  # noqa: E402

DEFAULT_REGISTRY_PATH = KNOWLEDGE_BASE_FOLDER / "taxonomy" / "market-indicators.yml"

VALID_KINDS = ("statistical", "priced")
VALID_OPS = ("spread", "ratio")
VALID_ALIGN = ("locf", "inner")
VALID_CADENCES = ("daily", "weekly", "monthly", "quarterly", "annual")
VALID_VALUE_FIELDS = ("close", "level")

# The thirteen domains from docs/plans/market-analyst-resources-skill.md §3.
# `events` is reserved for the hand-maintained events list itself -- no
# indicator or derived entry should target it.
VALID_DOMAINS = (
    "growth", "inflation", "labour", "policy", "rates", "credit",
    "breadth", "volatility", "sectors", "factors", "fx_commodities",
    "positioning", "events",
)

_ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
_TBD = "<TBD>"


class RegistryError(ValueError):
    """Raised when the registry file cannot be loaded or is structurally invalid."""


@dataclass(frozen=True)
class Indicator:
    id: str
    output_id: str
    domain: str
    kind: str  # statistical | priced
    source: str
    series_ref: str
    transform: str  # e.g. "none", "yoy_pct", "zscore(252)"
    cadence: str
    publication_lag_days: int
    backfill_years: int
    units: str
    value_field: str | None = None
    scale: float = 1.0

    @property
    def is_configured(self) -> bool:
        """False while `source`/`series_ref` are still the owner's `<TBD>`
        placeholder -- such indicators are skipped by fetch/gate, not treated
        as errors, since the registry ships intentionally incomplete."""
        return self.source != _TBD and self.series_ref != _TBD


@dataclass(frozen=True)
class Derived:
    id: str
    domain: str
    op: str  # spread | ratio
    inputs: tuple[str, str]
    align: str
    max_carry_days: int
    units: str


@dataclass(frozen=True)
class EventEntry:
    date: date
    label: str
    forces_due: tuple[str, ...]


@dataclass(frozen=True)
class Registry:
    version: int
    updated: date
    sources: dict[str, dict[str, Any]]
    indicators: tuple[Indicator, ...]
    derived: tuple[Derived, ...]
    events: tuple[EventEntry, ...]
    _by_id: dict[str, Indicator] = field(default_factory=dict, repr=False, compare=False)

    def indicator(self, indicator_id: str) -> Indicator | None:
        return self._by_id.get(indicator_id)

    def configured_indicators(self) -> list[Indicator]:
        return [ind for ind in self.indicators if ind.is_configured]

    def events_forcing(self, domain: str, *, today: date) -> list[EventEntry]:
        """Events from yesterday that force `domain` due today -- see the
        freshness gate's event-forced-due rule."""
        return [
            event for event in self.events
            if domain in event.forces_due and (today - event.date).days == 1
        ]


def _require_str(entry: dict[str, Any], key: str, *, context: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RegistryError(f"{context}: '{key}' must be a non-empty string")
    return value.strip()


def _require_int(entry: dict[str, Any], key: str, *, context: str) -> int:
    value = entry.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise RegistryError(f"{context}: '{key}' must be an integer")
    return value


def _require_choice(entry: dict[str, Any], key: str, choices: tuple[str, ...], *, context: str) -> str:
    value = _require_str(entry, key, context=context)
    if value not in choices:
        raise RegistryError(f"{context}: '{key}' must be one of {choices}, got {value!r}")
    return value


def _parse_indicator(raw: dict[str, Any], *, seen_ids: set[str]) -> Indicator:
    if not isinstance(raw, dict):
        raise RegistryError("each entry under 'indicators' must be a mapping")
    indicator_id = _require_str(raw, "id", context="indicator")
    context = f"indicator '{indicator_id}'"
    if not _ID_PATTERN.match(indicator_id):
        raise RegistryError(f"{context}: id must match {_ID_PATTERN.pattern}")
    if indicator_id in seen_ids:
        raise RegistryError(f"{context}: duplicate id -- ids must be globally unique")
    seen_ids.add(indicator_id)

    output_id = str(raw.get("output_id") or indicator_id).strip()
    domain = _require_choice(raw, "domain", VALID_DOMAINS, context=context)
    kind = _require_choice(raw, "kind", VALID_KINDS, context=context)
    source = _require_str(raw, "source", context=context)
    series_ref = _require_str(raw, "series_ref", context=context)

    transform = str(raw.get("transform") or "none").strip()
    name, _window = parse_transform(transform)
    if name not in UNARY_TRANSFORM_NAMES:
        raise RegistryError(f"{context}: unknown transform {transform!r}")

    cadence = _require_choice(raw, "cadence", VALID_CADENCES, context=context)
    publication_lag_days = _require_int(raw, "publication_lag_days", context=context)
    backfill_years = _require_int(raw, "backfill_years", context=context)
    units = _require_str(raw, "units", context=context)

    value_field = None
    scale = 1.0
    if kind == "priced" and source != _TBD:
        value_field = _require_choice(raw, "value_field", VALID_VALUE_FIELDS, context=context)
        scale_raw = raw.get("scale", 1.0)
        if not isinstance(scale_raw, (int, float)) or isinstance(scale_raw, bool):
            raise RegistryError(f"{context}: 'scale' must be numeric")
        scale = float(scale_raw)

    return Indicator(
        id=indicator_id, output_id=output_id, domain=domain, kind=kind,
        source=source, series_ref=series_ref, transform=transform, cadence=cadence,
        publication_lag_days=publication_lag_days, backfill_years=backfill_years,
        units=units, value_field=value_field, scale=scale,
    )


def _parse_derived(raw: dict[str, Any], *, seen_ids: set[str]) -> Derived:
    if not isinstance(raw, dict):
        raise RegistryError("each entry under 'derived' must be a mapping")
    derived_id = _require_str(raw, "id", context="derived")
    context = f"derived '{derived_id}'"
    if not _ID_PATTERN.match(derived_id):
        raise RegistryError(f"{context}: id must match {_ID_PATTERN.pattern}")
    if derived_id in seen_ids:
        raise RegistryError(f"{context}: duplicate id -- ids must be globally unique")
    seen_ids.add(derived_id)

    domain = _require_choice(raw, "domain", VALID_DOMAINS, context=context)
    op = _require_choice(raw, "op", VALID_OPS, context=context)
    inputs = raw.get("inputs")
    if not isinstance(inputs, list) or len(inputs) != 2 or not all(isinstance(i, str) for i in inputs):
        raise RegistryError(f"{context}: 'inputs' must be a list of exactly two indicator ids")
    align = _require_choice(raw, "align", VALID_ALIGN, context=context)
    max_carry_days = _require_int(raw, "max_carry_days", context=context)
    units = _require_str(raw, "units", context=context)

    return Derived(
        id=derived_id, domain=domain, op=op, inputs=(inputs[0], inputs[1]),
        align=align, max_carry_days=max_carry_days, units=units,
    )


def _parse_event(raw: dict[str, Any]) -> EventEntry:
    if not isinstance(raw, dict):
        raise RegistryError("each entry under 'events' must be a mapping")
    event_date = raw.get("date")
    if not isinstance(event_date, date):
        raise RegistryError(f"event {raw!r}: 'date' must be a YAML date (YYYY-MM-DD)")
    label = _require_str(raw, "label", context=f"event on {event_date}")
    forces_due = raw.get("forces_due") or []
    if not isinstance(forces_due, list) or not all(isinstance(d, str) for d in forces_due):
        raise RegistryError(f"event '{label}': 'forces_due' must be a list of domain names")
    for domain in forces_due:
        if domain not in VALID_DOMAINS:
            raise RegistryError(f"event '{label}': forces_due domain {domain!r} is not a known domain")
    return EventEntry(date=event_date, label=label, forces_due=tuple(forces_due))


def load_registry(path: Path | None = None) -> Registry:
    registry_path = path or DEFAULT_REGISTRY_PATH
    try:
        raw = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RegistryError(f"registry file not found: {registry_path}") from exc
    except yaml.YAMLError as exc:
        raise RegistryError(f"invalid YAML in {registry_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise RegistryError(f"{registry_path}: expected a mapping at the top level")

    version = raw.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise RegistryError("'version' must be an integer")
    updated = raw.get("updated")
    if not isinstance(updated, date):
        raise RegistryError("'updated' must be a YAML date (YYYY-MM-DD)")

    sources_raw = raw.get("sources") or {}
    if not isinstance(sources_raw, dict):
        raise RegistryError("'sources' must be a mapping")
    sources: dict[str, dict[str, Any]] = {}
    for name, spec in sources_raw.items():
        if not isinstance(spec, dict) or "adapter" not in spec:
            raise RegistryError(f"source '{name}': must be a mapping with an 'adapter' key")
        sources[name] = spec

    seen_ids: set[str] = set()
    indicators = tuple(
        _parse_indicator(raw_indicator, seen_ids=seen_ids)
        for raw_indicator in (raw.get("indicators") or [])
    )
    for indicator in indicators:
        if indicator.is_configured and indicator.source not in sources:
            raise RegistryError(
                f"indicator '{indicator.id}': source '{indicator.source}' is not declared under 'sources'"
            )

    derived = tuple(
        _parse_derived(raw_derived, seen_ids=seen_ids)
        for raw_derived in (raw.get("derived") or [])
    )
    leaf_ids = {indicator.id for indicator in indicators}
    for entry in derived:
        for input_id in entry.inputs:
            if input_id not in leaf_ids:
                raise RegistryError(
                    f"derived '{entry.id}': input {input_id!r} does not resolve to a leaf indicator "
                    "id -- a derived entry may not reference another derived entry (depth is capped at 1)"
                )

    events = tuple(_parse_event(raw_event) for raw_event in (raw.get("events") or []))

    return Registry(
        version=version, updated=updated, sources=sources,
        indicators=indicators, derived=derived, events=events,
        _by_id={indicator.id: indicator for indicator in indicators},
    )
