import React from "react";
import {
  Background,
  Controls,
  Handle,
  MiniMap,
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

function makeNodeComponent(typeName, typeSpec) {
  return function NodeComponent({ data }) {
    const spec = typeSpec || {};
    const summary = getPropertySummary(data, spec);
    const label = data?.label || spec.label || typeName;
    const showView = data?.view_component && !spec.minimal;

    return (
      <div style={{ padding: "8px 10px", minWidth: "140px" }}>
        {renderHandles("input", spec.inputs)}
        <div style={{ fontWeight: 600, marginBottom: summary.length ? 6 : 0 }}>
          {label}
        </div>
        {summary.length > 0 && (
          <div style={{ display: "grid", gap: "2px", fontSize: "12px" }}>
            {summary.map((item) => (
              <div key={item.label}>
                <span style={{ opacity: 0.7 }}>{item.label}:</span>{" "}
                <span>{String(item.value ?? "")}</span>
              </div>
            ))}
          </div>
        )}
        {showView && <div style={{ marginTop: 6 }}>{data.view_component}</div>}
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
    nodesRef.current = nodes;
  }, [nodes]);

  useEffect(() => {
    edgesRef.current = edges;
  }, [edges]);

  useEffect(() => {
    const nodesSig = signature(pyNodes);
    if (nodesSig !== lastHydrated.current.nodesSig || views !== lastHydrated.current.viewsRef) {
      lastHydrated.current.nodesSig = nodesSig;
      lastHydrated.current.viewsRef = views;
      setNodes(hydratedNodes);
    }
  }, [hydratedNodes, pyNodes, setNodes, views]);

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
      nodesDraggable={editable}
      nodesConnectable={editable && enableConnect}
      elementsSelectable={editable}
      deleteKeyCode={enableDelete ? "Backspace" : null}
      multiSelectionKeyCode={enableMultiselect ? "Shift" : null}
    >
      <Controls />
      {showMinimap ? <MiniMap /> : null}
      <Background />
    </ReactFlow>
  );
}

export function render({ model, view }) {
  const [pyNodes, setPyNodes] = model.useState("nodes");
  const [pyEdges, setPyEdges] = model.useState("edges");
  const [defaultEdgeOptions] = model.useState("default_edge_options");
  const [nodeTypesSpec] = model.useState("node_types");
  const [selection, setSelection] = model.useState("selection");
  const [syncMode] = model.useState("sync_mode");
  const [debounceMs] = model.useState("debounce_ms");
  const [editable] = model.useState("editable");
  const [enableConnect] = model.useState("enable_connect");
  const [enableDelete] = model.useState("enable_delete");
  const [enableMultiselect] = model.useState("enable_multiselect");
  const [showMinimap] = model.useState("show_minimap");
  const [viewport, setViewport] = model.useState("viewport");
  const views = model.get_child("_views");

  const hydratedNodes = useMemo(() => {
    return (pyNodes || []).map((node) => {
      const data = node.data || {};
      const viewIndex = data.view_idx;
      return {
        ...node,
        data: {
          ...data,
          view_component:
            viewIndex != null ? views[viewIndex] : data.view_component,
        },
      };
    });
  }, [views, pyNodes]);

  const mergedSpecs = useMemo(
    () => ({
      ...BUILTIN_NODE_TYPES,
      ...(nodeTypesSpec || {}),
    }),
    [nodeTypesSpec]
  );

  const nodeTypes = useMemo(() => {
    const mapping = {};
    Object.entries(mergedSpecs).forEach(([typeName, spec]) => {
      mapping[typeName] = makeNodeComponent(typeName, spec);
    });
    return mapping;
  }, [mergedSpecs]);

  useEffect(() => {
    const selectedNodes = new Set(selection.nodes || []);
    const selectedEdges = new Set(selection.edges || []);
    let nodesChanged = false;
    let edgesChanged = false;
    const nextNodes = (pyNodes || []).map((node) => {
      const selected = selectedNodes.has(node.id);
      if (node.selected !== selected) {
        nodesChanged = true;
        return { ...node, selected };
      }
      return node;
    });
    const nextEdges = (pyEdges || []).map((edge) => {
      const selected = selectedEdges.has(edge.id);
      if (edge.selected !== selected) {
        edgesChanged = true;
        return { ...edge, selected };
      }
      return edge;
    });
    if (nodesChanged) {
      setPyNodes(nextNodes);
    }
    if (edgesChanged) {
      setPyEdges(nextEdges);
    }
  }, [pyEdges, pyNodes, selection, setPyEdges, setPyNodes]);

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
      </ReactFlowProvider>
    </div>
  );
}
