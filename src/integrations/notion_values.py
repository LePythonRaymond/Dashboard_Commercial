"""
Reduce Notion property values to plain Python values, so a sync can tell
whether a property really changed and a check can compare Notion with Furious.

Two shapes exist for the same property:
- the payload a sync sends:   {"number": 4250.0}, {"status": {"name": "brief"}}
- the value Notion returns:   {"type": "number", "number": 4250.0}, {"type": "status", ...}

Both reduce to the same comparable value (4250.0, "brief"). Lists (people,
multi_select, relation) are sorted so their order never counts as a change,
numbers are rounded to the cent, dates keep only YYYY-MM-DD and page ids lose
their dashes (Notion accepts both spellings and returns the dashed one).
"""

from typing import Any, Dict, List


def _plain_from_payload(parts: List[Dict[str, Any]]) -> str:
    return "".join((p.get("text") or {}).get("content", "") for p in parts or [])


def _plain_from_page(parts: List[Dict[str, Any]]) -> str:
    return "".join(p.get("plain_text") or (p.get("text") or {}).get("content", "") for p in parts or [])


def _page_ids(items: List[Dict[str, Any]]) -> List[str]:
    return sorted(str(i.get("id", "")).replace("-", "") for i in items or [])


def value_from_payload(prop: Dict[str, Any]) -> Any:
    """Reduce a property we are about to send to a comparable Python value."""
    if "title" in prop:
        return _plain_from_payload(prop["title"])
    if "rich_text" in prop:
        return _plain_from_payload(prop["rich_text"])
    if "number" in prop:
        return None if prop["number"] is None else round(float(prop["number"]), 2)
    if "select" in prop:
        return (prop["select"] or {}).get("name")
    if "status" in prop:
        return (prop["status"] or {}).get("name")
    if "multi_select" in prop:
        return sorted(o["name"] for o in prop["multi_select"] or [])
    if "date" in prop:
        return ((prop["date"] or {}).get("start") or None)
    if "url" in prop:
        return prop["url"] or None
    if "people" in prop:
        return sorted(u["id"] for u in prop["people"] or [])
    if "checkbox" in prop:
        return bool(prop["checkbox"])
    if "relation" in prop:
        return _page_ids(prop["relation"])
    return None


def value_from_page(prop: Dict[str, Any]) -> Any:
    """Reduce a property read from Notion to the same comparable value."""
    kind = (prop or {}).get("type")
    if kind == "title":
        return _plain_from_page(prop["title"])
    if kind == "rich_text":
        return _plain_from_page(prop["rich_text"])
    if kind == "number":
        return None if prop["number"] is None else round(float(prop["number"]), 2)
    if kind == "select":
        return (prop["select"] or {}).get("name")
    if kind == "status":
        return (prop["status"] or {}).get("name")
    if kind == "multi_select":
        return sorted(o["name"] for o in prop["multi_select"] or [])
    if kind == "date":
        start = (prop["date"] or {}).get("start")
        return start[:10] if start else None
    if kind == "url":
        return prop["url"] or None
    if kind == "people":
        return sorted(u["id"] for u in prop["people"] or [])
    if kind == "checkbox":
        return bool(prop["checkbox"])
    if kind == "relation":
        return _page_ids(prop["relation"])
    return None


def page_value(page: Dict[str, Any], name: str) -> Any:
    """Comparable value of one property of a Notion page (None when absent)."""
    return value_from_page(((page or {}).get("properties") or {}).get(name, {}))
