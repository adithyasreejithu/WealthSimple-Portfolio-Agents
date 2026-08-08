"""Per-source fetch adapters: `fetch(series_ref, start, end) -> [(date, value)]`.

Named after the **source**, not the transport (`boc_valet`, `yfinance`, ...
`fred`/`statcan_wds` to be added when a `<TBD>` domain in the registry gets a
source). A generic `http_json` adapter that pushes a JSONPath into the YAML
was considered and rejected: Bank of Canada Valet, StatCan WDS, and FRED
return structurally different JSON, so a generic adapter only works by
debugging JSONPath expressions inside a config file. One function per source
with its own parser is easier to test and easier to fix when a source
changes shape.

Error taxonomy (the completeness trace in market_freshness_gate.py depends
on being able to tell these apart):
- `SourceConfigError` -- the request was rejected in a way that means the
  registry's `series_ref` is wrong (HTTP 404, or an unrecognized symbol).
  Not worth retrying; this is a config problem for the owner to fix.
- `SourceTransientError` -- network failure, timeout, or 5xx. Worth
  retrying on the next scheduled run.
- An empty-but-successful result (`[]`) is neither -- it means "no
  observations in this window yet," which is normal for a fresh release
  window and must not be raised as an error.

HTTP goes through `curl_cffi.requests`, already a hard runtime dependency
(yfinance pulls it in, and `yfinance_extractor._build_session` already uses
it) with a requests-compatible API -- no new dependency. Timeout and retry
are set explicitly here since there is no existing policy to inherit.
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from yfinance_extractor import _build_session, _require_yfinance  # noqa: E402

try:
    from curl_cffi import requests as curl_requests
except ImportError:  # pragma: no cover - exercised only when dependency is missing
    curl_requests = None

HTTP_TIMEOUT_SECONDS = 15
HTTP_MAX_RETRIES = 2
BOC_VALET_BASE_URL = "https://www.bankofcanada.ca/valet"


class SourceConfigError(RuntimeError):
    """The registry's series_ref is wrong for this source -- not worth retrying."""


class SourceTransientError(RuntimeError):
    """Network failure, timeout, or 5xx -- worth retrying on the next run."""


def _require_curl_cffi() -> object:
    if curl_requests is None:
        raise SourceTransientError("curl_cffi is not installed; cannot reach a live source")
    return curl_requests


def _get_json(url: str, *, params: dict[str, str]) -> dict:
    session_module = _require_curl_cffi()
    last_error: Exception | None = None
    for attempt in range(HTTP_MAX_RETRIES + 1):
        try:
            response = session_module.get(url, params=params, timeout=HTTP_TIMEOUT_SECONDS)
        except Exception as exc:  # network-level failure -- transient
            last_error = exc
            continue
        if response.status_code == 404:
            raise SourceConfigError(f"{url}: 404 -- check the registry's series_ref ({params})")
        if response.status_code >= 500:
            last_error = SourceTransientError(f"{url}: HTTP {response.status_code}")
            continue
        if response.status_code >= 400:
            raise SourceConfigError(f"{url}: HTTP {response.status_code} ({params})")
        try:
            return response.json()
        except ValueError as exc:
            raise SourceTransientError(f"{url}: response was not valid JSON") from exc
    raise SourceTransientError(f"{url}: failed after {HTTP_MAX_RETRIES + 1} attempts ({last_error})")


def fetch_boc_valet(series_ref: str, start: date, end: date) -> list[tuple[date, float]]:
    """Bank of Canada Valet API -- no key. `series_ref` is a Valet series
    name (e.g. `V39079` for the overnight target rate, `FXUSDCAD` for the
    noon USD/CAD rate). Response shape:
    `{"observations": [{"d": "2026-01-02", "<series_ref>": {"v": "4.25"}}, ...]}`.
    """
    url = f"{BOC_VALET_BASE_URL}/observations/{series_ref}/json"
    payload = _get_json(url, params={"start_date": start.isoformat(), "end_date": end.isoformat()})
    observations = payload.get("observations")
    if observations is None:
        raise SourceConfigError(f"{url}: response has no 'observations' key -- check series_ref")
    results: list[tuple[date, float]] = []
    for row in observations:
        obs_date_raw = row.get("d")
        cell = row.get(series_ref)
        if obs_date_raw is None or not isinstance(cell, dict) or cell.get("v") is None:
            continue
        try:
            obs_date = datetime.strptime(obs_date_raw, "%Y-%m-%d").date()
            value = float(cell["v"])
        except (ValueError, TypeError):
            continue
        results.append((obs_date, value))
    return results


def fetch_yfinance(series_ref: str, start: date, end: date) -> list[tuple[date, float]]:
    """yfinance daily close series for one symbol (`^VIX`, `CL=F`, `XLK`,
    `USDCAD=X`, ...). Only the close is used -- see
    docs/plans/market-analyst-resources-skill.md §1 on why OHLC isn't needed.
    """
    yf_module = _require_yfinance()
    session = _build_session()
    try:
        ticker = yf_module.Ticker(series_ref, session=session)
        history = ticker.history(
            start=start.isoformat(),
            end=(end).isoformat(),
            interval="1d",
            auto_adjust=False,
            raise_errors=False,
        )
    except Exception as exc:
        raise SourceTransientError(f"yfinance fetch failed for {series_ref!r}: {exc}") from exc
    if history is None or history.empty:
        return []
    if "Close" not in history.columns:
        raise SourceConfigError(f"yfinance returned no 'Close' column for {series_ref!r}")
    results: list[tuple[date, float]] = []
    for index, row in history.iterrows():
        value = row["Close"]
        if value is None or value != value:  # NaN check without importing pandas/numpy here
            continue
        obs_date = index.date() if hasattr(index, "date") else index
        results.append((obs_date, float(value)))
    return results


_ADAPTERS: dict[str, Callable[[str, date, date], list[tuple[date, float]]]] = {
    "boc_valet": fetch_boc_valet,
    "yfinance": fetch_yfinance,
}


def fetch(adapter: str, series_ref: str, start: date, end: date) -> list[tuple[date, float]]:
    handler = _ADAPTERS.get(adapter)
    if handler is None:
        raise SourceConfigError(f"no adapter registered for source type {adapter!r}")
    return handler(series_ref, start, end)
