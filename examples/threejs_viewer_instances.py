"""3D Cube Viewer using Node/Edge instances.

This mirrors ``threejs_viewer.py`` but models graph elements as ``Node`` and
``Edge`` subclasses instead of plain dictionaries.
"""

import panel as pn
import panel_material_ui as pmui
import param

from panel.custom import JSComponent
from panel_reactflow import Edge, Node, NodeType, ReactFlow

pn.extension("jsoneditor")


class CubeViewer(JSComponent):
    color = param.Color(default="#9c5afd")
    num_cubes = param.Integer(default=8, bounds=(1, 64))
    cube_size = param.Number(default=0.5, bounds=(0.1, 2.0))
    rotation_speed = param.Number(default=0.01, bounds=(0.0, 0.05))
    spacing = param.Number(default=1.8, bounds=(0.5, 4.0))
    background = param.Color(default="#0f172a")

    _importmap = {"imports": {"three": "https://esm.sh/three@0.160.0"}}

    _esm = """
    import * as THREE from "three"
    export function render({ model, el }) {
      const W = 420, H = 300;
      const scene = new THREE.Scene();
      scene.background = new THREE.Color(model.background);
      const camera = new THREE.PerspectiveCamera(45, W / H, 0.1, 100);
      camera.position.set(6, 4.5, 8); camera.lookAt(0, 0, 0);
      const renderer = new THREE.WebGLRenderer({ antialias: true });
      renderer.setSize(W, H); renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      el.appendChild(renderer.domElement);
      scene.add(new THREE.AmbientLight(0xffffff, 0.5));
      const key = new THREE.DirectionalLight(0xffffff, 1.0); key.position.set(5, 10, 7); scene.add(key);
      const rim = new THREE.DirectionalLight(0x8b5cf6, 0.3); rim.position.set(-5, 3, -5); scene.add(rim);
      const grid = new THREE.GridHelper(14, 14, 0x334155, 0x1e293b); grid.position.y = -1.2; scene.add(grid);
      const group = new THREE.Group(); scene.add(group);
      const material = new THREE.MeshStandardMaterial({ color: model.color, roughness: 0.3, metalness: 0.65 });
      function rebuild() {
        if (group.children.length > 0) group.children[0].geometry.dispose();
        group.clear();
        const n = model.num_cubes, s = model.cube_size, sp = model.spacing;
        const cols = Math.max(1, Math.ceil(Math.sqrt(n))), rows = Math.ceil(n / cols);
        const geo = new THREE.BoxGeometry(s, s, s);
        for (let i = 0; i < n; i++) {
          const c = i % cols, r = Math.floor(i / cols);
          const mesh = new THREE.Mesh(geo, material);
          mesh.position.set((c - (cols - 1) / 2) * sp, 0, (r - (rows - 1) / 2) * sp);
          mesh.rotation.set(Math.random() * Math.PI, Math.random() * Math.PI, 0);
          group.add(mesh);
        }
      }
      rebuild();
      let raf;
      (function loop() {
        raf = requestAnimationFrame(loop);
        group.rotation.y += model.rotation_speed;
        group.children.forEach((m, i) => { m.rotation.x += 0.003 + i * 0.0002; m.rotation.z += 0.002 + i * 0.0001; });
        renderer.render(scene, camera);
      })();
      model.on("change:color", () => material.color.set(model.color));
      model.on("change:background", () => { scene.background = new THREE.Color(model.background); });
      model.on("change:num_cubes", rebuild);
      model.on("change:cube_size", rebuild);
      model.on("change:spacing", rebuild);
      model.on("remove", () => { cancelAnimationFrame(raf); renderer.dispose(); });
    }
    """


STYLES = """
.react-flow__node-viewer { padding: 0; border-radius: 12px; border: 2px solid #7c3aed; background: #0f172a; box-shadow: 0 4px 24px rgba(124, 58, 237, .15); overflow: hidden; }
.react-flow__node-viewer.selected { box-shadow: 0 0 0 2.5px rgba(124, 58, 237, .35), 0 4px 24px rgba(124, 58, 237, .2); }
.rf-node-content { padding: 0; }
.react-flow__node-color,.react-flow__node-count,.react-flow__node-size,.react-flow__node-speed,.react-flow__node-spacing,.react-flow__node-background { border-radius: 8px; border: 1.5px solid #e2e8f0; border-left: 4px solid #94a3b8; background: #fff; box-shadow: 0 1px 4px rgba(0, 0, 0, .05); min-width: 180px; }
.react-flow__node-color { border-left-color: #ec4899; } .react-flow__node-count { border-left-color: #3b82f6; } .react-flow__node-size { border-left-color: #10b981; }
.react-flow__node-speed { border-left-color: #f59e0b; } .react-flow__node-spacing { border-left-color: #06b6d4; } .react-flow__node-background { border-left-color: #64748b; }
.react-flow__edge-path { stroke: #7c3aed; stroke-width: 2px; }
"""


class ViewerNode(Node):
    def __init__(self, viewer: CubeViewer, **params):
        params.setdefault("id", "viewer")
        params.setdefault("type", "viewer")
        params.setdefault("label", "")
        params.setdefault("position", {"x": 500, "y": 100})
        super().__init__(**params)
        self._viewer = viewer

    def __panel__(self):
        return self._viewer


class LinkEdge(Edge):
    pass


class ControllerNode(Node):
    viewer_param = ""
    ctrl_type = ""

    def __init__(self, viewer: CubeViewer, **params):
        params.setdefault("type", self.ctrl_type)
        super().__init__(**params)
        self._viewer = viewer
        self._widget = self._make_widget()

    def _make_widget(self):
        raise NotImplementedError

    def _param_value(self):
        return getattr(self, self.viewer_param)

    def editor(self, data, schema, *, id, type, on_patch):
        return self._widget

    def _push_if_connected(self, flow: ReactFlow) -> None:
        for edge in flow.edges:
            source = edge.source if isinstance(edge, Edge) else edge.get("source")
            target = edge.target if isinstance(edge, Edge) else edge.get("target")
            if source == self.id and target == "viewer":
                setattr(self._viewer, self.viewer_param, self._param_value())
                return

    def on_add(self, payload, flow):
        self._push_if_connected(flow)

    def on_data_change(self, payload, flow):
        if payload.get("node_id") == self.id:
            self._push_if_connected(flow)


class ColorController(ControllerNode):
    ctrl_type = "color"
    viewer_param = "color"
    color = param.Color(default=CubeViewer.color, precedence=0)

    def _make_widget(self):
        return pmui.ColorPicker.from_param(self.param.color, name="")


class CountController(ControllerNode):
    ctrl_type = "count"
    viewer_param = "num_cubes"
    num_cubes = param.Integer(default=CubeViewer.num_cubes, bounds=(1, 64), precedence=0)

    def _make_widget(self):
        return pmui.IntSlider.from_param(self.param.num_cubes, name="")


class SizeController(ControllerNode):
    ctrl_type = "size"
    viewer_param = "cube_size"
    cube_size = param.Number(default=CubeViewer.cube_size, bounds=(0.1, 2.0), precedence=0)

    def _make_widget(self):
        return pmui.FloatSlider.from_param(self.param.cube_size, step=0.05, name="")


class SpeedController(ControllerNode):
    ctrl_type = "speed"
    viewer_param = "rotation_speed"
    rotation_speed = param.Number(default=CubeViewer.rotation_speed, bounds=(0.0, 0.05), precedence=0)

    def _make_widget(self):
        return pmui.FloatSlider.from_param(self.param.rotation_speed, step=0.001, name="")


class SpacingController(ControllerNode):
    ctrl_type = "spacing"
    viewer_param = "spacing"
    spacing = param.Number(default=CubeViewer.spacing, bounds=(0.5, 4.0), precedence=0)

    def _make_widget(self):
        return pmui.FloatSlider.from_param(self.param.spacing, step=0.1, name="")


class BackgroundController(ControllerNode):
    ctrl_type = "background"
    viewer_param = "background"
    background = param.Color(default=CubeViewer.background, precedence=0)

    def _make_widget(self):
        return pmui.ColorPicker.from_param(self.param.background, name="")


CTRL_CLASSES = {
    "color": ColorController,
    "count": CountController,
    "size": SizeController,
    "speed": SpeedController,
    "spacing": SpacingController,
    "background": BackgroundController,
}

LABELS = {
    "color": "Color",
    "count": "Cube Count",
    "size": "Cube Size",
    "speed": "Rotation Speed",
    "spacing": "Spacing",
    "background": "Background",
}

node_types = {
    "viewer": NodeType(type="viewer", label="3D Viewer", inputs=["param"]),
    **{t: NodeType(type=t, label=LABELS[t], outputs=["out"]) for t in CTRL_CLASSES},
}

viewer_component = CubeViewer(margin=0, width=420, height=300)
viewer_node = ViewerNode(viewer=viewer_component)

flow = ReactFlow(
    nodes=[viewer_node],
    edges=[],
    node_types=node_types,
    editor_mode="node",
    stylesheets=[STYLES],
    sizing_mode="stretch_both",
)

_counter = [0]
_active_controllers: dict[int, str] = {}
_syncing = [False]


def _controller_by_id(node_id: str):
    for node in flow.nodes:
        if isinstance(node, ControllerNode) and node.id == node_id:
            return node
    return None


def _on_edge_added(payload):
    edge_payload = payload.get("edge", {})
    source = edge_payload.get("source")
    target = edge_payload.get("target")
    if source and target == "viewer":
        controller = _controller_by_id(source)
        if controller is not None:
            controller._push_if_connected(flow)


def _on_node_deleted(payload):
    node_ids = payload.get("node_ids") or [payload.get("node_id")]
    changed = False
    for node_id in node_ids:
        if node_id is None:
            continue
        for idx, nid in list(_active_controllers.items()):
            if nid == node_id:
                del _active_controllers[idx]
                changed = True
                break
    if changed:
        _syncing[0] = True
        menu_tree.active = [(idx,) for idx in sorted(_active_controllers)]
        _syncing[0] = False


flow.on("edge_added", _on_edge_added)
flow.on("node_deleted", _on_node_deleted)


def add_controller(ctrl_type: str) -> str:
    _counter[0] += 1
    node_id = f"{ctrl_type}_{_counter[0]}"
    y_pos = 30 + ((_counter[0] - 1) % 6) * 120
    controller = CTRL_CLASSES[ctrl_type](
        viewer=viewer_component,
        id=node_id,
        label=LABELS[ctrl_type],
        position={"x": 50, "y": y_pos},
    )
    flow.add_node(controller)
    return node_id


CTRL_ORDER = list(CTRL_CLASSES)

menu_tree = pmui.Tree(
    items=[
        {"label": "Color", "icon": "palette"},
        {"label": "Cube Count", "icon": "grid_view"},
        {"label": "Cube Size", "icon": "open_with"},
        {"label": "Rotation Speed", "icon": "speed"},
        {"label": "Spacing", "icon": "space_bar"},
        {"label": "Background", "icon": "dark_mode"},
    ],
    checkboxes=True,
    active=[(0,), (1,)],
    width=200,
    margin=5,
)


def _on_tree_change(event):
    if _syncing[0]:
        return
    new_indices = {idx for (idx,) in event.new}
    old_indices = set(_active_controllers)
    for idx in sorted(new_indices - old_indices):
        ctrl_type = CTRL_ORDER[idx]
        node_id = add_controller(ctrl_type)
        flow.add_edge(LinkEdge(source=node_id, target="viewer"))
        _active_controllers[idx] = node_id
    for idx in old_indices - new_indices:
        node_id = _active_controllers.pop(idx)
        flow.remove_node(node_id)


menu_tree.param.watch(_on_tree_change, "active")

menu = pn.Column(
    pn.pane.Markdown("#### Controllers"),
    menu_tree,
    width=210,
    margin=(10, 5),
)
flow.left_panel = [menu]

_color_id = add_controller("color")
_count_id = add_controller("count")
flow.add_edge(LinkEdge(source=_color_id, target="viewer"))
flow.add_edge(LinkEdge(source=_count_id, target="viewer"))
_active_controllers[0] = _color_id
_active_controllers[1] = _count_id

pn.Column(flow, sizing_mode="stretch_both").servable()
