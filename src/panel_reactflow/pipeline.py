"""Pipeline: visual data-flow built from parameterized stages."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import panel as pn
import param
from panel.viewable import Viewer

from .base import NodeSpec, ReactFlow

# ---------------------------------------------------------------------------
# Pipeline node styling
# ---------------------------------------------------------------------------

_PIPELINE_CSS = """\
.react-flow__node.rf-auto-input {
    border: 1.5px solid #6366f1;
    background: linear-gradient(180deg, #eef2ff 0%, #ffffff 100%);
    overflow: visible;
}
.react-flow__node.rf-auto-input::before {
    content: "input";
    position: absolute;
    top: -9px;
    left: 10px;
    background: #6366f1;
    color: #fff;
    font-size: 10px;
    font-weight: 600;
    padding: 1px 8px;
    border-radius: 9999px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    line-height: 16px;
}
.react-flow__node.rf-auto-input.selected {
    border-color: #4f46e5;
    box-shadow: 0 0 0 1.5px rgba(99, 102, 241, 0.3);
}
.react-flow__node.rf-stage {
    border: 1.5px solid #10b981;
    background: linear-gradient(180deg, #ecfdf5 0%, #ffffff 100%);
    overflow: visible;
}
.react-flow__node.rf-stage::before {
    content: "output";
    position: absolute;
    top: -9px;
    left: 10px;
    background: #10b981;
    color: #fff;
    font-size: 10px;
    font-weight: 600;
    padding: 1px 8px;
    border-radius: 9999px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    line-height: 16px;
}
.react-flow__node.rf-stage.selected {
    border-color: #059669;
    box-shadow: 0 0 0 1.5px rgba(16, 185, 129, 0.3);
}
"""


# ---------------------------------------------------------------------------
# Introspection helpers
# ---------------------------------------------------------------------------

# Parameters inherited from Parameterized that should never be treated as
# stage inputs.
_BASE_PARAMS: set[str] = set(param.Parameterized.param)
try:
    from panel.viewable import Viewable

    _BASE_PARAMS |= set(Viewable.param)
except Exception:
    pass


def _get_outputs(instance: param.Parameterized) -> dict[str, tuple]:
    """Return ``{name: (type, method, index)}`` from ``@param.output``."""
    return instance.param.outputs()


def _get_input_params(instance: param.Parameterized) -> dict[str, param.Parameter]:
    """Return non-base, non-private parameters suitable as stage inputs."""
    return {name: p for name, p in instance.param.objects("existing").items() if name not in _BASE_PARAMS and not name.startswith("_")}


def _get_view_methods(
    instance: param.Parameterized,
    output_method_names: set[str],
) -> list:
    """Return public ``@param.depends`` bound methods that are not outputs."""
    views = []
    for attr_name in sorted(dir(type(instance))):
        if attr_name.startswith("_"):
            continue
        if attr_name in output_method_names:
            continue
        func = getattr(type(instance), attr_name, None)
        if callable(func) and hasattr(func, "_dinfo"):
            views.append(getattr(instance, attr_name))
    return views


def _make_output_view(
    instance: param.Parameterized,
    method: Any,
    index: int | None,
) -> pn.viewable.Viewable:
    """Create a reactive Panel view for a single output.

    For single-output methods (``index is None``), the bound method is
    rendered directly.  For multi-output methods, a ``pn.bind`` wrapper
    extracts the correct element from the tuple.
    """
    if index is None:
        return pn.panel(method)

    method_name = method.__name__
    deps = instance.param.method_dependencies(method_name)
    dep_params = [instance.param[dep.name] for dep in deps]

    def _extract(*_args, _m=method, _i=index):
        try:
            return _m()[_i]
        except Exception:
            return None

    if dep_params:
        return pn.panel(pn.bind(_extract, *dep_params))
    return pn.panel(_extract())


def _infer_edges(
    stage_names: list[str],
    instances: dict[str, param.Parameterized],
    outputs_map: dict[str, dict[str, tuple]],
) -> list[tuple[str, str, str]]:
    """Infer edges by matching output names to downstream parameter names.

    Returns a list of ``(source_name, target_name, param_name)`` triples.
    """
    edges: list[tuple[str, str, str]] = []
    for i, src_name in enumerate(stage_names):
        src_outputs = outputs_map.get(src_name, {})
        for output_name in src_outputs:
            for tgt_name in stage_names[i + 1 :]:
                tgt_inputs = _get_input_params(instances[tgt_name])
                if output_name in tgt_inputs:
                    edges.append((src_name, tgt_name, output_name))
    return edges


def _resolve_explicit_graph(
    graph: dict[str, str | tuple[str, ...]],
    instances: dict[str, param.Parameterized],
    outputs_map: dict[str, dict[str, tuple]],
) -> list[tuple[str, str, str]]:
    """Convert an explicit ``graph`` dict into edge triples.

    For each ``source -> target(s)`` entry, match source outputs to target
    input parameters by name.
    """
    edges: list[tuple[str, str, str]] = []
    for src_name, targets in graph.items():
        if isinstance(targets, str):
            targets = (targets,)
        src_outputs = outputs_map.get(src_name, {})
        for tgt_name in targets:
            tgt_inputs = _get_input_params(instances[tgt_name])
            for output_name in src_outputs:
                if output_name in tgt_inputs:
                    edges.append((src_name, tgt_name, output_name))
    return edges


def _compute_positions(
    stage_names: list[str],
    edges: list[tuple[str, str, str]],
    spacing: tuple[float, float],
) -> dict[str, dict[str, float]]:
    """Compute simple left-to-right positions for stages.

    Linear chains are placed in a single row.  When fan-out occurs (a node
    has multiple outgoing targets at the same depth), branches are stacked
    vertically.
    """
    sx, sy = spacing

    # Build adjacency for topological depth assignment.
    children: dict[str, set[str]] = defaultdict(set)
    parents: dict[str, set[str]] = defaultdict(set)
    for src, tgt, _ in edges:
        children[src].add(tgt)
        parents[tgt].add(src)

    # Assign depth (column) via BFS from roots.
    depth: dict[str, int] = {}
    # Roots are stages with no parents (among stages in edges).
    all_in_edges = {s for s, _, _ in edges} | {t for _, t, _ in edges}
    roots = [name for name in stage_names if name not in parents or name not in all_in_edges]
    if not roots:
        roots = [stage_names[0]]

    queue = list(roots)
    for r in roots:
        depth.setdefault(r, 0)

    while queue:
        node = queue.pop(0)
        for child in children.get(node, []):
            new_depth = depth[node] + 1
            if child not in depth or new_depth > depth[child]:
                depth[child] = new_depth
                queue.append(child)

    # Stages with no edges get sequential depth.
    next_col = max(depth.values(), default=-1) + 1
    for name in stage_names:
        if name not in depth:
            depth[name] = next_col
            next_col += 1

    # Group stages by depth for vertical stacking.
    by_depth: dict[int, list[str]] = defaultdict(list)
    for name in stage_names:
        by_depth[depth[name]].append(name)

    positions: dict[str, dict[str, float]] = {}
    for col, names in by_depth.items():
        total_height = (len(names) - 1) * sy
        start_y = -total_height / 2
        for row, name in enumerate(names):
            positions[name] = {"x": col * sx, "y": start_y + row * sy}

    return positions


# ---------------------------------------------------------------------------
# Pipeline class
# ---------------------------------------------------------------------------


class Pipeline(Viewer):
    """Visual pipeline built from parameterized stages.

    Parameters
    ----------
    stages : list
        List of ``(name, class_or_instance)`` tuples.  Classes are
        instantiated automatically.
    graph : dict or None
        Explicit topology mapping source stage names to target name(s).
        When *None*, edges are inferred by matching ``@param.output``
        names to downstream parameter names.
    layout_spacing : tuple
        ``(horizontal, vertical)`` spacing in pixels between nodes.
    kwargs : dict
        Extra keyword arguments forwarded to the ``ReactFlow`` constructor.
    """

    stages = param.List(
        doc="List of (name, class_or_instance) tuples.",
    )
    graph = param.Dict(
        default=None,
        allow_None=True,
        doc=(
            "Explicit topology: {source_name: target_name | (t1, t2, ...)}. "
            "If None, edges are inferred by matching @param.output names to "
            "downstream parameter names."
        ),
    )
    layout_spacing = param.NumericTuple(
        default=(350, 150),
        doc="(horizontal, vertical) spacing between nodes in pixels.",
    )
    auto_inputs = param.Boolean(
        default=True,
        doc=("Auto-generate input widget nodes for stage parameters that have " "no incoming edge from another stage."),
    )
    kwargs = param.Dict(
        default={},
        doc="Extra keyword arguments forwarded to ReactFlow.",
    )

    def __init__(self, **params):
        super().__init__(**params)
        self._instances: dict[str, param.Parameterized] = {}
        self._outputs: dict[str, dict[str, tuple]] = {}
        self._edges: list[tuple[str, str, str]] = []
        self._input_views: dict[str, pn.viewable.Viewable] = {}
        self._flow: ReactFlow | None = None
        self._build()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _find_unconnected_params(
        self,
        stage_names: list[str],
    ) -> list[tuple[str, str]]:
        """Return ``(stage_name, param_name)`` pairs with no incoming edge."""
        connected: dict[str, set[str]] = defaultdict(set)
        for _src, tgt, pname in self._edges:
            connected[tgt].add(pname)

        result: list[tuple[str, str]] = []
        for name in stage_names:
            instance = self._instances[name]
            for pname in _get_input_params(instance):
                if pname not in connected.get(name, set()):
                    result.append((name, pname))
        return result

    def _build(self) -> None:
        stage_names: list[str] = []

        # 1. Instantiate stages
        for name, cls_or_instance in self.stages:
            stage_names.append(name)
            if isinstance(cls_or_instance, type):
                instance = cls_or_instance(name=name)
            else:
                instance = cls_or_instance
            self._instances[name] = instance

        # 2. Introspect outputs
        for name, instance in self._instances.items():
            self._outputs[name] = _get_outputs(instance)

        # 3. Infer or resolve edges
        if self.graph is None:
            self._edges = _infer_edges(stage_names, self._instances, self._outputs)
        else:
            self._edges = _resolve_explicit_graph(self.graph, self._instances, self._outputs)

        # 4. Wire reactivity (stage-to-stage only)
        self._wire(self._edges)

        # 5. Auto-generate input nodes for unconnected params
        all_names = list(stage_names)
        all_edges = list(self._edges)
        self._input_views.clear()

        if self.auto_inputs:
            unconnected = self._find_unconnected_params(stage_names)
            for stage_name, param_name in unconnected:
                input_id = f"{stage_name}:{param_name}"
                instance = self._instances[stage_name]
                widget = pn.panel(instance.param[param_name])
                self._input_views[input_id] = widget
                all_names.append(input_id)
                all_edges.append((input_id, stage_name, param_name))

        # 6. Build ReactFlow
        self._flow = self._build_reactflow(all_names, all_edges)

    def _wire(self, edges: list[tuple[str, str, str]]) -> None:
        """Set up ``param.watch`` watchers so upstream outputs propagate."""
        for src_name, tgt_name, param_name in edges:
            src = self._instances[src_name]
            tgt = self._instances[tgt_name]
            output_info = self._outputs[src_name][param_name]
            # output_info = (param_type_instance, bound_method, index)
            _ptype, method, index = output_info

            # Find the parameter dependencies of the output method.
            method_name = method.__name__
            deps = src.param.method_dependencies(method_name)
            dep_names = [dep.name for dep in deps]

            if not dep_names:
                # No explicit dependencies — try to fire on any non-private
                # param change; or at minimum set the initial value.
                self._propagate_output(src, method, index, tgt, param_name)
                continue

            # Capture variables for the closure.
            def _make_watcher(_src, _method, _index, _tgt, _param_name):
                def _watcher(*events):
                    self._propagate_output(_src, _method, _index, _tgt, _param_name)

                return _watcher

            watcher = _make_watcher(src, method, index, tgt, param_name)
            src.param.watch(watcher, dep_names)

            # Set initial value.
            self._propagate_output(src, method, index, tgt, param_name)

    @staticmethod
    def _propagate_output(
        src: param.Parameterized,
        method: Any,
        index: int | None,
        tgt: param.Parameterized,
        param_name: str,
    ) -> None:
        """Call the output method and assign the result to the target."""
        try:
            value = method()
        except Exception:
            return
        if index is not None:
            try:
                value = value[index]
            except (IndexError, TypeError, KeyError):
                return
        setattr(tgt, param_name, value)

    def _build_reactflow(
        self,
        all_names: list[str],
        edges: list[tuple[str, str, str]],
    ) -> ReactFlow:
        """Construct the ReactFlow component."""
        positions = _compute_positions(all_names, edges, self.layout_spacing)

        nodes: list[dict[str, Any]] = []
        for name in all_names:
            pos = positions[name]
            if name in self._input_views:
                # Auto-generated input widget node
                param_name = name.split(":", 1)[1]
                label = param_name.replace("_", " ").title()
                node_dict = NodeSpec(
                    id=name,
                    position=pos,
                    label=label,
                    className="rf-auto-input",
                ).to_dict()
                node_dict["view"] = self._input_views[name]
            else:
                # Stage node — prefer view methods, fall back to outputs
                instance = self._instances[name]
                outputs = self._outputs.get(name, {})
                output_method_names = {info[1].__name__ for info in outputs.values()}
                view_methods = _get_view_methods(instance, output_method_names)

                node_dict = NodeSpec(
                    id=name,
                    position=pos,
                    label=name,
                    className="rf-stage",
                ).to_dict()

                if view_methods:
                    # Use non-output @param.depends view methods
                    if len(view_methods) == 1:
                        node_dict["view"] = pn.panel(view_methods[0])
                    else:
                        node_dict["view"] = pn.Column(*(pn.panel(m) for m in view_methods))
                elif outputs:
                    # Fall back to rendering @param.output methods
                    output_names = []
                    output_views = []
                    for out_name, out_info in outputs.items():
                        _ptype, method, index = out_info
                        output_names.append(out_name)
                        output_views.append(_make_output_view(instance, method, index))
                    if len(output_views) == 1:
                        node_dict["view"] = output_views[0]
                    else:
                        node_dict["view"] = pn.Accordion(
                            *zip(output_names, output_views, strict=False),
                            active=list(range(len(output_views))),
                        )
            nodes.append(node_dict)

        edge_dicts: list[dict[str, Any]] = []
        seen_edges: set[tuple[str, str, str]] = set()
        edge_counter = 0
        for src_name, tgt_name, param_name in edges:
            key = (src_name, tgt_name, param_name)
            if key in seen_edges:
                continue
            seen_edges.add(key)
            edge_counter += 1
            edge_dicts.append(
                {
                    "id": f"e{edge_counter}",
                    "source": src_name,
                    "target": tgt_name,
                    "label": param_name,
                    "markerEnd": {"type": "arrowclosed"},
                }
            )

        flow_kwargs: dict[str, Any] = {
            "nodes": nodes,
            "edges": edge_dicts,
            "sizing_mode": "stretch_both",
            "min_height": 500,
            "editable": False,
        }
        flow_kwargs.update(self.kwargs)

        # Merge auto-input CSS with any user-provided stylesheets
        user_sheets = flow_kwargs.pop("stylesheets", [])
        flow_kwargs["stylesheets"] = [_PIPELINE_CSS] + list(user_sheets)

        return ReactFlow(**flow_kwargs)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def __panel__(self):
        return self._flow
