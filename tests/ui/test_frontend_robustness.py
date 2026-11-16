"""UI tests for debounced sync, file drops, render counts and hydration merging."""

import base64

import pytest
from panel.tests.util import serve_component, wait_until

from panel_reactflow import ReactFlow, drop_file_bytes

pytest.importorskip("playwright")

from playwright.sync_api import expect

pytestmark = pytest.mark.ui


# A stand-in for React DevTools: counts NodeComponent renders by watching for
# new hook state objects, which React only allocates when a component renders.
# Nodes are recognised by their root element, so this works on minified bundles.
RENDER_COUNTER = """
(() => {
  window.__nodeRenders = 0;
  const seen = new WeakSet();
  const isNode = (f) => f.tag === 0 && f.child && f.child.tag === 5 &&
    f.child.memoizedProps && f.child.memoizedProps.className === "rf-node-content";
  const walk = (root) => {
    const stack = [root];
    while (stack.length) {
      const f = stack.pop();
      if (isNode(f) && f.memoizedState && !seen.has(f.memoizedState)) {
        seen.add(f.memoizedState);
        window.__nodeRenders++;
      }
      if (f.sibling) stack.push(f.sibling);
      if (f.child) stack.push(f.child);
    }
  };
  window.__REACT_DEVTOOLS_GLOBAL_HOOK__ = {
    supportsFiber: true, isDisabled: false, renderers: new Map(),
    inject(r) { const id = this.renderers.size + 1; this.renderers.set(id, r); return id; },
    onScheduleFiberRoot() {}, onCommitFiberUnmount() {}, onPostCommitFiberRoot() {}, checkDCE() {},
    onCommitFiberRoot(_id, root) { try { walk(root.current); } catch (e) {} },
  };
})();
"""

TAKE_RENDERS = "() => { const count = window.__nodeRenders; window.__nodeRenders = 0; return count; }"


def _flow(n=4, **params):
    cols = 4 if n <= 4 else 8
    nodes = [{"id": f"n{i}", "position": {"x": (i % cols) * 200, "y": (i // cols) * 100}, "label": f"Node {i}", "data": {}} for i in range(n)]
    edges = [{"id": "e0", "source": "n0", "target": "n1"}, {"id": "e1", "source": "n2", "target": "n3"}] if n >= 4 else []
    params.setdefault("width", 1000)
    params.setdefault("height", 500)
    params.setdefault("editor_mode", "side")
    return ReactFlow(nodes=nodes, edges=edges, **params)


def _node(page, node_id):
    return page.locator(f'.react-flow__node[data-id="{node_id}"]')


def _select(page, flow, *node_ids):
    _node(page, node_ids[0]).click()
    for node_id in node_ids[1:]:
        _node(page, node_id).click(modifiers=["Shift"])
    wait_until(lambda: sorted(flow.selection["nodes"]) == sorted(node_ids), page)


def _drag(page, node_id, dx, dy, steps=20, release=True):
    box = _node(page, node_id).bounding_box()
    x0, y0 = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x0, y0)
    page.mouse.down()
    for i in range(1, steps + 1):
        page.mouse.move(x0 + dx * i / steps, y0 + dy * i / steps)
    if release:
        page.mouse.up()
    return box


def test_debounce_mode_deletes_every_node_of_a_selection(page):
    flow = _flow(sync_mode="debounce", debounce_ms=150)
    deleted = []
    flow.on("node_deleted", deleted.append)
    serve_component(page, flow)
    expect(page.locator(".react-flow__node")).to_have_count(4)

    _select(page, flow, "n0", "n2")
    page.keyboard.press("Delete")

    wait_until(lambda: sorted(flow._node_id(node) for node in flow.nodes) == ["n1", "n3"], page)
    assert flow.edges == []
    assert deleted


def test_debounce_mode_sends_a_move_for_every_dragged_node(page):
    flow = _flow(sync_mode="debounce", debounce_ms=150)
    serve_component(page, flow)
    expect(page.locator(".react-flow__node")).to_have_count(4)

    _select(page, flow, "n1", "n3")
    _drag(page, "n1", 60, 60)

    wait_until(lambda: flow.nodes[1]["position"]["y"] > 20 and flow.nodes[3]["position"]["y"] > 20, page)
    assert flow.nodes[0]["position"] == {"x": 0, "y": 0}


def _drop_files(locator, files):
    """Drop files given as ``(name, mime_type, bytes)`` on *locator*."""
    return locator.evaluate(
        """(el, files) => {
          const rect = el.getBoundingClientRect()
          const init = (dt) => ({
            dataTransfer: dt, bubbles: true, cancelable: true, composed: true,
            clientX: rect.left + 20, clientY: rect.top + 20,
          })
          const dt = new DataTransfer()
          for (const [name, type, size, fill] of files) {
            dt.items.add(new File([new Uint8Array(size).fill(fill)], name, { type }))
          }
          el.dispatchEvent(new DragEvent("dragover", init(dt)))
          el.dispatchEvent(new DragEvent("drop", init(dt)))
        }""",
        files,
    )


def test_binary_file_drop_is_base64_encoded(page):
    flow = _flow(n=1, drop_types=["Files"])
    drops = []
    flow.on("drop", drops.append)
    serve_component(page, flow)
    pane = page.locator(".react-flow__pane")
    expect(pane).to_be_visible()

    pane.evaluate(
        """(el) => {
          const rect = el.getBoundingClientRect()
          const dt = new DataTransfer()
          dt.items.add(new File([new Uint8Array([0, 255, 1, 2, 137, 80])], "image.png", { type: "image/png" }))
          dt.items.add(new File(["héllo"], "notes.txt", { type: "text/plain" }))
          const init = { dataTransfer: dt, bubbles: true, cancelable: true, composed: true, clientX: rect.left + 5, clientY: rect.top + 5 }
          el.dispatchEvent(new DragEvent("dragover", init))
          el.dispatchEvent(new DragEvent("drop", init))
        }"""
    )

    wait_until(lambda: len(drops) == 1, page)
    image, notes = drops[0]["data"]
    assert image["encoding"] == "base64"
    assert base64.b64decode(image["content"]) == bytes([0, 255, 1, 2, 137, 80])
    assert notes == {"name": "notes.txt", "type": "text/plain", "size": 6, "encoding": "text", "content": "héllo"}
    assert drops[0]["rejected"] == []


def _drop_raw_files(locator, files):
    """Drop files given as ``(name, mime_type, list of byte values)`` on *locator*."""
    return locator.evaluate(
        """(el, files) => {
          const rect = el.getBoundingClientRect()
          const dt = new DataTransfer()
          for (const [name, type, bytes] of files) {
            dt.items.add(new File([new Uint8Array(bytes)], name, { type }))
          }
          const init = { dataTransfer: dt, bubbles: true, cancelable: true, composed: true, clientX: rect.left + 5, clientY: rect.top + 5 }
          el.dispatchEvent(new DragEvent("dragover", init))
          el.dispatchEvent(new DragEvent("drop", init))
        }""",
        [[name, mime, list(data)] for name, mime, data in files],
    )


def test_text_file_with_vendor_mime_type_is_sent_as_text(page):
    # Windows reports .csv files as application/vnd.ms-excel.
    flow = _flow(n=1, drop_types=["Files"])
    drops = []
    flow.on("drop", drops.append)
    serve_component(page, flow)
    pane = page.locator(".react-flow__pane")
    expect(pane).to_be_visible()

    csv = "name,city\nZoë,Köln\n".encode()
    _drop_raw_files(
        pane,
        [
            ("data.csv", "application/vnd.ms-excel", csv),
            ("notes.cfg", "application/octet-stream", b"key = value"),
        ],
    )

    wait_until(lambda: len(drops) == 1, page)
    data_csv, notes = drops[0]["data"]
    assert data_csv["encoding"] == "text"
    assert data_csv["content"] == "name,city\nZoë,Köln\n"
    assert notes["encoding"] == "text"
    assert notes["content"] == "key = value"
    assert drop_file_bytes(data_csv) == csv


def test_file_with_text_mime_type_that_is_not_utf8_is_base64_encoded(page):
    flow = _flow(n=1, drop_types=["Files"])
    drops = []
    flow.on("drop", drops.append)
    serve_component(page, flow)
    pane = page.locator(".react-flow__pane")
    expect(pane).to_be_visible()

    latin1 = "Köln".encode("latin-1")
    _drop_raw_files(pane, [("latin1.csv", "text/csv", latin1), ("blob", "", bytes([0xFF, 0xFE, 0x00]))])

    wait_until(lambda: len(drops) == 1, page)
    csv_file, blob = drops[0]["data"]
    assert csv_file["encoding"] == "base64"
    assert drop_file_bytes(csv_file) == latin1
    assert blob["encoding"] == "base64"
    assert drop_file_bytes(blob) == bytes([0xFF, 0xFE, 0x00])


def test_non_file_drop_limit_counts_utf8_bytes(page):
    flow = _flow(n=1, drop_types=["application/x-test"], max_drop_size=10)
    drops = []
    flow.on("drop", drops.append)
    serve_component(page, flow)
    pane = page.locator(".react-flow__pane")
    expect(pane).to_be_visible()

    def drop(raw):
        pane.evaluate(
            """(el, raw) => {
              const rect = el.getBoundingClientRect()
              const dt = new DataTransfer()
              dt.setData("application/x-test", raw)
              const init = { dataTransfer: dt, bubbles: true, cancelable: true, composed: true, clientX: rect.left + 5, clientY: rect.top + 5 }
              el.dispatchEvent(new DragEvent("dragover", init))
              el.dispatchEvent(new DragEvent("drop", init))
            }""",
            raw,
        )

    # Seven characters, but fourteen bytes in UTF-8.
    drop("ééééééé")
    wait_until(lambda: len(drops) == 1, page)
    assert drops[0]["data"] is None
    [rejected] = drops[0]["rejected"]
    assert rejected["size"] == 14

    drop("abcdefghij")
    wait_until(lambda: len(drops) == 2, page)
    assert drops[1]["data"] == "abcdefghij"
    assert drops[1]["rejected"] == []


def test_oversized_file_drop_is_rejected_without_losing_the_session(page):
    flow = _flow(n=1, drop_types=["Files"], max_drop_size=1000)
    drops = []
    flow.on("drop", drops.append)
    serve_component(page, flow)
    pane = page.locator(".react-flow__pane")
    expect(pane).to_be_visible()

    _drop_files(pane, [["small.csv", "text/csv", 10, 65], ["big.csv", "text/csv", 5000, 65]])

    wait_until(lambda: len(drops) == 1, page)
    assert [f["name"] for f in drops[0]["data"]] == ["small.csv"]
    assert drops[0]["data"][0]["content"] == "A" * 10
    [rejected] = drops[0]["rejected"]
    assert rejected["name"] == "big.csv"
    assert rejected["size"] == 5000
    assert "limit" in rejected["reason"]

    # The session is still alive and keeps syncing.
    _drop_files(pane, [["again.csv", "text/csv", 3, 66]])
    wait_until(lambda: len(drops) == 2, page)
    assert drops[1]["data"][0]["content"] == "BBB"


def test_default_drop_limit_keeps_large_drops_off_the_websocket(page):
    flow = _flow(n=1, drop_types=["Files"])
    drops = []
    flow.on("drop", drops.append)
    serve_component(page, flow)
    pane = page.locator(".react-flow__pane")
    expect(pane).to_be_visible()

    _drop_files(pane, [["huge.csv", "text/csv", 25 * 1024 * 1024, 65]])
    wait_until(lambda: len(drops) == 1, page, timeout=20000)
    assert drops[0]["data"] == []
    assert drops[0]["rejected"][0]["name"] == "huge.csv"

    _drop_files(pane, [["small.csv", "text/csv", 2, 65]])
    wait_until(lambda: len(drops) == 2, page)


def test_unreadable_file_does_not_discard_the_rest_of_the_drop(page):
    flow = _flow(n=1, drop_types=["Files"])
    drops = []
    flow.on("drop", drops.append)
    serve_component(page, flow)
    pane = page.locator(".react-flow__pane")
    expect(pane).to_be_visible()

    # Chromium hands the handler the same File objects, so one can be made to
    # fail the way a dropped directory does.
    pane.evaluate(
        """(el) => {
          const rect = el.getBoundingClientRect()
          const dt = new DataTransfer()
          dt.items.add(new File(["ok"], "ok.txt", { type: "text/plain" }))
          dt.items.add(new File(["x"], "folder", { type: "" }))
          const files = dt.files
          const proto = Object.getPrototypeOf(files[1])
          const original = proto.arrayBuffer
          proto.arrayBuffer = function () {
            return this.name === "folder" ? Promise.reject(new Error("NotReadableError")) : original.call(this)
          }
          const init = { dataTransfer: dt, bubbles: true, cancelable: true, composed: true, clientX: rect.left + 5, clientY: rect.top + 5 }
          el.dispatchEvent(new DragEvent("dragover", init))
          el.dispatchEvent(new DragEvent("drop", init))
        }"""
    )

    wait_until(lambda: len(drops) == 1, page)
    assert [f["name"] for f in drops[0]["data"]] == ["ok.txt"]
    assert [(f["name"], f["reason"]) for f in drops[0]["rejected"]] == [("folder", "NotReadableError")]


def test_dragging_and_zooming_do_not_rerender_every_node(page):
    page.add_init_script(RENDER_COUNTER)
    flow = _flow(n=40, width=1200, height=800)
    serve_component(page, flow)
    expect(page.locator(".react-flow__node")).to_have_count(40)
    page.wait_for_timeout(500)
    page.evaluate(TAKE_RENDERS)

    _drag(page, "n0", 0, 40, steps=20)
    wait_until(lambda: flow.nodes[0]["position"]["y"] > 0, page)
    page.wait_for_timeout(300)
    # One render per drag step of the dragged node, not one per node per step.
    assert page.evaluate(TAKE_RENDERS) < 40

    box = page.locator(".react-flow__pane").bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    for _ in range(5):
        page.mouse.wheel(0, -100)
        page.wait_for_timeout(30)
    page.wait_for_timeout(500)
    assert page.evaluate(TAKE_RENDERS) == 0

    flow.patch_node_data("n5", {"value": 1})
    page.wait_for_timeout(500)
    assert page.evaluate(TAKE_RENDERS) <= 2

    flow.add_node({"id": "extra", "position": {"x": 0, "y": 300}, "label": "extra", "data": {}})
    expect(page.locator(".react-flow__node")).to_have_count(41)
    page.wait_for_timeout(500)
    assert page.evaluate(TAKE_RENDERS) < 10


def test_python_update_mid_drag_does_not_snap_node_back(page):
    flow = _flow()
    serve_component(page, flow)
    expect(page.locator(".react-flow__node")).to_have_count(4)

    start = _drag(page, "n0", 60, 60, release=False)
    flow.add_node({"id": "late", "position": {"x": 0, "y": 300}, "label": "late", "data": {}})
    expect(page.locator(".react-flow__node")).to_have_count(5)
    page.wait_for_timeout(300)

    during = _node(page, "n0").bounding_box()
    page.mouse.up()
    assert during["x"] > start["x"] + 40
    assert during["y"] > start["y"] + 40
    wait_until(lambda: flow.nodes[0]["position"]["x"] > 30, page)


def test_python_can_select_nodes(page):
    flow = _flow()
    serve_component(page, flow)
    expect(page.locator(".react-flow__node")).to_have_count(4)

    nodes = [dict(node) for node in flow.nodes]
    nodes[2]["selected"] = True
    flow.nodes = nodes

    wait_until(lambda: "selected" in (_node(page, "n2").get_attribute("class") or ""), page)
    wait_until(lambda: flow.selection["nodes"] == ["n2"], page)

    # A Python push that does not touch selection keeps the user's selection.
    _node(page, "n1").click()
    wait_until(lambda: flow.selection["nodes"] == ["n1"], page)
    flow.add_node({"id": "late", "position": {"x": 0, "y": 300}, "label": "late", "data": {}})
    expect(page.locator(".react-flow__node")).to_have_count(5)
    page.wait_for_timeout(300)
    assert "selected" in (_node(page, "n1").get_attribute("class") or "")
    assert "selected" not in (_node(page, "n2").get_attribute("class") or "")


def test_malformed_node_from_server_degrades_to_safe_mode(page):
    flow = _flow(n=2, sizing_mode="stretch_both", width=None, height=None)
    serve_component(page, flow)
    expect(page.locator(".react-flow__node")).to_have_count(2)

    # Python validates nodes on assignment, so push a malformed list straight
    # to the Bokeh model as a stale or buggy server state would.
    doc = next(iter(flow._documents))
    model = next(iter(flow._models.values()))[0]
    valid = flow.nodes[1]

    def push():
        model.data.nodes = [None, valid]

    doc.add_next_tick_callback(push)
    expect(page.locator(".rf-safe-mode-banner")).to_be_visible(timeout=20000)
    expect(page.locator(".react-flow__node")).to_have_count(1)
