import React from "react";
import {
  Background,
  Controls,
  Handle,
  MiniMap,
  NodeToolbar,
  Panel,
  Position,
  ReactFlow,
  ReactFlowProvider,
  addEdge,
  useEdgesState,
  useNodesState,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

const { useCallback, useEffect, useMemo, useRef } = React;

const BUILTIN_NODE_TYPES = {
  panel: { label: "Panel" },
  default: { label: "Default" },
  minimal: { label: "Minimal", minimal: true },
};

function getPropertySummary(data, spec) {
  if (!spec?.properties?.length) {
    return [];
  }
  return spec.properties
    .filter((prop) => prop.visible_in_node)
    .map((prop) => {
      const value = data?.[prop.name] ?? prop.default;
      const label = prop.label || prop.name;
      return { label, value };
    });
}

function renderHandles(direction, handles) {
  if (!handles?.length) {
    return (
      <Handle
        type={direction === "input" ? "target" : "source"}
        position={direction === "input" ? Position.Left : Position.Right}
      />
    );
  }
  const spacing = 100 / (handles.length + 1);
  return handles.map((handle, index) => (
    <Handle
      key={`${direction}-${handle}`}
      id={handle}
      type={direction === "input" ? "target" : "source"}
      position={direction === "input" ? Position.Left : Position.Right}
      style={{ top: `${(index + 1) * spacing}%` }}
    />
  ));
}

function makeNodeComponent(typeName, model, typeSpec, editorMode) {
  return function NodeComponent({ id, data }) {
    const [toolbarOpen, toggleToolbar] = React.useState(false);
    const spec = typeSpec || {};
    const showGear = editorMode === "toolbar";
    const showToolbar = (editorMode === "toolbar" && toolbarOpen);
    const showView = data?.view && !spec.minimal;

    const summary = getPropertySummary(data, spec);
    const label = data?.label || spec.label || typeName;

    const handleGearClick = (e) => {
      e.stopPropagation();
      toggleToolbar((v) => !v);
    };

    const handleCloseToolbar = (e) => {
      e.stopPropagation();
      toggleToolbar(false);
    };

    return (
      <div style={{ padding: "8px 10px", minWidth: "140px" }}>
        {showToolbar ? (
          <NodeToolbar
            isVisible={true}
            position={Position.Top}
            style={{background: "white"}}
          >
            {data.editor}
          </NodeToolbar>
        ): null}
        {showGear && (
          <button
            aria-label={showToolbar ? "Hide node toolbar" : "Show node toolbar"}
            onClick={handleGearClick}
            style={{
              position: "absolute",
              top: "7px",
              right: "7px",
              border: "none",
              background: "transparent",
              fontSize: "17px",
              lineHeight: "18px",
              cursor: "pointer",
              zIndex: 2,
              padding: 0,
              color: showToolbar ? "#3477db" : "#888",
              filter: showToolbar
                ? "drop-shadow(0 0 1px #3477db) brightness(1.15)"
                : "none",
              transition: "color 0.1s",
            }}
            tabIndex={0}
            type="button"
            title={showToolbar ? "Hide node toolbar" : "Show node toolbar"}
          >
            <img
              src={import.meta.url.replace(/(\/[^\/?#]+)?(\?.*)?$/,"/icons/gear.svg")}
              alt=""
              width={14}
              height={14}
              aria-hidden="true"
              style={{
                opacity: showToolbar ? 1 : 0.85,
                transform: showToolbar ? "rotate(22deg)" : "none",
                transition: "color 0.13s, filter 0.13s, opacity 0.13s, transform 0.18s",
                background: showToolbar ? "#eaf2fd" : "none",
                borderRadius: "50%",
                boxShadow: showToolbar
                  ? "0 0 0 2px #cfe1fc"
                  : "none",
                stroke: showToolbar ? "#3477db" : "#888",
                filter: showToolbar
                  ? "drop-shadow(0 0 1px #3477db) brightness(1.15)"
                  : "none",
              }}
            />
          </button>
        )}
        {renderHandles("input", spec.inputs)}
        <div style={{ fontWeight: 600, marginBottom: summary.length ? 6 : 0 }}>
          {label}
        </div>
        {summary.length > 0 && (
          <div style={{ display: "grid", gap: "2px"}}>
            {summary.map((item) => (
              <div key={item.label}>
                <span style={{ opacity: 0.7 }}>{item.label}:</span>{" "}
                <span>{String(item.value ?? "")}</span>
              </div>
            ))}
          </div>
        )}
        {(showView || editorMode === "node") && (
          <div style={{ marginTop: 6 }}>
            {data.view}
            {editorMode === "node" ? data.editor : null}
          </div>
        )}
        {renderHandles("output", spec.outputs)}
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
    [syncMode, debounceMs, syncFn]
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

function FlowInner({
  model,
  hydratedNodes,
  pyNodes,
  pyEdges,
  selectionSetter,
  currentSelection,
  views,
  viewportSetter,
  onNodeDoubleClick,
  onPaneClick,
  defaultEdgeOptions,
  nodeTypes,
  editable,
  enableConnect,
  enableDelete,
  enableMultiselect,
  showMinimap,
  syncMode,
  debounceMs,
  viewport,
}) {
  const [nodes, setNodes, onNodesChange] = useNodesState(hydratedNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(pyEdges);
  const nodesRef = useRef(nodes);
  const edgesRef = useRef(edges);
  const lastHydrated = useRef({ nodesSig: null, viewsRef: null, edgesSig: null });
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
          })
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
            return { ...edge, data };
          })
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
    const nodesSig = signature(pyNodes);
    if (nodesSig === lastHydrated.current.nodesSig) return;
    lastHydrated.current.nodesSig = nodesSig;

    setNodes((curr) => {
      const nextById = new Map(hydratedNodes.map((n) => [n.id, n]));
      const currById = new Map(curr.map((n) => [n.id, n]));
      const merged = hydratedNodes.map((n) => {
        const prev = currById.get(n.id);
        if (!prev) return n;
        return {
          ...n,
          selected: prev.selected,
          dragging: prev.dragging,
        };
      });
      return merged;
    });
  }, [hydratedNodes, pyNodes, setNodes]);

  useEffect(() => {
    const edgesSig = signature(pyEdges);
    if (edgesSig !== lastHydrated.current.edgesSig) {
      lastHydrated.current.edgesSig = edgesSig;
      setEdges(pyEdges);
    }
  }, [pyEdges, setEdges]);

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
    [model]
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
    [enableConnect, sendPatch, setEdges]
  );

  const onNodeDragStop = useCallback(
    (_event, node) => {
      schedulePatch({
        type: "node_moved",
        node_id: node.id,
        position: node.position,
      });
    },
    [schedulePatch]
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
    [currentSelection, schedulePatch, selectionSetter]
  );

  const onNodesDelete = useCallback(
    (deletedNodes) => {
      const deletedIds = deletedNodes.map((node) => node.id);
      const deletedEdges = edgesRef.current.filter(
        (edge) =>
          deletedIds.includes(edge.source) || deletedIds.includes(edge.target)
      );
      schedulePatch({
        type: "node_deleted",
        node_id: deletedIds.length === 1 ? deletedIds[0] : null,
        node_ids: deletedIds,
        deleted_edges: deletedEdges.map((edge) => edge.id),
      });
    },
    [schedulePatch]
  );

  const onEdgesDelete = useCallback(
    (deletedEdges) => {
      schedulePatch({
        type: "edge_deleted",
        edge_id: deletedEdges.length === 1 ? deletedEdges[0].id : null,
        edge_ids: deletedEdges.map((edge) => edge.id),
      });
    },
    [schedulePatch]
  );

  const onMoveEnd = useCallback(
    (_event, nextViewport) => {
      if (!areEqual(nextViewport, viewport)) {
        viewportSetter(nextViewport);
      }
    },
    [viewport, viewportSetter]
  );

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      defaultEdgeOptions={defaultEdgeOptions}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onNodeDragStop={onNodeDragStop}
      onSelectionChange={onSelectionChange}
      onNodesDelete={onNodesDelete}
      onEdgesDelete={onEdgesDelete}
      onConnect={onConnect}
      onMoveEnd={onMoveEnd}
      onNodeDoubleClick={onNodeDoubleClick}
      onPaneClick={onPaneClick}
      nodesDraggable={editable}
      nodesConnectable={editable && enableConnect}
      elementsSelectable={editable}
      deleteKeyCode={enableDelete ? "Backspace" : null}
      multiSelectionKeyCode={enableMultiselect ? "Shift" : null}
      fitView
    >
      <Controls />
      {showMinimap ? <MiniMap /> : null}
      <Background />
    </ReactFlow>
  );
}

export function render({ model, view }) {
  const [pyNodes] = model.useState("nodes");
  const [pyEdges] = model.useState("edges");
  const [defaultEdgeOptions] = model.useState("default_edge_options");
  const [selection, setSelection] = model.useState("selection");
  const [syncMode] = model.useState("sync_mode");
  const [debounceMs] = model.useState("debounce_ms");
  const [editable] = model.useState("editable");
  const [editorMode] = model.useState("editor_mode");
  const [enableConnect] = model.useState("enable_connect");
  const [enableDelete] = model.useState("enable_delete");
  const [enableMultiselect] = model.useState("enable_multiselect");
  const [showMinimap] = model.useState("show_minimap");
  const [viewport, setViewport] = model.useState("viewport");
  const views = model.get_child("_views");
  const nodeEditors = model.get_child("_node_editor_views");
  const topPanels = model.get_child("top_panel");
  const bottomPanels = model.get_child("bottom_panel");
  const leftPanels = model.get_child("left_panel");
  const rightPanels = model.get_child("right_panel");

  const nodeEditorMap = {};
  pyNodes.forEach((node, idx) => {
    if (node && node.id !== undefined) {
      nodeEditorMap[node.id] = nodeEditors[idx];
    }
  });

  const hydratedNodes = useMemo(() => {
    return (pyNodes || []).map((node, idx) => {
      const data = node.data || {};
      const viewIndex = data.view_idx;
      const baseView = views[viewIndex];
      const editorView = nodeEditors[idx];
      return {
        ...node,
        data: {
          ...data,
          view: baseView,
          editor: editorView,
        },
      };
    });
  }, [pyNodes, nodeEditors, views, editorMode]);

  const nodeTypes = useMemo(() => {
    const mapping = {};
    Object.entries(BUILTIN_NODE_TYPES).forEach(([typeName, spec]) => {
      mapping[typeName] = makeNodeComponent(
        typeName,
        model,
        spec,
        editorMode,
      );
    });
    return mapping;
  }, [editorMode]);

  return (
    <div style={{ width: "100%", height: "100%" }}>
      <ReactFlowProvider>
        <FlowInner
          model={model}
          hydratedNodes={hydratedNodes}
          pyNodes={pyNodes || []}
          pyEdges={pyEdges || []}
          selectionSetter={setSelection}
          currentSelection={selection}
          views={views}
          viewportSetter={setViewport}
          defaultEdgeOptions={defaultEdgeOptions}
          nodeTypes={nodeTypes}
          editable={editable}
          enableConnect={enableConnect}
          enableDelete={enableDelete}
          enableMultiselect={enableMultiselect}
          showMinimap={showMinimap}
          syncMode={syncMode}
          debounceMs={debounceMs}
          viewport={viewport}
        />
        {(topPanels || []).map((panel, idx) => (
          <Panel key={`top-${idx}`} position="top-center">
            {panel}
          </Panel>
        ))}
        {(bottomPanels || []).map((panel, idx) => (
          <Panel key={`bottom-${idx}`} position="bottom-center">
            {panel}
          </Panel>
        ))}
        {(leftPanels || []).map((panel, idx) => (
          <Panel key={`left-${idx}`} position="center-left">
            {panel}
          </Panel>
        ))}
        {(rightPanels || []).map((panel, idx) => (
          <Panel key={`right-${idx}`} position="center-right">
            {panel}
            {(selection.nodes.length && editorMode === "side") ? nodeEditorMap[selection.nodes[0]] : null}
          </Panel>
        ))}
      </ReactFlowProvider>
    </div>
  );
}
