"""Shared category/attribute registry. Never infer PHH select values.

This read-only resolver consumes evidence exported from an authoritative PHH API
or PHH UI session, and refuses any option not explicitly confirmed.
"""
from __future__ import annotations
from datetime import datetime, timezone


def index_taxonomy(categories):
    registry = {}
    for cat in categories:
        cid = str(cat.get("category_id") or "").strip()
        if not cid:
            continue
        attributes = []
        for field in cat.get("attributes") or []:
            field_id = str(field.get("field_id") or "").strip()
            if field_id:
                attributes.append({
                    "field_id": field_id,
                    "title_lt": str(field.get("title_lt") or "").strip(),
                    "required": field.get("required") is True,
                    "options_contract": "UNKNOWN"
                })
        registry[cid] = {
            "category_id": cid,
            "addable": cat.get("allow_add_products") is True,
            "required_fields": [x for x in attributes if x["required"]],
            "fields": attributes,
        }
    return registry


def resolve_attributes(category_id, supplied_facts, registry, confirmed_options=None):
    """Return mapped field IDs plus exact LT names. Never generate unknown enums."""
    confirmed_options = confirmed_options or {}
    category = registry.get(str(category_id))
    if not category or not category["addable"]:
        return {"state":"BLOCKED", "reasons":["CATEGORY_NOT_AUTHORIZED"], "attributes":[]}
    ready, gaps = [], []
    for field in category["required_fields"]:
        fid = field["field_id"]
        fact = supplied_facts.get(fid)
        if fact is None or str(fact).strip() == "":
            gaps.append("MISSING_REQUIRED_FIELD:" + fid)
            continue
        option_evidence = confirmed_options.get((str(category_id), fid))
        if not option_evidence:
            gaps.append("UNKNOWN_FIELD_VALUE_CONTRACT:" + fid)
            continue
        if option_evidence.get("source") not in ("PHH_API", "PHH_AUTHENTICATED_UI"):
            gaps.append("UNCONFIRMED_DICTIONARY_SOURCE:" + fid)
            continue
        if option_evidence.get("verified") is not True:
            gaps.append("UNVERIFIED_DICTIONARY:" + fid)
            continue
        allowed = option_evidence.get("values") or []
        if str(fact).strip() not in allowed:
            gaps.append("VALUE_NOT_IN_PHH_DICTIONARY:" + fid)
            continue
        ready.append({"field_id":fid,"name_lt":field["title_lt"],"value":str(fact).strip(),"verified":True})
    return {"state":"NEEDS_DATA" if gaps else "READY", "reasons":gaps, "attributes":ready}
