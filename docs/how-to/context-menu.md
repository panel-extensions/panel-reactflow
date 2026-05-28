# Node Context Menus

Panel-ReactFlow supports per-node context menus that appear on right-click.
Define the menu content by overriding the `context_menu()` method on a `Node`
subclass. The method returns any Panel component, which is rendered as a
floating overlay at the click position.

---

## Define a context menu

Override `context_menu()` on your `Node` subclass to return a Panel component.
The menu is dismissed automatically when the user clicks elsewhere on the
canvas.

```python
import panel as pn
import panel_material_ui as pmui
from panel_reactflow import Node, ReactFlow


class TaskNode(Node):
    def context_menu(self):
        return pn.Column(
            pmui.Button(name="Run", variant="text", size="small"),
            pmui.Button(name="Delete", variant="text", color="error", size="small"),
        )


flow = ReactFlow(nodes=[
    TaskNode(id="t1", position={"x": 0, "y": 0}, label="My Task", data={}),
])
```

---

## Access node state in the menu

The `context_menu()` method runs on the node instance, so you have access to
all its parameters and the parent flow via `self.flow`.

```python
class PipelineNode(Node):
    status = param.Selector(
        default="idle", objects=["idle", "running", "done"], precedence=0
    )

    def context_menu(self):
        def set_status(status):
            self.flow.patch_node_data(self.id, {"status": status})
            # Close the menu after action
            self.flow._handle_msg({"type": "close_context_menu"})

        return pn.Column(
            pn.pane.Markdown(f"**{self.label}** ({self.status})"),
            pmui.Button(
                name="Start", variant="text", size="small",
                on_click=lambda e: set_status("running"),
            ),
            pmui.Button(
                name="Delete", variant="text", color="error", size="small",
                on_click=lambda e: self.flow.remove_node(self.id),
            ),
        )
```

---

## Close the menu programmatically

The context menu closes when the user clicks anywhere on the canvas pane.
To close it from a button callback (e.g. after performing an action), send
the close message:

```python
self.flow._handle_msg({"type": "close_context_menu"})
```

---

## Listen for context menu events

You can also react to the right-click event without rendering a menu by
subscribing to the `"node_context_menu"` event:

```python
def on_context(payload, flow):
    print(f"Right-clicked node {payload['node_id']} at {payload['position']}")

flow.on("node_context_menu", on_context)
```

The payload includes `node_id` and `position` (with `x` and `y` screen
coordinates).

---

## Tips

- Return `None` from `context_menu()` to disable the menu for specific nodes
  (this is the default behavior).
- Only `Node` subclass instances support context menus. Dict-based nodes do
  not trigger a context menu on right-click.
- The menu overlay uses the `.rf-context-menu` CSS class for styling. Override
  it in a custom stylesheet to change appearance.
