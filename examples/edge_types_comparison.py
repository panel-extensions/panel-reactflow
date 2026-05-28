"""
Comparison of standard edges vs smart edges.

This example demonstrates the difference between standard React Flow edges
and smart edges that automatically route around obstacles.
"""

import panel as pn
from panel_reactflow import EdgeSpec, NodeSpec, ReactFlow

pn.extension()

# Create nodes arranged to show edge routing
nodes = [
    # Left column - sources
    NodeSpec(id="s1", position={"x": 0, "y": 0}, label="Source 1").to_dict(),
    NodeSpec(id="s2", position={"x": 0, "y": 100}, label="Source 2").to_dict(),
    NodeSpec(id="s3", position={"x": 0, "y": 200}, label="Source 3").to_dict(),
    NodeSpec(id="s4", position={"x": 0, "y": 300}, label="Source 4").to_dict(),
    # Middle obstacles
    NodeSpec(id="obs1", position={"x": 150, "y": 50}, label="Obstacle 1").to_dict(),
    NodeSpec(id="obs2", position={"x": 150, "y": 150}, label="Obstacle 2").to_dict(),
    NodeSpec(id="obs3", position={"x": 150, "y": 250}, label="Obstacle 3").to_dict(),
    # Right column - targets
    NodeSpec(id="t1", position={"x": 300, "y": 0}, label="Target 1").to_dict(),
    NodeSpec(id="t2", position={"x": 300, "y": 100}, label="Target 2").to_dict(),
    NodeSpec(id="t3", position={"x": 300, "y": 200}, label="Target 3").to_dict(),
    NodeSpec(id="t4", position={"x": 300, "y": 300}, label="Target 4").to_dict(),
]

# Create edges with different types
edges = [
    # Default edge (goes through obstacle)
    EdgeSpec(
        id="e1",
        source="s1",
        target="t2",
        label="default",
        type=None,
        style={"stroke": "#999"},
    ).to_dict(),
    # Smart bezier (routes around obstacle)
    EdgeSpec(
        id="e2",
        source="s2",
        target="t3",
        label="smart_bezier",
        type="smart_bezier",
        style={"stroke": "#3b82f6"},
    ).to_dict(),
    # Smart straight (routes around obstacle)
    EdgeSpec(
        id="e3",
        source="s3",
        target="t1",
        label="smart_straight",
        type="smart_straight",
        style={"stroke": "#10b981"},
    ).to_dict(),
    # Smart step (routes around obstacle)
    EdgeSpec(
        id="e4",
        source="s4",
        target="t4",
        label="smart_step",
        type="smart_step",
        style={"stroke": "#f59e0b"},
    ).to_dict(),
]

# Create the flow
flow = ReactFlow(
    nodes=nodes,
    edges=edges,
    height=600,
    width="100%",
)

# Layout
pn.template.FastListTemplate(
    title="Edge Types Comparison",
    sidebar=[
        pn.pane.Markdown(
            """
            ## Edge Types

            This example compares different edge types:

            - **Gray (default)**: Standard edge that goes straight through obstacles
            - **Blue (smart_bezier)**: Curved edge that routes around obstacles
            - **Green (smart_straight)**: Straight segments that avoid obstacles
            - **Orange (smart_step)**: Step-style edge that avoids obstacles

            ### Try it:
            - Drag the obstacle nodes around
            - Watch how smart edges automatically reroute
            - Notice the default edge doesn't avoid obstacles
            """,
        ),
    ],
    main=[flow],
).servable()
