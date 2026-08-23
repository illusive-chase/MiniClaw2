import { useCallback, useEffect, useMemo, useState } from "react";
import ReactFlow, {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  ReactFlowProvider,
  type Node as RFNodeType,
  type NodeChange,
  type NodeMouseHandler,
} from "reactflow";
import "reactflow/dist/style.css";

import { ApiError, getUserTemplate, listModelPresets, rewriteUserTemplate } from "../api";
import {
  DependencyEdge,
  ResumeEdge,
} from "../canvas/edges/TimelineEdge";
import {
  TemplateNode,
  TemplatePortNode,
  type TemplateNodeData,
  type TemplatePortNodeData,
} from "../canvas/nodes/TemplateNode";
import {
  addInputPort,
  addNode,
  argumentReferences,
  buildRewritePayload,
  connectInputPort,
  connectNodes,
  disconnectInputPort,
  disconnectNodes,
  inputDeps,
  inputReferences,
  internalDeps,
  isDirty,
  layoutTemplateGraph,
  pruneArguments,
  removeInputPort,
  removeNode,
  renameInputPort,
  resolveArguments,
  scanPlaceholders,
  setInputDescription,
  setResumeFrom,
  templateEditorStateFromDetail,
  updateNode,
  upsertArgument,
  validateEditorState,
  warningNames,
  type EditorNode,
  type TemplateEditorState,
  type TemplateRewritePayload,
} from "../templateEditor";
import {
  modelPresetDetail,
  modelPresetLabel,
  selectableModelPresets,
} from "../modelPresets";
import type { ModelPreset } from "../types";

const NODE_TYPES = {
  templateNode: TemplateNode,
  templatePort: TemplatePortNode,
};

const EDGE_TYPES = {
  dependency: DependencyEdge,
  resume: ResumeEdge,
};

type Props = {
  /** Slug to edit; null closes the editor. */
  slug: string | null;
  onClose: () => void;
  /** Fires after a successful save so the library dock can refresh. */
  onSaved?: (slug: string) => void;
};

/** Pending connect gesture: the author clicked a source's ↘ and now picks a
 * target. Two clicks beat drag-to-connect here because ports and nodes are
 * different shapes with different legal targets. */
type PendingLink =
  | { kind: "node"; id: string }
  | { kind: "port"; name: string }
  | null;

/**
 * Template editor: a dedicated canvas over one template's subgraph.
 *
 * Deliberately not the project Canvas: that component is built around
 * `NodeInfo` — run state, planspace lanes, commits, layout hints persisted per
 * session — none of which a template has. Reused instead are the pieces that
 * carry the visual language: React Flow with the same interaction config, the
 * dependency and resume edge renderers, and node tiles matching AgentNode's
 * geometry.
 *
 * The editor deliberately offers no run affordance (proposal §5) — a template
 * is a static definition; trying it out means instantiating it into a project.
 * Nesting another template is out of scope for v1 (§8), so there is no
 * template-drop target here.
 */
export function TemplateEditor(props: Props) {
  return (
    <ReactFlowProvider>
      <TemplateEditorInner {...props} />
    </ReactFlowProvider>
  );
}

function TemplateEditorInner({ slug, onClose, onSaved }: Props) {
  const [state, setState] = useState<TemplateEditorState | null>(null);
  const [savedPayload, setSavedPayload] = useState<TemplateRewritePayload | null>(
    null,
  );
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveNotice, setSaveNotice] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [pendingLink, setPendingLink] = useState<PendingLink>(null);
  const [newPortName, setNewPortName] = useState("");
  const [positions, setPositions] = useState<
    Record<string, { x: number; y: number }>
  >({});
  const [modelPresets, setModelPresets] = useState<ModelPreset[]>([]);

  /* Presets are global and immutable for the lifetime of the editor, so they
   * load once rather than per template. A failure leaves the list empty: the
   * model picker then shows the raw id it read from the template, which is
   * still editable text — it must not block editing prompts or topology. */
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const presets = await listModelPresets();
        if (!cancelled) setModelPresets(presets);
      } catch {
        if (!cancelled) setModelPresets([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!slug) {
      setState(null);
      setSavedPayload(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    setSaveError(null);
    setSaveNotice(null);
    setActionError(null);
    setPendingLink(null);
    setSelectedNodeId(null);
    setPositions({});
    void (async () => {
      try {
        const detail = await getUserTemplate(slug);
        if (cancelled) return;
        const loaded = templateEditorStateFromDetail(detail);
        setState(loaded);
        setSavedPayload(buildRewritePayload(loaded));
      } catch (err) {
        if (!cancelled) setLoadError(errorText(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [slug]);

  const dirty = state ? isDirty(state, savedPayload) : false;

  /* Any structural edit invalidates the "saved" banner and the pending link,
   * whose source may no longer exist. */
  const apply = useCallback(
    (next: TemplateEditorState | string) => {
      if (typeof next === "string") {
        setActionError(next);
        return;
      }
      setActionError(null);
      setSaveNotice(null);
      setState(next);
    },
    [],
  );

  const issues = useMemo(
    () => (state ? validateEditorState(state) : []),
    [state],
  );
  const resolvedArguments = useMemo(
    () => (state ? resolveArguments(state) : []),
    [state],
  );
  const argumentUsage = useMemo(
    () => (state ? argumentReferences(state.nodes) : {}),
    [state],
  );
  const inputUsage = useMemo(
    () => (state ? inputReferences(state.nodes) : {}),
    [state],
  );
  const danglingNames = useMemo(
    () => (state ? warningNames(state.warnings, "dangling_argument") : []),
    [state],
  );

  /* Auto-layout supplies a position for anything the author has not dragged;
   * dragged nodes keep theirs. Newly added nodes and ports therefore appear in
   * a sensible column without disturbing existing placement. */
  const layout = useMemo(
    () => (state ? layoutTemplateGraph(state) : { nodes: {}, ports: {} }),
    [state],
  );

  const handleStartNodeLink = useCallback((nodeId: string) => {
    setActionError(null);
    setPendingLink((current) =>
      current?.kind === "node" && current.id === nodeId
        ? null
        : { kind: "node", id: nodeId },
    );
  }, []);

  const handleStartPortLink = useCallback((port: string) => {
    setActionError(null);
    setPendingLink((current) =>
      current?.kind === "port" && current.name === port
        ? null
        : { kind: "port", name: port },
    );
  }, []);

  const handleRemoveNode = useCallback((nodeId: string) => {
    setPendingLink(null);
    setSelectedNodeId((current) => (current === nodeId ? null : current));
    setState((current) => (current ? removeNode(current, nodeId) : current));
    /* Drop the drag position too: `addNode` reuses the lowest free id, so a
     * later node could inherit this one's placement. */
    setPositions((current) => omitKey(current, `node:${nodeId}`));
    setSaveNotice(null);
    setActionError(null);
  }, []);

  const handleRemovePort = useCallback((port: string) => {
    setPendingLink(null);
    setState((current) => (current ? removeInputPort(current, port) : current));
    setPositions((current) => omitKey(current, `port:${port}`));
    setSaveNotice(null);
    setActionError(null);
  }, []);

  const rfNodes = useMemo(() => {
    if (!state) return [] as RFNodeType[];
    const out: RFNodeType[] = [];
    for (const input of state.inputs) {
      const consumerIds = inputUsage[input.name] ?? [];
      const data: TemplatePortNodeData = {
        name: input.name,
        description: input.description,
        consumerIds,
        unreferenced: consumerIds.length === 0,
        linking: pendingLink?.kind === "port" && pendingLink.name === input.name,
        onStartLink: handleStartPortLink,
        onRemove: handleRemovePort,
      };
      const id = `port:${input.name}`;
      out.push({
        id,
        type: "templatePort",
        position: positions[id] ?? layout.ports[input.name] ?? { x: 0, y: 0 },
        data,
        selected: false,
      });
    }
    for (const node of state.nodes) {
      const scanned = scanPlaceholders(node.prompt);
      const ports = new Set([...inputDeps(node), ...scanned.inputPorts]);
      const data: TemplateNodeData = {
        node,
        argumentNames: scanned.argumentNames,
        inputPorts: [...ports],
        linking: pendingLink?.kind === "node" && pendingLink.id === node.id,
        onStartLink: handleStartNodeLink,
        onRemove: handleRemoveNode,
      };
      const id = `node:${node.id}`;
      out.push({
        id,
        type: "templateNode",
        position: positions[id] ?? layout.nodes[node.id] ?? { x: 0, y: 0 },
        data,
        selected: node.id === selectedNodeId,
      });
    }
    return out;
  }, [
    handleRemoveNode,
    handleRemovePort,
    handleStartNodeLink,
    handleStartPortLink,
    inputUsage,
    layout,
    pendingLink,
    positions,
    selectedNodeId,
    state,
  ]);

  const rfEdges = useMemo(() => {
    if (!state) return [];
    const edges = [];
    for (const node of state.nodes) {
      for (const dep of internalDeps(node)) {
        const isResume = node.resume_from === dep;
        edges.push({
          id: `dep:${dep}->${node.id}`,
          source: `node:${dep}`,
          target: `node:${node.id}`,
          type: isResume ? "resume" : "dependency",
          data: {},
        });
      }
      for (const port of inputDeps(node)) {
        edges.push({
          id: `in:${port}->${node.id}`,
          source: `port:${port}`,
          target: `node:${node.id}`,
          type: "dependency",
          data: { root: true },
        });
      }
    }
    return edges;
  }, [state]);

  /* Positions are editor-local: a template has no persisted layout, and
   * inventing one would put view state into the on-disk schema. */
  const onNodesChange = useCallback((changes: NodeChange[]) => {
    const moved: Record<string, { x: number; y: number }> = {};
    for (const change of changes) {
      if (change.type === "position" && change.position) {
        moved[change.id] = { x: change.position.x, y: change.position.y };
      }
    }
    if (Object.keys(moved).length === 0) return;
    setPositions((current) => ({ ...current, ...moved }));
  }, []);

  /* Click resolves a pending link, or selects. Legal targets differ by source:
   * a port may only feed a body node, and a node may only depend on another
   * node. */
  const onNodeClick = useCallback<NodeMouseHandler>(
    (_event, rfNode) => {
      const isPort = rfNode.type === "templatePort";
      const rawId = isPort
        ? (rfNode.data as TemplatePortNodeData).name
        : (rfNode.data as TemplateNodeData).node.id;
      if (pendingLink && state) {
        if (isPort) {
          setActionError("依赖的终点必须是模板节点，不能是输入端口");
          return;
        }
        const next =
          pendingLink.kind === "port"
            ? connectInputPort(state, pendingLink.name, rawId)
            : connectNodes(state, pendingLink.id, rawId);
        setPendingLink(null);
        apply(next);
        return;
      }
      setSelectedNodeId(isPort ? null : rawId);
    },
    [apply, pendingLink, state],
  );

  const onEdgeClick = useCallback(
    (event: React.MouseEvent, edge: { id: string; source: string; target: string }) => {
      event.stopPropagation();
      if (!state) return;
      const target = edge.target.replace(/^node:/, "");
      if (edge.source.startsWith("port:")) {
        apply(
          disconnectInputPort(state, edge.source.slice("port:".length), target),
        );
        return;
      }
      apply(disconnectNodes(state, edge.source.replace(/^node:/, ""), target));
    },
    [apply, state],
  );

  const onPaneClick = useCallback(() => {
    setPendingLink(null);
    setSelectedNodeId(null);
  }, []);

  const handleSave = useCallback(async () => {
    if (!state || !slug) return;
    setSaving(true);
    setSaveError(null);
    setSaveNotice(null);
    try {
      const detail = await rewriteUserTemplate(slug, buildRewritePayload(state));
      /* Reload from the response so scanned arguments the backend persisted,
       * and its fresh warnings, replace the local guesses. Node prompts round
       * trip unchanged, so nothing the author typed is lost. */
      const reloaded = templateEditorStateFromDetail(detail);
      setState(reloaded);
      setSavedPayload(buildRewritePayload(reloaded));
      setSaveNotice("已保存");
      onSaved?.(slug);
    } catch (err) {
      // The backend validated a candidate directory and left the old template
      // intact, so keeping the editor state is safe and correct.
      setSaveError(errorText(err));
    } finally {
      setSaving(false);
    }
  }, [onSaved, slug, state]);

  const handleClose = useCallback(() => {
    if (dirty && !window.confirm("有未保存的修改，确认关闭？")) return;
    onClose();
  }, [dirty, onClose]);

  useEffect(() => {
    if (!slug) return;
    const handler = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) {
        return;
      }
      if (pendingLink) {
        setPendingLink(null);
        return;
      }
      handleClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [handleClose, pendingLink, slug]);

  if (!slug) return null;

  const selectedNode =
    state?.nodes.find((node) => node.id === selectedNodeId) ?? null;

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-surface">
      <header className="flex items-center justify-between gap-4 border-b border-line bg-surface-raised px-4 py-2.5">
        <div className="flex min-w-0 items-center gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-[10px] font-medium uppercase tracking-[0.16em] text-ink-subtle">
                Template editor
              </span>
              {dirty && (
                <span className="rounded border border-state-waiting/40 bg-state-waiting-soft px-1.5 py-0.5 text-[9px] font-medium uppercase text-state-waiting">
                  未保存
                </span>
              )}
            </div>
            <div className="truncate font-mono text-[11px] text-ink-muted">{slug}</div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {saveNotice && (
            <span className="text-[11px] text-state-done">{saveNotice}</span>
          )}
          <button
            type="button"
            onClick={handleClose}
            className="rounded-md border border-line bg-surface px-3 py-1.5 text-xs text-ink-muted transition hover:bg-surface-sunken hover:text-ink"
          >
            关闭
          </button>
          <button
            type="button"
            disabled={saving || loading || !state || issues.length > 0}
            onClick={() => void handleSave()}
            title={
              issues.length > 0
                ? "存在校验错误，无法保存"
                : "序列化回模板目录"
            }
            className="rounded-md bg-brand px-3 py-1.5 text-xs font-medium text-white shadow-card transition hover:brightness-[0.95] disabled:opacity-40"
          >
            {saving ? "保存中…" : "保存"}
          </button>
        </div>
      </header>

      {(loadError || saveError || actionError) && (
        <div className="border-b border-state-error/30 bg-state-error-soft px-4 py-2 text-xs text-state-error">
          {loadError ?? saveError ?? actionError}
        </div>
      )}

      {pendingLink && (
        <div className="border-b border-brand/30 bg-brand-soft px-4 py-2 text-xs text-brand-ink">
          {pendingLink.kind === "port"
            ? `点击一个节点，把输入端口 ${pendingLink.name} 连到它；Esc 取消。`
            : `点击一个节点，让它依赖 ${pendingLink.id}；Esc 取消。`}
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        <div className="relative min-w-0 flex-1">
          {loading && (
            <div className="absolute inset-0 z-10 flex items-center justify-center text-xs text-ink-muted">
              加载中…
            </div>
          )}
          <ReactFlow
            nodes={rfNodes}
            edges={rfEdges}
            onNodesChange={onNodesChange}
            onNodeClick={onNodeClick}
            onEdgeClick={onEdgeClick}
            onPaneClick={onPaneClick}
            nodeTypes={NODE_TYPES}
            edgeTypes={EDGE_TYPES}
            defaultEdgeOptions={{
              markerEnd: {
                type: MarkerType.ArrowClosed,
                color: "rgb(var(--border-strong))",
                width: 14,
                height: 14,
              },
            }}
            minZoom={0.3}
            maxZoom={1.6}
            zoomOnDoubleClick={false}
            panOnScroll
            panOnDrag={[2]}
            nodesConnectable={false}
            deleteKeyCode={null}
            elevateNodesOnSelect={false}
            proOptions={{ hideAttribution: true }}
            fitView
            fitViewOptions={{ padding: 0.25 }}
          >
            <Background
              variant={BackgroundVariant.Dots}
              gap={16}
              size={1}
              color="rgb(var(--grid-line))"
            />
            <Controls
              className="!border !border-line !bg-surface-raised !shadow-card"
              showInteractive={false}
            />
          </ReactFlow>

          <div className="pointer-events-none absolute left-3 top-3 flex flex-col gap-2">
            <button
              type="button"
              disabled={!state}
              onClick={() => {
                if (!state) return;
                const { state: next, node } = addNode(state);
                apply(next);
                setSelectedNodeId(node.id);
              }}
              className="pointer-events-auto rounded-md border border-line bg-surface-raised px-2.5 py-1.5 text-[11px] text-ink shadow-card transition hover:border-brand/60 disabled:opacity-40"
            >
              + 节点
            </button>
          </div>
        </div>

        <aside className="flex w-[380px] shrink-0 flex-col overflow-y-auto border-l border-line bg-surface-raised">
          <PanelSection title="模板">
            <LabeledInput
              label="名称"
              value={state?.name ?? ""}
              disabled={!state}
              onChange={(value) =>
                state && apply({ ...state, name: value })
              }
            />
            <LabeledInput
              label="简介"
              value={state?.brief ?? ""}
              disabled={!state}
              onChange={(value) =>
                state && apply({ ...state, brief: value })
              }
            />
          </PanelSection>

          <PanelSection title={`输入端口 (${state?.inputs.length ?? 0})`}>
            <p className="text-[10.5px] leading-snug text-ink-subtle">
              端口是模板的具名上游。实例化时每个端口必须绑定一个现有节点。
            </p>
            {state?.inputs.map((input) => {
              const consumers = inputUsage[input.name] ?? [];
              return (
                <div
                  key={input.name}
                  className="rounded-md border border-line bg-surface px-2.5 py-2"
                >
                  <div className="flex items-center gap-2">
                    <input
                      type="text"
                      defaultValue={input.name}
                      key={`name-${input.name}`}
                      onBlur={(event) => {
                        const value = event.target.value.trim();
                        if (value === input.name) return;
                        const next = renameInputPort(state, input.name, value);
                        if (typeof next === "string") {
                          event.target.value = input.name;
                          setActionError(next);
                          return;
                        }
                        // Carry the tile's placement to the new key so the
                        // rename does not visually relocate the port.
                        setPositions((current) =>
                          renameKey(current, `port:${input.name}`, `port:${value}`),
                        );
                        apply(next);
                      }}
                      className="min-w-0 flex-1 rounded border border-line bg-surface-sunken px-2 py-1 font-mono text-[11px] text-ink-strong focus:border-brand focus:outline-none"
                      title="重命名会同步改写所有 in: 依赖与 {{input.x}} 占位符"
                    />
                    <button
                      type="button"
                      onClick={() => handleRemovePort(input.name)}
                      className="rounded px-1.5 py-1 text-[11px] text-ink-subtle transition hover:bg-state-error-soft hover:text-state-error"
                      title={`删除端口 ${input.name}`}
                      aria-label={`删除端口 ${input.name}`}
                    >
                      ×
                    </button>
                  </div>
                  <input
                    type="text"
                    value={input.description}
                    placeholder="描述（实例化弹窗中展示）"
                    onChange={(event) =>
                      apply(
                        setInputDescription(
                          state,
                          input.name,
                          event.target.value,
                        ),
                      )
                    }
                    className="mt-1.5 w-full rounded border border-line bg-surface-sunken px-2 py-1 text-[11px] text-ink placeholder:text-ink-subtle focus:border-brand focus:outline-none"
                  />
                  <div className="mt-1.5 text-[10px] text-ink-subtle">
                    {consumers.length === 0 ? (
                      <span className="text-state-waiting">未被任何节点引用</span>
                    ) : (
                      <span className="font-mono">被引用：{consumers.join(", ")}</span>
                    )}
                  </div>
                </div>
              );
            })}
            <div className="flex items-center gap-2">
              <input
                type="text"
                value={newPortName}
                placeholder="新端口名，如 alpha_branch"
                disabled={!state}
                onChange={(event) => setNewPortName(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key !== "Enter" || !state) return;
                  const next = addInputPort(state, newPortName);
                  if (typeof next === "string") {
                    setActionError(next);
                    return;
                  }
                  apply(next);
                  setNewPortName("");
                }}
                className="min-w-0 flex-1 rounded border border-line bg-surface-sunken px-2 py-1 font-mono text-[11px] text-ink placeholder:text-ink-subtle focus:border-brand focus:outline-none disabled:opacity-40"
              />
              <button
                type="button"
                disabled={!state || !newPortName.trim()}
                onClick={() => {
                  if (!state) return;
                  const next = addInputPort(state, newPortName);
                  if (typeof next === "string") {
                    setActionError(next);
                    return;
                  }
                  apply(next);
                  setNewPortName("");
                }}
                className="rounded border border-line bg-surface px-2 py-1 text-[11px] text-ink-muted transition hover:border-brand/60 hover:text-ink disabled:opacity-40"
              >
                添加
              </button>
            </div>
          </PanelSection>

          <PanelSection title={`参数 (${resolvedArguments.length})`}>
            <p className="text-[10.5px] leading-snug text-ink-subtle">
              提示词里写 <code className="font-mono">{"{{name}}"}</code>{" "}
              即为参数。无默认值的参数在实例化时必填。
            </p>
            {danglingNames.length > 0 && (
              <div className="flex items-center justify-between gap-2 rounded-md border border-state-waiting/40 bg-state-waiting-soft px-2.5 py-2 text-[11px] text-state-waiting">
                <span className="min-w-0">
                  {danglingNames.length} 个悬空声明：{danglingNames.join(", ")}
                </span>
                <button
                  type="button"
                  onClick={() => state && apply(pruneArguments(state, danglingNames))}
                  className="shrink-0 rounded border border-state-waiting/50 px-2 py-1 text-[10.5px] transition hover:bg-state-waiting/10"
                >
                  一键清理
                </button>
              </div>
            )}
            {resolvedArguments.length === 0 && (
              <div className="text-[11px] text-ink-subtle">暂无参数。</div>
            )}
            {resolvedArguments.map((argument) => {
              const users = argumentUsage[argument.name] ?? [];
              const dangling = danglingNames.includes(argument.name);
              return (
                <div
                  key={argument.name}
                  className={
                    "rounded-md border px-2.5 py-2 " +
                    (dangling
                      ? "border-state-waiting/40 bg-state-waiting-soft"
                      : "border-line bg-surface")
                  }
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate font-mono text-[11px] text-ink-strong">
                      {`{{${argument.name}}}`}
                    </span>
                    <span className="flex shrink-0 items-center gap-1">
                      {!argument.declared && (
                        <span
                          className="rounded border border-line-strong px-1 py-0.5 text-[8.5px] uppercase text-ink-muted"
                          title="仅从提示词扫描出来，template.yaml 尚未声明；保存即正式声明"
                        >
                          未声明
                        </span>
                      )}
                      <span
                        className={
                          "rounded border px-1 py-0.5 text-[8.5px] uppercase " +
                          (argument.default === null
                            ? "border-brand/40 bg-brand-soft text-brand-ink"
                            : "border-line bg-surface text-ink-muted")
                        }
                      >
                        {argument.default === null ? "必填" : "可选"}
                      </span>
                    </span>
                  </div>
                  <input
                    type="text"
                    value={argument.description}
                    placeholder="描述（实例化弹窗中展示）"
                    onChange={(event) =>
                      state &&
                      apply(
                        upsertArgument(state, argument.name, {
                          description: event.target.value,
                        }),
                      )
                    }
                    className="mt-1.5 w-full rounded border border-line bg-surface-sunken px-2 py-1 text-[11px] text-ink placeholder:text-ink-subtle focus:border-brand focus:outline-none"
                  />
                  {/* "No default" and "default is the empty string" are
                    * different states, so a checkbox owns the distinction
                    * rather than leaving one blank field to mean both. */}
                  <label className="mt-1.5 flex items-center gap-1.5 text-[10.5px] text-ink-muted">
                    <input
                      type="checkbox"
                      checked={argument.default !== null}
                      onChange={(event) =>
                        state &&
                        apply(
                          upsertArgument(state, argument.name, {
                            default: event.target.checked ? "" : null,
                          }),
                        )
                      }
                    />
                    有默认值
                  </label>
                  {argument.default !== null && (
                    <input
                      type="text"
                      value={argument.default}
                      placeholder="默认值（可以是空串）"
                      onChange={(event) =>
                        state &&
                        apply(
                          upsertArgument(state, argument.name, {
                            default: event.target.value,
                          }),
                        )
                      }
                      className="mt-1 w-full rounded border border-line bg-surface-sunken px-2 py-1 text-[11px] text-ink placeholder:text-ink-subtle focus:border-brand focus:outline-none"
                    />
                  )}
                  <div className="mt-1.5 text-[10px] text-ink-subtle">
                    {users.length === 0 ? (
                      <span className="text-state-waiting">无提示词引用它</span>
                    ) : (
                      <span className="font-mono">被引用：{users.join(", ")}</span>
                    )}
                  </div>
                </div>
              );
            })}
          </PanelSection>

          <PanelSection title="节点">
            {!selectedNode && (
              <div className="text-[11px] text-ink-subtle">
                在画布上选中一个节点来编辑它的提示词。
              </div>
            )}
            {selectedNode && state && (
              <NodeInspector
                node={selectedNode}
                nodes={state.nodes}
                modelPresets={modelPresets}
                onPatch={(patch) =>
                  apply(updateNode(state, selectedNode.id, patch))
                }
                onSetResume={(resumeFrom) =>
                  apply(setResumeFrom(state, selectedNode.id, resumeFrom))
                }
              />
            )}
          </PanelSection>

          {issues.length > 0 && (
            <PanelSection title={`校验错误 (${issues.length})`}>
              <ul className="space-y-1">
                {issues.map((issue, index) => (
                  <li
                    key={`${issue.target}-${index}`}
                    className="rounded border border-state-error/30 bg-state-error-soft px-2 py-1 text-[11px] leading-snug text-state-error"
                  >
                    <span className="font-mono">{issue.target}</span>：{issue.message}
                  </li>
                ))}
              </ul>
            </PanelSection>
          )}
        </aside>
      </div>
    </div>
  );
}

function NodeInspector({
  node,
  nodes,
  modelPresets,
  onPatch,
  onSetResume,
}: {
  node: EditorNode;
  nodes: EditorNode[];
  modelPresets: ModelPreset[];
  onPatch: (patch: Partial<EditorNode>) => void;
  onSetResume: (resumeFrom: string | null) => void;
}) {
  const deps = internalDeps(node);
  const selectablePresets = selectableModelPresets(modelPresets);
  /* A template may name a model that has since been retired or made
   * compatibility-only. Keeping it as an option means opening the editor never
   * silently rewrites the node's model — the author sees what is stored and
   * chooses whether to change it. */
  const modelIsListed =
    !node.model_preset_id ||
    selectablePresets.some((preset) => preset.id === node.model_preset_id);
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <span className="font-mono text-[11px] text-ink-strong">{node.id}</span>
        <select
          value={node.category}
          onChange={(event) => {
            const category = event.target.value as EditorNode["category"];
            onPatch(
              category === "review"
                ? {
                    category,
                    subtype: node.subtype ?? "agentic_review",
                    brief:
                      node.brief ?? { check_what: "", expected: "", abnormal: "" },
                    // Review nodes have their own deliverable contract; leaving
                    // an artifact intent set here would be rejected on save.
                    artifact_mode: "default",
                    artifact_spec: "",
                  }
                : { category, subtype: null, brief: null },
            );
          }}
          className="rounded border border-line bg-surface-sunken px-1.5 py-1 text-[11px] text-ink focus:border-brand focus:outline-none"
        >
          <option value="regular">regular</option>
          <option value="planning">planning</option>
          <option value="review">review</option>
        </select>
      </div>

      {node.category === "review" && (
        <>
          <select
            value={node.subtype ?? "agentic_review"}
            onChange={(event) =>
              onPatch({ subtype: event.target.value as EditorNode["subtype"] })
            }
            className="w-full rounded border border-line bg-surface-sunken px-2 py-1 text-[11px] text-ink focus:border-brand focus:outline-none"
          >
            <option value="agentic_review">agentic_review</option>
            <option value="human_interact_review">human_interact_review</option>
            <option value="code_review">code_review</option>
          </select>
          {(["check_what", "expected", "abnormal"] as const).map((field) => (
            <LabeledInput
              key={field}
              label={field}
              value={node.brief?.[field] ?? ""}
              onChange={(value) =>
                onPatch({
                  brief: {
                    check_what: node.brief?.check_what ?? "",
                    expected: node.brief?.expected ?? "",
                    abnormal: node.brief?.abnormal ?? "",
                    [field]: value,
                  },
                })
              }
            />
          ))}
        </>
      )}

      <label className="flex flex-col gap-1">
        <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
          模型
        </span>
        <select
          value={node.model_preset_id ?? ""}
          onChange={(event) =>
            onPatch({ model_preset_id: event.target.value || null })
          }
          disabled={node.resume_from !== null}
          title={
            node.resume_from
              ? `恢复会话的节点沿用 ${node.resume_from} 的模型，无法单独设置`
              : modelPresetDetail(modelPresets, node.model_preset_id) ||
                "应用模板时，该节点将使用这里指定的模型"
          }
          className="w-full rounded border border-line bg-surface-sunken px-2 py-1 text-[11px] text-ink focus:border-brand focus:outline-none disabled:opacity-50"
        >
          <option value="">（跟随项目模型）</option>
          {selectablePresets.map((preset) => (
            <option key={preset.id} value={preset.id}>
              {modelPresetLabel(modelPresets, preset.id)}
            </option>
          ))}
          {!modelIsListed && node.model_preset_id && (
            <option value={node.model_preset_id}>
              {modelPresetLabel(modelPresets, node.model_preset_id)}（当前不可选）
            </option>
          )}
        </select>
        {node.resume_from ? (
          <span className="text-[10px] leading-snug text-ink-subtle">
            沿用 {node.resume_from} 的模型（恢复会话不能换模型）。
          </span>
        ) : (
          !modelIsListed &&
          node.model_preset_id && (
            <span className="text-[10px] leading-snug text-state-error">
              该模型已不可用于新工作，应用模板时会失败；请另选一个。
            </span>
          )
        )}
      </label>

      {node.category !== "review" && (
        <label className="flex flex-col gap-1">
          <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
            产出物
          </span>
          <select
            value={node.artifact_mode}
            onChange={(event) =>
              onPatch({
                artifact_mode: event.target.value as EditorNode["artifact_mode"],
              })
            }
            title="应用模板时，该节点会被要求产出这种形态的产出物"
            className="w-full rounded border border-line bg-surface-sunken px-2 py-1 text-[11px] text-ink focus:border-brand focus:outline-none"
          >
            <option value="default">（不要求产出物）</option>
            <option value="markdown">markdown</option>
            <option value="html">html</option>
            <option value="custom">custom（自定义描述）</option>
          </select>
          {node.artifact_mode === "custom" && (
            <textarea
              value={node.artifact_spec}
              onChange={(event) =>
                onPatch({ artifact_spec: event.target.value })
              }
              rows={3}
              placeholder="描述期望的产出物，这段文字会原样进入 agent 的提示。"
              className="w-full resize-y rounded border border-line bg-surface-sunken px-2 py-1.5 text-[11px] leading-snug text-ink placeholder:text-ink-subtle focus:border-brand focus:outline-none"
            />
          )}
        </label>
      )}

      <label className="flex flex-col gap-1">
        <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
          提示词（模板原文，含占位符）
        </span>
        <textarea
          value={node.prompt}
          onChange={(event) => onPatch({ prompt: event.target.value })}
          rows={10}
          spellCheck={false}
          placeholder={"围绕 {{topic}} …\n参考 {{input.alpha_branch}}"}
          className="w-full resize-y rounded border border-line bg-surface-sunken px-2 py-1.5 font-mono text-[11px] leading-snug text-ink-strong placeholder:text-ink-subtle focus:border-brand focus:outline-none"
        />
      </label>

      <label className="flex flex-col gap-1">
        <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
          恢复会话来源
        </span>
        <select
          value={node.resume_from ?? ""}
          onChange={(event) => onSetResume(event.target.value || null)}
          className="w-full rounded border border-line bg-surface-sunken px-2 py-1 text-[11px] text-ink focus:border-brand focus:outline-none"
          title="选中的节点会自动成为依赖（loader 要求 resume_from 同时出现在 scheduled_deps）"
        >
          <option value="">（不恢复，全新会话）</option>
          {nodes
            .filter((candidate) => candidate.id !== node.id)
            .map((candidate) => (
              <option key={candidate.id} value={candidate.id}>
                {candidate.id}
              </option>
            ))}
        </select>
      </label>

      <div className="text-[10px] text-ink-subtle">
        依赖：
        <span className="font-mono">
          {[...deps, ...inputDeps(node).map((port) => `in:${port}`)].join(", ") ||
            "（无）"}
        </span>
        <div className="mt-0.5">点击画布上的连线可删除该依赖。</div>
      </div>
    </div>
  );
}

function PanelSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="border-b border-line px-3 py-3">
      <div className="mb-2 text-[10px] font-medium uppercase tracking-[0.16em] text-ink-subtle">
        {title}
      </div>
      <div className="space-y-2">{children}</div>
    </section>
  );
}

function LabeledInput({
  label,
  value,
  onChange,
  disabled,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
        {label}
      </span>
      <input
        type="text"
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded border border-line bg-surface-sunken px-2 py-1 text-[11.5px] text-ink-strong focus:border-brand focus:outline-none disabled:opacity-40"
      />
    </label>
  );
}

function errorText(err: unknown): string {
  if (err instanceof ApiError) return err.detail ?? err.message;
  return err instanceof Error ? err.message : String(err);
}

function omitKey<T>(map: Record<string, T>, key: string): Record<string, T> {
  if (!(key in map)) return map;
  const { [key]: _dropped, ...rest } = map;
  return rest;
}

function renameKey<T>(
  map: Record<string, T>,
  from: string,
  to: string,
): Record<string, T> {
  if (!(from in map)) return map;
  const { [from]: moved, ...rest } = map;
  return { ...rest, [to]: moved };
}
