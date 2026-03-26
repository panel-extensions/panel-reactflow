"""Core React Flow component and helpers."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

import panel as pn
import param
from bokeh.embed.bundle import extension_dirs
from bokeh.plotting import figure
from panel.config import config
from panel.custom import Children, ReactComponent
from panel.io.resources import EXTENSION_CDN
from panel.io.state import state
from panel.util import base_version, classproperty
from panel.viewable import Viewer
from panel.widgets import JSONEditor

from .__version import __version__  # noqa

if TYPE_CHECKING:
    from bokeh.models import UIElement

IS_RELEASE = __version__ == base_version(__version__)
BASE_PATH = Path(__file__).parent
DIST_PATH = BASE_PATH / "dist"
CDN_BASE = f"https://cdn.holoviz.org/panel-reactflow/v{base_version(__version__)}"
CDN_DIST = f"{CDN_BASE}/panel-reactflow.bundle.js"

extension_dirs["panel-reactflow"] = DIST_PATH
EXTENSION_CDN[DIST_PATH] = CDN_BASE

BK_FIGURE_CSS = """
.bk-Canvas {
  transform: scale(var(--rf-inverse-zoom));
  transform-origin: top left;
  width: calc(var(--rf-zoom) * 100%);
  height: calc(var(--rf-zoom) * 100%);
}
"""


def _ensure_jsonable(value: Any, path: str) -> None:
    """Ensure value can be JSON-serialized for syncing to the frontend."""

    try:
        json.dumps(value)
    except Exception as exc:
        raise ValueError(f"Value at {path} is not JSON-serializable.") from exc


def _is_param_class(obj: Any) -> bool:
    """Check if *obj* is a ``param.Parameterized`` **subclass** (not instance)."""
    return isinstance(obj, type) and issubclass(obj, param.Parameterized)


def _is_pydantic_class(obj: Any) -> bool:
    """Check if *obj* is a Pydantic ``BaseModel`` subclass."""
    try:
        from pydantic import BaseModel

        return isinstance(obj, type) and issubclass(obj, BaseModel)
    except ImportError:
        return False


def _param_to_jsonschema(parameterized_cls: type) -> dict[str, Any]:
    """Convert a ``param.Parameterized`` class to a JSON Schema dict.

    Uses ``parameterized_cls.param.schema()`` for the per-property
    schemas, then wraps them in a standard JSON Schema object envelope
    while filtering out base ``Parameterized`` params and private
    (``_``-prefixed) params.
    """
    base_params = set(param.Parameterized.param)
    raw = parameterized_cls.param.schema()
    properties = {name: prop for name, prop in raw.items() if name not in base_params and not name.startswith("_")}
    return {"type": "object", "properties": properties}


def _parameterized_data_param_names(parameterized_cls: type[param.Parameterized], base_cls: type[param.Parameterized]) -> list[str]:
    """Return subclass-defined parameter names included in node/edge data.

    Only parameters with explicitly non-negative precedence are included.
    """
    base_params = set(base_cls.param)
    names: list[str] = []
    for name in parameterized_cls.param:
        if name in base_params or name.startswith("_"):
            continue
        precedence = parameterized_cls.param[name].precedence
        if precedence is not None and precedence >= 0:
            names.append(name)
    return names


def _parameterized_data_schema(parameterized_cls: type[param.Parameterized], base_cls: type[param.Parameterized]) -> dict[str, Any]:
    """Build a JSON Schema for subclass-defined data parameters."""
    names = _parameterized_data_param_names(parameterized_cls, base_cls)
    schema = _param_to_jsonschema(parameterized_cls)
    properties = schema.get("properties", {})
    return {"type": "object", "properties": {name: properties[name] for name in names if name in properties}}


def _pydantic_to_jsonschema(model_cls: type) -> dict[str, Any]:
    """Convert a Pydantic ``BaseModel`` class to a JSON Schema dict."""
    return model_cls.model_json_schema()


def _normalize_schema(schema: Any) -> dict[str, Any] | None:
    """Normalize a schema source to a JSON Schema dict (or ``None``)."""
    if schema is None:
        return None
    if isinstance(schema, SchemaSource):
        if schema.kind == "jsonschema":
            return schema.value
        elif schema.kind == "param":
            return _param_to_jsonschema(schema.value)
        elif schema.kind == "pydantic":
            return _pydantic_to_jsonschema(schema.value)
    if isinstance(schema, dict):
        return schema
    if _is_param_class(schema):
        return _param_to_jsonschema(schema)
    if _is_pydantic_class(schema):
        return _pydantic_to_jsonschema(schema)
    raise ValueError(f"Cannot normalize schema: {schema!r}")


def _validate_data(data: dict[str, Any], schema: dict[str, Any] | None) -> None:
    """Validate *data* against a JSON Schema if available."""
    if schema is None:
        return
    try:
        import jsonschema as _js
    except ImportError:
        return
    try:
        _js.validate(data, schema)
    except _js.ValidationError as exc:
        path = ".".join(str(p) for p in exc.absolute_path) or "(root)"
        raise ValueError(f"Validation failed at {path}: {exc.message}") from exc


def _coerce_spec_map(specs: dict[str, Any] | None, *, edge: bool = False) -> dict[str, dict[str, Any]]:
    """Normalize a dict of type specs to JSON-serializable descriptors."""
    if not specs:
        return {}
    normalized: dict[str, dict[str, Any]] = {}
    for key, value in specs.items():
        if hasattr(value, "to_dict") and callable(value.to_dict):
            normalized[key] = value.to_dict()
        elif isinstance(value, dict):
            normalized[key] = value
        elif _is_param_class(value):
            klass = EdgeType if edge else NodeType
            normalized[key] = klass(type=key, schema=value).to_dict()
        elif _is_pydantic_class(value):
            klass = EdgeType if edge else NodeType
            normalized[key] = klass(type=key, schema=value).to_dict()
        else:
            raise ValueError(f"Unsupported spec type for '{key}'.")
    return normalized


@dataclass
class SchemaSource:
    """Explicit schema source wrapper for type definitions.

    Use this wrapper when you need to explicitly specify the schema format
    for node or edge types. This is useful when automatic detection might
    be ambiguous or when you want to be explicit about the schema source.

    Parameters
    ----------
    kind : {"jsonschema", "param", "pydantic"}
        The schema format type:

        - ``"jsonschema"``: A standard JSON Schema dictionary
        - ``"param"``: A ``param.Parameterized`` class
        - ``"pydantic"``: A Pydantic ``BaseModel`` class
    value : dict or type
        The schema value matching the specified ``kind``:

        - For ``"jsonschema"``: A JSON Schema dictionary
        - For ``"param"``: A ``param.Parameterized`` subclass
        - For ``"pydantic"``: A Pydantic ``BaseModel`` subclass

    Examples
    --------
    Using a JSON Schema:

    >>> from panel_reactflow import SchemaSource
    >>> schema = SchemaSource(
    ...     kind="jsonschema",
    ...     value={"type": "object", "properties": {"name": {"type": "string"}}}
    ... )

    Using a Param class:

    >>> import param
    >>> class MyParams(param.Parameterized):
    ...     label = param.String(default="")
    >>> schema = SchemaSource(kind="param", value=MyParams)

    Using a Pydantic model:

    >>> from pydantic import BaseModel
    >>> class MyModel(BaseModel):
    ...     name: str
    >>> schema = SchemaSource(kind="pydantic", value=MyModel)
    """

    kind: Literal["jsonschema", "param", "pydantic"]
    value: Any


@dataclass
class NodeType:
    """Define a custom node type with schema and port configuration.

    Node types allow you to define reusable node templates with specific data
    schemas, input/output ports, and display policies. When nodes are created
    with this type, they automatically get schema validation and appropriate
    editors.

    Parameters
    ----------
    type : str
        Unique identifier for this node type. Used to reference this type
        when creating nodes.
    label : str, optional
        Human-readable display name for this node type. If not provided,
        the ``type`` value is used.
    schema : dict or type, optional
        Data schema for node validation and editor generation. Accepts:

        - A JSON Schema dictionary
        - A ``param.Parameterized`` subclass
        - A Pydantic ``BaseModel`` subclass
        - A :class:`SchemaSource` wrapper for explicit schema types

        The schema is normalized to JSON Schema format internally.
    inputs : list of str, optional
        List of input port names. If provided, these ports will be rendered
        on the node for incoming connections.
    outputs : list of str, optional
        List of output port names. If provided, these ports will be rendered
        on the node for outgoing connections.
    pane_policy : str, default "single"
        Display policy for Panel viewables inside nodes.

    Methods
    -------
    to_dict()
        Convert this node type to a JSON-serializable dictionary.

    Examples
    --------
    Define a simple node type with a JSON Schema:

    >>> from panel_reactflow import NodeType
    >>> transform_type = NodeType(
    ...     type="transform",
    ...     label="Data Transform",
    ...     schema={
    ...         "type": "object",
    ...         "properties": {
    ...             "operation": {"type": "string", "enum": ["filter", "map", "reduce"]},
    ...             "parameter": {"type": "number"}
    ...         }
    ...     },
    ...     inputs=["input"],
    ...     outputs=["output"]
    ... )

    Define a node type with a Param class:

    >>> import param
    >>> class TransformParams(param.Parameterized):
    ...     operation = param.Selector(default="filter", objects=["filter", "map", "reduce"])
    ...     parameter = param.Number(default=1.0)
    >>> transform_type = NodeType(
    ...     type="transform",
    ...     label="Data Transform",
    ...     schema=TransformParams,
    ...     inputs=["input"],
    ...     outputs=["output"]
    ... )

    Use the node type in a ReactFlow graph:

    >>> from panel_reactflow import ReactFlow, NodeSpec
    >>> flow = ReactFlow(node_types={"transform": transform_type})
    >>> flow.add_node(NodeSpec(
    ...     id="t1",
    ...     type="transform",
    ...     position={"x": 100, "y": 100},
    ...     data={"operation": "filter", "parameter": 2.5}
    ... ))
    """

    type: str
    label: str | None = None
    schema: Any = None
    inputs: list[str] | None = None
    outputs: list[str] | None = None
    pane_policy: str = "single"

    def to_dict(self) -> dict[str, Any]:
        """Convert the node type to a JSON-serializable dictionary.

        Returns
        -------
        dict
            Dictionary representation with normalized schema.
        """
        return {
            "type": self.type,
            "label": self.label,
            "schema": _normalize_schema(self.schema),
            "inputs": self.inputs,
            "outputs": self.outputs,
            "pane_policy": self.pane_policy,
        }


@dataclass
class EdgeType:
    """Define a custom edge type with schema for edge properties.

    Edge types allow you to define reusable edge templates with specific data
    schemas for validation and editor generation. Use this when your edges
    have custom properties beyond the basic source/target relationship.

    Parameters
    ----------
    type : str
        Unique identifier for this edge type. Used to reference this type
        when creating edges.
    label : str, optional
        Human-readable display name for this edge type. If not provided,
        the ``type`` value is used.
    schema : dict or type, optional
        Data schema for edge validation and editor generation. Accepts the
        same formats as :class:`NodeType`:

        - A JSON Schema dictionary
        - A ``param.Parameterized`` subclass
        - A Pydantic ``BaseModel`` subclass
        - A :class:`SchemaSource` wrapper for explicit schema types

    Methods
    -------
    to_dict()
        Convert this edge type to a JSON-serializable dictionary.

    Examples
    --------
    Define an edge type with properties:

    >>> from panel_reactflow import EdgeType
    >>> weighted_edge = EdgeType(
    ...     type="weighted",
    ...     label="Weighted Connection",
    ...     schema={
    ...         "type": "object",
    ...         "properties": {
    ...             "weight": {"type": "number", "minimum": 0, "maximum": 1},
    ...             "label": {"type": "string"}
    ...         }
    ...     }
    ... )

    Use the edge type in a ReactFlow graph:

    >>> from panel_reactflow import ReactFlow, EdgeSpec
    >>> flow = ReactFlow(edge_types={"weighted": weighted_edge})
    >>> flow.add_edge(EdgeSpec(
    ...     id="e1",
    ...     source="n1",
    ...     target="n2",
    ...     type="weighted",
    ...     data={"weight": 0.75, "label": "strong"}
    ... ))
    """

    type: str
    label: str | None = None
    schema: Any = None

    def to_dict(self) -> dict[str, Any]:
        """Convert the edge type to a JSON-serializable dictionary.

        Returns
        -------
        dict
            Dictionary representation with normalized schema.
        """
        return {
            "type": self.type,
            "label": self.label,
            "schema": _normalize_schema(self.schema),
        }


@dataclass
class NodeSpec:
    """Builder for node dictionaries with validation and type safety.

    This helper class simplifies node creation by providing a structured
    interface with sensible defaults. It ensures all required fields are
    present and provides convenient conversion to/from dictionaries.

    Parameters
    ----------
    id : str
        Unique identifier for the node. Must be unique within the graph.
    position : dict, optional
        Node position with ``x`` and ``y`` coordinates. Defaults to
        ``{"x": 0.0, "y": 0.0}`` if not provided.
    type : str, default "panel"
        Node type identifier. Use ``"panel"`` for basic nodes or reference
        a custom type defined in ``ReactFlow.node_types``.
    label : str, optional
        Display label shown on the node. If ``None``, no label is displayed.
    data : dict, optional
        Custom data dictionary for the node. Defaults to ``{}`` if not provided.
        This is where you store node-specific properties that match the schema.
    selected : bool, default False
        Whether the node is currently selected in the UI.
    draggable : bool, default True
        Whether the node can be dragged by users.
    connectable : bool, default True
        Whether edges can be connected to/from this node.
    deletable : bool, default True
        Whether the node can be deleted by users.
    style : dict, optional
        CSS style dictionary applied to the node. Example:
        ``{"backgroundColor": "#ff0000", "border": "2px solid black"}``
    className : str, optional
        CSS class name applied to the node for custom styling.
    view : Panel viewable, optional
        Optional Panel viewable (widget, pane, layout) to render inside
        the node. The view will be displayed as the node's content.

    Methods
    -------
    to_dict()
        Convert to a dictionary for use with ReactFlow.
    from_dict(payload)
        Create a NodeSpec from a dictionary.

    Examples
    --------
    Create a basic node:

    >>> from panel_reactflow import NodeSpec
    >>> node = NodeSpec(
    ...     id="node1",
    ...     position={"x": 100, "y": 50},
    ...     label="Start Node"
    ... )
    >>> node_dict = node.to_dict()

    Create a node with custom styling:

    >>> node = NodeSpec(
    ...     id="node2",
    ...     position={"x": 200, "y": 100},
    ...     label="Process",
    ...     style={"backgroundColor": "#e3f2fd", "border": "2px solid #1976d2"},
    ...     className="custom-node"
    ... )

    Create a node with data:

    >>> node = NodeSpec(
    ...     id="transform1",
    ...     type="transform",
    ...     position={"x": 300, "y": 150},
    ...     label="Data Transform",
    ...     data={"operation": "filter", "threshold": 0.5}
    ... )

    Create a node with an embedded view:

    >>> import panel as pn
    >>> node = NodeSpec(
    ...     id="plot1",
    ...     position={"x": 400, "y": 200},
    ...     label="Data Plot",
    ...     view=pn.pane.Markdown("# Hello World")
    ... )

    Add to a ReactFlow graph:

    >>> from panel_reactflow import ReactFlow
    >>> flow = ReactFlow()
    >>> flow.add_node(node)
    """

    id: str
    position: dict[str, float] | dict[str, Any] = None
    type: str = "panel"
    label: str | None = None
    data: dict[str, Any] | None = None
    selected: bool = False
    draggable: bool = True
    connectable: bool = True
    deletable: bool = True
    style: dict[str, Any] | None = None
    className: str | None = None
    view: Any | None = None

    def __post_init__(self) -> None:
        if self.position is None:
            self.position = {"x": 0.0, "y": 0.0}
        if self.data is None:
            self.data = {}

    def to_dict(self) -> dict[str, Any]:
        """Convert the NodeSpec to a dictionary.

        Returns
        -------
        dict
            Dictionary representation suitable for ReactFlow.
        """
        payload = {
            "id": self.id,
            "position": self.position,
            "type": self.type,
            "label": self.label,
            "data": self.data,
            "selected": self.selected,
            "draggable": self.draggable,
            "connectable": self.connectable,
            "deletable": self.deletable,
        }
        if self.style is not None:
            payload["style"] = self.style
        if self.className is not None:
            payload["className"] = self.className
        if self.view is not None:
            payload["view"] = self.view
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "NodeSpec":
        """Create a NodeSpec from a dictionary.

        Parameters
        ----------
        payload : dict
            Dictionary containing node properties.

        Returns
        -------
        NodeSpec
            A new NodeSpec instance.
        """
        return cls(**payload)


class Node(param.Parameterized):
    """Base class for object-oriented nodes.

    Subclass this class when you want node instances to keep Python-side state
    and react to graph events directly. Node instances can be passed anywhere
    a node dict/``NodeSpec`` is accepted.

    Subclasses can customize:

    - ``__panel__`` to render node content.
    - ``editor`` to provide a node-specific editor.
    - ``on_event`` (wildcard) and event-specific ``on_*`` hooks.
    """

    id = param.String(default="", doc="Unique node identifier.")
    position = param.Dict(default={"x": 0.0, "y": 0.0}, doc="Node position.")
    type = param.String(default="panel", doc="Node type.")
    label = param.String(default=None, allow_None=True, doc="Display label.")
    data = param.Dict(default={}, doc="Custom node data.")
    selected = param.Boolean(default=False, doc="Selection state.")
    draggable = param.Boolean(default=True, doc="Whether node is draggable.")
    connectable = param.Boolean(default=True, doc="Whether node is connectable.")
    deletable = param.Boolean(default=True, doc="Whether node is deletable.")
    style = param.Dict(default=None, allow_None=True, doc="Optional node style.")
    className = param.String(default=None, allow_None=True, doc="Optional CSS class.")
    flow = param.Parameter(default=None, allow_None=True, precedence=-1, doc="Parent ReactFlow instance.")

    @classmethod
    def _data_param_names(cls) -> list[str]:
        return _parameterized_data_param_names(cls, Node)

    @classmethod
    def _data_schema(cls) -> dict[str, Any]:
        return _parameterized_data_schema(cls, Node)

    def to_dict(self) -> dict[str, Any]:
        """Convert this node instance to a ReactFlow-compatible dictionary."""
        data = dict(self.data or {})
        for name in self._data_param_names():
            data[name] = getattr(self, name)
        payload = {
            "id": self.id,
            "position": dict(self.position or {"x": 0.0, "y": 0.0}),
            "type": self.type or "panel",
            "label": self.label,
            "data": data,
            "selected": self.selected,
            "draggable": self.draggable,
            "connectable": self.connectable,
            "deletable": self.deletable,
        }
        if self.style is not None:
            payload["style"] = dict(self.style)
        if self.className is not None:
            payload["className"] = self.className
        view = self.__panel__()
        if view is not None:
            payload["view"] = view
        return payload

    def __panel__(self) -> Any | None:
        """Optional view rendered inside the node."""
        return None

    def editor(self, data, schema, *, id, type, on_patch):
        """Optional per-node editor factory.

        Return ``None`` to fall back to type/default editors.
        """
        return None

    def on_event(self, payload: dict[str, Any], flow: "ReactFlow") -> None:
        """Wildcard event hook for node-related events."""

    def on_add(self, payload: dict[str, Any], flow: "ReactFlow") -> None:
        """Hook called when this node is added."""

    def on_delete(self, payload: dict[str, Any], flow: "ReactFlow") -> None:
        """Hook called when this node is deleted."""

    def on_move(self, payload: dict[str, Any], flow: "ReactFlow") -> None:
        """Hook called when this node moves."""

    def on_click(self, payload: dict[str, Any], flow: "ReactFlow") -> None:
        """Hook called when this node is clicked."""

    def on_data_change(self, payload: dict[str, Any], flow: "ReactFlow") -> None:
        """Hook called when this node's data changes."""

    def on_selection_changed(self, payload: dict[str, Any], flow: "ReactFlow") -> None:
        """Hook called when this node participates in a selection update."""

    def on_sync(self, payload: dict[str, Any], flow: "ReactFlow") -> None:
        """Hook called when the graph receives a sync payload."""


@dataclass
class EdgeSpec:
    """Builder for edge dictionaries with validation and type safety.

    This helper class simplifies edge creation by providing a structured
    interface with sensible defaults. It ensures all required fields are
    present and provides convenient conversion to/from dictionaries.

    Parameters
    ----------
    id : str
        Unique identifier for the edge. Must be unique within the graph.
    source : str
        ID of the source node where the edge originates.
    target : str
        ID of the target node where the edge terminates.
    label : str, optional
        Display label shown on the edge. If ``None``, no label is displayed.
    type : str, optional
        Edge type identifier. Reference a custom type defined in
        ``ReactFlow.edge_types`` for schema validation and custom rendering.
    selected : bool, default False
        Whether the edge is currently selected in the UI.
    data : dict, optional
        Custom data dictionary for the edge. Defaults to ``{}`` if not provided.
        This is where you store edge-specific properties that match the schema.
    style : dict, optional
        CSS style dictionary applied to the edge line. Example:
        ``{"stroke": "#ff0000", "strokeWidth": 3}``
    markerEnd : dict, optional
        Arrow marker configuration for the edge end. Example:
        ``{"type": "arrow", "color": "#000000"}``
    sourceHandle : str, optional
        ID of the specific handle on the source node where the edge originates.
        Use this when the source node has multiple output handles defined.
    targetHandle : str, optional
        ID of the specific handle on the target node where the edge terminates.
        Use this when the target node has multiple input handles defined.

    Methods
    -------
    to_dict()
        Convert to a dictionary for use with ReactFlow.
    from_dict(payload)
        Create an EdgeSpec from a dictionary.

    Examples
    --------
    Create a basic edge:

    >>> from panel_reactflow import EdgeSpec
    >>> edge = EdgeSpec(
    ...     id="edge1",
    ...     source="node1",
    ...     target="node2"
    ... )
    >>> edge_dict = edge.to_dict()

    Create an edge with styling:

    >>> edge = EdgeSpec(
    ...     id="edge2",
    ...     source="node2",
    ...     target="node3",
    ...     label="Connection",
    ...     style={"stroke": "#1976d2", "strokeWidth": 2},
    ...     markerEnd={"type": "arrowclosed", "color": "#1976d2"}
    ... )

    Create a typed edge with data:

    >>> edge = EdgeSpec(
    ...     id="weighted_edge",
    ...     source="n1",
    ...     target="n2",
    ...     type="weighted",
    ...     label="0.75",
    ...     data={"weight": 0.75, "confidence": 0.9}
    ... )

    Create an edge with specific handles:

    >>> edge = EdgeSpec(
    ...     id="handle_edge",
    ...     source="producer",
    ...     target="consumer",
    ...     sourceHandle="result",
    ...     targetHandle="mode"
    ... )

    Add to a ReactFlow graph:

    >>> from panel_reactflow import ReactFlow
    >>> flow = ReactFlow()
    >>> flow.add_edge(edge)
    """

    id: str
    source: str
    target: str
    label: str | None = None
    type: str | None = None
    selected: bool = False
    data: dict[str, Any] | None = None
    style: dict[str, Any] | None = None
    markerEnd: dict[str, Any] | None = None
    sourceHandle: str | None = None
    targetHandle: str | None = None

    def __post_init__(self) -> None:
        if self.data is None:
            self.data = {}

    def to_dict(self) -> dict[str, Any]:
        """Convert the EdgeSpec to a dictionary.

        Returns
        -------
        dict
            Dictionary representation suitable for ReactFlow.
        """
        payload = {
            "id": self.id,
            "source": self.source,
            "target": self.target,
            "label": self.label,
            "type": self.type,
            "selected": self.selected,
            "data": self.data,
        }
        if self.style is not None:
            payload["style"] = self.style
        if self.markerEnd is not None:
            payload["markerEnd"] = self.markerEnd
        if self.sourceHandle is not None:
            payload["sourceHandle"] = self.sourceHandle
        if self.targetHandle is not None:
            payload["targetHandle"] = self.targetHandle
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EdgeSpec":
        """Create an EdgeSpec from a dictionary.

        Parameters
        ----------
        payload : dict
            Dictionary containing edge properties.

        Returns
        -------
        EdgeSpec
            A new EdgeSpec instance.
        """
        return cls(**payload)


class Edge(param.Parameterized):
    """Base class for object-oriented edges."""

    id = param.String(default="", doc="Unique edge identifier.")
    source = param.String(default="", doc="Source node id.")
    target = param.String(default="", doc="Target node id.")
    label = param.String(default=None, allow_None=True, doc="Display label.")
    type = param.String(default=None, allow_None=True, doc="Edge type.")
    selected = param.Boolean(default=False, doc="Selection state.")
    data = param.Dict(default={}, doc="Custom edge data.")
    style = param.Dict(default=None, allow_None=True, doc="Optional edge style.")
    markerEnd = param.Dict(default=None, allow_None=True, doc="Optional edge end marker.")
    sourceHandle = param.String(default=None, allow_None=True, doc="Optional source handle id.")
    targetHandle = param.String(default=None, allow_None=True, doc="Optional target handle id.")
    flow = param.Parameter(default=None, allow_None=True, precedence=-1, doc="Parent ReactFlow instance.")

    @classmethod
    def _data_param_names(cls) -> list[str]:
        return _parameterized_data_param_names(cls, Edge)

    @classmethod
    def _data_schema(cls) -> dict[str, Any]:
        return _parameterized_data_schema(cls, Edge)

    def to_dict(self) -> dict[str, Any]:
        """Convert this edge instance to a ReactFlow-compatible dictionary."""
        data = dict(self.data or {})
        for name in self._data_param_names():
            data[name] = getattr(self, name)
        payload = {
            "id": self.id,
            "source": self.source,
            "target": self.target,
            "label": self.label,
            "type": self.type,
            "selected": self.selected,
            "data": data,
        }
        if self.style is not None:
            payload["style"] = dict(self.style)
        if self.markerEnd is not None:
            payload["markerEnd"] = dict(self.markerEnd)
        if self.sourceHandle is not None:
            payload["sourceHandle"] = self.sourceHandle
        if self.targetHandle is not None:
            payload["targetHandle"] = self.targetHandle
        return payload

    def editor(self, data, schema, *, id, type, on_patch):
        """Optional per-edge editor factory.

        Return ``None`` to fall back to type/default editors.
        """
        return None

    def on_event(self, payload: dict[str, Any], flow: "ReactFlow") -> None:
        """Wildcard event hook for edge-related events."""

    def on_add(self, payload: dict[str, Any], flow: "ReactFlow") -> None:
        """Hook called when this edge is added."""

    def on_delete(self, payload: dict[str, Any], flow: "ReactFlow") -> None:
        """Hook called when this edge is deleted."""

    def on_data_change(self, payload: dict[str, Any], flow: "ReactFlow") -> None:
        """Hook called when this edge's data changes."""

    def on_selection_changed(self, payload: dict[str, Any], flow: "ReactFlow") -> None:
        """Hook called when this edge participates in a selection update."""

    def on_sync(self, payload: dict[str, Any], flow: "ReactFlow") -> None:
        """Hook called when the graph receives a sync payload."""


class Editor(Viewer):
    """Base class for custom node and edge editors.

    The Editor class provides a standardized interface for creating custom
    property editors for nodes and edges. All editors receive a unified
    signature and can report data changes back to the graph through a
    callback mechanism.

    All editor implementations (whether classes or functions) receive this
    unified signature::

        editor(data, schema, *, id, type, on_patch) -> Viewable

    Parameters
    ----------
    data : dict
        Current node or edge data dictionary. This contains all the custom
        properties stored in the node/edge.
    schema : dict or None
        Normalized JSON Schema for the node/edge type, or ``None`` if no
        schema is defined. Use this to drive form generation or validation.
    id : str
        Unique identifier of the node or edge being edited.
    type : str
        Type name of the node or edge being edited.
    on_patch : callable
        Callback function ``on_patch(patch_dict)`` to report data changes
        back to the graph. Call this with a dictionary of updated properties
        when the user modifies data.

    Examples
    --------
    Create a custom editor class:

    >>> import panel as pn
    >>> from panel_reactflow import Editor
    >>>
    >>> class ColorEditor(Editor):
    ...     def __init__(self, data=None, schema=None, **kwargs):
    ...         super().__init__(data, schema, **kwargs)
    ...         self.color_picker = pn.widgets.ColorPicker(
    ...             name="Node Color",
    ...             value=self._data.get("color", "#000000")
    ...         )
    ...         self.color_picker.param.watch(self._on_change, "value")
    ...
    ...     def _on_change(self, event):
    ...         if self._on_patch:
    ...             self._on_patch({"color": event.new})
    ...
    ...     def __panel__(self):
    ...         return self.color_picker

    Use the custom editor:

    >>> from panel_reactflow import ReactFlow
    >>> flow = ReactFlow(
    ...     node_editors={"panel": ColorEditor}
    ... )

    Create an editor as a simple function:

    >>> def simple_editor(data, schema, *, id, type, on_patch):
    ...     widget = pn.widgets.TextInput(
    ...         name="Label",
    ...         value=data.get("label", "")
    ...     )
    ...     widget.param.watch(
    ...         lambda e: on_patch({"label": e.new}),
    ...         "value"
    ...     )
    ...     return widget
    >>>
    >>> flow = ReactFlow(default_node_editor=simple_editor)
    """

    _data = param.Dict(default={}, doc="Node or edge data.")
    _schema = param.Dict(default=None, allow_None=True, doc="JSON Schema for data.")
    _node_id = param.String(default="", doc="Node or edge ID.")
    _node_type = param.String(default="", doc="Node or edge type.")
    _on_patch = param.Callable(default=None, allow_None=True, doc="Callback to report data changes.")

    def __init__(self, data=None, schema=None, *, id="", type="", on_patch=None, **kwargs):
        super().__init__(
            _data=data if data is not None else {},
            _schema=schema,
            _node_id=id,
            _node_type=type or "",
            _on_patch=on_patch,
            **kwargs,
        )


class JsonEditor(Editor):
    """Simple JSON editor for node and edge data.

    This editor provides a raw JSON editing interface using Panel's
    JSONEditor widget. It's useful for debugging or when you want full
    control over the data structure without schema-driven forms.

    The editor automatically syncs changes back to the graph when the
    user modifies the JSON content.

    Parameters
    ----------
    data : dict, optional
        Initial node or edge data dictionary. Defaults to ``{}``.
    schema : dict, optional
        JSON Schema (not used by this editor but part of the standard
        interface). This editor ignores the schema and allows free-form
        JSON editing.
    **kwargs
        Additional keyword arguments passed to the :class:`Editor` base class.

    Examples
    --------
    Use as default editor for all nodes:

    >>> from panel_reactflow import ReactFlow, JsonEditor
    >>> flow = ReactFlow(default_node_editor=JsonEditor)

    Use for specific node types:

    >>> flow = ReactFlow(
    ...     node_editors={"custom": JsonEditor}
    ... )

    The editor will display a JSON editor interface where users can
    directly edit the node's data dictionary.
    """

    def __init__(self, data=None, schema=None, **kwargs):
        super().__init__(data, schema, **kwargs)
        self._editor = JSONEditor(value=self._data)
        self._editor.param.watch(self._on_json_change, "value")

    def _on_json_change(self, event: param.parameterized.Event) -> None:
        if self._on_patch is not None and event.new != self._data:
            self._data = event.new
            self._on_patch(event.new)

    def __panel__(self):
        return self._editor


class SchemaEditor(Editor):
    """Smart schema-driven editor with automatic form generation.

    This is the default editor used by ReactFlow. When a JSON Schema with
    properties is available, it automatically generates an appropriate form
    with widgets for each property based on the schema definition. If no
    schema is available or form generation fails, it gracefully falls back
    to a JSON editor.

    The editor uses the ``panel_reactflow.schema.JSONSchema`` component to
    render widgets based on JSON Schema property definitions, supporting
    various types including strings, numbers, booleans, enums, dates, and
    more.

    Parameters
    ----------
    data : dict, optional
        Initial node or edge data dictionary. Defaults to ``{}``.
    schema : dict, optional
        JSON Schema dictionary with a ``properties`` field defining the
        form fields. Each property's schema determines the widget type.
    **kwargs
        Additional keyword arguments passed to the :class:`Editor` base class.

    Examples
    --------
    The SchemaEditor is used automatically when you define node types:

    >>> from panel_reactflow import ReactFlow, NodeType
    >>> flow = ReactFlow(
    ...     node_types={
    ...         "transform": NodeType(
    ...             type="transform",
    ...             schema={
    ...                 "type": "object",
    ...                 "properties": {
    ...                     "operation": {
    ...                         "type": "string",
    ...                         "enum": ["filter", "map", "reduce"],
    ...                         "title": "Operation"
    ...                     },
    ...                     "threshold": {
    ...                         "type": "number",
    ...                         "minimum": 0,
    ...                         "maximum": 1,
    ...                         "title": "Threshold"
    ...                     }
    ...                 }
    ...             }
    ...         )
    ...     }
    ... )

    The editor will automatically generate:
    - A Select widget for the "operation" property (due to enum)
    - A Slider widget for the "threshold" property (due to min/max)

    Set as default editor explicitly:

    >>> from panel_reactflow import SchemaEditor
    >>> flow = ReactFlow(default_node_editor=SchemaEditor)

    Notes
    -----
    The editor falls back to :class:`JsonEditor` if:
    - No schema is provided
    - Schema has no ``properties`` field
    - Form generation fails (e.g., missing dependencies)
    """

    def __init__(self, data=None, schema=None, **kwargs):
        super().__init__(data, schema, **kwargs)
        if self._schema and self._schema.get("properties"):
            try:
                from .schema import JSONSchema

                self._form = JSONSchema(
                    self._data,
                    schema=self._schema["properties"],
                    multi=False,
                )
                for name, widget in self._form._widgets.items():
                    widget.param.watch(
                        lambda event, _n=name: self._on_widget_change(_n, event),
                        "value",
                    )
                self._panel = self._form.layout
            except Exception:
                # Graceful fallback if JSONSchema rendering fails
                # (e.g. missing pandas dependency).
                self._init_json_fallback()
        else:
            self._init_json_fallback()

    def _init_json_fallback(self) -> None:
        self._json_editor = JSONEditor(value=self._data)
        self._json_editor.param.watch(self._on_json_change, "value")
        self._panel = self._json_editor

    def _on_widget_change(self, name: str, event: param.parameterized.Event) -> None:
        if self._on_patch is not None:
            self._on_patch({name: event.new})

    def _on_json_change(self, event: param.parameterized.Event) -> None:
        if self._on_patch is not None and event.new != self._data:
            self._data = event.new
            self._on_patch(event.new)

    def __panel__(self):
        return self._panel


class ReactFlow(ReactComponent):
    """Interactive flow-based graph visualization and editing component.

    ReactFlow is a Panel wrapper around the React Flow library, providing
    a Python-first interface for creating interactive node-based graphs.
    It supports dragging, connecting, selecting, and deleting nodes and edges,
    with automatic synchronization between Python and JavaScript.

    The component is ideal for building workflow editors, data pipelines,
    state machines, mind maps, and other node-based interfaces.

    Parameters
    ----------
    nodes : list of dict or Node, default []
        List of node dictionaries or :class:`Node` instances defining the
        graph nodes. Each node should have at minimum ``id``, ``position``,
        and ``type`` fields. Use :class:`NodeSpec` or :class:`Node` for
        structured node creation.
    edges : list of dict or Edge, default []
        List of edge dictionaries or :class:`Edge` instances defining
        connections between nodes. Each edge should have ``id``, ``source``,
        and ``target`` fields. Use :class:`EdgeSpec` or :class:`Edge` for
        structured edge creation.
    node_types : dict, default {}
        Dictionary mapping type names to :class:`NodeType` definitions or dicts.
        Define custom node types with schemas, ports, and validation.
    edge_types : dict, default {}
        Dictionary mapping type names to :class:`EdgeType` definitions or dicts.
        Define custom edge types with schemas and validation.
    node_editors : dict, default {}
        Dictionary mapping node type names to custom editor classes or functions.
        Editors must follow the standard signature:
        ``editor(data, schema, *, id, type, on_patch)``.
    edge_editors : dict, default {}
        Dictionary mapping edge type names to custom editor classes or functions.
    default_node_editor : type or callable, optional
        Default editor factory used for nodes without a specific editor.
        Defaults to :class:`SchemaEditor`.
    default_edge_editor : type or callable, optional
        Default editor factory used for edges without a specific editor.
        Defaults to :class:`SchemaEditor`.
    debounce_ms : int, default 150
        Debounce delay in milliseconds when ``sync_mode='debounce'``.
        Controls how often updates are sent from JavaScript to Python.
    default_edge_options : dict, default {}
        Default React Flow edge options applied to all edges (e.g., ``animated``,
        ``type``). See React Flow documentation for available options.
    editable : bool, default True
        Enable interactive editing on the canvas (drag, connect, delete).
        Set to ``False`` for read-only visualization.
    editor_mode : {"toolbar", "node", "side"}, default "toolbar"
        Where to render node editors:

        - ``"toolbar"``: Editors appear in a toolbar above the canvas
        - ``"node"``: Editors appear embedded within each node
        - ``"side"``: Editors appear in a side panel
    enable_connect : bool, default True
        Allow users to create new edges by connecting nodes.
    enable_delete : bool, default True
        Allow users to delete selected nodes or edges using keyboard shortcuts.
    enable_multiselect : bool, default True
        Allow selecting multiple nodes/edges with modifier keys (Shift/Ctrl).
    selection : dict, default {"nodes": [], "edges": []}
        Current selection state with lists of selected node and edge IDs.
        Read-only; updated automatically when selection changes.
    show_minimap : bool, default False
        Show a minimap overlay in the corner for navigation in large graphs.
    sync_mode : {"event", "debounce"}, default "event"
        Synchronization mode for JavaScript to Python updates:

        - ``"event"``: Immediate sync on every change
        - ``"debounce"``: Batched sync with ``debounce_ms`` delay
    validate_on_add : bool, default True
        Validate node/edge data against schemas when adding via
        :meth:`add_node` or :meth:`add_edge`.
    validate_on_patch : bool, default False
        Validate node/edge data against schemas when patching via
        :meth:`patch_node_data` or :meth:`patch_edge_data`.
    viewport : dict, optional
        Persisted viewport state with ``x``, ``y`` (position) and ``zoom``.
        Set to restore a specific view on initialization.
    top_panel : list, default []
        Panel viewables rendered in a top-center overlay panel.
    bottom_panel : list, default []
        Panel viewables rendered in a bottom-center overlay panel.
    left_panel : list, default []
        Panel viewables rendered in a center-left overlay panel.
    right_panel : list, default []
        Panel viewables rendered in a center-right overlay panel.

    Examples
    --------
    Create a basic flow graph:

    >>> import panel as pn
    >>> from panel_reactflow import ReactFlow, NodeSpec, EdgeSpec
    >>>
    >>> pn.extension()
    >>>
    >>> nodes = [
    ...     NodeSpec(id="1", position={"x": 0, "y": 0}, label="Start").to_dict(),
    ...     NodeSpec(id="2", position={"x": 200, "y": 0}, label="Process").to_dict(),
    ...     NodeSpec(id="3", position={"x": 400, "y": 0}, label="End").to_dict(),
    ... ]
    >>> edges = [
    ...     EdgeSpec(id="e1", source="1", target="2").to_dict(),
    ...     EdgeSpec(id="e2", source="2", target="3").to_dict(),
    ... ]
    >>> flow = ReactFlow(nodes=nodes, edges=edges)
    >>> flow.servable()

    Define custom node types with schemas:

    >>> from panel_reactflow import NodeType
    >>> import param
    >>>
    >>> class FilterParams(param.Parameterized):
    ...     threshold = param.Number(default=0.5, bounds=(0, 1))
    ...     operation = param.Selector(default="gt", objects=["gt", "lt", "eq"])
    >>>
    >>> flow = ReactFlow(
    ...     node_types={
    ...         "filter": NodeType(
    ...             type="filter",
    ...             label="Filter Node",
    ...             schema=FilterParams,
    ...             inputs=["input"],
    ...             outputs=["output"]
    ...         )
    ...     }
    ... )
    >>> flow.add_node(NodeSpec(
    ...     id="f1",
    ...     type="filter",
    ...     position={"x": 100, "y": 100},
    ...     data={"threshold": 0.7, "operation": "gt"}
    ... ))

    Listen to events:

    >>> def on_node_moved(event):
    ...     print(f"Node {event['node_id']} moved to {event['position']}")
    >>>
    >>> flow.on("node_moved", on_node_moved)
    >>> flow.on("edge_added", lambda e: print(f"Edge added: {e['edge']}"))

    Embed Panel viewables in nodes:

    >>> nodes = [
    ...     {
    ...         "id": "plot1",
    ...         "position": {"x": 0, "y": 0},
    ...         "label": "Markdown View",
    ...         "view": pn.pane.Markdown("# Hello World"),
    ...         "data": {}
    ...     }
    ... ]
    >>> flow = ReactFlow(nodes=nodes)

    Convert from/to NetworkX:

    >>> import networkx as nx
    >>> G = nx.DiGraph()
    >>> G.add_edge("A", "B", weight=0.5)
    >>> G.add_edge("B", "C", weight=0.8)
    >>>
    >>> flow = ReactFlow.from_networkx(G)
    >>>
    >>> # Make modifications...
    >>>
    >>> G_modified = flow.to_networkx()

    See Also
    --------
    Node : Base class for object-oriented nodes
    NodeSpec : Builder for node dictionaries
    Edge : Base class for object-oriented edges
    EdgeSpec : Builder for edge dictionaries
    NodeType : Define custom node types with schemas
    EdgeType : Define custom edge types with schemas
    Editor : Base class for custom editors
    SchemaEditor : Smart schema-driven editor (default)
    JsonEditor : Simple JSON editor

    Notes
    -----
    The component requires the Panel extension to be loaded. Make sure to
    call ``pn.extension()`` before using ReactFlow.

    For optimal performance with large graphs (>100 nodes), consider:
    - Using ``sync_mode='debounce'`` with appropriate ``debounce_ms``
    - Setting ``validate_on_patch=False`` if validation is expensive
    - Disabling ``show_minimap`` if not needed
    """

    nodes = param.List(default=[], doc="Canonical list of node dictionaries or Node instances.")
    edges = param.List(default=[], doc="Canonical list of edge dictionaries or Edge instances.")
    node_types = param.Dict(default={}, doc="Node type descriptors keyed by type name.")
    edge_types = param.Dict(default={}, doc="Edge type descriptors keyed by type name.")

    node_editors = param.Dict(default={}, doc="Node editor factories keyed by type name.", precedence=-1)
    edge_editors = param.Dict(default={}, doc="Edge editor factories keyed by type name.", precedence=-1)
    default_node_editor = param.Parameter(default=None, doc="Default node editor factory.", precedence=-1)
    default_edge_editor = param.Parameter(default=None, doc="Default edge editor factory.", precedence=-1)

    color_mode = param.ObjectSelector(default=None, objects=["light", "dark"], doc="Color mode for the graph.")

    allow_edge_loops = param.Boolean(default=False, doc="Allow to have edge loops in the graph (can lead to update infinite loops).")

    display_side_bar = param.Boolean(default=True, doc="Display the side bar to drag and drop new nodes.")

    debounce_ms = param.Integer(default=150, bounds=(0, None), doc="Debounce delay in milliseconds when sync_mode='debounce'.")

    default_edge_options = param.Dict(default={}, doc="Default React Flow edge options.")

    editable = param.Boolean(default=True, doc="Enable interactive editing on the canvas.")

    editor_mode = param.ObjectSelector(
        default="toolbar",
        objects=["toolbar", "node", "side"],
        doc="Where to render node editors: toolbar, node, or side panel.",
    )

    enable_connect = param.Boolean(default=True, doc="Allow connecting nodes to create edges.")

    enable_delete = param.Boolean(default=True, doc="Allow deleting selected nodes or edges.")

    enable_multiselect = param.Boolean(default=True, doc="Allow multiselect with modifier key.")

    selection = param.Dict(default={"nodes": [], "edges": []}, doc="Derived selection state for node and edge ids.")

    show_minimap = param.Boolean(default=False, doc="Show the minimap overlay.")

    sync_mode = param.ObjectSelector(default="event", objects=["event", "debounce"], doc="Sync mode for JS->Python updates.")

    validate_on_add = param.Boolean(default=True, doc="Validate data against schema on add_node/add_edge.")
    validate_on_patch = param.Boolean(default=False, doc="Validate data against schema on patch_node_data/patch_edge_data.")

    viewport = param.Dict(default=None, allow_None=True, doc="Optional persisted viewport state.")

    top_panel = Children(default=[], doc="Children rendered in a top-center panel.")
    bottom_panel = Children(default=[], doc="Children rendered in a bottom-center panel.")
    left_panel = Children(default=[], doc="Children rendered in a center-left panel.")
    right_panel = Children(default=[], doc="Children rendered in a center-right panel.")

    # Internal view parameters
    _node_editors = param.Dict(default={}, doc="Per-node editors.", precedence=-1)
    _node_editor_views = Children(default=[], doc="Node editor views (one per node, same order).")
    _edge_editors = param.Dict(default={}, doc="Per-edge editors.", precedence=-1)
    _edge_editor_views = Children(default=[], doc="Edge editor views (one per edge, same order).")
    _views = Children(default=[], doc="Panel viewables rendered inside nodes via view_idx.")

    _bundle = DIST_PATH / "panel-reactflow.bundle.js"
    _esm = Path(__file__).parent / "models" / "reactflow.jsx"
    _importmap = {"imports": {"@xyflow/react": "https://esm.sh/@xyflow/react@12.8.3"}}
    _stylesheets = [DIST_PATH / "panel-reactflow.bundle.css", DIST_PATH / "css" / "reactflow.css"]
    _render_policy = "manual"

    def __init__(self, **params: Any):
        if "color_mode" not in params:
            params["color_mode"] = "dark" if pn.config.theme == "dark" else "light"
        self._node_ids: list[str] = []
        self._edge_ids: list[str] = []
        self._attached_node_instances: dict[int, Node] = {}
        self._attached_edge_instances: dict[int, Edge] = {}
        self._node_data_param_watchers: dict[str, tuple[Node, list[Any]]] = {}
        self._edge_data_param_watchers: dict[str, tuple[Edge, list[Any]]] = {}
        # Normalize type specs before parent init so the frontend receives
        # JSON-serializable descriptors from the start.
        if "node_types" in params:
            params["node_types"] = _coerce_spec_map(params["node_types"])
        if "edge_types" in params:
            params["edge_types"] = _coerce_spec_map(params["edge_types"], edge=True)
        # Normalize nodes and edges to ensure NodeSpec/EdgeSpec are converted.
        if "nodes" in params:
            params["nodes"] = [ReactFlow._coerce_node(node) for node in params["nodes"]]
        if "edges" in params:
            params["edges"] = [ReactFlow._coerce_edge(edge) for edge in params["edges"]]
        super().__init__(**params)
        self._event_handlers: dict[str, list[Callable]] = {"*": []}
        self.param.watch(self._normalize_nodes, ["nodes"])
        self.param.watch(self._normalize_edges, ["edges"])
        self.param.watch(self._sync_instance_flow_refs, ["nodes", "edges"])
        self.param.watch(self._update_instance_data_param_watchers, ["nodes", "edges"])
        self.param.watch(self._update_selection_from_graph, ["nodes", "edges"])
        self.param.watch(self._normalize_specs, ["node_types", "edge_types"])
        self.param.watch(
            self._update_node_editors,
            ["nodes", "editor_mode", "selection", "node_editors", "default_node_editor"],
        )
        self.param.watch(
            self._update_edge_editors,
            ["edges", "selection", "edge_editors", "default_edge_editor"],
        )
        self._update_node_editors()
        self._update_edge_editors()
        self._sync_instance_flow_refs()
        self._update_instance_data_param_watchers()

    @classmethod
    def _esm_path(cls, compiled: bool | Literal["compiling"] = True) -> os.PathLike | None:
        return super()._esm_path(compiled or True)

    @classmethod
    def _render_esm(cls, compiled: bool | Literal["compiling"] = True, server: bool = False):
        esm_path = cls._esm_path(compiled=compiled)
        if compiled != "compiling" and server:
            # Generate relative path to handle apps served on subpaths
            esm = ("" if state.rel_path else "./") + cls._component_resource_path(esm_path, compiled)
            if config.autoreload:
                modified = hashlib.sha256(str(esm_path.stat().st_mtime).encode("utf-8")).hexdigest()
                esm += f"?{modified}"
        else:
            esm = esm_path.read_text(encoding="utf-8")
        return esm

    @classproperty
    def _bundle_path(cls) -> os.PathLike | None:
        return cls._bundle

    def _get_node_schema(self, node_type: str) -> dict[str, Any] | None:
        """Return the normalized JSON Schema for *node_type*, or ``None``."""
        type_spec = self.node_types.get(node_type)
        if type_spec is not None and type_spec.get("schema") is not None:
            return type_spec.get("schema")
        for node in self.nodes:
            if isinstance(node, Node) and node.type == node_type:
                schema = node._data_schema()
                if schema.get("properties"):
                    return schema
                break
        return None

    def _get_edge_schema(self, edge_type: str) -> dict[str, Any] | None:
        """Return the normalized JSON Schema for *edge_type*, or ``None``."""
        type_spec = self.edge_types.get(edge_type)
        if type_spec is not None and type_spec.get("schema") is not None:
            return type_spec.get("schema")
        for edge in self.edges:
            if isinstance(edge, Edge) and (edge.type or "") == edge_type:
                schema = edge._data_schema()
                if schema.get("properties"):
                    return schema
                break
        return None

    def _create_editor(
        self,
        factory: Any,
        item_id: str,
        data: dict,
        schema: dict | None,
        item_type: str,
        *,
        patch_fn: Callable[[str, dict], None],
    ) -> Any:
        """Instantiate an editor from *factory*.

        All editors (classes and plain callables) receive the unified
        signature ``(data, schema, *, id, type, on_patch)``.

        *patch_fn* is the method to call when data changes –
        ``patch_node_data`` for nodes, ``patch_edge_data`` for edges.
        """

        def on_patch(patch: dict) -> None:
            patch_fn(item_id, patch)

        return factory(data, schema, id=item_id, type=item_type, on_patch=on_patch)

    @staticmethod
    def _node_is_instance(node: Any) -> bool:
        return isinstance(node, Node)

    @staticmethod
    def _node_id(node: dict[str, Any] | Node) -> str | None:
        return node.id if isinstance(node, Node) else node.get("id")

    @staticmethod
    def _node_type(node: dict[str, Any] | Node) -> str:
        if isinstance(node, Node):
            return node.type or "panel"
        return node.get("type", "panel")

    @staticmethod
    def _node_data(node: dict[str, Any] | Node) -> dict[str, Any]:
        if isinstance(node, Node):
            return dict(node.data or {})
        return dict(node.get("data", {}))

    @staticmethod
    def _node_view(node: dict[str, Any] | Node) -> Any | None:
        if isinstance(node, Node):
            return node.__panel__()
        return node.get("view", None)

    @staticmethod
    def _node_set_position(node: dict[str, Any] | Node, position: dict[str, Any]) -> None:
        if isinstance(node, Node):
            node.position = dict(position)
        else:
            node["position"] = position

    @staticmethod
    def _node_set_selected(node: dict[str, Any] | Node, selected: bool) -> None:
        if isinstance(node, Node):
            node.selected = selected
        else:
            node["selected"] = selected

    @staticmethod
    def _node_set_data(node: dict[str, Any] | Node, data: dict[str, Any]) -> None:
        if isinstance(node, Node):
            node.data = dict(data)
        else:
            node["data"] = data

    @staticmethod
    def _node_payload(node: dict[str, Any] | NodeSpec | Node) -> dict[str, Any]:
        if isinstance(node, Node):
            return node.to_dict()
        if isinstance(node, NodeSpec):
            return node.to_dict()
        return dict(node)

    @staticmethod
    def _edge_id(edge: dict[str, Any] | Edge) -> str | None:
        return edge.id if isinstance(edge, Edge) else edge.get("id")

    @staticmethod
    def _edge_type(edge: dict[str, Any] | Edge) -> str:
        if isinstance(edge, Edge):
            return edge.type or ""
        return edge.get("type", "")

    @staticmethod
    def _edge_data(edge: dict[str, Any] | Edge) -> dict[str, Any]:
        if isinstance(edge, Edge):
            return dict(edge.data or {})
        return dict(edge.get("data", {}))

    @staticmethod
    def _edge_set_selected(edge: dict[str, Any] | Edge, selected: bool) -> None:
        if isinstance(edge, Edge):
            edge.selected = selected
        else:
            edge["selected"] = selected

    @staticmethod
    def _edge_set_data(edge: dict[str, Any] | Edge, data: dict[str, Any]) -> None:
        if isinstance(edge, Edge):
            edge.data = dict(data)
        else:
            edge["data"] = data

    @staticmethod
    def _edge_payload(edge: dict[str, Any] | EdgeSpec | Edge) -> dict[str, Any]:
        if isinstance(edge, Edge):
            return edge.to_dict()
        if isinstance(edge, EdgeSpec):
            return edge.to_dict()
        return dict(edge)

    def _get_node_instance(self, node_id: str) -> Node | None:
        for node in self.nodes:
            if isinstance(node, Node) and node.id == node_id:
                return node
        return None

    def _get_edge_instance(self, edge_id: str) -> Edge | None:
        for edge in self.edges:
            if isinstance(edge, Edge) and edge.id == edge_id:
                return edge
        return None

    @staticmethod
    def _sync_node_data_params_from_data(node: Node) -> None:
        for name in node._data_param_names():
            if name in (node.data or {}):
                setattr(node, name, node.data[name])

    @staticmethod
    def _sync_edge_data_params_from_data(edge: Edge) -> None:
        for name in edge._data_param_names():
            if name in (edge.data or {}):
                setattr(edge, name, edge.data[name])

    def _teardown_node_data_param_watcher(self, node_id: str) -> None:
        record = self._node_data_param_watchers.pop(node_id, None)
        if record is None:
            return
        node, watchers = record
        for watcher in watchers:
            try:
                node.param.unwatch(watcher)
            except Exception:
                pass

    def _teardown_edge_data_param_watcher(self, edge_id: str) -> None:
        record = self._edge_data_param_watchers.pop(edge_id, None)
        if record is None:
            return
        edge, watchers = record
        for watcher in watchers:
            try:
                edge.param.unwatch(watcher)
            except Exception:
                pass

    def _update_instance_data_param_watchers(self, *_: param.parameterized.Event) -> None:
        self._update_node_data_param_watchers()
        self._update_edge_data_param_watchers()

    def _sync_instance_flow_refs(self, *_: param.parameterized.Event) -> None:
        current_nodes = {id(node): node for node in self.nodes if isinstance(node, Node)}
        for node_ref, old_node in list(self._attached_node_instances.items()):
            if node_ref not in current_nodes:
                old_node.flow = None
        for node in current_nodes.values():
            if node.flow is not self:
                node.flow = self
        self._attached_node_instances = current_nodes

        current_edges = {id(edge): edge for edge in self.edges if isinstance(edge, Edge)}
        for edge_ref, old_edge in list(self._attached_edge_instances.items()):
            if edge_ref not in current_edges:
                old_edge.flow = None
        for edge in current_edges.values():
            if edge.flow is not self:
                edge.flow = self
        self._attached_edge_instances = current_edges

    def _update_node_data_param_watchers(self) -> None:
        current_nodes = {node.id: node for node in self.nodes if isinstance(node, Node) and node.id}
        for node_id, (watched_node, _) in list(self._node_data_param_watchers.items()):
            current = current_nodes.get(node_id)
            if current is None or current is not watched_node:
                self._teardown_node_data_param_watcher(node_id)
        for node_id, node in current_nodes.items():
            if node_id in self._node_data_param_watchers:
                continue
            watchers = []
            for name in node._data_param_names():
                watchers.append(
                    node.param.watch(
                        lambda event, _id=node_id, _name=name, _node=node: self._on_node_data_param_change(_id, _name, _node, event),
                        name,
                    )
                )
            self._node_data_param_watchers[node_id] = (node, watchers)

    def _update_edge_data_param_watchers(self) -> None:
        current_edges = {edge.id: edge for edge in self.edges if isinstance(edge, Edge) and edge.id}
        for edge_id, (watched_edge, _) in list(self._edge_data_param_watchers.items()):
            current = current_edges.get(edge_id)
            if current is None or current is not watched_edge:
                self._teardown_edge_data_param_watcher(edge_id)
        for edge_id, edge in current_edges.items():
            if edge_id in self._edge_data_param_watchers:
                continue
            watchers = []
            for name in edge._data_param_names():
                watchers.append(
                    edge.param.watch(
                        lambda event, _id=edge_id, _name=name, _edge=edge: self._on_edge_data_param_change(_id, _name, _edge, event),
                        name,
                    )
                )
            self._edge_data_param_watchers[edge_id] = (edge, watchers)

    def _on_node_data_param_change(
        self,
        node_id: str,
        param_name: str,
        node: Node,
        event: param.parameterized.Event,
    ) -> None:
        if self._get_node_instance(node_id) is not node:
            return
        if (node.data or {}).get(param_name) == event.new:
            return
        self.patch_node_data(node_id, {param_name: event.new})

    def _on_edge_data_param_change(
        self,
        edge_id: str,
        param_name: str,
        edge: Edge,
        event: param.parameterized.Event,
    ) -> None:
        if self._get_edge_instance(edge_id) is not edge:
            return
        if (edge.data or {}).get(param_name) == event.new:
            return
        self.patch_edge_data(edge_id, {param_name: event.new})

    @staticmethod
    def _invoke_node_callback(callback: Callable, payload: dict[str, Any], flow: "ReactFlow") -> None:
        if len(inspect.signature(callback).parameters) == 2:
            cb = partial(callback, payload, flow)
        else:
            cb = partial(callback, payload)
        pn.state.execute(cb)

    def _invoke_node_hook(self, node: Node, hook_name: str, payload: dict[str, Any]) -> None:
        hook = getattr(node, hook_name, None)
        if callable(hook):
            self._invoke_node_callback(hook, payload, self)

    def _invoke_edge_hook(self, edge: Edge, hook_name: str, payload: dict[str, Any]) -> None:
        hook = getattr(edge, hook_name, None)
        if callable(hook):
            self._invoke_node_callback(hook, payload, self)

    def _dispatch_node_hooks(self, event_type: str, payload: dict[str, Any]) -> None:
        node_ids: list[str] = []
        if event_type == "node_added":
            node_payload = payload.get("node", {})
            if isinstance(node_payload, dict) and node_payload.get("id"):
                node_ids = [node_payload["id"]]
        elif event_type in ("node_moved", "node_clicked", "node_data_changed"):
            node_id = payload.get("node_id")
            if node_id:
                node_ids = [node_id]
        elif event_type == "selection_changed":
            node_ids = list(payload.get("nodes") or [])
        elif event_type == "sync":
            node_ids = [node.id for node in self.nodes if isinstance(node, Node)]

        hook_map = {
            "node_added": "on_add",
            "node_moved": "on_move",
            "node_clicked": "on_click",
            "node_data_changed": "on_data_change",
            "selection_changed": "on_selection_changed",
            "sync": "on_sync",
        }
        hook_name = hook_map.get(event_type)
        for node_id in node_ids:
            node = self._get_node_instance(node_id)
            if node is None:
                continue
            if hook_name is not None:
                self._invoke_node_hook(node, hook_name, payload)
            self._invoke_node_hook(node, "on_event", payload)

    def _dispatch_edge_hooks(self, event_type: str, payload: dict[str, Any]) -> None:
        edge_ids: list[str] = []
        if event_type == "edge_added":
            edge_payload = payload.get("edge", {})
            if isinstance(edge_payload, dict) and edge_payload.get("id"):
                edge_ids = [edge_payload["id"]]
        elif event_type in ("edge_deleted", "edge_data_changed"):
            edge_id = payload.get("edge_id")
            if edge_id:
                edge_ids = [edge_id]
        elif event_type == "selection_changed":
            edge_ids = list(payload.get("edges") or [])
        elif event_type == "sync":
            edge_ids = [edge.id for edge in self.edges if isinstance(edge, Edge)]

        hook_map = {
            "edge_added": "on_add",
            "edge_deleted": "on_delete",
            "edge_data_changed": "on_data_change",
            "selection_changed": "on_selection_changed",
            "sync": "on_sync",
        }
        hook_name = hook_map.get(event_type)
        for edge_id in edge_ids:
            edge = self._get_edge_instance(edge_id)
            if edge is None:
                continue
            if hook_name is not None:
                self._invoke_edge_hook(edge, hook_name, payload)
            self._invoke_edge_hook(edge, "on_event", payload)

    def _update_node_editors(self, *events: tuple[param.parameterized.Event]) -> None:
        node_ids = [self._node_id(node) for node in self.nodes]
        node_ids = [node_id for node_id in node_ids if node_id is not None]
        config_changed = any(event.name in ("editor_mode", "node_editors", "default_node_editor") for event in events)
        if node_ids == self._node_ids and not config_changed:
            return
        self._node_ids = node_ids

        editors = {}
        for node in self.nodes:
            node_id = self._node_id(node)
            if node_id is None:
                continue
            if node_id in self._node_editors and not config_changed:
                editors[node_id] = self._node_editors[node_id]
                continue
            node_type = self._node_type(node)
            editor_factory = None
            if isinstance(node, Node) and type(node).editor is not Node.editor:
                editor_factory = node.editor
            if editor_factory is None:
                editor_factory = self.node_editors.get(node_type) or self.default_node_editor or SchemaEditor
            schema = self._get_node_schema(node_type)
            data = self._node_data(node)
            if callable(editor_factory):
                editor = self._create_editor(
                    editor_factory,
                    node_id,
                    data,
                    schema,
                    node_type,
                    patch_fn=self.patch_node_data,
                )
            else:
                editor = editor_factory
            editors[node_id] = editor
        self._node_editors = editors
        self.param.trigger("_node_editor_views")

    def _update_edge_editors(self, *events: tuple[param.parameterized.Event]) -> None:
        edge_ids = [self._edge_id(edge) for edge in self.edges]
        edge_ids = [edge_id for edge_id in edge_ids if edge_id is not None]
        config_changed = any(event.name in ("edge_editors", "default_edge_editor") for event in events)
        if edge_ids == self._edge_ids and not config_changed:
            return
        self._edge_ids = edge_ids

        editors = {}
        for edge in self.edges:
            edge_id = self._edge_id(edge)
            if edge_id is None:
                continue
            if edge_id in self._edge_editors and not config_changed:
                editors[edge_id] = self._edge_editors[edge_id]
                continue
            edge_type = self._edge_type(edge)
            editor_factory = None
            if isinstance(edge, Edge) and type(edge).editor is not Edge.editor:
                editor_factory = edge.editor
            if editor_factory is None:
                editor_factory = self.edge_editors.get(edge_type) or self.default_edge_editor or SchemaEditor
            schema = self._get_edge_schema(edge_type) if edge_type else None
            data = self._edge_data(edge)
            if callable(editor_factory):
                editor = self._create_editor(
                    editor_factory,
                    edge_id,
                    data,
                    schema,
                    edge_type,
                    patch_fn=self.patch_edge_data,
                )
            else:
                editor = editor_factory
            editors[edge_id] = editor
        self._edge_editors = editors
        self.param.trigger("_edge_editor_views")

    @staticmethod
    def _resolve_editor_view(editor: Any) -> Any:
        """Return a Panel viewable from an editor (class or plain object)."""
        if editor is None:
            return pn.pane.HTML("")
        if hasattr(editor, "__panel__"):
            return editor.__panel__()
        return pn.panel(editor)

    def _get_children(self, data_model, doc, root, parent, comm) -> tuple[dict[str, list[UIElement] | UIElement | None], list[UIElement]]:
        views = []
        node_editors = []
        for node in self.nodes:
            view = self._node_view(node)
            if view is not None:
                views.append(self._resolve_editor_view(view))
            node_editors.append(self._resolve_editor_view(self._node_editors.get(self._node_id(node))))
        edge_editors = [self._resolve_editor_view(self._edge_editors.get(self._edge_id(edge))) for edge in self.edges]

        children: dict[str, list[UIElement] | UIElement | None] = {}
        old_models: list[UIElement] = []
        views, view_models = self._get_child_model(views, doc, root, parent, comm)
        self._patch_views(view_models)
        children["_views"] = views
        old_models += view_models
        editor_models, editor_old = self._get_child_model(node_editors, doc, root, parent, comm)
        children["_node_editor_views"] = editor_models
        old_models += editor_old
        edge_models, edge_old = self._get_child_model(edge_editors, doc, root, parent, comm)
        children["_edge_editor_views"] = edge_models
        old_models += edge_old
        for name in ("top_panel", "bottom_panel", "left_panel", "right_panel"):
            panels = list(getattr(self, name, []) or [])
            if panels:
                panel_models, panel_old = self._get_child_model(panels, doc, root, parent, comm)
                children[name] = panel_models
                old_models += panel_old
            else:
                children[name] = []
        return children, old_models

    def _patch_views(self, view_models: list[UIElement]) -> None:
        for model in view_models:
            for fig in model.select({"type": figure}):
                if BK_FIGURE_CSS not in fig.stylesheets:
                    fig.stylesheets = fig.stylesheets + [BK_FIGURE_CSS]

    def _process_param_change(self, params):
        params = super()._process_param_change(params)
        if "nodes" in params:
            nodes = []
            view_idx = 0
            for node in params["nodes"]:
                node = self._node_payload(node)
                view = node.pop("view", None)
                data = dict(node.get("data", {}))
                if view is not None:
                    data["view_idx"] = view_idx
                    view_idx += 1
                node["data"] = data
                nodes.append(node)
            params["nodes"] = nodes
        if "edges" in params:
            params["edges"] = [self._edge_payload(edge) for edge in params["edges"]]
        # node_types / edge_types are now JSON-serializable descriptors
        # and intentionally synced to the frontend.
        # Pop Python-only editor registries and internal state.
        params.pop("node_editors", None)
        params.pop("edge_editors", None)
        params.pop("default_node_editor", None)
        params.pop("default_edge_editor", None)
        params.pop("validate_on_add", None)
        params.pop("validate_on_patch", None)
        params.pop("_node_editors", None)
        params.pop("_edge_editors", None)
        return params

    def add_node(self, node: dict[str, Any] | NodeSpec | Node) -> None:
        """Add a node to the graph.

        Adds a new node to the graph with optional validation. If a ``view``
        is included in the node dict or :class:`NodeSpec`, it will be embedded
        inside the node and rendered as Panel content.

        Parameters
        ----------
        node : dict or NodeSpec or Node
            Node dictionary, :class:`NodeSpec`, or :class:`Node` instance to add. The only
            required field is ``id``. Other fields have defaults:

            - ``id``: Unique node identifier (required)
            - ``position``: Dict with ``x`` and ``y`` coordinates
              (defaults to ``{"x": 0.0, "y": 0.0}``)
            - ``type``: Node type (defaults to ``"panel"``)
            - ``data``: Custom data dict (defaults to ``{}``)

        Raises
        ------
        ValueError
            If the node is missing the required ``id`` field or if
            validation is enabled and the data doesn't match the schema.

        Examples
        --------
        Add a simple node:

        >>> flow = ReactFlow()
        >>> flow.add_node({
        ...     "id": "n1",
        ...     "position": {"x": 0, "y": 0},
        ...     "type": "panel",
        ...     "label": "My Node",
        ...     "data": {}
        ... })

        Add a node using NodeSpec:

        >>> from panel_reactflow import NodeSpec
        >>> flow.add_node(NodeSpec(
        ...     id="n2",
        ...     position={"x": 100, "y": 100},
        ...     label="Another Node"
        ... ))

        Add a node with embedded view via NodeSpec:

        >>> import panel as pn
        >>> flow.add_node(NodeSpec(
        ...     id="plot1",
        ...     position={"x": 200, "y": 0},
        ...     view=pn.pane.Markdown("# Hello World")
        ... ))

        Add a typed node with data:

        >>> flow.add_node(NodeSpec(
        ...     id="filter1",
        ...     type="filter",
        ...     position={"x": 300, "y": 100},
        ...     data={"threshold": 0.7, "operation": "gt"}
        ... ))

        See Also
        --------
        remove_node : Remove a node from the graph
        NodeSpec : Helper for constructing node dictionaries
        """
        raw_node = self._coerce_node(node)
        payload = self._node_payload(raw_node)
        payload.setdefault("type", "panel")
        payload.setdefault("data", {})
        payload.setdefault("position", {"x": 0.0, "y": 0.0})
        payload.setdefault("selected", False)
        payload.setdefault("draggable", True)
        payload.setdefault("connectable", True)
        payload.setdefault("deletable", True)
        if isinstance(raw_node, Node):
            raw_node.type = payload["type"]
            raw_node.data = dict(payload["data"])
            raw_node.position = dict(payload["position"])
            raw_node.selected = payload["selected"]
            raw_node.draggable = payload["draggable"]
            raw_node.connectable = payload["connectable"]
            raw_node.deletable = payload["deletable"]
            raw_node.flow = self
            self._sync_node_data_params_from_data(raw_node)
        self._validate_graph_payload(payload, kind="node")
        if self.validate_on_add:
            schema = self._get_node_schema(payload.get("type", "panel"))
            _validate_data(payload.get("data", {}), schema)
        self.nodes = self.nodes + [raw_node if isinstance(raw_node, Node) else payload]
        self._emit("node_added", {"type": "node_added", "node": payload})

    def _handle_msg(self, msg: dict[str, Any]) -> None:
        """Handle sync messages from the frontend."""
        if not isinstance(msg, dict):
            return
        match msg.get("type"):
            case "sync":
                nodes = msg.get("nodes")
                edges = msg.get("edges")
                if nodes is not None:
                    current_instances = {node.id: node for node in self.nodes if isinstance(node, Node)}
                    synced_nodes: list[dict[str, Any] | Node] = []
                    for payload in nodes:
                        node_id = payload.get("id")
                        if node_id in current_instances:
                            node = current_instances[node_id]
                            node.position = dict(payload.get("position", node.position))
                            node.type = payload.get("type", node.type)
                            node.label = payload.get("label", node.label)
                            node.data = dict(payload.get("data", node.data))
                            self._sync_node_data_params_from_data(node)
                            node.selected = payload.get("selected", node.selected)
                            node.draggable = payload.get("draggable", node.draggable)
                            node.connectable = payload.get("connectable", node.connectable)
                            node.deletable = payload.get("deletable", node.deletable)
                            if "style" in payload:
                                node.style = payload.get("style")
                            if "className" in payload:
                                node.className = payload.get("className")
                            synced_nodes.append(node)
                        else:
                            synced_nodes.append(payload)
                    self.nodes = synced_nodes
                if edges is not None:
                    current_instances = {edge.id: edge for edge in self.edges if isinstance(edge, Edge)}
                    synced_edges: list[dict[str, Any] | Edge] = []
                    for payload in edges:
                        edge_id = payload.get("id")
                        if edge_id in current_instances:
                            edge = current_instances[edge_id]
                            edge.source = payload.get("source", edge.source)
                            edge.target = payload.get("target", edge.target)
                            edge.label = payload.get("label", edge.label)
                            edge.type = payload.get("type", edge.type)
                            edge.selected = payload.get("selected", edge.selected)
                            edge.data = dict(payload.get("data", edge.data))
                            self._sync_edge_data_params_from_data(edge)
                            if "style" in payload:
                                edge.style = payload.get("style")
                            if "markerEnd" in payload:
                                edge.markerEnd = payload.get("markerEnd")
                            if "sourceHandle" in payload:
                                edge.sourceHandle = payload.get("sourceHandle")
                            if "targetHandle" in payload:
                                edge.targetHandle = payload.get("targetHandle")
                            synced_edges.append(edge)
                        else:
                            synced_edges.append(payload)
                    self.edges = synced_edges
                self._emit("sync", msg)
            case "node_moved":
                node_id = msg.get("node_id")
                position = msg.get("position")
                if node_id is None or position is None:
                    return
                for node in self.nodes:
                    if self._node_id(node) == node_id:
                        self._node_set_position(node, position)
                self._emit("node_moved", msg)
            case "selection_changed":
                node_ids = msg.get("nodes") or []
                edge_ids = msg.get("edges") or []
                for node in self.nodes:
                    self._node_set_selected(node, self._node_id(node) in node_ids)
                for edge in self.edges:
                    self._edge_set_selected(edge, self._edge_id(edge) in edge_ids)
                self.selection = {"nodes": list(node_ids), "edges": list(edge_ids)}
                self._emit("selection_changed", msg)
            case "edge_added":
                edge = msg.get("edge")
                if edge is None:
                    return
                self.add_edge(edge)
                self._emit("edge_added", msg)
            case "node_deleted":
                node_ids = msg.get("node_ids") or []
                if msg.get("node_id"):
                    node_ids = list(set(node_ids) | {msg.get("node_id")})
                for node_id in node_ids:
                    self.remove_node(node_id)
                self._emit("node_deleted", msg)
            case "edge_deleted":
                edge_ids = msg.get("edge_ids") or []
                if msg.get("edge_id"):
                    edge_ids = list(set(edge_ids) | {msg.get("edge_id")})
                for edge_id in edge_ids:
                    self.remove_edge(edge_id)
                self._emit("edge_deleted", msg)
            case "node_clicked":
                node_id = msg.get("node_id")
                if node_id is None:
                    return
                self._emit("node_clicked", msg)
            case _:
                return

    def remove_node(self, node_id: str) -> None:
        """Remove a node and all connected edges from the graph.

        Removes the specified node and automatically removes any edges that
        are connected to it (either as source or target). This ensures the
        graph remains consistent.

        Parameters
        ----------
        node_id : str
            Unique identifier of the node to remove.

        Examples
        --------
        Remove a node:

        >>> flow = ReactFlow()
        >>> flow.add_node(NodeSpec(id="n1", position={"x": 0, "y": 0}))
        >>> flow.add_node(NodeSpec(id="n2", position={"x": 100, "y": 0}))
        >>> flow.add_edge(EdgeSpec(id="e1", source="n1", target="n2"))
        >>>
        >>> flow.remove_node("n1")  # Also removes edge "e1"

        See Also
        --------
        add_node : Add a node to the graph
        remove_edge : Remove an edge from the graph
        """
        removed_node = next((node for node in self.nodes if self._node_id(node) == node_id), None)
        nodes = [node for node in self.nodes if self._node_id(node) != node_id]
        removed_edges = [
            edge
            for edge in self.edges
            if (edge.source if isinstance(edge, Edge) else edge.get("source")) == node_id
            or (edge.target if isinstance(edge, Edge) else edge.get("target")) == node_id
        ]
        self.nodes = nodes
        if removed_edges:
            remaining_edges = [edge for edge in self.edges if edge not in removed_edges]
            self.edges = remaining_edges
        self._emit(
            "node_deleted",
            {
                "type": "node_deleted",
                "node_id": node_id,
                "deleted_edges": [self._edge_id(edge) for edge in removed_edges],
            },
        )
        if isinstance(removed_node, Node):
            removed_node.flow = None
            payload = {
                "type": "node_deleted",
                "node_id": node_id,
                "deleted_edges": [self._edge_id(edge) for edge in removed_edges],
            }
            self._invoke_node_hook(removed_node, "on_delete", payload)
            self._invoke_node_hook(removed_node, "on_event", payload)

    def add_edge(self, edge: dict[str, Any] | EdgeSpec | Edge) -> None:
        """Add an edge to the graph.

        Adds a new edge connecting two nodes with optional validation. If no
        ``id`` is provided, one will be automatically generated based on the
        source and target nodes.

        Parameters
        ----------
        edge : dict or EdgeSpec or Edge
            Edge dictionary, :class:`EdgeSpec`, or :class:`Edge` instance to add. Required
            fields are ``source`` and ``target``. Other fields have defaults:

            - ``source``: ID of the source node (required)
            - ``target``: ID of the target node (required)
            - ``id``: Unique edge identifier (auto-generated if not provided)
            - ``data``: Custom data dict (defaults to ``{}``)

        Raises
        ------
        ValueError
            If the edge is missing required fields (``source``, ``target``)
            or if validation is enabled and the data doesn't match the schema.

        Examples
        --------
        Add a simple edge:

        >>> flow = ReactFlow()
        >>> flow.add_edge({
        ...     "id": "e1",
        ...     "source": "n1",
        ...     "target": "n2"
        ... })

        Add an edge using EdgeSpec:

        >>> from panel_reactflow import EdgeSpec
        >>> flow.add_edge(EdgeSpec(
        ...     id="e2",
        ...     source="n2",
        ...     target="n3",
        ...     label="Connection"
        ... ))

        Add a typed edge with data:

        >>> flow.add_edge(EdgeSpec(
        ...     id="weighted1",
        ...     source="n1",
        ...     target="n3",
        ...     type="weighted",
        ...     data={"weight": 0.75}
        ... ))

        Add an edge with styling:

        >>> flow.add_edge(EdgeSpec(
        ...     id="e3",
        ...     source="n3",
        ...     target="n4",
        ...     style={"stroke": "#ff0000", "strokeWidth": 3},
        ...     markerEnd={"type": "arrowclosed", "color": "#ff0000"}
        ... ))

        See Also
        --------
        remove_edge : Remove an edge from the graph
        EdgeSpec : Helper for constructing edge dictionaries
        """
        raw_edge = self._coerce_edge(edge)
        payload = self._edge_payload(raw_edge)
        payload.setdefault("data", {})
        if not payload.get("id"):
            payload["id"] = self._generate_edge_id(payload["source"], payload["target"])
        if isinstance(raw_edge, Edge):
            raw_edge.id = payload["id"]
            raw_edge.data = dict(payload["data"])
            raw_edge.flow = self
            self._sync_edge_data_params_from_data(raw_edge)
        self._validate_graph_payload(payload, kind="edge")
        if self.validate_on_add:
            edge_type = payload.get("type")
            schema = self._get_edge_schema(edge_type) if edge_type else None
            _validate_data(payload.get("data", {}), schema)
        self.edges = self.edges + [raw_edge if isinstance(raw_edge, Edge) else payload]
        self._emit("edge_added", {"type": "edge_added", "edge": payload})

    def remove_edge(self, edge_id: str) -> None:
        """Remove an edge from the graph by its ID.

        Parameters
        ----------
        edge_id : str
            Unique identifier of the edge to remove.

        Examples
        --------
        Remove an edge:

        >>> flow = ReactFlow()
        >>> flow.add_edge(EdgeSpec(id="e1", source="n1", target="n2"))
        >>> flow.remove_edge("e1")

        See Also
        --------
        add_edge : Add an edge to the graph
        remove_node : Remove a node from the graph
        """
        removed_edge = next((edge for edge in self.edges if self._edge_id(edge) == edge_id), None)
        removed = [edge for edge in self.edges if self._edge_id(edge) == edge_id]
        self.edges = [edge for edge in self.edges if self._edge_id(edge) != edge_id]
        if removed:
            self._emit("edge_deleted", {"type": "edge_deleted", "edge_id": edge_id})
        if isinstance(removed_edge, Edge):
            removed_edge.flow = None
            payload = {"type": "edge_deleted", "edge_id": edge_id}
            self._invoke_edge_hook(removed_edge, "on_delete", payload)
            self._invoke_edge_hook(removed_edge, "on_event", payload)

    def patch_node_data(self, node_id: str, patch: dict[str, Any]) -> None:
        """Update specific properties in a node's data dictionary.

        Merges the provided patch dictionary into the node's existing ``data``
        dict, allowing you to update individual properties without replacing
        the entire data object.

        Parameters
        ----------
        node_id : str
            Unique identifier of the node to update.
        patch : dict
            Dictionary of key-value pairs to merge into the node's ``data``.
            Existing keys will be updated, new keys will be added.

        Raises
        ------
        ValueError
            If validation is enabled (``validate_on_patch=True``) and the
            patched data doesn't match the node type's schema.

        Examples
        --------
        Update a single property:

        >>> flow = ReactFlow()
        >>> flow.add_node(NodeSpec(
        ...     id="n1",
        ...     position={"x": 0, "y": 0},
        ...     data={"threshold": 0.5, "name": "Filter"}
        ... ))
        >>>
        >>> # Update just the threshold
        >>> flow.patch_node_data("n1", {"threshold": 0.8})

        Update multiple properties:

        >>> flow.patch_node_data("n1", {
        ...     "threshold": 0.9,
        ...     "name": "Updated Filter"
        ... })

        Notes
        -----
        This method is typically called automatically by editors when users
        modify node properties in the UI. The patch is also sent to the
        frontend to keep the visualization in sync.

        See Also
        --------
        patch_edge_data : Update edge data properties
        add_node : Add a new node to the graph
        """
        for node in self.nodes:
            if self._node_id(node) == node_id:
                data = self._node_data(node)
                data.update(patch)
                if self.validate_on_patch:
                    schema = self._get_node_schema(self._node_type(node))
                    _validate_data(data, schema)
                self._node_set_data(node, data)
                if isinstance(node, Node):
                    self._sync_node_data_params_from_data(node)
                break
        self._send_msg({"type": "patch_node_data", "node_id": node_id, "patch": patch})
        self._emit("node_data_changed", {"type": "node_data_changed", "node_id": node_id, "patch": patch})

    def patch_edge_data(self, edge_id: str, patch: dict[str, Any]) -> None:
        """Update specific properties in an edge's data dictionary.

        Merges the provided patch dictionary into the edge's existing ``data``
        dict, allowing you to update individual properties without replacing
        the entire data object.

        Parameters
        ----------
        edge_id : str
            Unique identifier of the edge to update.
        patch : dict
            Dictionary of key-value pairs to merge into the edge's ``data``.
            Existing keys will be updated, new keys will be added.

        Raises
        ------
        ValueError
            If validation is enabled (``validate_on_patch=True``) and the
            patched data doesn't match the edge type's schema.

        Examples
        --------
        Update edge properties:

        >>> flow = ReactFlow()
        >>> flow.add_edge(EdgeSpec(
        ...     id="e1",
        ...     source="n1",
        ...     target="n2",
        ...     data={"weight": 0.5, "label": "weak"}
        ... ))
        >>>
        >>> # Update just the weight
        >>> flow.patch_edge_data("e1", {"weight": 0.9, "label": "strong"})

        Notes
        -----
        This method is typically called automatically by editors when users
        modify edge properties in the UI. The patch is also sent to the
        frontend to keep the visualization in sync.

        See Also
        --------
        patch_node_data : Update node data properties
        add_edge : Add a new edge to the graph
        """
        for edge in self.edges:
            if self._edge_id(edge) == edge_id:
                data = self._edge_data(edge)
                data.update(patch)
                if self.validate_on_patch:
                    edge_type = self._edge_type(edge)
                    schema = self._get_edge_schema(edge_type) if edge_type else None
                    _validate_data(data, schema)
                self._edge_set_data(edge, data)
                if isinstance(edge, Edge):
                    self._sync_edge_data_params_from_data(edge)
                break
        self._send_msg({"type": "patch_edge_data", "edge_id": edge_id, "patch": patch})
        self._emit("edge_data_changed", {"type": "edge_data_changed", "edge_id": edge_id, "patch": patch})

    def to_networkx(self, *, multigraph: bool = False):
        """Convert the current graph to NetworkX format.

        Converts the ReactFlow graph state into a NetworkX graph object,
        preserving node positions, types, labels, and data. Useful for
        graph analysis, algorithms, and integration with the NetworkX
        ecosystem.

        Parameters
        ----------
        multigraph : bool, default False
            If ``True``, returns a ``MultiDiGraph`` allowing multiple edges
            between the same node pair. If ``False``, returns a ``DiGraph``
            with single edges between nodes.

        Returns
        -------
        networkx.DiGraph or networkx.MultiDiGraph
            NetworkX graph representation with node and edge data preserved.

            Node attributes include:
            - All properties from ``node["data"]``
            - ``position``: Node position dict
            - ``type``: Node type string
            - ``label``: Node label (if present)

            Edge attributes include:
            - All properties from ``edge["data"]``
            - ``label``: Edge label (if present)
            - ``type``: Edge type string (if present)
            - For MultiDiGraphs, ``key`` is the edge ID

        Raises
        ------
        ImportError
            If NetworkX is not installed.

        Examples
        --------
        Convert to NetworkX and run graph algorithms:

        >>> import networkx as nx
        >>> from panel_reactflow import ReactFlow, NodeSpec, EdgeSpec
        >>>
        >>> flow = ReactFlow()
        >>> flow.add_node(NodeSpec(id="A", position={"x": 0, "y": 0}))
        >>> flow.add_node(NodeSpec(id="B", position={"x": 100, "y": 0}))
        >>> flow.add_node(NodeSpec(id="C", position={"x": 50, "y": 100}))
        >>> flow.add_edge(EdgeSpec(id="e1", source="A", target="B"))
        >>> flow.add_edge(EdgeSpec(id="e2", source="B", target="C"))
        >>> flow.add_edge(EdgeSpec(id="e3", source="A", target="C"))
        >>>
        >>> G = flow.to_networkx()
        >>>
        >>> # Run NetworkX algorithms
        >>> shortest = nx.shortest_path(G, "A", "C")
        >>> print(shortest)  # ['A', 'C']
        >>>
        >>> centrality = nx.betweenness_centrality(G)
        >>> print(centrality)

        Convert with edge data:

        >>> flow = ReactFlow()
        >>> flow.add_node(NodeSpec(id="1", position={"x": 0, "y": 0}))
        >>> flow.add_node(NodeSpec(id="2", position={"x": 100, "y": 0}))
        >>> flow.add_edge(EdgeSpec(
        ...     id="e1",
        ...     source="1",
        ...     target="2",
        ...     data={"weight": 0.75, "distance": 10}
        ... ))
        >>>
        >>> G = flow.to_networkx()
        >>> print(G["1"]["2"]["weight"])  # 0.75

        See Also
        --------
        from_networkx : Create ReactFlow from NetworkX graph
        """
        try:
            import networkx as nx  # type: ignore[import-not-found]
        except Exception as exc:  # pragma: no cover
            raise ImportError("networkx is required for to_networkx.") from exc

        graph = nx.MultiDiGraph() if multigraph else nx.DiGraph()
        for node in self.nodes:
            payload = self._node_payload(node)
            data = dict(payload.get("data", {}))
            data.update({"position": payload.get("position"), "type": payload.get("type")})
            if payload.get("label") is not None:
                data["label"] = payload.get("label")
            graph.add_node(payload["id"], **data)
        for edge in self.edges:
            payload = self._edge_payload(edge)
            data = dict(payload.get("data", {}))
            if payload.get("label") is not None:
                data["label"] = payload["label"]
            if payload.get("type") is not None:
                data["type"] = payload["type"]
            if payload.get("sourceHandle") is not None:
                data["sourceHandle"] = payload["sourceHandle"]
            if payload.get("targetHandle") is not None:
                data["targetHandle"] = payload["targetHandle"]
            if multigraph:
                graph.add_edge(payload["source"], payload["target"], key=payload.get("id"), **data)
            else:
                graph.add_edge(payload["source"], payload["target"], **data)
        return graph

    @classmethod
    def from_networkx(
        cls,
        graph,
        *,
        node_type: str = "panel",
        default_position: tuple[float, float] = (0.0, 0.0),
    ) -> "ReactFlow":
        """Create a ReactFlow instance from a NetworkX graph.

        Converts a NetworkX graph into a ReactFlow component, extracting
        node positions, labels, and data from node/edge attributes. This
        enables seamless integration with NetworkX's graph creation and
        analysis capabilities.

        Parameters
        ----------
        graph : networkx.Graph
            A NetworkX graph instance (directed or undirected). Node attributes
            are converted to node data, and edge attributes to edge data.
        node_type : str, default "panel"
            Default node type assigned to all nodes. Use a custom type if you
            want schema validation or custom rendering.
        default_position : tuple of float, default (0.0, 0.0)
            Default (x, y) position used when a node doesn't have a ``position``
            attribute. Nodes can specify position via a ``position`` attribute
            as either a dict ``{"x": ..., "y": ...}`` or tuple/list ``[x, y]``.

        Returns
        -------
        ReactFlow
            A new ReactFlow instance populated with nodes and edges from the
            NetworkX graph.

        Examples
        --------
        Create from a simple NetworkX graph:

        >>> import networkx as nx
        >>> from panel_reactflow import ReactFlow
        >>>
        >>> G = nx.DiGraph()
        >>> G.add_node("A", position={"x": 0, "y": 0}, label="Start")
        >>> G.add_node("B", position={"x": 100, "y": 0}, label="Process")
        >>> G.add_node("C", position={"x": 200, "y": 0}, label="End")
        >>> G.add_edge("A", "B", weight=0.5)
        >>> G.add_edge("B", "C", weight=0.8)
        >>>
        >>> flow = ReactFlow.from_networkx(G)

        Use with NetworkX graph generators:

        >>> G = nx.karate_club_graph()
        >>> # Add positions using a layout algorithm
        >>> pos = nx.spring_layout(G, scale=500)
        >>> for node_id, (x, y) in pos.items():
        ...     G.nodes[node_id]["position"] = {"x": x, "y": y}
        >>>
        >>> flow = ReactFlow.from_networkx(G)

        Custom node type and attributes:

        >>> G = nx.DiGraph()
        >>> G.add_node("filter1", position=[0, 0], threshold=0.5, op="gt")
        >>> G.add_node("filter2", position=[100, 0], threshold=0.7, op="lt")
        >>> G.add_edge("filter1", "filter2", label="pipe")
        >>>
        >>> flow = ReactFlow.from_networkx(G, node_type="filter")

        Notes
        -----
        Node attributes are converted as follows:
        - ``position``: Used directly (converted to dict if tuple/list)
        - ``label``: Used as node label
        - ``type``: Overrides the default ``node_type`` parameter
        - ``data``: Merged with other attributes as node data
        - All other attributes become node data properties

        Edge attributes are converted as follows:
        - ``label``: Used as edge label
        - ``type``: Used as edge type
        - ``data``: Merged with other attributes as edge data
        - All other attributes become edge data properties
        - For MultiDiGraphs, the edge key becomes the edge ID

        See Also
        --------
        to_networkx : Convert ReactFlow to NetworkX graph
        """
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        for node_id, attrs in graph.nodes(data=True):
            attrs = dict(attrs)  # avoids mutating the original graph
            position = attrs.pop("position", {"x": default_position[0], "y": default_position[1]})
            if isinstance(position, (tuple, list)):
                position = {"x": position[0], "y": position[1]}
            label = attrs.pop("label", None)
            node_data = dict(attrs)
            node_data.pop("type", None)
            embedded_data = node_data.pop("data", None)
            if isinstance(embedded_data, dict):
                node_data = {**embedded_data, **node_data}
            node_payload = {"id": str(node_id), "position": position, "type": node_type, "data": node_data}
            if label is not None:
                node_payload["label"] = label
            nodes.append(node_payload)
        if graph.is_multigraph():
            edge_iter = graph.edges(keys=True, data=True)
        else:
            edge_iter = ((source, target, None, attrs) for source, target, attrs in graph.edges(data=True))
        for source, target, key, attrs in edge_iter:
            edge_data = dict(attrs)
            embedded_edge_data = edge_data.pop("data", None)
            if isinstance(embedded_edge_data, dict):
                edge_data = {**embedded_edge_data, **edge_data}
            label = edge_data.pop("label", None)
            edge_type = edge_data.pop("type", None)
            source_handle = edge_data.pop("sourceHandle", None)
            target_handle = edge_data.pop("targetHandle", None)
            edge_id = key if key is not None else f"{source}->{target}"
            edge = {
                "id": str(edge_id),
                "source": str(source),
                "target": str(target),
                "data": edge_data,
            }
            if label is not None:
                edge["label"] = label
            if edge_type is not None:
                edge["type"] = edge_type
            if source_handle is not None:
                edge["sourceHandle"] = source_handle
            if target_handle is not None:
                edge["targetHandle"] = target_handle
            edges.append(edge)
        return cls(nodes=nodes, edges=edges)

    def on(self, event_type: str, callback) -> None:
        """Register a callback for graph events.

        Subscribe to events emitted by the ReactFlow component. Events are
        triggered by user interactions (node moves, selections, etc.) and
        programmatic changes (adding/removing nodes/edges).

        Parameters
        ----------
        event_type : str
            Type of event to listen for. Use ``"*"`` to listen to all events.
            Available event types:

            - ``"node_added"``: Node was added to the graph
            - ``"node_deleted"``: Node was removed from the graph
            - ``"node_moved"``: Node was dragged to a new position
            - ``"node_clicked"``: Node was clicked
            - ``"node_data_changed"``: Node data was modified
            - ``"edge_added"``: Edge was added to the graph
            - ``"edge_deleted"``: Edge was removed from the graph
            - ``"edge_data_changed"``: Edge data was modified
            - ``"selection_changed"``: Selection changed
            - ``"sync"``: Full graph sync from frontend
            - ``"*"``: All events (wildcard)
        callback : callable
            Function called when the event occurs. Receives the event payload
            dictionary as the first argument. The callback may optionally
            accept the ``ReactFlow`` instance as a second argument, e.g.
            ``callback(payload, flow)``.

        Examples
        --------
        Listen for node movements:

        >>> from panel_reactflow import ReactFlow
        >>>
        >>> flow = ReactFlow()
        >>>
        >>> def on_move(payload, flow):
        ...     node_id = payload["node_id"]
        ...     position = payload["position"]
        ...     print(f"Node {node_id} moved to ({position['x']}, {position['y']})")
        ...     print(f"Graph currently has {len(flow.nodes)} nodes")
        >>>
        >>> flow.on("node_moved", on_move)

        Track all graph changes:

        >>> def on_any_event(payload):
        ...     event_type = payload["type"]
        ...     print(f"Event: {event_type}")
        >>>
        >>> flow.on("*", on_any_event)

        Build a workflow tracker:

        >>> nodes_added = []
        >>> edges_added = []
        >>>
        >>> def track_node(event):
        ...     nodes_added.append(event["node"])
        >>>
        >>> def track_edge(event):
        ...     edges_added.append(event["edge"])
        >>>
        >>> flow.on("node_added", track_node)
        >>> flow.on("edge_added", track_edge)

        React to selection changes:

        >>> def on_selection(event):
        ...     selected_nodes = event["nodes"]
        ...     selected_edges = event["edges"]
        ...     print(f"Selected {len(selected_nodes)} nodes and {len(selected_edges)} edges")
        >>>
        >>> flow.on("selection_changed", on_selection)

        Update analytics on data changes:

        >>> def on_data_change(event):
        ...     node_id = event["node_id"]
        ...     patch = event["patch"]
        ...     print(f"Node {node_id} updated: {patch}")
        ...     # Update database, trigger recalculation, etc.
        >>>
        >>> flow.on("node_data_changed", on_data_change)

        Notes
        -----
        Multiple callbacks can be registered for the same event type.
        They will be called in the order they were registered.

        The ``"*"`` wildcard receives all events, making it useful for
        logging, debugging, or implementing undo/redo functionality.

        Event payloads always include a ``"type"`` field indicating the
        event type, plus event-specific fields.
        """
        self._event_handlers.setdefault(event_type, []).append(callback)

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        for callback in self._event_handlers.get(event_type, []):
            if len(inspect.signature(callback).parameters) == 2:
                cb = partial(callback, payload, self)
            else:
                cb = partial(callback, payload)
            pn.state.execute(cb)
        for callback in self._event_handlers.get("*", []):
            if len(inspect.signature(callback).parameters) == 2:
                cb = partial(callback, payload, self)
            else:
                cb = partial(callback, payload)
            pn.state.execute(cb)
        self._dispatch_node_hooks(event_type, payload)
        self._dispatch_edge_hooks(event_type, payload)

    def _update_selection_from_graph(self, *_: param.parameterized.Event) -> None:
        selected_node_ids: list[str] = []
        for node in self.nodes:
            node_id = self._node_id(node)
            if node_id is None:
                continue
            is_selected = node.selected if isinstance(node, Node) else node.get("selected")
            if is_selected:
                selected_node_ids.append(node_id)
        selected_edge_ids: list[str] = []
        for edge in self.edges:
            edge_id = self._edge_id(edge)
            if edge_id is None:
                continue
            is_selected = edge.selected if isinstance(edge, Edge) else edge.get("selected")
            if is_selected:
                selected_edge_ids.append(edge_id)
        selection = {
            "nodes": selected_node_ids,
            "edges": selected_edge_ids,
        }
        if selection != self.selection:
            self.selection = selection
            self._emit(
                "selection_changed",
                {"type": "selection_changed", "nodes": selection["nodes"], "edges": selection["edges"]},
            )

    def _normalize_specs(self, event: param.parameterized.Event) -> None:
        is_edge = event.name == "edge_types"
        normalized = _coerce_spec_map(event.new, edge=is_edge)
        if normalized != event.new:
            setattr(self, event.name, normalized)

    def _normalize_nodes(self, event: param.parameterized.Event) -> None:
        """Normalize nodes list by converting NodeSpec objects to dicts."""
        normalized = [self._coerce_node(node) for node in event.new]
        # Only update if there were actual changes to avoid infinite recursion
        if any(n1 is not n2 for n1, n2 in zip(normalized, event.new, strict=False)):
            self.nodes = normalized

    def _normalize_edges(self, event: param.parameterized.Event) -> None:
        """Normalize edges list by converting EdgeSpec objects to dicts."""
        normalized = [self._coerce_edge(edge) for edge in event.new]
        # Only update if there were actual changes to avoid infinite recursion
        if any(e1 is not e2 for e1, e2 in zip(normalized, event.new, strict=False)):
            self.edges = normalized

    @staticmethod
    def _generate_edge_id(source: str, target: str) -> str:
        existing = f"{source}->{target}"
        return f"{existing}-{uuid4().hex[:8]}"

    @staticmethod
    def _coerce_node(node: dict[str, Any] | NodeSpec | Node):
        if isinstance(node, Node):
            return node
        if isinstance(node, NodeSpec):
            return node.to_dict()
        return node.to_dict() if hasattr(node, "to_dict") else node

    @staticmethod
    def _coerce_edge(edge: dict[str, Any] | EdgeSpec | Edge):
        if isinstance(edge, Edge):
            return edge
        if isinstance(edge, EdgeSpec):
            return edge.to_dict()
        return edge.to_dict() if hasattr(edge, "to_dict") else edge

    def _validate_graph_payload(self, payload: dict[str, Any], *, kind: str) -> None:
        required = {"node": ["id", "position", "data"], "edge": ["id", "source", "target"]}[kind]
        for key in required:
            if key not in payload:
                raise ValueError(f"Missing '{key}' in {kind} payload.")
