"""Contract 3 — ``@tool``: one decorator, four artifacts.

Decorating a function produces:
    1. a JSON schema for native function calling,
    2. a permission declaration (``network`` / ``sensitive`` / ``writes``),
    3. a **mock implementation** (not optional — it is what makes
       ``ack demo --offline`` real, invariant 5),
    4. a doc entry (the docstring, surfaced by ``ack manifest`` and MCP).

Example:
    >>> def mock_add(a: int, b: int) -> int:
    ...     return 3
    >>> @tool(permissions={"network"}, mock=mock_add)
    ... def add(a: int, b: int) -> int:
    ...     '''Add two integers.'''
    ...     return a + b
    >>> add.spec.name
    'add'
    >>> sorted(add.spec.permissions)
    ['network']
    >>> add.spec.json_schema["properties"]["a"]["type"]
    'integer'
    >>> add(1, 2)
    3
    >>> add.spec.mock(1, 2)
    3
"""

from __future__ import annotations

import dataclasses
import inspect
import types
import typing
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, get_args, get_origin, get_type_hints

from .errors import AckError

__all__ = ["Permission", "ToolSpec", "tool"]

Permission = Literal["network", "sensitive", "writes"]

_VALID_PERMISSIONS = {"network", "sensitive", "writes"}


@dataclass(frozen=True)
class ToolSpec:
    """The four artifacts a ``@tool`` decoration emits, in one place."""

    name: str
    description: str
    json_schema: dict[str, Any]
    permissions: frozenset[str]
    fn: Callable[..., Any]
    mock: Callable[..., Any]

    def as_function_schema(self) -> dict[str, Any]:
        """Provider-facing function-calling declaration."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.json_schema,
            },
        }

    def to_manifest(self) -> dict[str, Any]:
        """Entry for the tool manifest (``spec/schemas/tool-manifest``)."""
        return {
            "name": self.name,
            "description": self.description,
            "permissions": sorted(self.permissions),
            "parameters": self.json_schema,
            "has_mock": True,
        }


class Tool:
    """Callable wrapper carrying its ``ToolSpec``. Calling it calls the
    real function; ``ack demo --offline`` swaps in ``spec.mock``."""

    def __init__(self, spec: ToolSpec) -> None:
        self.spec = spec
        self.__name__ = spec.name
        self.__doc__ = spec.description

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.spec.fn(*args, **kwargs)


def tool(
    *,
    permissions: set[str] | frozenset[str] = frozenset(),
    mock: Callable[..., Any] | None = None,
    name: str | None = None,
) -> Callable[[Callable[..., Any]], Tool]:
    """Declare a tool. See module docstring for a runnable example.

    Raises:
        AckError E502: the tool has no mock (mocks are not optional).
        AckError E503: an unknown permission was declared.
    """
    perms = frozenset(permissions)
    unknown = perms - _VALID_PERMISSIONS
    if unknown:
        raise AckError(
            f"unknown tool permission(s): {', '.join(sorted(unknown))}",
            code="E503",
            why=f"valid permissions are: {', '.join(sorted(_VALID_PERMISSIONS))}",
            fix='use @tool(permissions={"network"}, ...)',
        )

    def deco(fn: Callable[..., Any]) -> Tool:
        if mock is None:
            raise AckError(
                f"tool '{name or fn.__name__}' declared without a mock",
                code="E502",
                why="every tool ships a mock — it is what makes `ack demo --offline` real.",
                fix=f"@tool(mock=mock_{fn.__name__}, ...)  # def mock_{fn.__name__}(...) returns canned data",
            )
        spec = ToolSpec(
            name=name or fn.__name__,
            description=inspect.getdoc(fn) or "",
            json_schema=_schema_from_signature(fn),
            permissions=perms,
            fn=fn,
            mock=mock,
        )
        return Tool(spec)

    return deco


# ── schema derivation ────────────────────────────────────────────────────

_SCALARS: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def _annotation_to_schema(ann: Any) -> dict[str, Any]:
    """Best-effort JSON Schema for a Python annotation. Unknown types map
    to an unconstrained schema rather than failing — a tool author's exotic
    type must never break decoration."""
    if ann is inspect.Parameter.empty or ann is Any:
        return {}
    if ann in _SCALARS:
        return {"type": _SCALARS[ann]}
    origin = get_origin(ann)
    if origin in (list, tuple, set, frozenset):
        args = get_args(ann)
        item = _annotation_to_schema(args[0]) if args else {}
        return {"type": "array", "items": item}
    if origin is dict:
        return {"type": "object"}
    if origin in (typing.Union, types.UnionType):
        args = [a for a in get_args(ann) if a is not type(None)]
        variants = [_annotation_to_schema(a) for a in args]
        if len(variants) == 1:
            return variants[0]
        return {"anyOf": variants}
    if origin is Literal:
        return {"enum": list(get_args(ann))}
    if dataclasses.is_dataclass(ann):
        props = {
            f.name: _annotation_to_schema(f.type) for f in dataclasses.fields(ann)
        }
        return {"type": "object", "properties": props}
    if hasattr(ann, "model_json_schema"):  # pydantic v2
        return ann.model_json_schema()
    return {}


def _schema_from_signature(fn: Callable[..., Any]) -> dict[str, Any]:
    """Derive the function-calling parameter schema from type hints."""
    sig = inspect.signature(fn)
    try:
        hints = get_type_hints(fn)
    except Exception:
        hints = {}
    props: dict[str, Any] = {}
    required: list[str] = []
    for pname, param in sig.parameters.items():
        if pname in ("self", "cls"):
            continue
        props[pname] = _annotation_to_schema(hints.get(pname, param.annotation))
        if param.default is inspect.Parameter.empty:
            required.append(pname)
    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema
