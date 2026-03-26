import React from "react";
import { Background, Controls, Handle, MiniMap, NodeToolbar, Panel, Position, ReactFlow, ReactFlowProvider, addEdge, useEdgesState, useNodesState, useReactFlow, useStore } from "@xyflow/react";
import "@xyflow/react/dist/style.css";

const { useCallback, useEffect, useMemo, useRef, useState } = React;

const BUILTIN_NODE_TYPES = {
  panel: { label: "Panel" },
  default: { label: "Default" },
  minimal: { label: "Minimal", minimal: true },
};

const viewWrapperClassName = "rf-node-view-wrapper rf-node-view-wrapper--bokeh-scale nodrag nopan nowheel";

const figureStylesheet = `
.bk-Canvas {
  transform: scale(var(--rf-inverse-zoom));
  transform-origin: top left;
  width: calc(var(--rf-zoom) * 100%);
  height: calc(var(--rf-zoom) * 100%);
}`.trim();

function renderHandles(direction, handles) {
  // Explicitly empty array → no handles
  if (Array.isArray(handles) && handles.length === 0) {
    return null;
  }
  // null/undefined → default handle
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
        {renderHandles("input", spec.inputs)}
        <div className="rf-node-label" style={{ fontWeight: 600, margin: displayLabel ? "0.2em 0 0.5em 0.5em" : "0" }}>
          {displayLabel}
        </div>
        {(showView || showInlineEditor) && (
          <div>
            {showView ? <div className={viewWrapperClassName} style={viewWrapperStyle}>{data.view}</div> : null}
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
  nodeEditors,
  edgeEditors,
  colorMode,
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
  const hydrationFrameRef = useRef(null);
  const edgeHydrationFrameRef = useRef(null);
  const lastHydrated = useRef({ nodeRevision: null, edgesSig: null, edgeEditorsSig: null });
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
    if (nodeUpdateCount === lastHydrated.current.nodeRevision) {
      return;
    }
    const expectedViewCount = (pyNodes || []).reduce((maxIdx, node) => {
      const idx = node?.data?.view_idx;
      if (Number.isFinite(idx)) {
        return Math.max(maxIdx, idx);
      }
      return maxIdx;
    }, -1) + 1;
    const expectedEditorCount = (pyNodes || []).length;
    if ((views || []).length !== expectedViewCount || (nodeEditors || []).length !== expectedEditorCount) {
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
      hydrationFrameRef.current = null;
    });
  }, [hydratedNodes, pyNodes, setNodes, views, nodeEditors, nodeUpdateCount]);

  useEffect(() => {
    const edgesSig = signature(hydratedEdges);
    const editorsSig = signature((edgeEditors || []).map((editor) => editor?.props?.id ?? null));
    if (edgesSig !== lastHydrated.current.edgesSig || editorsSig !== lastHydrated.current.edgeEditorsSig) {
      lastHydrated.current.edgesSig = edgesSig;
      lastHydrated.current.edgeEditorsSig = editorsSig;
      if (edgeHydrationFrameRef.current !== null) {
        cancelAnimationFrame(edgeHydrationFrameRef.current);
      }
      edgeHydrationFrameRef.current = requestAnimationFrame(() => {
        setEdges((curr) => (areEqual(curr, hydratedEdges) ? curr : hydratedEdges));
        edgeHydrationFrameRef.current = null;
      });
    }
  }, [hydratedEdges, setEdges, edgeEditors]);

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
      colorMode={colorMode}
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

  return (
    <div style={{ width: "100%", height: "100%" }}>
      <ReactFlowProvider>
        <FlowInner
          model={model}
          hydratedNodes={hydratedNodes}
          pyNodes={pyNodes || []}
          nodeUpdateCount={nodeUpdateCount}
          hydratedEdges={hydratedEdges}
          selectionSetter={setSelection}
          currentSelection={selection}
          views={views}
          viewportSetter={setViewport}
          defaultEdgeOptions={defaultEdgeOptions}
          colorMode={colorMode}
          nodeTypes={hydratedNodeTypes}
          nodeEditors={nodeEditors}
          edgeEditors={edgeEditors}
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
