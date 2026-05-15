"""
Overrides Store

Persists per-project user overrides applied on top of the Furious-derived
revenue engine output. Two kinds of overrides are tracked per project id:

- input_overrides: values fed back into the revenue engine BEFORE it spreads
  amounts (amount, probability, dates, BU, typology, ...). The engine then
  recomputes quarterly columns cleanly from the corrected inputs.
- quarter_overrides: direct cell overrides applied AFTER the engine has run
  (e.g. ``{"Montant Total Q1_2026": 300}``). Year totals and the matching
  pondéré columns are recomputed from the overridden quarter values.

Atomic writes are performed via tempfile + os.replace so a crashed
write never leaves a half-flushed file on disk.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Dict, Iterator, Optional


SCHEMA_VERSION = 1


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class ProjectOverride:
    """Container for a single project's overrides."""

    input_overrides: Dict[str, Any] = field(default_factory=dict)
    quarter_overrides: Dict[str, float] = field(default_factory=dict)
    updated_at: str = field(default_factory=_utc_now_iso)
    updated_by: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "input_overrides": dict(self.input_overrides),
            "quarter_overrides": {k: float(v) for k, v in self.quarter_overrides.items()},
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "ProjectOverride":
        return cls(
            input_overrides=dict(raw.get("input_overrides", {})),
            quarter_overrides={
                str(k): float(v) for k, v in (raw.get("quarter_overrides", {}) or {}).items()
            },
            updated_at=str(raw.get("updated_at") or _utc_now_iso()),
            updated_by=str(raw.get("updated_by", "")),
        )

    def is_empty(self) -> bool:
        return not self.input_overrides and not self.quarter_overrides


class OverridesStore:
    """
    JSON-backed persistent store of per-project overrides.

    The store is keyed by the proposal id (as a string). Manual project ids
    (e.g. ``"MAN-2026-0001"``) and Furious numeric ids share the same key
    space — when a manual is linked to a Furious id, the existing entry is
    migrated under the new key.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = RLock()
        self._cache: Optional[Dict[str, ProjectOverride]] = None

    @staticmethod
    def _empty_payload() -> Dict[str, Any]:
        return {"version": SCHEMA_VERSION, "overrides": {}}

    def _ensure_loaded(self) -> Dict[str, ProjectOverride]:
        if self._cache is not None:
            return self._cache

        if not self.path.exists():
            self._cache = {}
            return self._cache

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._cache = {}
            return self._cache

        items = raw.get("overrides", {}) or {}
        self._cache = {
            str(pid): ProjectOverride.from_dict(payload) for pid, payload in items.items()
        }
        return self._cache

    def _flush(self) -> None:
        if self._cache is None:
            return
        payload = {
            "version": SCHEMA_VERSION,
            "overrides": {pid: ov.to_dict() for pid, ov in self._cache.items() if not ov.is_empty()},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def reload(self) -> None:
        with self._lock:
            self._cache = None
            self._ensure_loaded()

    def all(self) -> Dict[str, ProjectOverride]:
        with self._lock:
            return {pid: ProjectOverride.from_dict(ov.to_dict()) for pid, ov in self._ensure_loaded().items()}

    def get(self, project_id: Any) -> Optional[ProjectOverride]:
        key = str(project_id)
        with self._lock:
            ov = self._ensure_loaded().get(key)
            if ov is None:
                return None
            return ProjectOverride.from_dict(ov.to_dict())

    def has(self, project_id: Any) -> bool:
        return self.get(project_id) is not None

    def __iter__(self) -> Iterator[str]:
        return iter(self.all().keys())

    def upsert(
        self,
        project_id: Any,
        *,
        input_overrides: Optional[Dict[str, Any]] = None,
        quarter_overrides: Optional[Dict[str, float]] = None,
        updated_by: str = "",
    ) -> ProjectOverride:
        key = str(project_id)
        with self._lock:
            cache = self._ensure_loaded()
            existing = cache.get(key) or ProjectOverride()
            if input_overrides is not None:
                existing.input_overrides = {
                    k: v for k, v in input_overrides.items() if v is not None and v != ""
                }
            if quarter_overrides is not None:
                cleaned = {}
                for k, v in quarter_overrides.items():
                    if v is None or v == "":
                        continue
                    try:
                        cleaned[str(k)] = float(v)
                    except (TypeError, ValueError):
                        continue
                existing.quarter_overrides = cleaned
            existing.updated_at = _utc_now_iso()
            if updated_by:
                existing.updated_by = updated_by
            cache[key] = existing
            self._flush()
            return ProjectOverride.from_dict(existing.to_dict())

    def clear_inputs(self, project_id: Any) -> None:
        self._mutate(project_id, lambda ov: setattr(ov, "input_overrides", {}))

    def clear_quarters(self, project_id: Any) -> None:
        self._mutate(project_id, lambda ov: setattr(ov, "quarter_overrides", {}))

    def delete(self, project_id: Any) -> bool:
        key = str(project_id)
        with self._lock:
            cache = self._ensure_loaded()
            if key in cache:
                del cache[key]
                self._flush()
                return True
            return False

    def migrate(self, old_id: Any, new_id: Any) -> bool:
        """Move overrides from ``old_id`` to ``new_id``.

        If both ids exist, the new id wins on a per-field basis (input_overrides
        and quarter_overrides at the top level), and the old entry is removed.
        Returns True when an actual migration happened.
        """
        old_key = str(old_id)
        new_key = str(new_id)
        if old_key == new_key:
            return False
        with self._lock:
            cache = self._ensure_loaded()
            old = cache.get(old_key)
            if old is None:
                return False
            new = cache.get(new_key)
            if new is None:
                cache[new_key] = old
            else:
                merged = ProjectOverride.from_dict(new.to_dict())
                for k, v in old.input_overrides.items():
                    merged.input_overrides.setdefault(k, v)
                for k, v in old.quarter_overrides.items():
                    merged.quarter_overrides.setdefault(k, v)
                merged.updated_at = _utc_now_iso()
                cache[new_key] = merged
            del cache[old_key]
            self._flush()
            return True

    def _mutate(self, project_id: Any, fn) -> None:
        key = str(project_id)
        with self._lock:
            cache = self._ensure_loaded()
            ov = cache.get(key)
            if ov is None:
                return
            fn(ov)
            ov.updated_at = _utc_now_iso()
            if ov.is_empty():
                del cache[key]
            self._flush()
