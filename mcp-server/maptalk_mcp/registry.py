"""Config-driven tool registry.

Tools are declared in ``tools.yaml``. The registry turns each entry into an MCP
``Tool`` (with a JSON-Schema input) and dispatches calls to the handler named by
the entry. The file is re-read whenever its mtime changes, so tools and their
descriptions can be edited at runtime — reconnect the client to refresh the
visible list; a call always uses the latest file.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import mcp.types as types
import yaml

from .client import MaptalkClient
from .handlers import HANDLERS, ContentBlock

_JSON_TYPES = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
}


@dataclass(frozen=True)
class ParamSpec:
    name: str
    location: str = "query"          # query | path | arg
    maps_to: str | None = None       # backend param name if it differs from `name`
    type: str = "string"
    required: bool = False
    default: Any = None
    description: str = ""

    def coerce(self, value: Any) -> Any:
        try:
            if self.type == "integer":
                return int(value)
            if self.type == "number":
                return float(value)
            if self.type == "boolean":
                return bool(value)
        except (TypeError, ValueError):
            raise ValueError(f"Argument '{self.name}' must be a {self.type}.")
        return value


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    handler: str
    method: str = "GET"
    path: str = ""
    params: list[ParamSpec] = field(default_factory=list)

    def input_schema(self) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        required: list[str] = []
        for param in self.params:
            prop: dict[str, Any] = {"type": _JSON_TYPES.get(param.type, "string")}
            if param.description:
                prop["description"] = param.description
            if param.default is not None:
                prop["default"] = param.default
            properties[param.name] = prop
            if param.required:
                required.append(param.name)
        schema: dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required
        return schema

    def resolve_args(self, arguments: dict[str, Any]) -> dict[str, Any]:
        resolved: dict[str, Any] = {}
        for param in self.params:
            if arguments.get(param.name) is not None:
                resolved[param.name] = param.coerce(arguments[param.name])
            elif param.default is not None:
                resolved[param.name] = param.default
            elif param.required:
                raise ValueError(f"Missing required argument: {param.name}")
        return resolved


def _parse_param(raw: dict[str, Any]) -> ParamSpec:
    return ParamSpec(
        name=raw["name"],
        location=raw.get("in", "query"),
        maps_to=raw.get("maps_to"),
        type=raw.get("type", "string"),
        required=bool(raw.get("required", False)),
        default=raw.get("default"),
        description=raw.get("description", ""),
    )


def _parse_tool(raw: dict[str, Any]) -> ToolSpec:
    handler = raw["handler"]
    if handler not in HANDLERS:
        raise ValueError(f"Tool '{raw.get('name')}' uses unknown handler '{handler}'.")
    return ToolSpec(
        name=raw["name"],
        description=" ".join(raw.get("description", "").split()),
        handler=handler,
        method=raw.get("method", "GET"),
        path=raw.get("path", ""),
        params=[_parse_param(p) for p in raw.get("params", [])],
    )


class ToolRegistry:
    def __init__(
        self,
        tools_file: str,
        client: MaptalkClient,
        web_client: httpx.AsyncClient | None = None,
    ):
        self._path = Path(tools_file)
        self.client = client
        self.web_client = web_client
        self._mtime: float | None = None
        self._specs: dict[str, ToolSpec] = {}
        self._reload(force=True)

    def _reload(self, force: bool = False) -> None:
        try:
            mtime = self._path.stat().st_mtime
        except OSError:
            if force:
                raise
            return  # file vanished mid-run — keep the last good registry
        if not force and mtime == self._mtime:
            return
        try:
            raw = yaml.safe_load(self._path.read_text(encoding="utf-8")) or {}
            specs = [_parse_tool(t) for t in raw.get("tools", [])]
        except (yaml.YAMLError, KeyError, ValueError) as exc:
            if force:
                raise
            print(f"[maptalk-mcp] tools.yaml reload failed, keeping previous: {exc}",
                  file=sys.stderr)
            return
        self._specs = {spec.name: spec for spec in specs}
        self._mtime = mtime

    def list_tools(self) -> list[types.Tool]:
        self._reload()
        return [
            types.Tool(name=s.name, description=s.description, inputSchema=s.input_schema())
            for s in self._specs.values()
        ]

    async def dispatch(
        self, name: str, arguments: dict[str, Any], token: str | None = None
    ) -> list[ContentBlock]:
        self._reload()
        spec = self._specs.get(name)
        if spec is None:
            return [types.TextContent(type="text", text=f"Unknown tool: {name}")]
        try:
            resolved = spec.resolve_args(arguments)
            return await HANDLERS[spec.handler](self, spec, resolved, token)
        except Exception as exc:  # surface a readable error to the model
            return [types.TextContent(type="text", text=f"Error calling {name}: {exc}")]
