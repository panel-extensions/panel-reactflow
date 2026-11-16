import React from "react";
import { Background, BezierEdge, Controls, Handle, MiniMap, NodeToolbar, Panel, Position, ReactFlow, ReactFlowProvider, SimpleBezierEdge, SmoothStepEdge, StraightEdge, StepEdge, addEdge, useEdgesState, useInternalNode, useNodes, useNodesState, useReactFlow, useStore, useStoreApi, BaseEdge, getBezierPath, getSmoothStepPath, getStraightPath } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { SmartBezierEdge, SmartStraightEdge, SmartStepEdge } from "@tisoap/react-flow-smart-edge";

const { useCallback, useEffect, useMemo, useRef, useState } = React;

const BUILTIN_NODE_TYPES = {
  panel: { label: "Panel" },
  default: { label: "Default" },
  minimal: { label: "Minimal", minimal: true },
};

const viewWrapperClassName = "rf-node-view-wrapper rf-node-view-wrapper--bokeh-scale nodrag nopan nowheel";

// Recovery: attempt 1 remounts the flow as-is, attempt 2 remounts it in safe
// mode with an invalid graph elements dropped from the view. Beyond that we
// stop retrying and hand control to the user.
const SAFE_MODE_ATTEMPT = 2;
const MAX_RECOVERY_ATTEMPTS = 2;
const RETRY_DELAY_MS = 100;
// How long a remounted flow must survive before its retry budget is refilled.
const HEALTHY_RESET_MS = 5000;
const HOVER_CLOSE_GRACE_MS = 150;
const CONNECTION_VALIDATION_TIMEOUT_MS = 3000;
const popupHoverState = { inside: false, closeTimers: new Set() };
const ConnectionValidationContext = React.createContext(null);

function connectionKey(nodeId, handleId, handleType) {
  return JSON.stringify([nodeId, handleId ?? null, handleType]);
}

function getHandle(spec, direction, id) {
  const handles = spec?.[direction === "source" ? "outputs" : "inputs"];
  if (handles == null) return id == null ? {} : null;
  return handles.find((handle) => (typeof handle === "string" ? handle : handle.id) === id) ?? null;
}

const graphIndexCache = new WeakMap();

// Lookups the connection policies need per candidate handle. Built once per
// nodes/edges snapshot, so a drag evaluating every handle in the graph does
// not rescan the edges (and rerun a BFS) for each handle.
function graphIndex(nodes, edges) {
  let byNodes = graphIndexCache.get(edges);
  if (!byNodes) {
    byNodes = new WeakMap();
    graphIndexCache.set(edges, byNodes);
  }
  let index = byNodes.get(nodes);
  if (index) return index;
  const successors = new Map();
  const predecessors = new Map();
  const edgeKeys = new Set();
  const targetCounts = new Map();
  edges.forEach((edge) => {
    if (!successors.has(edge.source)) successors.set(edge.source, []);
    successors.get(edge.source).push(edge.target);
    if (!predecessors.has(edge.target)) predecessors.set(edge.target, []);
    predecessors.get(edge.target).push(edge.source);
    edgeKeys.add(JSON.stringify([edge.source, edge.target, edge.sourceHandle ?? null, edge.targetHandle ?? null]));
    const targetKey = JSON.stringify([edge.target, edge.targetHandle ?? null]);
    targetCounts.set(targetKey, (targetCounts.get(targetKey) || 0) + 1);
  });
  const reachCache = new Map();
  const reachable = (start, adjacency, tag) => {
    const key = `${tag}:${start}`;
    let visited = reachCache.get(key);
    if (visited) return visited;
    visited = new Set();
    const queue = [start];
    while (queue.length) {
      const current = queue.pop();
      if (visited.has(current)) continue;
      visited.add(current);
      queue.push(...(adjacency.get(current) || []));
    }
    reachCache.set(key, visited);
    return visited;
  };
  index = {
    byId: new Map(nodes.map((node) => [node.id, node])),
    edgeKeys,
    targetCounts,
    // Reaching the fixed end of the drag reuses one traversal for every candidate.
    wouldCycle: (sourceId, targetId, pinned) => (pinned === "source"
      ? reachable(sourceId, predecessors, "up").has(targetId)
      : reachable(targetId, successors, "down").has(sourceId)),
  };
  byNodes.set(nodes, index);
  return index;
}

function localConnectionReason(connection, nodes, edges, nodeTypeSpecs, policy, pinned) {
  if (!Object.values(policy || {}).some(Boolean)) return null;
  const index = graphIndex(nodes, edges);
  const source = index.byId.get(connection.source);
  const target = index.byId.get(connection.target);
  if (!source || !target) return "Node not found";
  const sourceHandle = getHandle(nodeTypeSpecs[source.type], "source", connection.sourceHandle);
  const targetHandle = getHandle(nodeTypeSpecs[target.type], "target", connection.targetHandle);
  if (policy.direction && (!sourceHandle || !targetHandle)) return "Connect an output to an input";
  if (policy.cycles) {
    if (source.id === target.id) return "Connection would create a cycle";
    if (index.wouldCycle(source.id, target.id, pinned)) return "Connection would create a cycle";
  }
  if (policy.duplicates && index.edgeKeys.has(JSON.stringify([
    source.id, target.id, connection.sourceHandle ?? null, connection.targetHandle ?? null,
  ]))) return "Connection already exists";
  if (policy.capacity && targetHandle && typeof targetHandle === "object") {
    const limit = targetHandle.maxConnections;
    if (Number.isInteger(limit) && limit > 0 &&
      (index.targetCounts.get(JSON.stringify([target.id, connection.targetHandle ?? null])) || 0) >= limit
    ) return "Input already has a connection";
  }
  if (policy.types && sourceHandle && targetHandle && typeof sourceHandle === "object" && typeof targetHandle === "object") {
    const sourceType = sourceHandle.type;
    const targetType = targetHandle.type;
    if (sourceType && targetType && String(sourceType).toLowerCase() !== String(targetType).toLowerCase()) {
      return `Type mismatch: ${sourceType} → ${targetType}`;
    }
  }
  return null;
}

function schedulePopupClose(callback) {
  const timer = setTimeout(() => {
    popupHoverState.closeTimers.delete(timer);
    if (!popupHoverState.inside) {
      callback();
    }
  }, HOVER_CLOSE_GRACE_MS);
  popupHoverState.closeTimers.add(timer);
}

function enterValuePopup() {
  popupHoverState.inside = true;
  popupHoverState.closeTimers.forEach((timer) => clearTimeout(timer));
  popupHoverState.closeTimers.clear();
}

function leaveValuePopup() {
  popupHoverState.inside = false;
}

const figureStylesheet = `
.bk-Canvas {
  transform: scale(var(--rf-inverse-zoom));
  transform-origin: top left;
  width: calc(var(--rf-zoom) * 100%);
  height: calc(var(--rf-zoom) * 100%);
}`.trim();

function isInsideValuePopup(x, y) {
  const popup = document.querySelector(".rf-value-popup");
  if (!popup) {
    return false;
  }
  const rect = popup.getBoundingClientRect();
  return x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom;
}

function popupCloseDistance(event) {
  const frame = event.currentTarget.closest(".react-flow")?.getBoundingClientRect();
  return Math.max(48, Math.min(frame?.width ?? 0, frame?.height ?? 0) * 0.1);
}

// The DataTransfer type browsers report for files dragged in from the OS.
const FILES_DROP_TYPE = "Files";

// Types that are never text, so their bytes skip the UTF-8 decode attempt.
const BINARY_MIME_PATTERN = /^(image\/(?!svg\+xml$)|audio\/|video\/|font\/)|^application\/(pdf|zip|gzip|x-gzip|x-tar|x-bzip2|x-7z-compressed|x-rar-compressed|wasm)$/;

function bytesToBase64(bytes) {
  const chunks = [];
  for (let i = 0; i < bytes.length; i += 0x8000) {
    chunks.push(String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000)));
  }
  return btoa(chunks.join(""));
}

async function readDroppedFile(file) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  // The reported MIME type is unreliable (Windows reports .csv as
  // application/vnd.ms-excel), so any file that decodes cleanly as UTF-8 is
  // sent as text and only the rest base64 encoded, which also keeps binaries
  // from being mangled by replacement characters.
  if (!BINARY_MIME_PATTERN.test(file.type)) {
    try {
      const content = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
      return { name: file.name, type: file.type, size: file.size, encoding: "text", content };
    } catch (_error) {
      // Not valid UTF-8, fall through to base64.
    }
  }
  return { name: file.name, type: file.type, size: file.size, encoding: "base64", content: bytesToBase64(bytes) };
}

/**
 * Read dropped OS files, keeping the total payload under `maxSize` bytes so a
 * large drop cannot exceed the websocket message limit and drop the session.
 * Files that are too large or cannot be read (e.g. directories) are reported
 * in `rejected` instead of failing the whole drop.
 */
async function readDroppedFiles(files, maxSize) {
  const list = Array.from(files);
  const accepted = [];
  const rejected = [];
  let budget = Number.isFinite(maxSize) && maxSize > 0 ? maxSize : Infinity;
  list.forEach((file) => {
    const meta = { name: file.name, type: file.type, size: file.size };
    if (file.size > budget) {
      rejected.push({ ...meta, reason: `File exceeds the remaining drop size limit of ${budget} bytes` });
    } else {
      budget -= file.size;
      accepted.push(file);
    }
  });
  const results = await Promise.allSettled(accepted.map(readDroppedFile));
  const data = [];
  results.forEach((result, index) => {
    if (result.status === "fulfilled") {
      data.push(result.value);
    } else {
      const file = accepted[index];
      rejected.push({
        name: file.name, type: file.type, size: file.size,
        reason: String(result.reason?.message ?? result.reason ?? "File could not be read"),
      });
    }
  });
  return { data, rejected };
}

// Resolve the node or handle under a drop so a handler can wire what was dropped.
function dropTarget(element) {
  const handle = element?.closest?.(".react-flow__handle");
  if (handle) {
    return {
      node_id: handle.dataset.nodeid,
      handle_id: handle.dataset.handleid || null,
      direction: handle.classList.contains("target") ? "input" : "output",
    };
  }
  const node = element?.closest?.(".react-flow__node");
  return node ? { node_id: node.dataset.id, handle_id: null, direction: null } : null;
}

function renderHandles(direction, handles, opts = {}) {
  const handleType = direction === "input" ? "target" : "source";
  const position = direction === "input" ? Position.Left : Position.Right;
  const tooltipPos = direction === "input" ? "left" : "right";
  const onHandleClick = opts.onHandleClick;
  const onHandleHover = opts.onHandleHover;
  const validation = opts.validation;
  const cannotReceive = validation?.drag && validation.drag.handleType !== handleType &&
    (opts.connectable === false || opts.connectableEnd === false);

  const makeHoverHandler = (id, eventType) =>
    onHandleHover
      ? (event) => onHandleHover(id, direction, eventType, event)
      : undefined;

  const makeClickHandler = (id) =>
    onHandleClick
      ? (event) => {
          // A handle click also bubbles to the node underneath, which would
          // otherwise select/drag it instead of opening the value popup.
          event.stopPropagation();
          onHandleClick(id, direction, event);
        }
      : undefined;

  // Build handle props from opts, only including defined values
  const handleProps = {};
  if (opts.connectable !== undefined) {
    handleProps.isConnectable = opts.connectable;
  }
  if (opts.connectableStart !== undefined) {
    handleProps.isConnectableStart = opts.connectableStart;
  }
  if (opts.connectableEnd !== undefined) {
    handleProps.isConnectableEnd = opts.connectableEnd;
  }

  // Explicitly empty array → no handles
  if (Array.isArray(handles) && handles.length === 0) {
    return null;
  }
  // null/undefined → default handle
  if (!handles?.length) {
    const reason = cannotReceive ? "Handle cannot accept connections" : validation?.reasonForHandle(opts.nodeId, null, handleType);
    return (
      <Handle
        type={handleType}
        position={position}
        onClick={makeClickHandler(null)}
        onPointerEnter={makeHoverHandler(null, "enter")}
        onPointerLeave={makeHoverHandler(null, "leave")}
        className={reason === "Checking connections..." ? "rf-handle-pending" : reason ? "rf-handle-invalid" : validation?.drag && validation.drag.handleType !== handleType ? "rf-handle-valid" : undefined}
        title={reason || undefined}
        aria-label={reason || `${handleType} handle`}
        {...handleProps}
      />
    );
  }
  const spacing = 100 / (handles.length + 1);
  return handles.map((handle, index) => {
    const id = typeof handle === "string" ? handle : handle.id;
    const label = typeof handle === "object" ? handle.label : undefined;
    const dtype = typeof handle === "object" ? handle.type : undefined;
    const tooltip = label && dtype ? `${label} (${dtype})` : label || dtype;
    const reason = cannotReceive ? "Handle cannot accept connections" : validation?.reasonForHandle(opts.nodeId, id, handleType);
    return (
      <Handle
        key={`${direction}-${id}`}
        id={id}
        type={handleType}
        position={position}
        style={{ top: `${(index + 1) * spacing}%` }}
        {...(reason || tooltip ? {"data-tooltip": reason || tooltip, "data-tooltip-pos": tooltipPos} : {})}
        className={reason === "Checking connections..." ? "rf-handle-pending" : reason ? "rf-handle-invalid" : validation?.drag && validation.drag.handleType !== handleType ? "rf-handle-valid" : undefined}
        aria-label={reason || tooltip || id}
        onClick={makeClickHandler(id)}
        onPointerEnter={makeHoverHandler(id, "enter")}
        onPointerLeave={makeHoverHandler(id, "leave")}
        {...handleProps}
      />
    );
  });
}

// Node/NodeSpec serialize per-node flags, which override the canvas-wide
// nodesDraggable/nodesConnectable in React Flow.
const READ_ONLY_NODE_FLAGS = { draggable: false, connectable: false, deletable: false };

function currentZoom(storeApi) {
  const zoom = storeApi.getState().transform?.[2];
  return Number.isFinite(zoom) && zoom > 0 ? zoom : 1;
}

function isFigureModel(modelNode) {
  const typeName = String(modelNode?.type || modelNode?.name || modelNode?.constructor?.__name__ || "");
  return typeName === "Figure" || typeName.endsWith(".Figure");
}

// Waits until the child view exists, giving up after maxTries * interval ms or
// once the caller is cancelled.
async function resolveChildView(viewInstance, childModel, isCancelled, maxTries = 50, interval = 20) {
  for (let tries = 0; tries < maxTries && !isCancelled(); tries++) {
    try {
      const result = await Promise.resolve(viewInstance._child_views.get(childModel));
      if (result) {
        return result;
      }
    } catch (_error) {
      // The child view is not registered yet, try again.
    }
    await new Promise((resolve) => setTimeout(resolve, interval));
  }
  return null;
}

/**
 * Inject the zoom compensating stylesheet into every Bokeh figure rendered
 * inside the node view with id `viewKey`. `onInject` runs before the first
 * injection so the wrapper's CSS variables can capture the current zoom.
 */
async function applyFigureStyles(viewKey, isCancelled, onInject) {
  const found = globalThis.Bokeh?.index?.find_by_id?.(viewKey);
  const views = found ? [...found] : [];
  const visited = new Set();

  const walkSubView = async (subView) => {
    if (isCancelled()) {
      return;
    }
    const modelNode = subView?.model;
    const modelId = String(modelNode?.id ?? "");
    if (!modelId || visited.has(modelId)) {
      return;
    }
    visited.add(modelId);

    if (isFigureModel(modelNode)) {
      const stylesheets = Array.isArray(modelNode.stylesheets) ? modelNode.stylesheets : [];
      const alreadyInjected = stylesheets.some(
        (entry) => typeof entry === "string" && entry.includes("scale(var(--rf-inverse-zoom))"),
      );
      if (!alreadyInjected) {
        onInject();
        modelNode.stylesheets = [...stylesheets, figureStylesheet];
      }
      return;
    }

    const childModels = Object.values(subView?.child_models || {}).filter(Boolean);
    for (const childModel of childModels) {
      const childSubView = await resolveChildView(subView, childModel, isCancelled);
      if (childSubView) {
        await walkSubView(childSubView);
      }
    }
  };

  for (const view of views) {
    await walkSubView(view);
  }
}

function makeNodeComponent(typeName, typeSpec, editorMode, model, valuePopupTrigger, hoverDelay) {
  return function NodeComponent({ id, data, isConnectable }) {
    const validation = React.useContext(ConnectionValidationContext);
    const [toolbarOpen, toggleToolbar] = React.useState(false);
    const hoverTimerRef = useRef(null);
    const hoverCleanupRef = useRef(null);
    const onHandleHover = useCallback(
      (handleId, direction, eventType, event) => {
        if (valuePopupTrigger !== "hover") {
          return;
        }
        if (eventType === "leave") {
          if (hoverTimerRef.current) {
            clearTimeout(hoverTimerRef.current);
            hoverTimerRef.current = null;
          }
          return;
        }
        const position = { x: event.clientX, y: event.clientY };
        const closeDistance = popupCloseDistance(event);
        if (hoverTimerRef.current) {
          clearTimeout(hoverTimerRef.current);
        }
        if (hoverCleanupRef.current) {
          hoverCleanupRef.current();
        }
        const target = { node_id: id, handle_id: handleId, direction };
        const cleanup = () => {
          if (hoverTimerRef.current) {
            clearTimeout(hoverTimerRef.current);
            hoverTimerRef.current = null;
          }
          if (hoverCleanupRef.current === cleanup) {
            hoverCleanupRef.current = null;
          }
          document.removeEventListener("pointermove", onPointerMove, true);
        };
        const onPointerMove = (moveEvent) => {
          const dx = moveEvent.clientX - position.x;
          const dy = moveEvent.clientY - position.y;
          if (isInsideValuePopup(moveEvent.clientX, moveEvent.clientY)) {
            return;
          }
          if (dx * dx + dy * dy >= closeDistance * closeDistance) {
            schedulePopupClose(() => {
              model.send_msg({ type: "handle_unhovered", ...target });
              cleanup();
            });
          }
        };
        hoverCleanupRef.current = cleanup;
        hoverTimerRef.current = setTimeout(() => {
          hoverTimerRef.current = null;
          model.send_msg({ type: "handle_hovered", ...target, position });
          document.addEventListener("pointermove", onPointerMove, true);
        }, hoverDelay);
      },
      [id, valuePopupTrigger, hoverDelay],
    );
    const onHandleClick = useCallback(
      (handleId, direction, event) => {
        model.send_msg({
          type: "handle_clicked",
          node_id: id,
          handle_id: handleId,
          direction,
          position: { x: event.clientX, y: event.clientY },
        });
      },
      [id],
    );
    const storeApi = useStoreApi();
    const spec = typeSpec || {};
    const hasEditor = data?._hasEditor;
    useEffect(() => {
      if (!hasEditor) {
        toggleToolbar(false);
      }
    }, [hasEditor]);
    const showGear = editorMode === "toolbar" && hasEditor;
    const showToolbar = editorMode === "toolbar" && toolbarOpen && hasEditor;
    const showInlineEditor = editorMode === "node" && hasEditor;
    const showView = data?.view && !spec.minimal;

    const displayLabel = data?._label ?? spec.label ?? typeName;
    // The zoom is read from the store instead of subscribed to, otherwise every
    // node re-renders on every zoom step. It only matters at the moment a
    // figure's stylesheet is injected.
    const [figureZoom, setFigureZoom] = useState(() => currentZoom(storeApi));

    const viewWrapperStyle = useMemo(() => ({
      "--rf-inverse-zoom": 1 / figureZoom,
      "--rf-zoom": figureZoom,
    }), [figureZoom]);

    const viewKey = data?.view?.key;
    const viewReady = data?._viewReady;
    useEffect(() => {
      if (viewKey == null) {
        return undefined;
      }
      let cancelled = false;
      applyFigureStyles(viewKey, () => cancelled, () => setFigureZoom(currentZoom(storeApi)))
        .catch((error) => console.warn("[panel-reactflow] failed to style node figure", error));
      return () => {
        cancelled = true;
      };
    }, [viewKey, viewReady, data, storeApi]);

    const handleGearClick = (e) => {
      e.stopPropagation();
      if (!toolbarOpen && !data.editor) {
        // Editors are built on demand, the first time a toolbar is opened.
        model.send_msg({ type: "editor_requested", node_id: id });
      }
      toggleToolbar((v) => !v);
    };

    return (
      <div className="rf-node-content">
        {showGear ? (
          <NodeToolbar isVisible={showToolbar} position={Position.Top} style={{ background: "white" }}>
            {data.editor}
          </NodeToolbar>
        ) : null}
        {showGear && (
          <button
            aria-label={showToolbar ? "Hide node toolbar" : "Show node toolbar"}
            onClick={handleGearClick}
            className={`rf-node-toolbar-button ${showToolbar ? "rf-node-toolbar-button--open" : "rf-node-toolbar-button--closed"}`}
            tabIndex={0}
            type="button"
            title={showToolbar ? "Hide node toolbar" : "Show node toolbar"}
          >
            <img
              src={import.meta.url.replace(/(\/[^\/?#]+)?(\?.*)?$/, "/icons/gear.svg")}
              alt=""
              width={14}
              height={14}
              aria-hidden="true"
              className={`rf-node-toolbar-icon ${showToolbar ? "rf-node-toolbar-icon--open" : "rf-node-toolbar-icon--closed"}`}
            />
          </button>
        )}
        {renderHandles("input", spec.inputs, {
          nodeId: id,
          validation,
          connectable: isConnectable === false ? false : spec.inputConnectable,
          connectableStart: spec.inputConnectableStart,
          connectableEnd: spec.inputConnectableEnd,
          onHandleClick,
          onHandleHover,
        })}
        <div className="rf-node-label" style={{ fontWeight: 600, margin: displayLabel ? "0.2em 0 0.5em 0.5em" : "0" }}>
          {displayLabel}
        </div>
        {(showView || showInlineEditor) && (
          <div>
            {showView ? <div className={viewWrapperClassName} style={viewWrapperStyle}>{data.view}</div> : null}
            {showInlineEditor ? <div className="rf-node-inline-editor nodrag">{data.editor}</div> : null}
          </div>
        )}
        {renderHandles("output", spec.outputs, {
          nodeId: id,
          validation,
          connectable: isConnectable === false ? false : spec.outputConnectable,
          connectableStart: spec.outputConnectableStart,
          connectableEnd: spec.outputConnectableEnd,
          onHandleClick,
          onHandleHover,
        })}
      </div>
    );
  };
}



const SELF_LOOP_OFFSET = 40;
const SELF_LOOP_REACH = 30;
const SELF_LOOP_SPACING = 20;

const POSITION_VECTORS = {
  [Position.Left]: [-1, 0],
  [Position.Right]: [1, 0],
  [Position.Top]: [0, -1],
  [Position.Bottom]: [0, 1],
};

function point(x, y) {
  return `${x},${y}`;
}

/**
 * Build a loop that leaves the source handle and re-enters the same node at
 * the target handle, keeping the curve outside the node body. `index` stacks
 * multiple loops on one node.
 */
function getSelfLoopPath({ sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition, bounds, index = 0 }) {
  const offset = SELF_LOOP_OFFSET + index * SELF_LOOP_SPACING;
  const reach = SELF_LOOP_REACH + index * SELF_LOOP_SPACING / 2;
  const [sdx, sdy] = POSITION_VECTORS[sourcePosition] ?? [1, 0];
  const [tdx, tdy] = POSITION_VECTORS[targetPosition] ?? [-1, 0];
  const s1 = [sourceX + sdx * reach, sourceY + sdy * reach];
  const t1 = [targetX + tdx * reach, targetY + tdy * reach];
  const outside = (axisDir, start, size) => (axisDir > 0 ? start + size + offset : start - offset);
  if (sdx && tdx && sdx === -tdx) {
    const labelX = bounds.x + bounds.width / 2;
    const labelY = bounds.y - offset;
    const path = `M ${point(sourceX, sourceY)} C ${point(...s1)} ${point(s1[0], labelY)} ${point(labelX, labelY)}` +
      ` C ${point(t1[0], labelY)} ${point(...t1)} ${point(targetX, targetY)}`;
    return { path, labelX, labelY };
  }
  if (sdy && tdy && sdy === -tdy) {
    const labelX = bounds.x + bounds.width + offset;
    const labelY = bounds.y + bounds.height / 2;
    const path = `M ${point(sourceX, sourceY)} C ${point(...s1)} ${point(labelX, s1[1])} ${point(labelX, labelY)}` +
      ` C ${point(labelX, t1[1])} ${point(...t1)} ${point(targetX, targetY)}`;
    return { path, labelX, labelY };
  }
  if (sdx === tdx && sdy === tdy) {
    // Splay the ends apart along the side, source away from target, so the
    // curve forms a loop rather than a line or a knot.
    const [ax, ay] = [Math.abs(sdy), Math.abs(sdx)];
    const sign = (sourceX - targetX) * ax + (sourceY - targetY) * ay >= 0 ? 1 : -1;
    const spread = Math.max(reach, Math.abs((sourceX - targetX) * ax + (sourceY - targetY) * ay) / 2);
    const c1 = [sourceX + sdx * 2 * reach + ax * sign * spread, sourceY + sdy * 2 * reach + ay * sign * spread];
    const c2 = [targetX + tdx * 2 * reach - ax * sign * spread, targetY + tdy * 2 * reach - ay * sign * spread];
    const path = `M ${point(sourceX, sourceY)} C ${point(...c1)} ${point(...c2)} ${point(targetX, targetY)}`;
    return {
      path,
      labelX: (sourceX + 3 * c1[0] + 3 * c2[0] + targetX) / 8,
      labelY: (sourceY + 3 * c1[1] + 3 * c2[1] + targetY) / 8,
    };
  }
  // Adjacent sides route through the shared corner. Each half's control
  // points stay beyond the side its handle sits on, so neither crosses the body.
  const labelX = outside(sdx || tdx, bounds.x, bounds.width);
  const labelY = outside(sdy || tdy, bounds.y, bounds.height);
  const [ux, uy] = [tdx - sdx, tdy - sdy];
  const k = offset / 2;
  const path = `M ${point(sourceX, sourceY)} C ${point(...s1)} ${point(labelX - ux * k, labelY - uy * k)} ${point(labelX, labelY)}` +
    ` C ${point(labelX + ux * k, labelY + uy * k)} ${point(...t1)} ${point(targetX, targetY)}`;
  return { path, labelX, labelY };
}

const SelfLoopEdge = React.memo(function SelfLoopEdge({
  id,
  source,
  sourceX,
  sourceY,
  sourcePosition,
  targetX,
  targetY,
  targetPosition,
  label,
  labelStyle,
  labelShowBg,
  labelBgStyle,
  labelBgPadding,
  labelBgBorderRadius,
  style,
  markerStart,
  markerEnd,
  interactionWidth,
}) {
  const node = useInternalNode(source);
  const index = useStore((state) => {
    let count = 0;
    for (const edge of state.edges) {
      if (edge.id === id) return count;
      if (edge.source === source && edge.target === source && !edge.hidden) count++;
    }
    return 0;
  });
  const position = node?.internals?.positionAbsolute;
  const bounds = position && node.measured?.width != null
    ? { x: position.x, y: position.y, width: node.measured.width, height: node.measured.height ?? 0 }
    : {
        x: Math.min(sourceX, targetX),
        y: Math.min(sourceY, targetY),
        width: Math.abs(sourceX - targetX),
        height: Math.abs(sourceY - targetY),
      };
  const { path, labelX, labelY } = getSelfLoopPath({
    sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition, bounds, index,
  });
  return (
    <BaseEdge
      id={id}
      path={path}
      labelX={labelX}
      labelY={labelY}
      label={label}
      labelStyle={labelStyle}
      labelShowBg={labelShowBg}
      labelBgStyle={labelBgStyle}
      labelBgPadding={labelBgPadding}
      labelBgBorderRadius={labelBgBorderRadius}
      style={style}
      markerStart={markerStart}
      markerEnd={markerEnd}
      interactionWidth={interactionWidth}
      className="rf-self-loop"
    />
  );
});

// Built-in edge paths collapse when both ends sit on one node, so any edge
// type falls back to a loop for self-connections.
function withSelfLoop(EdgeComponent) {
  return function EdgeOrSelfLoop(props) {
    return props.source === props.target ? <SelfLoopEdge {...props} /> : <EdgeComponent {...props} />;
  };
}

const EDGE_TYPES = Object.fromEntries(Object.entries({
  default: BezierEdge,
  bezier: BezierEdge,
  simplebezier: SimpleBezierEdge,
  straight: StraightEdge,
  step: StepEdge,
  smoothstep: SmoothStepEdge,
  smart_bezier: SmartBezierEdge,
  smart_straight: SmartStraightEdge,
  smart_step: SmartStepEdge,
}).map(([name, component]) => [name, withSelfLoop(component)]));

// Messages that only carry the latest state of something, so a newer one
// supersedes a pending one with the same key. Everything else is an event that
// must reach Python.
function supersedeKey(payload) {
  if (payload.type === "node_moved") return `node_moved:${payload.node_id}`;
  if (payload.type === "selection_changed") return "selection_changed";
  return null;
}

/**
 * In debounce mode, queue messages until `debounceMs` pass without a new one
 * and then send them in order. State messages (moves, selection) are coalesced
 * to their latest value; events such as deletions are never dropped. Any
 * pending messages are flushed on unmount or when leaving debounce mode.
 */
function useDebouncedSync(syncMode, debounceMs, syncFn) {
  const timeoutRef = useRef(null);
  const queueRef = useRef([]);
  const syncRef = useRef(syncFn);
  syncRef.current = syncFn;

  const flush = useCallback(() => {
    if (timeoutRef.current !== null) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
    const queue = queueRef.current;
    queueRef.current = [];
    queue.forEach((payload) => {
      try {
        syncRef.current(payload);
      } catch (error) {
        console.error("[panel-reactflow] failed to send", payload?.type, error);
      }
    });
  }, []);

  const schedule = useCallback(
    (payload) => {
      if (syncMode !== "debounce") {
        flush();
        syncRef.current(payload);
        return;
      }
      const key = supersedeKey(payload);
      if (key !== null) {
        queueRef.current = queueRef.current.filter((queued) => supersedeKey(queued) !== key);
      }
      queueRef.current.push(payload);
      if (timeoutRef.current !== null) {
        clearTimeout(timeoutRef.current);
      }
      timeoutRef.current = setTimeout(flush, debounceMs);
    },
    [syncMode, debounceMs, flush],
  );

  useEffect(() => {
    if (syncMode !== "debounce") flush();
  }, [syncMode, flush]);
  useEffect(() => flush, [flush]);

  return { schedule, flush, queueRef };
}

function areEqual(a, b) {
  try {
    return JSON.stringify(a) === JSON.stringify(b);
  } catch (error) {
    return false;
  }
}

function signature(value) {
  try {
    return JSON.stringify(value);
  } catch (error) {
    return null;
  }
}

/**
 * Close a positioned overlay (context menu, value popup) when the user
 * clicks outside of it, by sending `closeMsgType` back to Python.
 */
function useCloseOnOutsideClick(position, ref, closeMsgType, model) {
  useEffect(() => {
    if (!position) return undefined;
    const handleClick = (event) => {
      const el = ref.current;
      if (el) {
        const rect = el.getBoundingClientRect();
        if (
          event.clientX >= rect.left &&
          event.clientX <= rect.right &&
          event.clientY >= rect.top &&
          event.clientY <= rect.bottom
        ) {
          return;
        }
      }
      model.send_msg({ type: closeMsgType });
    };
    const id = requestAnimationFrame(() => {
      document.addEventListener("mousedown", handleClick, true);
    });
    return () => {
      cancelAnimationFrame(id);
      document.removeEventListener("mousedown", handleClick, true);
    };
  }, [position, ref, closeMsgType, model]);
}

/**
 * Combine the class React Flow needs for its own node chrome with the class
 * declared on the Python side.
 */
function nodeClassName(type, className, model) {
  const base = (type === "panel" || model.stylesheets.length > 7) ? "" : "react-flow__node-default";
  return [base, className].filter(Boolean).join(" ");
}

/**
 * Merge a patch of top-level fields (`style`, `type`, `label`, ...) into a node
 * or edge. `null` removes the field so the element falls back to the CSS/theme
 * default, mirroring how Python omits `None` when it serializes the graph.
 */
function applyPropPatch(element, patch) {
  const next = { ...element };
  Object.entries(patch || {}).forEach(([key, value]) => {
    if (value === null) {
      delete next[key];
    } else {
      next[key] = value;
    }
  });
  return next;
}

function withoutKeys(data, keys) {
  const next = { ...(data || {}) };
  (keys || []).forEach((key) => {
    delete next[key];
  });
  return next;
}

/**
 * Drop or repair graph elements that React Flow cannot render, so a structurally
 * broken graph degrades to a partial view instead of an unmounted canvas.
 *
 * This only filters what is handed to React Flow. Nothing is sent back to
 * Python, so the authoritative graph is left untouched and anything dropped here
 * reappears once the underlying problem is fixed.
 *
 * Every issue records whether the element was `repaired` and still rendered, or
 * `dropped` from the view entirely.
 */
function sanitizeGraph(nodes, edges, nodeTypes, edgeTypes) {
  const issues = [];
  const drop = (kind, id, detail) => issues.push({ kind, id, detail, action: "dropped" });
  const repair = (kind, id, detail) => issues.push({ kind, id, detail, action: "repaired" });

  const safeNodes = [];
  const nodeIds = new Set();
  (nodes || []).forEach((node, index) => {
    if (!node || typeof node !== "object") {
      drop("invalid_node", `#${index}`, "Node is not an object");
      return;
    }
    if (typeof node.id !== "string" || !node.id) {
      drop("missing_node_id", `#${index}`, "Node has no usable id");
      return;
    }
    if (nodeIds.has(node.id)) {
      drop("duplicate_node_id", node.id, "Duplicate node id");
      return;
    }
    let safeNode = node;
    const { x, y } = safeNode.position || {};
    if (!Number.isFinite(x) || !Number.isFinite(y)) {
      repair("invalid_position", node.id, "Position is not finite, reset to the origin");
      safeNode = { ...safeNode, position: { x: 0, y: 0 } };
    }
    if (safeNode.type && !nodeTypes?.[safeNode.type]) {
      repair("unknown_node_type", node.id, `Unknown node type "${safeNode.type}", rendered as "default"`);
      safeNode = { ...safeNode, type: "default" };
    }
    nodeIds.add(node.id);
    safeNodes.push(safeNode);
  });

  const safeEdges = [];
  const edgeIds = new Set();
  (edges || []).forEach((edge, index) => {
    if (!edge || typeof edge !== "object") {
      drop("invalid_edge", `#${index}`, "Edge is not an object");
      return;
    }
    if (typeof edge.id !== "string" || !edge.id) {
      drop("missing_edge_id", `#${index}`, "Edge has no usable id");
      return;
    }
    if (edgeIds.has(edge.id)) {
      drop("duplicate_edge_id", edge.id, "Duplicate edge id");
      return;
    }
    if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target)) {
      drop("dangling_edge", edge.id, `Connects a missing node (${edge.source} -> ${edge.target})`);
      return;
    }
    let safeEdge = edge;
    if (safeEdge.type && !edgeTypes?.[safeEdge.type]) {
      repair("unknown_edge_type", edge.id, `Unknown edge type "${safeEdge.type}", rendered as default`);
      const { type, ...rest } = safeEdge;
      safeEdge = rest;
    }
    edgeIds.add(edge.id);
    safeEdges.push(safeEdge);
  });

  return { nodes: safeNodes, edges: safeEdges, issues };
}

function summarizeIssues(issues) {
  const repaired = issues.filter((issue) => issue.action === "repaired").length;
  const dropped = issues.length - repaired;
  const parts = [];
  if (repaired) {
    parts.push(`repaired ${repaired} element${repaired === 1 ? "" : "s"}`);
  }
  if (dropped) {
    parts.push(`hid ${dropped} element${dropped === 1 ? "" : "s"} that could not be rendered`);
  }
  return `Safe mode: ${parts.join(" and ")}.`;
}

function describeError(error, info) {
  return {
    name: error?.name || "Error",
    message: String(error?.message ?? error ?? "Unknown error"),
    stack: error?.stack || null,
    component_stack: info?.componentStack || null,
  };
}

class FlowErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    this.props.onError?.(error, info);
  }

  render() {
    if (this.state.error) {
      return this.props.fallback?.(this.state.error) ?? null;
    }
    return this.props.children;
  }
}

function RecoveryOverlay({ status, error, attempt, mode, onRetry, onReload, onCopy, copied }) {
  if (status === "recovering") {
    return (
      <div className="rf-recovery rf-recovery--retrying">
        <div className="rf-recovery-card">
          <div className="rf-recovery-title">Recovering the graph view…</div>
          <div className="rf-recovery-body">
            {mode === "safe"
              ? "Retrying in safe mode, which hides graph elements that cannot be rendered."
              : "Rebuilding the canvas from the state held on the server."}
          </div>
        </div>
      </div>
    );
  }
  return (
    <div className="rf-recovery rf-recovery--failed">
      <div className="rf-recovery-card">
        <div className="rf-recovery-title">The graph view stopped rendering</div>
        <div className="rf-recovery-body">
          Your graph is still held on the server and has not been modified. Reloading the page
          will restore it.
        </div>
        <pre className="rf-recovery-error">{describeError(error).message}</pre>
        <div className="rf-recovery-actions">
          <button type="button" className="rf-recovery-button rf-recovery-button--primary" onClick={onRetry}>
            Try again
          </button>
          <button type="button" className="rf-recovery-button" onClick={onReload}>
            Reload page
          </button>
          <button type="button" className="rf-recovery-button" onClick={onCopy}>
            {copied ? "Copied" : "Copy details"}
          </button>
        </div>
        <div className="rf-recovery-meta">
          {attempt} recovery {attempt === 1 ? "attempt" : "attempts"} failed. The error has been
          reported to the server log.
        </div>
      </div>
    </div>
  );
}

function SafeModeBanner({ issues, onDismiss }) {
  const [expanded, setExpanded] = useState(false);
  if (!issues.length) {
    return null;
  }
  return (
    <div className="rf-safe-mode-banner">
      <span className="rf-safe-mode-text">
        {summarizeIssues(issues)} Nothing was deleted on the server.
      </span>
      <button type="button" className="rf-recovery-button rf-recovery-button--small" onClick={() => setExpanded((value) => !value)}>
        {expanded ? "Hide" : "Details"}
      </button>
      <button type="button" className="rf-recovery-button rf-recovery-button--small" onClick={onDismiss}>
        Dismiss
      </button>
      {expanded ? (
        <ul className="rf-safe-mode-issues">
          {issues.map((issue) => (
            <li key={`${issue.kind}:${issue.id}`}>
              <code>{issue.id}</code> {issue.detail}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function FlowInner({
  model,
  reportError,
  hydratedNodes,
  pyNodes,
  nodeUpdateCount,
  hydratedEdges,
  currentSelection,
  views,
  viewportSetter,
  onNodeDoubleClick,
  onPaneClick,
  defaultEdgeOptions,
  nodeTypes,
  nodeTypeSpecs,
  edgeTypes,
  nodeEditors,
  colorMode,
  editable,
  selectable,
  enableConnect,
  connectionValidation,
  hasConnectionValidators,
  enableDelete,
  enableMultiselect,
  maxZoom,
  minZoom,
  showMinimap,
  syncMode,
  debounceMs,
  viewport,
  valuePopupTrigger,
  hoverDelay,
  dropTypes,
  maxDropSize,
}) {
  const [nodes, setNodes, onNodesChange] = useNodesState(hydratedNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(hydratedEdges);
  const nodesRef = useRef(nodes);
  const edgesRef = useRef(edges);
  const hydrationFrameRef = useRef(null);
  const edgeHydrationFrameRef = useRef(null);
  const lastHydrated = useRef({ nodeRevision: null, nodesSig: null, edgesSig: null, editable: null, nodeEditors: null });
  const lastPySelectedRef = useRef(new Map());
  const lastViewportSig = useRef(null);
  const editableRef = useRef(editable);
  editableRef.current = editable;
  const { setViewport: setRfViewport, screenToFlowPosition, fitView } = useReactFlow();
  const [hydratedRevision, setHydratedRevision] = useState(null);
  const pendingFitRef = useRef(null);
  const [fitRequest, setFitRequest] = useState(0);
  const [drag, setDrag] = useState(null);
  const [validationResults, setValidationResults] = useState(null);
  const dragRef = useRef(null);
  const resultsRef = useRef(null);
  const validationTimerRef = useRef(null);
  const requestIdRef = useRef(0);

  const candidateReason = useCallback((connection) => {
    const local = localConnectionReason(
      connection, nodesRef.current, edgesRef.current, nodeTypeSpecs, connectionValidation, dragRef.current?.handleType,
    );
    if (local) return local;
    if (!hasConnectionValidators) return null;
    const current = dragRef.current;
    if (!current) return "Connection validation unavailable";
    const result = resultsRef.current;
    if (!result || result.requestId !== current.requestId) return "Checking connections...";
    if (result.error) return result.error;
    const end = current.handleType === "source"
      ? connectionKey(connection.target, connection.targetHandle, "target")
      : connectionKey(connection.source, connection.sourceHandle, "source");
    if (!result.reasons.has(end)) return "Connection not validated";
    return result.reasons.get(end) === "" ? "Connection rejected" : result.reasons.get(end);
  }, [connectionValidation, hasConnectionValidators, nodeTypeSpecs]);

  const reasonForHandle = useCallback((nodeId, handleId, handleType) => {
    if (!drag || drag.handleType === handleType) return null;
    const connection = drag.handleType === "source"
      ? { source: drag.nodeId, sourceHandle: drag.handleId, target: nodeId, targetHandle: handleId }
      : { source: nodeId, sourceHandle: handleId, target: drag.nodeId, targetHandle: drag.handleId };
    return candidateReason(connection);
  }, [candidateReason, drag, validationResults]);

  const onConnectStart = useCallback((_event, { nodeId, handleId, handleType }) => {
    if (!hasConnectionValidators && !Object.values(connectionValidation || {}).some(Boolean)) return;
    const next = { nodeId, handleId: handleId ?? null, handleType, requestId: ++requestIdRef.current };
    dragRef.current = next;
    resultsRef.current = null;
    setDrag(next);
    setValidationResults(null);
    if (validationTimerRef.current) clearTimeout(validationTimerRef.current);
    if (hasConnectionValidators) {
      model.send_msg({
        type: "connection_validation_requested", request_id: next.requestId,
        node_id: nodeId, handle_id: handleId ?? null, handle_type: handleType,
      });
      validationTimerRef.current = setTimeout(() => {
        if (dragRef.current?.requestId !== next.requestId) return;
        dragRef.current = { ...dragRef.current, expired: true };
        const failure = { requestId: next.requestId, reasons: new Map(), error: "Connection validation timed out" };
        resultsRef.current = failure;
        setValidationResults(failure);
      }, CONNECTION_VALIDATION_TIMEOUT_MS);
    }
  }, [connectionValidation, hasConnectionValidators, model]);

  const acceptedDropType = useCallback((event) => {
    if (!editable || !dropTypes?.length) return null;
    const types = Array.from(event.dataTransfer?.types || []);
    return dropTypes.find((type) => types.includes(type)) ?? null;
  }, [dropTypes, editable]);

  const onDragOver = useCallback((event) => {
    if (!acceptedDropType(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  }, [acceptedDropType]);

  const onDrop = useCallback(async (event) => {
    const dropType = acceptedDropType(event);
    if (!dropType) return;
    event.preventDefault();
    // The event is recycled once the handler yields, so read it before awaiting files.
    const position = screenToFlowPosition({ x: event.clientX, y: event.clientY });
    const target = dropTarget(event.target);
    const files = dropType === FILES_DROP_TYPE ? event.dataTransfer.files : null;
    const raw = files ? null : event.dataTransfer.getData(dropType);
    // The limit is in bytes, so measure the UTF-8 encoding rather than UTF-16 code units.
    const rawSize = raw !== null && maxDropSize > 0 ? new TextEncoder().encode(raw).length : 0;
    try {
      let data;
      let rejected = [];
      if (files) {
        ({ data, rejected } = await readDroppedFiles(files, maxDropSize));
      } else if (rawSize > maxDropSize) {
        data = null;
        rejected = [{ type: dropType, size: rawSize, reason: `Data exceeds the drop size limit of ${maxDropSize} bytes` }];
      } else {
        data = raw;
        try {
          data = JSON.parse(raw);
        } catch (_error) {
          // Not JSON, so hand the handler the raw string.
        }
      }
      model.send_msg({ type: "drop", drop_type: dropType, data, position, target, rejected });
    } catch (error) {
      reportError(error, null, { source: "handler", handler: "onDrop" });
    }
  }, [acceptedDropType, maxDropSize, model, reportError, screenToFlowPosition]);

  const onConnectEnd = useCallback(() => {
    if (validationTimerRef.current) clearTimeout(validationTimerRef.current);
    validationTimerRef.current = null;
    dragRef.current = null;
    resultsRef.current = null;
    setDrag(null);
    setValidationResults(null);
  }, []);

  useEffect(() => {
    const handler = (msg) => {
      if (!msg || typeof msg !== "object") {
        return;
      }
      if (msg.type === "connection_validation_result") {
        if (dragRef.current?.requestId !== msg.request_id || dragRef.current.expired) return;
        if (validationTimerRef.current) clearTimeout(validationTimerRef.current);
        validationTimerRef.current = null;
        const result = {
          requestId: msg.request_id,
          reasons: new Map((msg.results || []).map((entry) => [
            connectionKey(entry.node_id, entry.handle_id, entry.handle_type), entry.reason ?? null,
          ])),
          error: msg.error || null,
        };
        resultsRef.current = result;
        setValidationResults(result);
        return;
      }
      if (msg.type === "edge_rejected") {
        // Python refused a connection the browser already drew.
        setEdges((current) => current.filter((edge) => edge.id !== msg.edge_id));
        return;
      }
      if (msg.type === "fit_view") {
        pendingFitRef.current = { revision: msg.node_revision ?? 0, options: msg.options || {} };
        setFitRequest((count) => count + 1);
        return;
      }
      if (msg.type === "patch_node_data") {
        setNodes((current) =>
          current.map((node) => {
            if (node.id !== msg.node_id) {
              return node;
            }
            const data = withoutKeys(node.data, msg.remove);
            Object.assign(data, msg.patch || {});
            return { ...node, data };
          }),
        );
        return;
      }
      if (msg.type === "patch_edge_data") {
        setEdges((current) =>
          current.map((edge) => {
            if (edge.id !== msg.edge_id) {
              return edge;
            }
            const data = withoutKeys(edge.data, msg.remove);
            Object.assign(data, msg.patch || {});
            const nextLabel = msg.patch?.label ?? edge.label;
            return { ...edge, data, label: nextLabel };
          }),
        );
        return;
      }
      if (msg.type === "patch_node_props") {
        const patch = msg.patch || {};
        setNodes((current) =>
          current.map((node) => {
            if (node.id !== msg.node_id) {
              return node;
            }
            const next = applyPropPatch(node, patch);
            if (!editableRef.current) {
              Object.assign(next, READ_ONLY_NODE_FLAGS);
            }
            if ("label" in patch) {
              // The node components render `data._label`, not the top-level label.
              next.data = { ...(next.data || {}), _label: patch.label };
            }
            if ("className" in patch || "type" in patch) {
              const declared = "className" in patch ? patch.className : node._className;
              next._className = declared ?? null;
              next.className = nodeClassName(next.type, declared, model);
            }
            return next;
          }),
        );
        return;
      }
      if (msg.type === "patch_edge_props") {
        setEdges((current) =>
          current.map((edge) => (edge.id === msg.edge_id ? applyPropPatch(edge, msg.patch) : edge)),
        );
      }
    };
    model.on("msg:custom", handler);
    return () => {
      model.off("msg:custom", handler);
    };
  }, [model, setEdges, setNodes]);

  useEffect(() => () => {
    if (validationTimerRef.current) clearTimeout(validationTimerRef.current);
  }, []);

  useEffect(() => {
    nodesRef.current = nodes;
  }, [nodes]);

  useEffect(() => {
    edgesRef.current = edges;
  }, [edges]);

  useEffect(() => {
    return () => {
      if (hydrationFrameRef.current !== null) {
        cancelAnimationFrame(hydrationFrameRef.current);
        hydrationFrameRef.current = null;
      }
      if (edgeHydrationFrameRef.current !== null) {
        cancelAnimationFrame(edgeHydrationFrameRef.current);
        edgeHydrationFrameRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    const nodesSig = signature(hydratedNodes);
    // Nodes carrying views or editors do not stringify, so the signature
    // alone cannot detect an editable toggle hiding their editors.
    if (
      nodeUpdateCount === lastHydrated.current.nodeRevision &&
      nodesSig === lastHydrated.current.nodesSig &&
      editable === lastHydrated.current.editable &&
      nodeEditors === lastHydrated.current.nodeEditors
    ) {
      return;
    }

    if (hydrationFrameRef.current !== null) {
      cancelAnimationFrame(hydrationFrameRef.current);
    }
    hydrationFrameRef.current = requestAnimationFrame(() => {
      const lastPySelected = lastPySelectedRef.current;
      const nextPySelected = new Map();
      const pendingMoves = new Set(
        patchQueueRef.current.filter((payload) => payload.type === "node_moved").map((payload) => payload.node_id),
      );
      setNodes((curr) => {
        const currById = new Map(curr.map((n) => [n?.id, n]));
        const merged = hydratedNodes.map((n) => {
          if (!n || typeof n !== "object") return n;
          nextPySelected.set(n.id, Boolean(n.selected));
          const prev = currById.get(n.id);
          if (!prev) return n;
          // Selection is owned by the browser unless Python changed it since
          // the last push; positions of nodes being dragged, or whose move
          // has not been sent yet, stay where the pointer put them.
          const pythonSelected = Boolean(n.selected) !== Boolean(lastPySelected.get(n.id));
          const keepPosition = prev.dragging || pendingMoves.has(n.id);
          const next = {
            ...n,
            ...(prev.measured ? { measured: prev.measured } : {}),
            ...(keepPosition ? { position: prev.position } : {}),
            selected: pythonSelected ? n.selected : prev.selected,
            dragging: prev.dragging,
          };
          return areEqual(prev, next) ? prev : next;
        });
        lastPySelectedRef.current = nextPySelected;
        if (merged.length === curr.length && merged.every((node, index) => node === curr[index])) {
          return curr;
        }
        return merged;
      });
      lastHydrated.current.nodeRevision = nodeUpdateCount;
      lastHydrated.current.nodesSig = nodesSig;
      lastHydrated.current.editable = editable;
      lastHydrated.current.nodeEditors = nodeEditors;
      setHydratedRevision(nodeUpdateCount);
      hydrationFrameRef.current = null;
    });
  }, [hydratedNodes, pyNodes, setNodes, views, nodeEditors, nodeUpdateCount, editable]);

  useEffect(() => {
    const edgesSig = signature(hydratedEdges);
    if (edgesSig !== lastHydrated.current.edgesSig) {
      lastHydrated.current.edgesSig = edgesSig;
      if (edgeHydrationFrameRef.current !== null) {
        cancelAnimationFrame(edgeHydrationFrameRef.current);
      }
      edgeHydrationFrameRef.current = requestAnimationFrame(() => {
        setEdges((curr) => (areEqual(curr, hydratedEdges) ? curr : hydratedEdges));
        edgeHydrationFrameRef.current = null;
      });
    }
  }, [hydratedEdges, setEdges]);

  // Runs after the commit that hands the hydrated nodes to React Flow, whose
  // fitView then waits until those nodes are measured.
  useEffect(() => {
    const pending = pendingFitRef.current;
    if (!pending || hydratedRevision === null || hydratedRevision < pending.revision) {
      return;
    }
    pendingFitRef.current = null;
    fitView(pending.options).catch((error) => reportError(error, null, { source: "handler", handler: "fitView" }));
  }, [fitRequest, fitView, hydratedRevision, reportError]);

  useEffect(() => {
    if (viewport) {
      const nextSig = signature(viewport);
      if (nextSig !== lastViewportSig.current) {
        lastViewportSig.current = nextSig;
        setRfViewport(viewport);
      }
    }
  }, [setRfViewport, viewport]);

  const sendPatch = useCallback(
    (payload) => {
      if (!payload) {
        return;
      }
      model.send_msg(payload);
    },
    [model],
  );

  const { schedule: schedulePatch, flush: flushPatches, queueRef: patchQueueRef } = useDebouncedSync(syncMode, debounceMs, sendPatch);

  // Immediate messages flush the debounce queue first so Python sees events in
  // the order they happened.
  const sendNow = useCallback(
    (payload) => {
      flushPatches();
      sendPatch(payload);
    },
    [flushPatches, sendPatch],
  );

  const onConnect = useCallback(
    (connection) => {
      if (!editable || !enableConnect || candidateReason(connection)) {
        return;
      }
      const baseId = connection.id || `${connection.source}->${connection.target}`;
      let edgeId = baseId;
      let suffix = 1;
      while (edgesRef.current.some((edge) => edge.id === edgeId)) edgeId = `${baseId}:${suffix++}`;
      const newEdge = { ...connection, id: edgeId };
      const updated = addEdge(newEdge, edgesRef.current);
      if (updated === edgesRef.current) return;
      setEdges(updated);
      sendNow({ type: "edge_added", edge: newEdge });
    },
    [candidateReason, editable, enableConnect, sendNow, setEdges],
  );

  const handleNodesChange = useCallback(
    (changes) => {
      onNodesChange(changes);
      const moved = changes.filter((change) => change.type === "position" && change.dragging !== true);
      if (!editable || !moved.length) {
        return;
      }
      moved.forEach((change) => {
        schedulePatch({
          type: "node_moved",
          node_id: change.id,
          position: change.position,
        });
      });
    },
    [editable, onNodesChange, schedulePatch],
  );

  useEffect(() => {
    if (selectable) return;
    setNodes((curr) => (curr.some((n) => n.selected) ? curr.map((n) => (n.selected ? { ...n, selected: false } : n)) : curr));
    setEdges((curr) => (curr.some((e) => e.selected) ? curr.map((e) => (e.selected ? { ...e, selected: false } : e)) : curr));
  }, [selectable, setNodes, setEdges]);

  // The last selection sent to Python, plus the ones Python has not echoed
  // back yet. An update of the ``selection`` parameter that matches a pending
  // send is an echo; anything else was set from Python and becomes the new
  // baseline.
  const lastSentSelectionRef = useRef(currentSelection || { nodes: [], edges: [] });
  const pendingSelectionsRef = useRef([]);
  useEffect(() => {
    const pending = pendingSelectionsRef.current;
    const echoed = pending.findIndex((sent) => areEqual(sent, currentSelection));
    if (echoed >= 0) {
      pendingSelectionsRef.current = pending.slice(echoed + 1);
      return;
    }
    pendingSelectionsRef.current = [];
    lastSentSelectionRef.current = currentSelection || { nodes: [], edges: [] };
  }, [currentSelection]);

  const onSelectionChange = useCallback(
    ({ nodes: selectedNodes, edges: selectedEdges }) => {
      if (!selectable) {
        return;
      }
      const selection = {
        nodes: selectedNodes.map((node) => node.id),
        edges: selectedEdges.map((edge) => edge.id),
      };
      // Compare against what was last sent rather than the ``selection``
      // parameter: Python only echoes it back after a round trip (or the
      // debounce delay), so a quick select-then-clear would otherwise look
      // unchanged and never be sent.
      if (areEqual(selection, lastSentSelectionRef.current)) {
        return;
      }
      lastSentSelectionRef.current = selection;
      // Python does not echo a selection it already had, so keep the list short.
      pendingSelectionsRef.current = [...pendingSelectionsRef.current, selection].slice(-10);
      // Python updates ``selection`` from this message, syncing it here as
      // well would make it process every selection change twice.
      schedulePatch({
        type: "selection_changed",
        nodes: selection.nodes,
        edges: selection.edges,
      });
    },
    [schedulePatch, selectable],
  );

  const onNodesDelete = useCallback(
    (deletedNodes) => {
      const deletedIds = deletedNodes.map((node) => node.id);
      const deletedEdges = edgesRef.current.filter((edge) => deletedIds.includes(edge.source) || deletedIds.includes(edge.target));
      schedulePatch({
        type: "node_deleted",
        node_id: deletedIds.length === 1 ? deletedIds[0] : null,
        node_ids: deletedIds,
        deleted_edges: deletedEdges.map((edge) => edge.id),
      });
    },
    [schedulePatch],
  );

  const onEdgesDelete = useCallback(
    (deletedEdges) => {
      schedulePatch({
        type: "edge_deleted",
        edge_id: deletedEdges.length === 1 ? deletedEdges[0].id : null,
        edge_ids: deletedEdges.map((edge) => edge.id),
      });
    },
    [schedulePatch],
  );

  const onNodeContextMenu = useCallback(
    (event, node) => {
      event.preventDefault();
      sendNow({
        type: "node_context_menu",
        node_id: node.id,
        position: { x: event.clientX, y: event.clientY },
      });
    },
    [sendNow],
  );

  const edgeHoverTimerRef = useRef(null);
  const edgeHoverCleanupRef = useRef(null);
  const onEdgeHover = useCallback(
    (event, edge) => {
      if (valuePopupTrigger !== "hover") {
        return;
      }
      if (edgeHoverTimerRef.current) {
        clearTimeout(edgeHoverTimerRef.current);
      }
      if (edgeHoverCleanupRef.current) {
        edgeHoverCleanupRef.current();
      }
      const position = { x: event.clientX, y: event.clientY };
      const closeDistance = popupCloseDistance(event);
      const target = { edge_id: edge.id };
      const cleanup = () => {
        if (edgeHoverTimerRef.current) {
          clearTimeout(edgeHoverTimerRef.current);
          edgeHoverTimerRef.current = null;
        }
        if (edgeHoverCleanupRef.current === cleanup) {
          edgeHoverCleanupRef.current = null;
        }
        document.removeEventListener("pointermove", onPointerMove, true);
      };
      const onPointerMove = (moveEvent) => {
        const dx = moveEvent.clientX - position.x;
        const dy = moveEvent.clientY - position.y;
        if (isInsideValuePopup(moveEvent.clientX, moveEvent.clientY)) {
          return;
        }
        if (dx * dx + dy * dy >= closeDistance * closeDistance) {
          schedulePopupClose(() => {
            sendNow({ type: "edge_unhovered", ...target });
            cleanup();
          });
        }
      };
      edgeHoverCleanupRef.current = cleanup;
      edgeHoverTimerRef.current = setTimeout(() => {
        edgeHoverTimerRef.current = null;
        sendNow({ type: "edge_hovered", ...target, position });
        document.addEventListener("pointermove", onPointerMove, true);
      }, hoverDelay);
    },
    [sendNow, valuePopupTrigger, hoverDelay],
  );

  const onEdgeHoverEnd = useCallback(
    () => {},
    [],
  );

  const onNodeClick = useCallback(
    (event, node) => {
      // Interacting with an embedded view or editor (a button, an input, a
      // plot) is not a click on the node itself. The toolbar editor is
      // portaled outside the node's DOM but its clicks still bubble here
      // through the React tree.
      const target = event.target;
      if (
        !event.currentTarget?.contains?.(target) ||
        target?.closest?.(".rf-node-view-wrapper, .rf-node-inline-editor")
      ) {
        return;
      }
      sendNow({
        type: "node_clicked",
        node_id: node.id,
        position: { x: event.clientX, y: event.clientY },
      });
    },
    [sendNow],
  );

  const onEdgeClick = useCallback(
    (event, edge) => {
      sendNow({
        type: "edge_clicked",
        edge_id: edge.id,
        position: { x: event.clientX, y: event.clientY },
      });
    },
    [sendNow],
  );

  const onMoveEnd = useCallback(
    (_event, nextViewport) => {
      if (!areEqual(nextViewport, viewport)) {
        viewportSetter(nextViewport);
      }
    },
    [viewport, viewportSetter],
  );

  // An exception in an interaction handler leaves the canvas showing a change
  // that never reached Python. Report those instead of letting them vanish into
  // the browser console.
  const latestHandlers = {
    onNodesChange: handleNodesChange,
    onEdgesChange,
    onSelectionChange,
    onNodesDelete,
    onEdgesDelete,
    onConnect,
    onConnectStart,
    onConnectEnd,
    onMoveEnd,
    onNodeClick,
    onNodeDoubleClick,
    onNodeContextMenu,
    onEdgeClick,
    onEdgeMouseEnter: onEdgeHover,
    onEdgeMouseLeave: onEdgeHoverEnd,
    onPaneClick,
    onDragOver,
    onDrop,
  };
  const handlersRef = useRef(latestHandlers);
  handlersRef.current = latestHandlers;
  const definedHandlers = Object.keys(latestHandlers).filter((name) => typeof latestHandlers[name] === "function").join(",");
  // The wrappers dispatch to the latest handler through a ref so their
  // identity stays stable. React Flow passes several of them to every node
  // wrapper, so a new identity (e.g. onMoveEnd after each viewport sync)
  // would re-render every node.
  const handlers = useMemo(() => {
    const wrap = (name) => (...args) => {
      try {
        const result = handlersRef.current[name]?.(...args);
        if (result && typeof result.then === "function") {
          return result.catch((error) => {
            reportError(error, null, { source: "handler", handler: name });
          });
        }
        return result;
      } catch (error) {
        reportError(error, null, { source: "handler", handler: name });
        return undefined;
      }
    };
    return Object.fromEntries(definedHandlers.split(",").filter(Boolean).map((name) => [name, wrap(name)]));
  }, [definedHandlers, reportError]);

  const validationEnabled = hasConnectionValidators || Object.values(connectionValidation || {}).some(Boolean);
  // Every node consumes this context, so a fresh object on each FlowInner
  // render would re-render every node on every drag step or data patch.
  const validationContext = useMemo(() => ({ drag, reasonForHandle }), [drag, reasonForHandle]);
  const isValidConnection = useMemo(
    () => (validationEnabled ? (connection) => !candidateReason(connection) : undefined),
    [candidateReason, validationEnabled],
  );
  return (
    <ConnectionValidationContext.Provider value={validationContext}>
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      defaultEdgeOptions={defaultEdgeOptions}
      colorMode={colorMode}
      {...handlers}
      isValidConnection={isValidConnection}
      nodesDraggable={editable}
      nodesConnectable={editable && enableConnect}
      elementsSelectable={selectable}
      deleteKeyCode={editable && enableDelete ? ["Backspace", "Delete"] : null}
      multiSelectionKeyCode={enableMultiselect ? "Shift" : null}
      minZoom={minZoom}
      maxZoom={maxZoom}
      fitView
    >
      <Controls showInteractive={false} />
      {showMinimap ? <MiniMap /> : null}
      <Background />
    </ReactFlow>
    {drag && hasConnectionValidators && (!validationResults || validationResults.error) ? (
      <div className="rf-validation-status" role="status" aria-live="polite">
        {validationResults?.error || "Checking connections..."}
      </div>
    ) : null}
    </ConnectionValidationContext.Provider>
  );
}

export function render({ model, view }) {
  const [readyViewMap, setReadyViewMap] = useState(() => new Map());
  const readyCheckTimeoutsRef = useRef(new Map());
  const [pyNodes] = model.useState("nodes");
  const [nodeUpdateCount] = model.useState("_node_update_count");
  const [pyEdges] = model.useState("edges");
  const [pyNodeTypes] = model.useState("node_types");
  const [defaultEdgeOptions] = model.useState("default_edge_options");
  const [selection] = model.useState("selection");
  const [syncMode] = model.useState("sync_mode");
  const [colorMode] = model.useState("color_mode");
  const [debounceMs] = model.useState("debounce_ms");
  const [editable] = model.useState("editable");
  const [selectable] = model.useState("selectable");
  const [editorMode] = model.useState("editor_mode");
  const [valuePopupTrigger] = model.useState("popup_trigger");
  const [dropTypes] = model.useState("drop_types");
  const [maxDropSize] = model.useState("max_drop_size");
  const [hoverDelay] = model.useState("popup_hover_delay");
  const [errorRecovery] = model.useState("error_recovery");
  const [enableConnect] = model.useState("enable_connect");
  const [connectionValidation] = model.useState("connection_validation");
  const [hasConnectionValidators] = model.useState("has_connection_validators");
  const [enableDelete] = model.useState("enable_delete");
  const [enableMultiselect] = model.useState("enable_multiselect");
  const [maxZoom] = model.useState("max_zoom");
  const [minZoom] = model.useState("min_zoom");
  const [showMinimap] = model.useState("show_minimap");
  const [viewport, setViewport] = model.useState("viewport");
  const [contextMenuPosition] = model.useState("_context_menu_position");
  const contextMenu = model.get_child("_context_menu");
  const [valuePopupPosition] = model.useState("_value_popup_position");
  const valuePopup = model.get_child("_value_popup");
  const selectedEditor = model.get_child("_selected_editor");
  const views = model.get_child("_views");
  const nodeEditors = model.get_child("_node_editor_views");
  const [nodeEditorIds] = model.useState("_node_editor_ids");
  const nodeEditorsById = useMemo(() => {
    const byId = new Map();
    (nodeEditorIds || []).forEach((nodeId, idx) => {
      if (nodeEditors[idx] !== undefined) {
        byId.set(nodeId, nodeEditors[idx]);
      }
    });
    return byId;
  }, [nodeEditorIds, nodeEditors]);
  const topPanels = model.get_child("top_panel");
  const bottomPanels = model.get_child("bottom_panel");
  const leftPanels = model.get_child("left_panel");
  const rightPanels = model.get_child("right_panel");

  const allNodeTypes = useMemo(() => ({ ...BUILTIN_NODE_TYPES, ...(pyNodeTypes || {}) }), [pyNodeTypes]);

  // Recovery state machine. `recoveryRef` mirrors the attempt counter so the
  // error handler, which runs during a commit, can decide what to do without
  // reading stale state.
  const recoveryRef = useRef({ attempt: 0, mode: "normal" });
  const [recovery, setRecovery] = useState({ status: "ok", error: null, info: null, attempt: 0, mode: "normal" });
  const [mountKey, setMountKey] = useState(0);
  const [copied, setCopied] = useState(false);
  const [bannerDismissed, setBannerDismissed] = useState(false);
  const reportedIssuesRef = useRef(null);

  const reportError = useCallback(
    (error, info, context = {}) => {
      const detail = describeError(error, info);
      console.error("[panel-reactflow]", error);
      try {
        model.send_msg({ type: "client_error", source: "render", ...context, ...detail });
      } catch (sendError) {
        console.error("[panel-reactflow] failed to report error to the server", sendError);
      }
      return detail;
    },
    [model],
  );

  const handleRenderError = useCallback(
    (error, info) => {
      const attempt = recoveryRef.current.attempt + 1;
      const mode = attempt >= SAFE_MODE_ATTEMPT ? "safe" : recoveryRef.current.mode;
      const autoRetry = errorRecovery === "auto" && attempt <= MAX_RECOVERY_ATTEMPTS;
      recoveryRef.current = { attempt, mode };
      reportError(error, info, { source: "render", attempt, mode, auto_retry: autoRetry });
      setRecovery({ status: autoRetry ? "recovering" : "failed", error, info, attempt, mode });
    },
    [errorRecovery, reportError],
  );

  const retry = useCallback(() => {
    setRecovery((prev) => ({ ...prev, status: "ok", error: null, info: null }));
    setMountKey((key) => key + 1);
  }, []);

  useEffect(() => {
    if (recovery.status !== "recovering") {
      return undefined;
    }
    const timeout = setTimeout(retry, RETRY_DELAY_MS);
    return () => clearTimeout(timeout);
  }, [recovery.attempt, recovery.status, retry]);

  // Refill the retry budget once a remounted flow has stayed alive, so a later
  // unrelated failure is not immediately treated as unrecoverable.
  useEffect(() => {
    if (recovery.status !== "ok" || recovery.attempt === 0) {
      return undefined;
    }
    const timeout = setTimeout(() => {
      recoveryRef.current = { ...recoveryRef.current, attempt: 0 };
      setRecovery((prev) => (prev.status === "ok" ? { ...prev, attempt: 0 } : prev));
    }, HEALTHY_RESET_MS);
    return () => clearTimeout(timeout);
  }, [mountKey, recovery.attempt, recovery.status]);

  const copyDetails = useCallback(() => {
    const payload = JSON.stringify(
      {
        ...describeError(recovery.error, recovery.info),
        attempt: recovery.attempt,
        mode: recovery.mode,
        node_count: (pyNodes || []).length,
        edge_count: (pyEdges || []).length,
        user_agent: navigator.userAgent,
      },
      null,
      2,
    );
    const done = () => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    };
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(payload).then(done, () => console.log(payload));
    } else {
      console.log(payload);
      done();
    }
  }, [pyEdges, pyNodes, recovery]);


  useEffect(() => {
    const clearReadyCheckTimeouts = () => {
      readyCheckTimeoutsRef.current.forEach((timeoutId) => clearTimeout(timeoutId));
      readyCheckTimeoutsRef.current.clear();
    };

    const setViewReadyState = (viewId, isReady) => {
      setReadyViewMap((previousMap) => {
        const previousReady = previousMap.get(viewId);
        if (previousReady === isReady && previousMap.has(viewId)) {
          return previousMap;
        }
        const nextMap = new Map(previousMap);
        nextMap.set(viewId, isReady);
        return nextMap;
      });
    };

    const checkViewReadyState = (childView) => {
      const viewId = childView?.id;
      if (!viewId) {
        return;
      }

      const isMounted = Boolean(view._mounted.get("_views")?.has(viewId));
      const hasFinished = view.get_child_view(childView)?.has_finished() ?? false;//childView.has_finished();
      const isReady = isMounted && hasFinished;

      setViewReadyState(viewId, isReady);

      if (hasFinished) {
        const existingTimeoutId = readyCheckTimeoutsRef.current.get(viewId);
        if (existingTimeoutId) {
          clearTimeout(existingTimeoutId);
          readyCheckTimeoutsRef.current.delete(viewId);
        }
        return;
      }

      const existingTimeoutId = readyCheckTimeoutsRef.current.get(viewId);
      if (existingTimeoutId) {
        clearTimeout(existingTimeoutId);
      }
      const timeoutId = setTimeout(() => {
        readyCheckTimeoutsRef.current.delete(viewId);
        checkViewReadyState(childView);
      }, 25);
      readyCheckTimeoutsRef.current.set(viewId, timeoutId);
    };

    const handleAfterLayout = () => {
      const childViews = view?.model?.data?._views || [];
      childViews.forEach((childView) => checkViewReadyState(childView));
    };

    model.on("lifecycle:after_layout", handleAfterLayout);
    handleAfterLayout();

    return () => {
      model.off("lifecycle:after_layout", handleAfterLayout);
      clearReadyCheckTimeouts();
    };
  }, [model, view]);


  const hydratedNodes = useMemo(() => {
    return (pyNodes || []).map((node, idx) => {
      // Malformed entries are passed through untouched so the error boundary
      // and safe mode can deal with them instead of this render crashing the
      // whole component outside the boundary.
      if (!node || typeof node !== "object") {
        return node;
      }
      const data = node.data && typeof node.data === "object" ? node.data : {};
      const viewIndex = data.view_idx;
      const { view_idx, ...dataWithoutViewIdx } = data;
      const baseView = views[viewIndex];
      const baseViewId = baseView?.key;
      const isViewReady = baseViewId ? Boolean(readyViewMap.get(baseViewId)) : true;
      const editorView = nodeEditorsById.get(node.id);
      const typeSpec = allNodeTypes[node.type] || {};
      const realKeys = Object.keys(dataWithoutViewIdx);
      const hasEditor = editable && (realKeys.length > 0 || !!typeSpec.schema);
      return {
        ...node,
        ...(editable ? {} : READ_ONLY_NODE_FLAGS),
        // Keep the declared class around so a later className/type patch can
        // recombine it with the React Flow node chrome class.
        _className: node.className ?? null,
        className: nodeClassName(node.type, node.className, model),
        data: {
          ...dataWithoutViewIdx,
          view: baseView,
          editor: editorView,
          _viewReady: isViewReady,
          _hasEditor: hasEditor,
          _label: node.label,
        },
      };
    });
  }, [pyNodes, nodeEditorsById, views, editable, editorMode, allNodeTypes]);

  const hydratedEdges = useMemo(() => {
    return (pyEdges || []).map((edge) => {
      if (!edge || typeof edge !== "object") {
        return edge;
      }
      const data = edge.data || {};
      const label = edge.label;
      if (label === undefined) {
        return edge;
      }
      return { ...edge, data, label };
    });
  }, [pyEdges]);

  // A new component function makes React remount every node of that type,
  // and re-attached Bokeh views stay blank. Reuse each type's component until
  // its own spec or the popup settings change, so adding a type leaves the
  // nodes of other types, and their embedded views, mounted.
  const nodeComponentCache = useRef(new Map());
  const hydratedNodeTypes = useMemo(() => {
    const mapping = {};
    const cache = nodeComponentCache.current;
    const next = new Map();
    Object.entries({ ...BUILTIN_NODE_TYPES, ...(pyNodeTypes || {}) }).forEach(([typeName, spec]) => {
      const key = JSON.stringify([spec, editorMode, valuePopupTrigger, hoverDelay]);
      const cached = cache.get(typeName);
      const component = cached?.key === key
        ? cached.component
        : makeNodeComponent(typeName, spec, editorMode, model, valuePopupTrigger, hoverDelay);
      next.set(typeName, { key, component });
      mapping[typeName] = component;
    });
    nodeComponentCache.current = next;
    return mapping;
  }, [editorMode, pyNodeTypes, model, valuePopupTrigger, hoverDelay]);

  const contextMenuRef = useRef(null);
  const valuePopupRef = useRef(null);
  const containerRef = useRef(null);

  // Shared outside-click dismissal for the context menu and value popup
  // overlays: both are absolutely-positioned children the frontend renders
  // on demand, so Python has no click event to react to when the user just
  // wants to dismiss one without interacting with the canvas underneath.
  useCloseOnOutsideClick(contextMenuPosition, contextMenuRef, "close_context_menu", model);
  useCloseOnOutsideClick(valuePopupPosition, valuePopupRef, "close_value_popup", model);

  const hydratedEdgeTypes = EDGE_TYPES;

  const safeMode = recovery.mode === "safe";
  const safeGraph = useMemo(
    () => (safeMode ? sanitizeGraph(hydratedNodes, hydratedEdges, hydratedNodeTypes, hydratedEdgeTypes) : null),
    [safeMode, hydratedNodes, hydratedEdges, hydratedNodeTypes, hydratedEdgeTypes],
  );
  const safeModeIssues = safeGraph?.issues ?? [];

  useEffect(() => {
    if (!safeMode || !safeModeIssues.length) {
      return;
    }
    const sig = signature(safeModeIssues);
    if (sig === reportedIssuesRef.current) {
      return;
    }
    reportedIssuesRef.current = sig;
    model.send_msg({
      type: "client_error",
      source: "safe_mode",
      name: "SafeModeDegraded",
      message: summarizeIssues(safeModeIssues),
      issues: safeModeIssues,
    });
  }, [model, safeMode, safeModeIssues]);

  const renderRecoveryOverlay = useCallback(
    () => (
      <RecoveryOverlay
        status={recovery.status === "recovering" ? "recovering" : "failed"}
        error={recovery.error}
        attempt={recovery.attempt}
        mode={recovery.mode}
        onRetry={retry}
        onReload={() => window.location.reload()}
        onCopy={copyDetails}
        copied={copied}
      />
    ),
    [copied, copyDetails, recovery, retry],
  );

  const flow = (
    <FlowInner
      key={mountKey}
      model={model}
      reportError={reportError}
      hydratedNodes={safeGraph ? safeGraph.nodes : hydratedNodes}
      pyNodes={pyNodes || []}
      nodeUpdateCount={nodeUpdateCount}
      hydratedEdges={safeGraph ? safeGraph.edges : hydratedEdges}
      currentSelection={selection}
      views={views}
      viewportSetter={setViewport}
      defaultEdgeOptions={defaultEdgeOptions}
      colorMode={colorMode}
      nodeTypes={hydratedNodeTypes}
      nodeTypeSpecs={allNodeTypes}
      edgeTypes={hydratedEdgeTypes}
      nodeEditors={nodeEditorsById}
      editable={editable}
      selectable={selectable}
      enableConnect={enableConnect}
      connectionValidation={connectionValidation}
      hasConnectionValidators={hasConnectionValidators}
      enableDelete={enableDelete}
      enableMultiselect={enableMultiselect}
      maxZoom={maxZoom}
      minZoom={minZoom}
      showMinimap={showMinimap}
      syncMode={syncMode}
      debounceMs={debounceMs}
      viewport={viewport}
      valuePopupTrigger={valuePopupTrigger}
      hoverDelay={hoverDelay}
      dropTypes={dropTypes}
      maxDropSize={maxDropSize}
    />
  );

  return (
    <div ref={containerRef} style={{ width: "100%", height: "100%", position: "relative" }}>
      <ReactFlowProvider>
        {errorRecovery === "off" ? (
          flow
        ) : (
          <FlowErrorBoundary key={mountKey} onError={handleRenderError} fallback={renderRecoveryOverlay}>
            {flow}
          </FlowErrorBoundary>
        )}
        <Panel key="top-panel" position="top-center">
          {topPanels}
        </Panel>
        <Panel key="bottom-panel" position="bottom-center">
          {bottomPanels}
        </Panel>
        <Panel key="left-panel" position="center-left">
          {leftPanels}
        </Panel>
        <Panel key="right-panel" position="center-right">
          {rightPanels}
          {selectedEditor}
        </Panel>
      </ReactFlowProvider>
      {safeMode && recovery.status === "ok" && !bannerDismissed ? (
        <SafeModeBanner issues={safeModeIssues} onDismiss={() => setBannerDismissed(true)} />
      ) : null}
      {contextMenu && contextMenuPosition ? (
        <div
          ref={contextMenuRef}
          className="rf-context-menu"
          style={{
            position: "absolute",
            top: contextMenuPosition.y - (containerRef.current?.getBoundingClientRect().top ?? 0),
            left: contextMenuPosition.x - (containerRef.current?.getBoundingClientRect().left ?? 0),
            zIndex: 1000,
          }}
        >
          {contextMenu}
        </div>
      ) : null}
      {valuePopup && valuePopupPosition ? (
        <div
          ref={valuePopupRef}
          className="rf-value-popup"
          onPointerEnter={enterValuePopup}
          onPointerLeave={leaveValuePopup}
          style={{
            position: "absolute",
            top: valuePopupPosition.y - (containerRef.current?.getBoundingClientRect().top ?? 0),
            left: valuePopupPosition.x - (containerRef.current?.getBoundingClientRect().left ?? 0),
            zIndex: 1000,
          }}
        >
          {valuePopup}
        </div>
      ) : null}
    </div>
  );
}
