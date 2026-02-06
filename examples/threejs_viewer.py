"""3D Cube Viewer — interactive parameter control via a node graph.

A Three.js scene renders rotating cubes inside a central graph node.
Add parameter controllers from the left-hand menu, draw an edge from a
controller to the viewer, and watch the 3D scene update in real time.
"""

import panel as pn
import panel_material_ui as pmui
import param

from panel.custom import JSComponent
from panel_reactflow import NodeType, ReactFlow

pn.extension("jsoneditor")


# ── Three.js 3D viewer component ───────────────────────────────────────────


class CubeViewer(JSComponent):
    """Configurable grid of rotating cubes rendered with Three.js."""

    color = param.Color(default="#9c5afd")
    num_cubes = param.Integer(default=8, bounds=(1, 64))
    cube_size = param.Number(default=0.5, bounds=(0.1, 2.0))
    rotation_speed = param.Number(default=0.01, bounds=(0.0, 0.05))
    spacing = param.Number(default=1.8, bounds=(0.5, 4.0))
    background = param.Color(default="#0f172a")

    _importmap = {
        "imports": {
            "three": "https://esm.sh/three@0.160.0",
        },
    }

    _esm = """
    import * as THREE from "three"

    export function render({ model, el }) {
      const W = 420, H = 300;

      // ── Scene ─────────────────────────────────────────────────────
      const scene = new THREE.Scene();
      scene.background = new THREE.Color(model.background);

      const camera = new THREE.PerspectiveCamera(45, W / H, 0.1, 100);
      camera.position.set(6, 4.5, 8);
      camera.lookAt(0, 0, 0);

      const renderer = new THREE.WebGLRenderer({ antialias: true });
      renderer.setSize(W, H);
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      el.appendChild(renderer.domElement);

      // ── Lighting ──────────────────────────────────────────────────
      scene.add(new THREE.AmbientLight(0xffffff, 0.5));
      const key = new THREE.DirectionalLight(0xffffff, 1.0);
      key.position.set(5, 10, 7);
      scene.add(key);
      const rim = new THREE.DirectionalLight(0x8b5cf6, 0.3);
      rim.position.set(-5, 3, -5);
      scene.add(rim);

      // ── Ground grid ───────────────────────────────────────────────
      const grid = new THREE.GridHelper(14, 14, 0x334155, 0x1e293b);
      grid.position.y = -1.2;
      scene.add(grid);

      // ── Cubes ─────────────────────────────────────────────────────
      const group = new THREE.Group();
      scene.add(group);

      const material = new THREE.MeshStandardMaterial({
        color: model.color,
        roughness: 0.3,
        metalness: 0.65,
      });

      function rebuild() {
        // dispose shared geometry once, then clear the group
        if (group.children.length > 0) {
          group.children[0].geometry.dispose();
        }
        group.clear();

        const n    = model.num_cubes;
        const s    = model.cube_size;
        const sp   = model.spacing;
        const cols = Math.max(1, Math.ceil(Math.sqrt(n)));
        const rows = Math.ceil(n / cols);
        const geo  = new THREE.BoxGeometry(s, s, s);

        for (let i = 0; i < n; i++) {
          const c = i % cols, r = Math.floor(i / cols);
          const mesh = new THREE.Mesh(geo, material);
          mesh.position.set(
            (c - (cols - 1) / 2) * sp,
            0,
            (r - (rows - 1) / 2) * sp
          );
          mesh.rotation.set(
            Math.random() * Math.PI,
            Math.random() * Math.PI,
            0
          );
          group.add(mesh);
        }
      }
      rebuild();

      // ── Animation loop ────────────────────────────────────────────
      let raf;
      (function loop() {
        raf = requestAnimationFrame(loop);
        group.rotation.y += model.rotation_speed;
        group.children.forEach((m, i) => {
          m.rotation.x += 0.003 + i * 0.0002;
          m.rotation.z += 0.002 + i * 0.0001;
        });
        renderer.render(scene, camera);
      })();

      // ── React to Python parameter changes ─────────────────────────
      model.on("change:color",      () => material.color.set(model.color));
      model.on("change:background", () => {
        scene.background = new THREE.Color(model.background);
      });
      model.on("change:num_cubes", rebuild);
      model.on("change:cube_size", rebuild);
      model.on("change:spacing",   rebuild);

      // ── Cleanup ───────────────────────────────────────────────────
      model.on("remove", () => {
        cancelAnimationFrame(raf);
        renderer.dispose();
      });
    }
    """


# ── Stylesheet ──────────────────────────────────────────────────────────────

STYLES = """
/* ── Viewer node ───────────────────────────────────────── */
.react-flow__node-viewer {
  padding: 0;
  border-radius: 12px;
  border: 2px solid #7c3aed;
  background: #0f172a;
  box-shadow: 0 4px 24px rgba(124, 58, 237, .15);
  overflow: hidden;
}
.react-flow__node-viewer.selected {
  box-shadow: 0 0 0 2.5px rgba(124, 58, 237, .35),
              0 4px 24px rgba(124, 58, 237, .2);
}
.rf-node-content { padding: 0 }

/* ── Shared controller styling ─────────────────────────── */
.react-flow__node-color,
.react-flow__node-count,
.react-flow__node-size,
.react-flow__node-speed,
.react-flow__node-spacing,
.react-flow__node-background {
  border-radius: 8px;
  border: 1.5px solid #e2e8f0;
  border-left: 4px solid #94a3b8;
  background: #fff;
  box-shadow: 0 1px 4px rgba(0, 0, 0, .05);
  min-width: 180px;
  transition: box-shadow .2s ease, border-color .2s ease;
}

/* Per-type accent colour on the left border */
.react-flow__node-color       { border-left-color: #ec4899; }
.react-flow__node-count       { border-left-color: #3b82f6; }
.react-flow__node-size        { border-left-color: #10b981; }
.react-flow__node-speed       { border-left-color: #f59e0b; }
.react-flow__node-spacing     { border-left-color: #06b6d4; }
.react-flow__node-background  { border-left-color: #64748b; }

/* Hover */
.react-flow__node-color.selectable:hover,
.react-flow__node-count.selectable:hover,
.react-flow__node-size.selectable:hover,
.react-flow__node-speed.selectable:hover,
.react-flow__node-spacing.selectable:hover,
.react-flow__node-background.selectable:hover {
  box-shadow: 0 4px 12px rgba(0, 0, 0, .08);
}

/* Selected */
.react-flow__node-color.selected,
.react-flow__node-count.selected,
.react-flow__node-size.selected,
.react-flow__node-speed.selected,
.react-flow__node-spacing.selected,
.react-flow__node-background.selected {
  border-color: #7c3aed;
  box-shadow: 0 0 0 2px rgba(124, 58, 237, .2);
}

/* ── Edges ─────────────────────────────────────────────── */
.react-flow__edge-path {
  stroke: #7c3aed;
  stroke-width: 2px;
}
.react-flow__edge.selected .react-flow__edge-path {
  stroke: #a78bfa;
  stroke-width: 2.5px;
}
"""


# ── Constants ───────────────────────────────────────────────────────────────

PARAM_MAP = {
    "color":      "color",
    "count":      "num_cubes",
    "size":       "cube_size",
    "speed":      "rotation_speed",
    "spacing":    "spacing",
    "background": "background",
}

DEFAULTS = {
    "color":      CubeViewer.color,
    "count":      CubeViewer.num_cubes,
    "size":       CubeViewer.cube_size,
    "speed":      CubeViewer.rotation_speed,
    "spacing":    CubeViewer.spacing,
    "background": CubeViewer.background,
}

LABELS = {
    "color":      "Color",
    "count":      "Cube Count",
    "size":       "Cube Size",
    "speed":      "Rotation Speed",
    "spacing":    "Spacing",
    "background": "Background",
}


# ── Node types ──────────────────────────────────────────────────────────────

node_types = {
    "viewer": NodeType(type="viewer", label="3D Viewer", inputs=["param"]),
    **{
        t: NodeType(type=t, label=LABELS[t], outputs=["out"])
        for t in PARAM_MAP
    },
}


# ── Widget factory ──────────────────────────────────────────────────────────

def _make_widget(ctrl_type, value):
    """Return the appropriate Panel widget for *ctrl_type*."""
    if ctrl_type in ("color", "background"):
        return pmui.ColorPicker(value=value, name="", stylesheets=[".MuiPopover-paper { max-height: none !important; max-width: none !important; }"])
    if ctrl_type == "count":
        return pmui.IntSlider(value=value, start=1, end=64, name="")
    if ctrl_type == "size":
        return pmui.FloatSlider(
            value=value, start=0.1, end=2.0, step=0.05, name="",
        )
    if ctrl_type == "speed":
        return pmui.FloatSlider(
            value=value, start=0.0, end=0.05, step=0.001, name="",
        )
    if ctrl_type == "spacing":
        return pmui.FloatSlider(
            value=value, start=0.5, end=4.0, step=0.1, name="",
        )
    raise ValueError(f"Unknown controller type: {ctrl_type}")


# ── Create the viewer and the flow ──────────────────────────────────────────

viewer_component = CubeViewer(margin=0, width=420, height=300)

flow = ReactFlow(
    nodes=[
        {
            "id": "viewer",
            "type": "viewer",
            "label": "",
            "position": {"x": 500, "y": 100},
            "data": {},
            "view": viewer_component,
        },
    ],
    edges=[],
    node_types=node_types,
    stylesheets=[STYLES],
    sizing_mode="stretch_both",
)


# ── State tracking ──────────────────────────────────────────────────────────

# Maps node_id → (ctrl_type, widget)
_widgets: dict[str, tuple[str, pn.widgets.Widget]] = {}
_counter = [0]


# ── Pipe changes to the viewer when connected ───────────────────────────────

def _push_if_connected(node_id, ctrl_type, value):
    """If *node_id* has an edge to the viewer, update the viewer param."""
    for edge in flow.edges:
        if edge.get("source") == node_id and edge.get("target") == "viewer":
            setattr(viewer_component, PARAM_MAP[ctrl_type], value)
            return


def _on_edge_added(payload):
    """When a new edge connects a controller to the viewer, apply its value."""
    edge = payload.get("edge", {})
    src = edge.get("source")
    if edge.get("target") == "viewer" and src in _widgets:
        ctrl_type, widget = _widgets[src]
        setattr(viewer_component, PARAM_MAP[ctrl_type], widget.value)


def _on_node_deleted(payload):
    """Clean up tracked widgets and sync the tree when a controller is removed."""
    node_ids = payload.get("node_ids") or [payload.get("node_id")]
    changed = False
    for node_id in node_ids:
        if node_id is None:
            continue
        _widgets.pop(node_id, None)
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


# ── Add a controller node ───────────────────────────────────────────────────

def add_controller(ctrl_type):
    """Create a parameter-controller node and add it to the graph."""
    _counter[0] += 1
    node_id = f"{ctrl_type}_{_counter[0]}"
    y_pos = 30 + ((_counter[0] - 1) % 6) * 120

    widget = _make_widget(ctrl_type, DEFAULTS[ctrl_type])
    _widgets[node_id] = (ctrl_type, widget)

    # When the widget changes, push the new value if connected
    widget.param.watch(
        lambda evt, nid=node_id, ct=ctrl_type: _push_if_connected(nid, ct, evt.new),
        "value",
    )

    flow.add_node(
        {
            "id": node_id,
            "type": ctrl_type,
            "label": LABELS[ctrl_type],
            "position": {"x": 50, "y": y_pos},
            "data": {},
        },
        view=widget,
    )
    return node_id


# ── Left-panel tree menu ────────────────────────────────────────────────────

CTRL_ORDER = list(PARAM_MAP)  # ["color", "count", "size", "speed", "spacing", "background"]

menu_tree = pmui.Tree(
    items=[
        {"label": "Color",          "icon": "palette"},
        {"label": "Cube Count",     "icon": "grid_view"},
        {"label": "Cube Size",      "icon": "open_with"},
        {"label": "Rotation Speed", "icon": "speed"},
        {"label": "Spacing",        "icon": "space_bar"},
        {"label": "Background",     "icon": "dark_mode"},
    ],
    checkboxes=True,
    active=[(0,), (1,)],  # Color & Count checked initially
    width=200,
    margin=5,
)

# Track which tree indices have active controller nodes
_active_controllers: dict[int, str] = {}  # tree index → node_id
_syncing = [False]


def _on_tree_change(event):
    """Add or remove controller nodes when tree checkboxes change."""
    if _syncing[0]:
        return
    new_indices = {idx for (idx,) in event.new}
    old_indices = set(_active_controllers)

    # Checked → add controller + edge
    for idx in sorted(new_indices - old_indices):
        ctrl_type = CTRL_ORDER[idx]
        node_id = add_controller(ctrl_type)
        flow.add_edge({"source": node_id, "target": "viewer", "data": {}})
        _active_controllers[idx] = node_id

    # Unchecked → remove controller (and its edges)
    for idx in old_indices - new_indices:
        node_id = _active_controllers.pop(idx)
        flow.remove_node(node_id)
        _widgets.pop(node_id, None)


menu_tree.param.watch(_on_tree_change, "active")

menu = pn.Column(
    pn.pane.Markdown("#### Controllers"),
    menu_tree,
    width=210,
    margin=(10, 5),
)
flow.left_panel = [menu]


# ── Seed the two initially-checked controllers ──────────────────────────────

_color_id = add_controller("color")
_count_id = add_controller("count")
flow.add_edge({"source": _color_id, "target": "viewer", "data": {}})
flow.add_edge({"source": _count_id, "target": "viewer", "data": {}})
_active_controllers[0] = _color_id
_active_controllers[1] = _count_id


# ── Serve ───────────────────────────────────────────────────────────────────

pn.Column(flow, sizing_mode="stretch_both").servable()
