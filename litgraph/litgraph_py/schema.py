"""A small JSON Schema validator covering the subset our schemas use.

Supported: type (incl. lists and "null"), enum, properties, required, items,
minItems/maxItems, minLength, minimum/maximum, pattern, $ref (local "#/$defs/x"
and sibling files "finding.schema.json#/$defs/x"). Kept dependency-free so the
plugin works with a bare Python install.
"""

from __future__ import annotations

import re
from functools import lru_cache

from .io import SCHEMA_DIR, read_json

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


@lru_cache(maxsize=None)
def load(name: str) -> dict:
    return read_json(SCHEMA_DIR / name)


def _resolve(ref: str, doc: dict) -> tuple[dict, dict]:
    file, _, frag = ref.partition("#")
    target_doc = load(file) if file else doc
    node = target_doc
    for part in [p for p in frag.split("/") if p]:
        node = node[part]
    return node, target_doc


def errors(instance, schema: dict | str, doc: dict | None = None, path: str = "$") -> list[str]:
    """Return a list of human-readable problems ('$.findings[2].page: ...')."""
    if isinstance(schema, str):
        schema = load(schema)
    doc = doc if doc is not None else schema
    if "$ref" in schema:
        node, ndoc = _resolve(schema["$ref"], doc)
        return errors(instance, node, ndoc, path)

    out: list[str] = []
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_TYPES[x](instance) for x in types):
            return [f"{path}: expected {'/'.join(types)}, got {type(instance).__name__} {_short(instance)}"]
    if "enum" in schema and instance not in schema["enum"]:
        allowed = ", ".join("null" if e is None else str(e) for e in schema["enum"])
        out.append(f"{path}: {_short(instance)} is not one of [{allowed}]")
    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            out.append(f"{path}: shorter than {schema['minLength']} characters")
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            out.append(f"{path}: {instance!r} does not match {schema['pattern']}")
    if _TYPES["number"](instance):
        if "minimum" in schema and instance < schema["minimum"]:
            out.append(f"{path}: {instance} < minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            out.append(f"{path}: {instance} > maximum {schema['maximum']}")
    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                out.append(f"{path}: missing required field '{key}'")
        for key, sub in schema.get("properties", {}).items():
            if key in instance:
                out.extend(errors(instance[key], sub, doc, f"{path}.{key}"))
    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            out.append(f"{path}: needs at least {schema['minItems']} item(s)")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            out.append(f"{path}: at most {schema['maxItems']} item(s)")
        if "items" in schema:
            for i, item in enumerate(instance):
                out.extend(errors(item, schema["items"], doc, f"{path}[{i}]"))
    return out


def _short(v) -> str:
    s = repr(v)
    return s if len(s) <= 40 else s[:37] + "..."
