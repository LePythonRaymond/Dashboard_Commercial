"""
Manual Projects Store

Persistent JSON-backed store for projects added through the dashboard before
they exist in Furious. A manual project carries the same fields the revenue
engine needs (amount, dates, BU, typology, probability, ...) plus enough
metadata for the dashboard to display and link them later.

Manual ids are sequentially generated as ``MAN-{year}-{NNNN}`` so they sort
naturally and never collide with Furious numeric ids.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional


SCHEMA_VERSION = 1
DEFAULT_STATUT = "envoyée(s) en attente de réponse"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class ManualProject:
    """Container for a single manually-created project."""

    manual_id: str
    title: str
    company_name: str = ""
    amount: float = 0.0
    probability: float = 50.0
    date: Optional[str] = None
    projet_start: Optional[str] = None
    projet_stop: Optional[str] = None
    cf_bu: str = "AUTRE"
    cf_typologie_de_devis: str = ""
    assigned_to: str = ""
    statut: str = DEFAULT_STATUT
    created_at: str = field(default_factory=_utc_now_iso)
    created_by: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "ManualProject":
        return cls(
            manual_id=str(raw["manual_id"]),
            title=str(raw.get("title", "")),
            company_name=str(raw.get("company_name", "")),
            amount=float(raw.get("amount") or 0),
            probability=float(raw.get("probability") or 0),
            date=raw.get("date") or None,
            projet_start=raw.get("projet_start") or None,
            projet_stop=raw.get("projet_stop") or None,
            cf_bu=str(raw.get("cf_bu") or "AUTRE"),
            cf_typologie_de_devis=str(raw.get("cf_typologie_de_devis") or ""),
            assigned_to=str(raw.get("assigned_to") or ""),
            statut=str(raw.get("statut") or DEFAULT_STATUT),
            created_at=str(raw.get("created_at") or _utc_now_iso()),
            created_by=str(raw.get("created_by") or ""),
        )


class ManualProjectsStore:
    """JSON-backed persistent store of manually-added projects."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = RLock()
        self._cache: Optional[Dict[str, Any]] = None

    @staticmethod
    def _empty_payload() -> Dict[str, Any]:
        return {"version": SCHEMA_VERSION, "next_seq": 1, "projects": []}

    def _ensure_loaded(self) -> Dict[str, Any]:
        if self._cache is not None:
            return self._cache

        if not self.path.exists():
            self._cache = self._empty_payload()
            return self._cache

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._cache = self._empty_payload()
            return self._cache

        projects = [ManualProject.from_dict(p) for p in raw.get("projects", [])]
        next_seq = int(raw.get("next_seq", 1) or 1)
        if next_seq < 1:
            next_seq = 1
        self._cache = {
            "version": SCHEMA_VERSION,
            "next_seq": next_seq,
            "projects": projects,
        }
        return self._cache

    def _flush(self) -> None:
        if self._cache is None:
            return
        payload = {
            "version": SCHEMA_VERSION,
            "next_seq": int(self._cache.get("next_seq", 1)),
            "projects": [p.to_dict() for p in self._cache.get("projects", [])],
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

    def all(self) -> List[ManualProject]:
        with self._lock:
            return [ManualProject.from_dict(p.to_dict()) for p in self._ensure_loaded()["projects"]]

    def get(self, manual_id: str) -> Optional[ManualProject]:
        key = str(manual_id)
        with self._lock:
            for p in self._ensure_loaded()["projects"]:
                if p.manual_id == key:
                    return ManualProject.from_dict(p.to_dict())
            return None

    def _next_id(self) -> str:
        cache = self._ensure_loaded()
        seq = int(cache.get("next_seq", 1))
        year = datetime.now().year
        cache["next_seq"] = seq + 1
        return f"MAN-{year}-{seq:04d}"

    def add(
        self,
        *,
        title: str,
        company_name: str,
        amount: float,
        probability: float,
        date: Optional[str],
        projet_start: Optional[str],
        projet_stop: Optional[str],
        cf_bu: str,
        cf_typologie_de_devis: str,
        assigned_to: str = "",
        statut: str = DEFAULT_STATUT,
        created_by: str = "",
    ) -> ManualProject:
        with self._lock:
            cache = self._ensure_loaded()
            project = ManualProject(
                manual_id=self._next_id(),
                title=title.strip(),
                company_name=(company_name or "").strip(),
                amount=float(amount or 0),
                probability=float(probability or 0),
                date=date or None,
                projet_start=projet_start or None,
                projet_stop=projet_stop or None,
                cf_bu=(cf_bu or "AUTRE").strip(),
                cf_typologie_de_devis=(cf_typologie_de_devis or "").strip(),
                assigned_to=(assigned_to or "").strip(),
                statut=(statut or DEFAULT_STATUT).strip(),
                created_by=(created_by or "").strip(),
            )
            cache["projects"].append(project)
            self._flush()
            return ManualProject.from_dict(project.to_dict())

    def update(self, manual_id: str, **fields: Any) -> Optional[ManualProject]:
        key = str(manual_id)
        allowed = {
            "title",
            "company_name",
            "amount",
            "probability",
            "date",
            "projet_start",
            "projet_stop",
            "cf_bu",
            "cf_typologie_de_devis",
            "assigned_to",
            "statut",
        }
        with self._lock:
            cache = self._ensure_loaded()
            for idx, p in enumerate(cache["projects"]):
                if p.manual_id != key:
                    continue
                for field_name, value in fields.items():
                    if field_name not in allowed:
                        continue
                    if field_name in ("amount", "probability"):
                        value = float(value or 0)
                    elif value is not None:
                        value = str(value).strip() if isinstance(value, str) else value
                    setattr(p, field_name, value)
                cache["projects"][idx] = p
                self._flush()
                return ManualProject.from_dict(p.to_dict())
            return None

    def delete(self, manual_id: str) -> bool:
        key = str(manual_id)
        with self._lock:
            cache = self._ensure_loaded()
            before = len(cache["projects"])
            cache["projects"] = [p for p in cache["projects"] if p.manual_id != key]
            if len(cache["projects"]) != before:
                self._flush()
                return True
            return False

    def count(self) -> int:
        with self._lock:
            return len(self._ensure_loaded()["projects"])
