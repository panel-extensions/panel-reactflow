"""
Example demonstrating smart edges that automatically route around nodes.

Smart edges automatically find paths around nodes to avoid overlaps.
Available edge types:
- 'bezier' (default): Smooth bezier curve
- 'straight': Straight line between nodes
- 'step': Orthogonal step path (right angles)
- 'smoothstep': Step path with rounded corners
- 'smart_bezier': Smart bezier curve that routes around nodes
- 'smart_straight': Smart straight segments that route around nodes
- 'smart_step': Smart step path that routes around nodes
"""

import panel as pn
import panel_material_ui as pmui
from panel_reactflow import EdgeSpec, NodeSpec, ReactFlow

pn.extension()

# Create a simple graph with nodes that would normally cause edge overlaps
nodes = [
    NodeSpec(id="1", position={"x": 0, "y": 100}, label="Start").to_dict(),
    NodeSpec(id="2", position={"x": 200, "y": 0}, label="Top").to_dict(),
    NodeSpec(id="3", position={"x": 200, "y": 200}, label="Bottom").to_dict(),
    NodeSpec(id="4", position={"x": 400, "y": 100}, label="End").to_dict(),
    NodeSpec(id="5", position={"x": 200, "y": 100}, label="Middle Obstacle").to_dict(),
]

# Create edges with different types
edges = [
    EdgeSpec(id="e1", source="1", target="4", label="Regular", type=None).to_dict(),
    EdgeSpec(id="e2", source="2", target="3", label="Smart Bezier", type="smart_bezier").to_dict(),
    EdgeSpec(id="e3", source="1", target="2", label="Smart Straight", type="smart_straight").to_dict(),
    EdgeSpec(id="e4", source="1", target="3", label="Smart Step", type="smart_step").to_dict(),
]

# Create the flow with smart edges
flow = ReactFlow(
    nodes=nodes,
    edges=edges,
    sizing_mode="stretch_both"
)

# Create edge type selector
edge_type_selector = pmui.Select(
    label="Edge Type for e1",
    options={
        "Bezier (default)": "bezier",
        "Straight": "straight",
        "Step": "step",
        "Smooth Step": "smoothstep",
        "Smart Bezier": "smart_bezier",
        "Smart Straight": "smart_straight",
        "Smart Step": "smart_step",
    },
    value=None,
)


def update_edge_type(event):
    """Update the edge type when selector changes."""
    for edge in flow.edges:
        edge["type"] = event.new
        flow.edges = flow.edges  # Trigger update

edge_type_selector.param.watch(update_edge_type, "value")

# Layout
pmui.Page(
    title="Smart Edges Example",
    sidebar=[
        pn.pane.Markdown(
            """
            ## Smart Edges Demo

            Smart edges automatically route around nodes to avoid overlaps.

            **Standard Edge Types:**
            - **Bezier**: Smooth bezier curve (default)
            - **Straight**: Direct straight line
            - **Step**: Orthogonal path (right angles)
            - **Smooth Step**: Step path with rounded corners

            **Smart Edge Types:**
            - **Smart Bezier**: Curved path that avoids nodes
            - **Smart Straight**: Straight segments that route around nodes
            - **Smart Step**: Step-style path that avoids nodes

            **Try it:**
            1. Change the edge type for the "Start → End" connection
            2. Drag nodes around to see smart edges automatically reroute
            3. Notice how smart edges avoid the "Middle Obstacle" node
            """,
        ),
        edge_type_selector,
    ],
    main=[flow],
).servable()
