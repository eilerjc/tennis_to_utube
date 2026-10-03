"""Issues: problems found by validation, trimming or export, each pointing at a time."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Issue:
    code: str  # stable machine-readable kind, e.g. "event_in_removed_region"
    message: str
    t_ms: int | None = None  # joined-timeline time to jump to, when there is one
    event_id: str | None = None
    severity: str = "warning"  # "info" | "warning" | "error"
