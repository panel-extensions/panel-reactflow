# Recover from Rendering Errors

A React rendering error is unforgiving: when a component throws during
render, React unmounts the whole subtree.  In a graph editor that means a
single malformed node can blank the canvas, and because the exception dies
in the browser console the server never learns about it.  The user is left
staring at an empty viewport with no way back other than reloading the page,
even though their graph is still safely held in Python.

Panel-ReactFlow wraps the canvas in an error boundary that catches those
errors, tries to bring the view back, and reports what happened to the
server.  This is on by default, controlled by the `error_recovery`
parameter.

```python
from panel_reactflow import ReactFlow

flow = ReactFlow(nodes=nodes, edges=edges, error_recovery="auto")
```

---

## Recovery modes

| Mode       | Behavior |
|------------|----------|
| `"auto"`   | *(default)* Remount the canvas once, then remount again in safe mode.  If it still fails, show the recovery panel. |
| `"manual"` | Report the error and show the recovery panel immediately, without retrying. |
| `"off"`    | Disable the error boundary entirely so exceptions propagate to the browser.  Useful when debugging a custom node component. |

Each retry budget refills once a remounted canvas has survived for five
seconds, so a graph that breaks again much later still gets a fresh set of
attempts rather than going straight to the failure panel.

---

## What safe mode does

On the second attempt the frontend validates the graph before handing it to
React Flow and either repairs or hides anything it cannot render:

| Issue | Action |
|-------|--------|
| `invalid_position` | Position is missing or not finite, so the node is placed at the origin. |
| `unknown_node_type` | Node type is not registered, so the node falls back to the default renderer. |
| `unknown_edge_type` | Edge type is not registered, so the type is stripped. |
| `dangling_edge` | Edge references a node that does not exist, so it is hidden. |
| `duplicate_node_id` / `duplicate_edge_id` | Later duplicates are hidden. |
| `missing_node_id` / `missing_edge_id` / `invalid_node` / `invalid_edge` | The element is hidden. |

Safe mode is **view-only**.  It filters what the browser renders and never
sends a graph mutation back to Python, so `flow.nodes` and `flow.edges` keep
every element they had before the error.  Once the underlying state is
repaired on the server, the affected elements reappear.

A banner tells the user what was changed and offers a details list of the
individual issues:

```text
Safe mode: repaired 1 element and hid 1 element that could not be rendered.
Nothing was deleted on the server.
```

---

## The recovery panel

When retries are exhausted, or in `"manual"` mode, the canvas is replaced by
a panel that names the error and offers three actions: *Try again*, which
remounts the canvas, *Reload page*, which rebuilds the session from the
server-side state, and *Copy details*, which puts a JSON diagnostic blob on
the clipboard for a bug report.

Because Python holds the canonical graph, reloading is genuinely safe: no
work is lost.  The panel says so explicitly, which matters when the
alternative is a user assuming their graph is gone.

---

## Log and handle errors in Python

Every error the frontend catches is reported to the server, logged to the
`panel.reactflow` logger, and emitted as a `client_error` event.

```python
import logging

logging.getLogger("panel.reactflow").setLevel(logging.INFO)

def on_client_error(payload, flow):
    if payload["source"] == "safe_mode":
        print("hidden or repaired:", payload["issues"])
    else:
        print(f"render error on attempt {payload['attempt']}: {payload['message']}")

flow.on("client_error", on_client_error)
```

Render errors carry `name`, `message`, `stack`, `component_stack`, `attempt`,
`mode` and `auto_retry`.  Errors raised inside interaction handlers are
reported with `source="handler"` and the `handler` name, which catches the
case where a drag or connect silently fails and leaves the canvas showing a
change that never reached Python.  Safe mode reports arrive with
`source="safe_mode"` and the list of `issues`.

Use this hook to forward errors to your own telemetry, to snapshot the graph
for later inspection, or to attempt a server-side repair before the user
clicks *Try again*.

---

## Tips

- Keep `error_recovery="auto"` in production; switch to `"off"` while
  developing a custom node component so you see the real stack trace.
- A `client_error` with `source="safe_mode"` is a strong signal that
  something upstream produced invalid state.  Treat it as a bug report
  rather than a warning to be ignored.
- The error boundary only covers the graph canvas.  Content you pass to
  `top_panel`, `bottom_panel`, `left_panel` and `right_panel` stays mounted
  when the canvas fails, so side panels remain usable during recovery.
