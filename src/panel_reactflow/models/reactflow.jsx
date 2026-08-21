import React from "react";
import { Background, BezierEdge, Controls, Handle, MiniMap, NodeToolbar, Panel, Position, ReactFlow, ReactFlowProvider, SmoothStepEdge, StraightEdge, StepEdge, addEdge, useEdgesState, useNodes, useNodesState, useReactFlow, useStore, BaseEdge, getBezierPath, getSmoothStepPath, getStraightPath } from "@xyflow/react";
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

const figureStylesheet = `
.bk-Canvas {
  transform: scale(var(--rf-inverse-zoom));
  transform-origin: top left;
  width: calc(var(--rf-zoom) * 100%);
  height: calc(var(--rf-zoom) * 100%);
}`.trim();

function renderHandles(direction, handles, opts = {}) {
  const handleType = direction === "input" ? "target" : "source";
  const position = direction === "input" ? Position.Left : Position.Right;

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
    return <Handle type={handleType} position={position} {...handleProps} />;
  }
  const spacing = 100 / (handles.length + 1);
  return handles.map((handle, index) => {
    const id = typeof handle === "string" ? handle : handle.id;
    const label = typeof handle === "object" ? handle.label : undefined;
    return (
      <Handle
        key={`${direction}-${id}`}
        id={id}
        type={handleType}
        position={position}
        style={{ top: `${(index + 1) * spacing}%` }}
        {...(label ? {"data-tooltip": label, "data-tooltip-pos": direction === "input" ? "left" : "right"} : {})}
        {...handleProps}
      />
    );
  });
}

function makeNodeComponent(typeName, typeSpec, editorMode) {
  return function NodeComponent({ id, data }) {
    const [toolbarOpen, toggleToolbar] = React.useState(false);
    const zoom = useStore((s) => s.transform?.[2] ?? 1);
    const spec = typeSpec || {};
    const hasEditor = data?._hasEditor;
    const showGear = editorMode === "toolbar" && hasEditor;
    const showToolbar = editorMode === "toolbar" && toolbarOpen && hasEditor;
    const showInlineEditor = editorMode === "node" && hasEditor;
    const showView = data?.view && !spec.minimal;

    const displayLabel = data?._label ?? spec.label ?? typeName;
    const initialZoomRef = useRef(Number.isFinite(zoom) && zoom > 0 ? zoom : 1);

    const viewWrapperStyle = {
      "--rf-inverse-zoom": 1 / initialZoomRef.current,
      "--rf-zoom": initialZoomRef.current,
    };

    const injectFigureStylesheet = (figureModel) => {
      const stylesheets = Array.isArray(figureModel.stylesheets) ? figureModel.stylesheets : [];
      const alreadyInjected = stylesheets.some(
        (entry) => typeof entry === "string" && entry.includes("scale(var(--rf-inverse-zoom))"),
      );
      if (alreadyInjected) {
        return;
      }
      initialZoomRef.current = zoom;
      figureModel.stylesheets = [...stylesheets, figureStylesheet];
    };

    const isFigureModel = (modelNode) => {
      const typeName = String(modelNode?.type || modelNode?.name || modelNode?.constructor?.__name__ || "");
      return typeName === "Figure" || typeName.endsWith(".Figure");
    };

    // Waits until get_child_view returns a non-null/undefined value or times out (maxTries * interval ms)
    const resolveChildView = async (viewInstance, childModel, maxTries = 50, interval = 20) => {
      let tries = 0;
      while (tries < maxTries) {
        try {
          let result = await Promise.resolve(viewInstance._child_views.get(childModel));
          if (result) {
            return result;
          }
        } catch (error) {
          // Ignore this error, try again
        }
        await new Promise(res => setTimeout(res, interval));
        tries++;
      }
      return null;
    };


    const applyFigureStyles = async () => {
      const views = [...Bokeh.index.find_by_id(data.view?.key)]
      if (!views.length) {
        return;
      }
      const visited = new Set();

      const walkSubView = async (subView) => {
        const modelNode = subView?.model;
        const modelId = String(modelNode?.id ?? "");
        if (!modelId || visited.has(modelId)) {
          return;
        }
        visited.add(modelId);

        if (isFigureModel(modelNode)) {
          injectFigureStylesheet(modelNode);
          return
        }

        const childModels = Object.values(subView?.child_models || {}).filter(Boolean);
        for (const childModel of childModels) {
          const childSubView = await resolveChildView(subView, childModel);
          if (childSubView) {
            await walkSubView(childSubView);
          }
        }
      };

      for (const view of views) {
        await walkSubView(view);
      }
    };

    applyFigureStyles();

    const handleGearClick = (e) => {
      e.stopPropagation();
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
          connectable: spec.inputConnectable,
          connectableStart: spec.inputConnectableStart,
          connectableEnd: spec.inputConnectableEnd,
        })}
        <div className="rf-node-label" style={{ fontWeight: 600, margin: displayLabel ? "0.2em 0 0.5em 0.5em" : "0" }}>
          {displayLabel}
        </div>
        {(showView || showInlineEditor) && (
          <div>
            {showView ? <div className={viewWrapperClassName} style={viewWrapperStyle}>{data.view}</div> : null}
            {showInlineEditor ? data.editor : null}
          </div>
        )}
        {renderHandles("output", spec.outputs, {
          connectable: spec.outputConnectable,
          connectableStart: spec.outputConnectableStart,
          connectableEnd: spec.outputConnectableEnd,
        })}
      </div>
    );
  };
}



function useDebouncedSync(syncMode, debounceMs, syncFn) {
  const timeoutRef = useRef(null);

  return useCallback(
    (payload) => {
      if (syncMode === "debounce") {
        if (timeoutRef.current) {
          clearTimeout(timeoutRef.current);
        }
        timeoutRef.current = setTimeout(() => syncFn(payload), debounceMs);
      } else {
        syncFn(payload);
      }
    },
    [syncMode, debounceMs, syncFn],
  );
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
  selectionSetter,
  currentSelection,
  views,
  viewportSetter,
  onNodeDoubleClick,
  onPaneClick,
  defaultEdgeOptions,
  nodeTypes,
  edgeTypes,
  nodeEditors,
  colorMode,
  editable,
  enableConnect,
  enableDelete,
  enableMultiselect,
  maxZoom,
  minZoom,
  showMinimap,
  syncMode,
  debounceMs,
  viewport,
}) {
  const [nodes, setNodes, onNodesChange] = useNodesState(hydratedNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(hydratedEdges);
  const nodesRef = useRef(nodes);
  const edgesRef = useRef(edges);
  const hydrationFrameRef = useRef(null);
  const edgeHydrationFrameRef = useRef(null);
  const lastHydrated = useRef({ nodeRevision: null, nodesSig: null, edgesSig: null });
  const lastViewportSig = useRef(null);
  const { setViewport: setRfViewport } = useReactFlow();

  useEffect(() => {
    const handler = (msg) => {
      if (!msg || typeof msg !== "object") {
        return;
      }
      if (msg.type === "patch_node_data") {
        setNodes((current) =>
          current.map((node) => {
            if (node.id !== msg.node_id) {
              return node;
            }
            const data = { ...(node.data || {}), ...(msg.patch || {}) };
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
            const data = { ...(edge.data || {}), ...(msg.patch || {}) };
            const nextLabel = msg.patch?.label ?? edge.label;
            return { ...edge, data, label: nextLabel };
          }),
        );
      }
    };
    model.on("msg:custom", handler);
    return () => {
      model.off("msg:custom", handler);
    };
  }, [model, setEdges, setNodes]);

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
    if (
      nodeUpdateCount === lastHydrated.current.nodeRevision &&
      nodesSig === lastHydrated.current.nodesSig
    ) {
      return;
    }

    if (hydrationFrameRef.current !== null) {
      cancelAnimationFrame(hydrationFrameRef.current);
    }
    hydrationFrameRef.current = requestAnimationFrame(() => {
      setNodes((curr) => {
        const currById = new Map(curr.map((n) => [n.id, n]));
        const merged = hydratedNodes.map((n) => {
          const prev = currById.get(n.id);
          if (!prev) return n;
          const next = {
            ...n,
            selected: prev.selected,
            dragging: prev.dragging,
          };
          return areEqual(prev, next) ? prev : next;
        });
        if (merged.length === curr.length && merged.every((node, index) => node === curr[index])) {
          return curr;
        }
        return merged;
      });
      lastHydrated.current.nodeRevision = nodeUpdateCount;
      lastHydrated.current.nodesSig = nodesSig;
      hydrationFrameRef.current = null;
    });
  }, [hydratedNodes, pyNodes, setNodes, views, nodeEditors, nodeUpdateCount]);

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

  const schedulePatch = useDebouncedSync(syncMode, debounceMs, sendPatch);

  const onConnect = useCallback(
    (connection) => {
      if (!enableConnect) {
        return;
      }
      const edgeId = connection.id || `${connection.source}->${connection.target}`;
      const newEdge = { ...connection, id: edgeId };
      const updated = addEdge(newEdge, edgesRef.current);
      setEdges(updated);
      sendPatch({ type: "edge_added", edge: newEdge });
    },
    [enableConnect, sendPatch, setEdges],
  );

  const handleNodesChange = useCallback(
    (changes) => {
      onNodesChange(changes);
      const moved = changes.filter((change) => change.type === "position" && change.dragging !== true);
      if (!moved.length) {
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
    [onNodesChange, schedulePatch],
  );

  const onSelectionChange = useCallback(
    ({ nodes: selectedNodes, edges: selectedEdges }) => {
      const selection = {
        nodes: selectedNodes.map((node) => node.id),
        edges: selectedEdges.map((edge) => edge.id),
      };
      if (areEqual(selection, currentSelection)) {
        return;
      }
      selectionSetter(selection);
      schedulePatch({
        type: "selection_changed",
        nodes: selection.nodes,
        edges: selection.edges,
      });
    },
    [currentSelection, schedulePatch, selectionSetter],
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
      sendPatch({
        type: "node_context_menu",
        node_id: node.id,
        position: { x: event.clientX, y: event.clientY },
      });
    },
    [sendPatch],
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
  const handlers = useMemo(() => {
    const wrap = (name, fn) =>
      typeof fn === "function"
        ? (...args) => {
            try {
              return fn(...args);
            } catch (error) {
              reportError(error, null, { source: "handler", handler: name });
              return undefined;
            }
          }
        : undefined;
    return {
      onNodesChange: wrap("onNodesChange", handleNodesChange),
      onEdgesChange: wrap("onEdgesChange", onEdgesChange),
      onSelectionChange: wrap("onSelectionChange", onSelectionChange),
      onNodesDelete: wrap("onNodesDelete", onNodesDelete),
      onEdgesDelete: wrap("onEdgesDelete", onEdgesDelete),
      onConnect: wrap("onConnect", onConnect),
      onMoveEnd: wrap("onMoveEnd", onMoveEnd),
      onNodeDoubleClick: wrap("onNodeDoubleClick", onNodeDoubleClick),
      onNodeContextMenu: wrap("onNodeContextMenu", onNodeContextMenu),
      onPaneClick: wrap("onPaneClick", onPaneClick),
    };
  }, [
    handleNodesChange,
    onConnect,
    onEdgesChange,
    onEdgesDelete,
    onMoveEnd,
    onNodeContextMenu,
    onNodeDoubleClick,
    onNodesDelete,
    onPaneClick,
    onSelectionChange,
    reportError,
  ]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      defaultEdgeOptions={defaultEdgeOptions}
      colorMode={colorMode}
      {...handlers}
      nodesDraggable={editable}
      nodesConnectable={editable && enableConnect}
      elementsSelectable={editable}
      deleteKeyCode={enableDelete ? ["Backspace", "Delete"] : null}
      multiSelectionKeyCode={enableMultiselect ? "Shift" : null}
      minZoom={minZoom}
      maxZoom={maxZoom}
      fitView
    >
      <Controls />
      {showMinimap ? <MiniMap /> : null}
      <Background />
    </ReactFlow>
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
  const [selection, setSelection] = model.useState("selection");
  const [syncMode] = model.useState("sync_mode");
  const [colorMode] = model.useState("color_mode");
  const [debounceMs] = model.useState("debounce_ms");
  const [editable] = model.useState("editable");
  const [editorMode] = model.useState("editor_mode");
  const [errorRecovery] = model.useState("error_recovery");
  const [enableConnect] = model.useState("enable_connect");
  const [enableDelete] = model.useState("enable_delete");
  const [enableMultiselect] = model.useState("enable_multiselect");
  const [maxZoom] = model.useState("max_zoom");
  const [minZoom] = model.useState("min_zoom");
  const [showMinimap] = model.useState("show_minimap");
  const [viewport, setViewport] = model.useState("viewport");
  const [contextMenuPosition] = model.useState("_context_menu_position");
  const contextMenu = model.get_child("_context_menu");
  const selectedEditor = model.get_child("_selected_editor");
  const views = model.get_child("_views");
  const nodeEditors = model.get_child("_node_editor_views");
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
      const data = node.data || {};
      const viewIndex = data.view_idx;
      const { view_idx, ...dataWithoutViewIdx } = data;
      const baseView = views[viewIndex];
      const baseViewId = baseView?.key;
      const isViewReady = baseViewId ? Boolean(readyViewMap.get(baseViewId)) : true;
      const editorView = nodeEditors[idx];
      const typeSpec = allNodeTypes[node.type] || {};
      const realKeys = Object.keys(dataWithoutViewIdx);
      const hasEditor = realKeys.length > 0 || !!typeSpec.schema;
      return {
        ...node,
        className: (node.type === "panel" || model.stylesheets.length > 7) ? "" : "react-flow__node-default",
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
  }, [pyNodes, nodeEditors, views, editorMode, allNodeTypes]);

  const hydratedEdges = useMemo(() => {
    return (pyEdges || []).map((edge) => {
      const data = edge.data || {};
      const label = edge.label;
      if (label === undefined) {
        return edge;
      }
      return { ...edge, data, label };
    });
  }, [pyEdges]);

  const hydratedNodeTypes = useMemo(() => {
    const mapping = {};
    Object.entries({ ...BUILTIN_NODE_TYPES, ...(pyNodeTypes || {}) }).forEach(([typeName, spec]) => {
      mapping[typeName] = makeNodeComponent(typeName, spec, editorMode);
    });
    return mapping;
  }, [editorMode, pyNodeTypes]);

  const contextMenuRef = useRef(null);
  const containerRef = useRef(null);

  useEffect(() => {
    if (!contextMenuPosition) return;
    const handleClick = (event) => {
      const el = contextMenuRef.current;
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
      model.send_msg({ type: "close_context_menu" });
    };
    const id = requestAnimationFrame(() => {
      document.addEventListener("mousedown", handleClick, true);
    });
    return () => {
      cancelAnimationFrame(id);
      document.removeEventListener("mousedown", handleClick, true);
    };
  }, [contextMenuPosition, model]);

  const hydratedEdgeTypes = useMemo(() => ({
    bezier: BezierEdge,
    straight: StraightEdge,
    step: StepEdge,
    smoothstep: SmoothStepEdge,
    smart_bezier: SmartBezierEdge,
    smart_straight: SmartStraightEdge,
    smart_step: SmartStepEdge,
  }), []);

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
      selectionSetter={setSelection}
      currentSelection={selection}
      views={views}
      viewportSetter={setViewport}
      defaultEdgeOptions={defaultEdgeOptions}
      colorMode={colorMode}
      nodeTypes={hydratedNodeTypes}
      edgeTypes={hydratedEdgeTypes}
      nodeEditors={nodeEditors}
      editable={editable}
      enableConnect={enableConnect}
      enableDelete={enableDelete}
      enableMultiselect={enableMultiselect}
      maxZoom={maxZoom}
      minZoom={minZoom}
      showMinimap={showMinimap}
      syncMode={syncMode}
      debounceMs={debounceMs}
      viewport={viewport}
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
    </div>
  );
}
