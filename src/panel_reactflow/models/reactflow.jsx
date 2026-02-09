import React from "react";
import { Background, Controls, Handle, MiniMap, NodeToolbar, Panel, Position, ReactFlow, ReactFlowProvider, addEdge, useEdgesState, useNodesState, useReactFlow } from "@xyflow/react";
import "@xyflow/react/dist/style.css";

const { useCallback, useEffect, useMemo, useRef } = React;

const BUILTIN_NODE_TYPES = {
  panel: { label: "Panel" },
  default: { label: "Default" },
  minimal: { label: "Minimal", minimal: true },
};

function renderHandles(direction, handles) {
  if (!handles?.length) {
    return <Handle type={direction === "input" ? "target" : "source"} position={direction === "input" ? Position.Left : Position.Right} />;
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

function makeNodeComponent(typeName, typeSpec, editorMode) {
  return function NodeComponent({ id, data }) {
    const [toolbarOpen, toggleToolbar] = React.useState(false);
    const spec = typeSpec || {};
    const hasEditor = data?._hasEditor;
    const showGear = editorMode === "toolbar" && hasEditor;
    const showToolbar = editorMode === "toolbar" && toolbarOpen && hasEditor;
    const showInlineEditor = editorMode === "node" && hasEditor;
    const showView = data?.view && !spec.minimal;

    const displayLabel = data?._label ?? spec.label ?? typeName;

    const handleGearClick = (e) => {
      e.stopPropagation();
      toggleToolbar((v) => !v);
    };

    return (
      <div className="rf-node-content">
        {showToolbar ? (
          <NodeToolbar isVisible={true} position={Position.Top} style={{ background: "white" }}>
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
        {renderHandles("input", spec.inputs)}
        <div className="rf-node-label" style={{ fontWeight: 600, margin: displayLabel ? "0.2em 0 0.5em 0.5em" : "0" }}>
          {displayLabel}
        </div>
        {(showView || showInlineEditor) && (
          <div>
            {data.view}
            {showInlineEditor ? data.editor : null}
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

function FlowInner({
  model,
  hydratedNodes,
  pyNodes,
  hydratedEdges,
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
  const [edges, setEdges, onEdgesChange] = useEdgesState(hydratedEdges);
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
    const nodesSig = signature(pyNodes);
    const viewsSig = signature((views || []).map((view) => view?.props?.id ?? null));
    if (nodesSig === lastHydrated.current.nodesSig && viewsSig === lastHydrated.current.viewsRef) {
      return;
    }
    lastHydrated.current.nodesSig = nodesSig;
    lastHydrated.current.viewsRef = viewsSig;

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
  }, [hydratedNodes, pyNodes, setNodes, views]);

  useEffect(() => {
    const edgesSig = signature(hydratedEdges);
    if (edgesSig !== lastHydrated.current.edgesSig) {
      lastHydrated.current.edgesSig = edgesSig;
      setEdges(hydratedEdges);
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
      const deletedViewIdx = deletedNodes
        .map((node) => node?.data?.view_idx)
        .filter((value) => Number.isFinite(value))
        .sort((a, b) => a - b);
      if (deletedViewIdx.length) {
        const deletedSet = new Set(deletedIds);
        setNodes((current) =>
          current.map((node) => {
            if (deletedSet.has(node.id)) {
              return node;
            }
            const viewIdx = node?.data?.view_idx;
            if (!Number.isFinite(viewIdx)) {
              return node;
            }
            const shift = deletedViewIdx.filter((idx) => idx < viewIdx).length;
            if (!shift) {
              return node;
            }
            return {
              ...node,
              data: { ...node.data, view_idx: viewIdx - shift },
            };
          }),
        );
      }
      const deletedEdges = edgesRef.current.filter((edge) => deletedIds.includes(edge.source) || deletedIds.includes(edge.target));
      schedulePatch({
        type: "node_deleted",
        node_id: deletedIds.length === 1 ? deletedIds[0] : null,
        node_ids: deletedIds,
        deleted_edges: deletedEdges.map((edge) => edge.id),
      });
    },
    [schedulePatch, setNodes],
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

  const onMoveEnd = useCallback(
    (_event, nextViewport) => {
      if (!areEqual(nextViewport, viewport)) {
        viewportSetter(nextViewport);
      }
    },
    [viewport, viewportSetter],
  );

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      defaultEdgeOptions={defaultEdgeOptions}
      onNodesChange={handleNodesChange}
      onEdgesChange={onEdgesChange}
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
  const [pyNodeTypes] = model.useState("node_types");
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
  const edgeEditors = model.get_child("_edge_editor_views");
  const topPanels = model.get_child("top_panel");
  const bottomPanels = model.get_child("bottom_panel");
  const leftPanels = model.get_child("left_panel");
  const rightPanels = model.get_child("right_panel");

  const allNodeTypes = useMemo(() => ({ ...BUILTIN_NODE_TYPES, ...(pyNodeTypes || {}) }), [pyNodeTypes]);

  const nodeEditorMap = {};
  const nodeHasEditorMap = {};
  pyNodes.forEach((node, idx) => {
    if (node && node.id !== undefined) {
      nodeEditorMap[node.id] = nodeEditors[idx];
      const data = node.data || {};
      const typeSpec = allNodeTypes[node.type] || {};
      const realKeys = Object.keys(data).filter((k) => k !== "view_idx");
      nodeHasEditorMap[node.id] = realKeys.length > 0 || !!typeSpec.schema;
    }
  });

  const edgeEditorMap = {};
  const edgeHasEditorMap = {};
  (pyEdges || []).forEach((edge, idx) => {
    if (edge && edge.id !== undefined) {
      edgeEditorMap[edge.id] = edgeEditors[idx];
      const data = edge.data || {};
      edgeHasEditorMap[edge.id] = Object.keys(data).length > 0;
    }
  });

  const hydratedNodes = useMemo(() => {
    return (pyNodes || []).map((node, idx) => {
      const data = node.data || {};
      const viewIndex = data.view_idx;
      const baseView = views[viewIndex];
      const editorView = nodeEditors[idx];
      const typeSpec = allNodeTypes[node.type] || {};
      const realKeys = Object.keys(data).filter((k) => k !== "view_idx");
      const hasEditor = realKeys.length > 0 || !!typeSpec.schema;
      return {
        ...node,
        data: {
          ...data,
          view: baseView,
          editor: editorView,
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

  return (
    <div style={{ width: "100%", height: "100%" }}>
      <ReactFlowProvider>
        <FlowInner
          model={model}
          hydratedNodes={hydratedNodes}
          pyNodes={pyNodes || []}
          hydratedEdges={hydratedEdges}
          selectionSetter={setSelection}
          currentSelection={selection}
          views={views}
          viewportSetter={setViewport}
          defaultEdgeOptions={defaultEdgeOptions}
          nodeTypes={hydratedNodeTypes}
          editable={editable}
          enableConnect={enableConnect}
          enableDelete={enableDelete}
          enableMultiselect={enableMultiselect}
          showMinimap={showMinimap}
          syncMode={syncMode}
          debounceMs={debounceMs}
          viewport={viewport}
        />
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
          {selection.nodes.length && editorMode === "side" && nodeHasEditorMap[selection.nodes[0]] ? nodeEditorMap[selection.nodes[0]] : null}
          {selection.edges.length && !selection.nodes.length && edgeHasEditorMap[selection.edges[0]] ? edgeEditorMap[selection.edges[0]] : null}
        </Panel>
      </ReactFlowProvider>
    </div>
  );
}
