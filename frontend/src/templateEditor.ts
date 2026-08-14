/** Pure logic behind the template editor.
 *
 * The editor is a canvas plus two side panels over one plain state object.
 * Everything that decides *what gets written back* lives here so it can be
 * tested without DOM plumbing: placeholder scanning, reference counting, the
 * input-port rename that has to rewrite every reference, and the serialization
 * into the `PUT /user-templates/{slug}` body.
 *
 * Two deliberate non-goals:
 *
 * - **Warnings are not re-derived here.** `dangling_argument` and
 *   `unreferenced_input` come from the loader and travel in the template
 *   payload's `warnings`; the panel renders those. The scanner below exists for
 *   the read-only "referenced by" lists, which no endpoint provides.
 * - **Validation mirrors the loader rather than replacing it.** The backend
 *   writes a candidate directory and reads it back through
 *   `loader._load_from_root`, so it stays authoritative. These checks only stop
 *   a save that is already known to fail, so the author sees the problem next
 *   to the field that causes it.
 */
import type {
  NodeCategory,
  ReviewBrief,
  ReviewSubtype,
  TemplateDetail,
  TemplateWarningMeta,
} from "./types";

/** Mirrors `loader.PARAM_NAME_RE`. */
export const PARAM_NAME_RE = /^[a-z][a-z0-9_]*$/;

/** Mirrors `loader._PLACEHOLDER_RE`. Deliberately permissive inside the
 * braces so `{{Bad-Name}}` is matched and then discarded, leaving it literal
 * text exactly as the backend does. */
const PLACEHOLDER_RE = /\{\{\s*([^{}]*?)\s*\}\}/g;

/** Mirrors `loader.INPUT_DEP_PREFIX`. */
export const INPUT_DEP_PREFIX = "in:";

const INPUT_PLACEHOLDER_PREFIX = "input.";

export type EditorNode = {
  /** Template-internal slug, unique within the template (`lane.yaml` id). */
  id: string;
  /** Only agent nodes are writable; a loaded verifier keeps its kind so
   * validation can refuse the save instead of silently rewriting it. */
  kind: "agent" | "verifier";
  category: NodeCategory;
  subtype: ReviewSubtype | null;
  brief: ReviewBrief | null;
  /** Template prompt source, placeholders intact. */
  prompt: string;
  /** Stable node label carried through saves even though v1 does not edit it. */
  motivation: string;
  /** Internal node ids plus `in:<port>` entries. */
  scheduled_deps: string[];
  resume_from: string | null;
  /** Model this node runs on. null means "inherit the project preset at
   * apply time" — a distinct, legitimate state, not a missing value. */
  model_preset_id: string | null;
};

export type EditorArgument = {
  name: string;
  description: string;
  /** null means required. `""` is an optional empty value — the two are
   * different states and the UI must not collapse them into one blank field. */
  default: string | null;
  /** false while the argument exists only as a prompt placeholder. Saving
   * declares it, which is the editor's main reason to exist. */
  declared: boolean;
};

export type EditorInput = {
  name: string;
  description: string;
};

export type TemplateEditorState = {
  slug: string;
  name: string;
  brief: string;
  nodes: EditorNode[];
  arguments: EditorArgument[];
  inputs: EditorInput[];
  /** Loader warnings from the last server response (load or save). */
  warnings: TemplateWarningMeta[];
};

/* ───────── loading ───────── */

/** Build editor state from a `GET /user-templates/{slug}` detail response.
 *
 * `nodes[].prompt` is only present on detail responses; `prompt_preview` is a
 * 160-character truncation and must never be used as edit source, so a node
 * without `prompt` is loaded as empty rather than silently truncated.
 */
export function templateEditorStateFromDetail(
  detail: TemplateDetail,
): TemplateEditorState {
  return {
    slug: detail.slug,
    name: detail.name,
    brief: detail.brief,
    nodes: (detail.nodes ?? []).map((node) => ({
      id: node.id,
      kind: node.kind === "verifier" ? "verifier" : "agent",
      category: node.category,
      subtype: node.subtype ?? null,
      brief: node.brief ?? null,
      prompt: node.prompt ?? "",
      motivation: node.motivation ?? "",
      scheduled_deps: [...(node.scheduled_deps ?? [])],
      resume_from: node.resume_from ?? null,
      model_preset_id: node.model_preset_id ?? null,
    })),
    arguments: detail.arguments.map((argument) => ({
      name: argument.name,
      description: argument.description,
      default: argument.default,
      declared: argument.declared,
    })),
    inputs: detail.inputs.map((input) => ({
      name: input.name,
      description: input.description,
    })),
    warnings: [...detail.warnings],
  };
}

/* ───────── placeholder scanning ───────── */

/** Argument names and input ports referenced by `text`.
 *
 * Mirrors `loader._scan_placeholders`, including its ordering and its rule
 * that a placeholder matching neither shape stays literal text.
 */
export function scanPlaceholders(text: string): {
  argumentNames: string[];
  inputPorts: string[];
} {
  const argumentNames: string[] = [];
  const inputPorts: string[] = [];
  for (const match of text.matchAll(PLACEHOLDER_RE)) {
    const body = match[1];
    if (body.startsWith(INPUT_PLACEHOLDER_PREFIX)) {
      const port = body.slice(INPUT_PLACEHOLDER_PREFIX.length);
      if (PARAM_NAME_RE.test(port) && !inputPorts.includes(port)) {
        inputPorts.push(port);
      }
    } else if (PARAM_NAME_RE.test(body) && !argumentNames.includes(body)) {
      argumentNames.push(body);
    }
  }
  return { argumentNames, inputPorts };
}

/** Node ids whose prompt references each argument, keyed by argument name. */
export function argumentReferences(
  nodes: EditorNode[],
): Record<string, string[]> {
  const references: Record<string, string[]> = {};
  for (const node of nodes) {
    for (const name of scanPlaceholders(node.prompt).argumentNames) {
      (references[name] ??= []).push(node.id);
    }
  }
  return references;
}

/** Node ids referencing each input port, via `in:<port>` or `{{input.<port>}}`.
 *
 * A node that both depends on the port and names it in prompt text appears
 * once — the list answers "who uses this port", not "how many ways".
 */
export function inputReferences(nodes: EditorNode[]): Record<string, string[]> {
  const references: Record<string, string[]> = {};
  for (const node of nodes) {
    const ports = new Set(scanPlaceholders(node.prompt).inputPorts);
    for (const port of inputDeps(node)) ports.add(port);
    for (const port of ports) (references[port] ??= []).push(node.id);
  }
  return references;
}

/** Port names this node depends on. Mirrors `TemplateNodeSpec.input_deps`. */
export function inputDeps(node: EditorNode): string[] {
  return node.scheduled_deps
    .filter((dep) => dep.startsWith(INPUT_DEP_PREFIX))
    .map((dep) => dep.slice(INPUT_DEP_PREFIX.length));
}

/** Deps naming another node in this template. Mirrors `internal_deps`. */
export function internalDeps(node: EditorNode): string[] {
  return node.scheduled_deps.filter((dep) => !dep.startsWith(INPUT_DEP_PREFIX));
}

/* ───────── arguments ───────── */

/** Declared arguments followed by those found only in prompt text.
 *
 * Mirrors `loader._resolve_parameters`: declaration order first, then scan
 * order. Dangling declarations stay in the list — the loader keeps them too,
 * and the panel needs a row to offer the cleanup on.
 */
export function resolveArguments(state: TemplateEditorState): EditorArgument[] {
  const resolved = state.arguments.map((argument) => ({ ...argument }));
  const known = new Set(resolved.map((argument) => argument.name));
  for (const node of state.nodes) {
    for (const name of scanPlaceholders(node.prompt).argumentNames) {
      if (known.has(name)) continue;
      resolved.push({ name, description: "", default: null, declared: false });
      known.add(name);
    }
  }
  return resolved;
}

/** Edit one argument's description or default, declaring it if needed.
 *
 * Touching a scanned-only argument is the author declaring it, so the entry is
 * appended to the declared list at the position `resolveArguments` already
 * showed it in.
 */
export function upsertArgument(
  state: TemplateEditorState,
  name: string,
  patch: Partial<Pick<EditorArgument, "description" | "default">>,
): TemplateEditorState {
  const existing = state.arguments.find((argument) => argument.name === name);
  if (existing) {
    return {
      ...state,
      arguments: state.arguments.map((argument) =>
        argument.name === name
          ? { ...argument, ...patch, declared: true }
          : argument,
      ),
    };
  }
  const scanned = resolveArguments(state).find(
    (argument) => argument.name === name,
  );
  if (!scanned) return state;
  return {
    ...state,
    arguments: [...state.arguments, { ...scanned, ...patch, declared: true }],
  };
}

/** Drop declared arguments the backend reported as dangling.
 *
 * The names come from the loader's `dangling_argument` warnings, never from a
 * local guess. An argument that is still referenced by a prompt would simply
 * be re-scanned on the next load, so removing it silently would be a no-op —
 * hence the caller passes warning names, not arbitrary ones.
 */
export function pruneArguments(
  state: TemplateEditorState,
  names: string[],
): TemplateEditorState {
  const drop = new Set(names);
  if (drop.size === 0) return state;
  return {
    ...state,
    arguments: state.arguments.filter((argument) => !drop.has(argument.name)),
    warnings: state.warnings.filter(
      (warning) =>
        !(warning.code === "dangling_argument" && drop.has(warning.name)),
    ),
  };
}

/** Warning names for one loader warning code, in backend order. */
export function warningNames(
  warnings: TemplateWarningMeta[],
  code: string,
): string[] {
  return warnings
    .filter((warning) => warning.code === code && warning.name)
    .map((warning) => warning.name);
}

/* ───────── input ports ───────── */

/** Add an input port declaration. Returns an error string when illegal. */
export function addInputPort(
  state: TemplateEditorState,
  name: string,
): TemplateEditorState | string {
  const trimmed = name.trim();
  if (!PARAM_NAME_RE.test(trimmed)) {
    return `端口名 ${trimmed || "(空)"} 必须匹配 [a-z][a-z0-9_]*`;
  }
  if (state.inputs.some((input) => input.name === trimmed)) {
    return `端口 ${trimmed} 已存在`;
  }
  return {
    ...state,
    inputs: [...state.inputs, { name: trimmed, description: "" }],
  };
}

/** Rename an input port and every reference to it.
 *
 * Both reference forms move together — `in:<port>` deps and
 * `{{input.<port>}}` placeholders — because leaving either behind makes the
 * template unloadable: the loader raises on a dep or placeholder naming an
 * undeclared port.
 */
export function renameInputPort(
  state: TemplateEditorState,
  from: string,
  to: string,
): TemplateEditorState | string {
  const trimmed = to.trim();
  if (!state.inputs.some((input) => input.name === from)) {
    return `端口 ${from} 不存在`;
  }
  if (trimmed === from) return state;
  if (!PARAM_NAME_RE.test(trimmed)) {
    return `端口名 ${trimmed || "(空)"} 必须匹配 [a-z][a-z0-9_]*`;
  }
  if (state.inputs.some((input) => input.name === trimmed)) {
    return `端口 ${trimmed} 已存在`;
  }
  return {
    ...state,
    inputs: state.inputs.map((input) =>
      input.name === from ? { ...input, name: trimmed } : input,
    ),
    nodes: state.nodes.map((node) => ({
      ...node,
      scheduled_deps: node.scheduled_deps.map((dep) =>
        dep === `${INPUT_DEP_PREFIX}${from}`
          ? `${INPUT_DEP_PREFIX}${trimmed}`
          : dep,
      ),
      prompt: rewritePlaceholders(node.prompt, (body) =>
        body === `${INPUT_PLACEHOLDER_PREFIX}${from}`
          ? `${INPUT_PLACEHOLDER_PREFIX}${trimmed}`
          : null,
      ),
    })),
  };
}

/** Delete an input port and its `in:` deps.
 *
 * `{{input.<port>}}` placeholders are left in the prompt text on purpose:
 * silently editing prompt bodies on a delete would destroy wording the author
 * may want to re-point at another port. Validation reports the leftovers so
 * the save is blocked until they are dealt with.
 */
export function removeInputPort(
  state: TemplateEditorState,
  name: string,
): TemplateEditorState {
  return {
    ...state,
    inputs: state.inputs.filter((input) => input.name !== name),
    nodes: state.nodes.map((node) => ({
      ...node,
      scheduled_deps: node.scheduled_deps.filter(
        (dep) => dep !== `${INPUT_DEP_PREFIX}${name}`,
      ),
    })),
    warnings: state.warnings.filter(
      (warning) =>
        !(warning.code === "unreferenced_input" && warning.name === name),
    ),
  };
}

export function setInputDescription(
  state: TemplateEditorState,
  name: string,
  description: string,
): TemplateEditorState {
  return {
    ...state,
    inputs: state.inputs.map((input) =>
      input.name === name ? { ...input, description } : input,
    ),
  };
}

/** Connect a port to a node — the canvas edge from an `⟨in:x⟩` placeholder. */
export function connectInputPort(
  state: TemplateEditorState,
  port: string,
  nodeId: string,
): TemplateEditorState {
  const dep = `${INPUT_DEP_PREFIX}${port}`;
  return {
    ...state,
    nodes: state.nodes.map((node) =>
      node.id === nodeId && !node.scheduled_deps.includes(dep)
        ? { ...node, scheduled_deps: [...node.scheduled_deps, dep] }
        : node,
    ),
  };
}

/** Cut one port→node edge, leaving the port declared.
 *
 * Unlike {@link disconnectNodes} there is no resume link to consider: a resume
 * target must name a node inside the template, never a port.
 */
export function disconnectInputPort(
  state: TemplateEditorState,
  port: string,
  nodeId: string,
): TemplateEditorState {
  const dep = `${INPUT_DEP_PREFIX}${port}`;
  return {
    ...state,
    nodes: state.nodes.map((node) =>
      node.id === nodeId
        ? {
            ...node,
            scheduled_deps: node.scheduled_deps.filter((item) => item !== dep),
          }
        : node,
    ),
  };
}

/* ───────── nodes and dependencies ───────── */

/** Append a node with an unused id. */
export function addNode(
  state: TemplateEditorState,
  overrides: Partial<EditorNode> = {},
): { state: TemplateEditorState; node: EditorNode } {
  const node: EditorNode = {
    id: nextNodeId(state.nodes),
    kind: "agent",
    category: "regular",
    subtype: null,
    brief: null,
    prompt: "",
    motivation: "",
    scheduled_deps: [],
    resume_from: null,
    model_preset_id: null,
    ...overrides,
  };
  return { state: { ...state, nodes: [...state.nodes, node] }, node };
}

function nextNodeId(nodes: EditorNode[]): string {
  const taken = new Set(nodes.map((node) => node.id));
  for (let index = nodes.length; ; index += 1) {
    const candidate = `n${index}`;
    if (!taken.has(candidate)) return candidate;
  }
}

/** Remove a node along with every dep and resume link pointing at it. */
export function removeNode(
  state: TemplateEditorState,
  nodeId: string,
): TemplateEditorState {
  return {
    ...state,
    nodes: state.nodes
      .filter((node) => node.id !== nodeId)
      .map((node) => ({
        ...node,
        scheduled_deps: node.scheduled_deps.filter((dep) => dep !== nodeId),
        resume_from: node.resume_from === nodeId ? null : node.resume_from,
      })),
  };
}

export function updateNode(
  state: TemplateEditorState,
  nodeId: string,
  patch: Partial<EditorNode>,
): TemplateEditorState {
  return {
    ...state,
    nodes: state.nodes.map((node) =>
      node.id === nodeId ? { ...node, ...patch } : node,
    ),
  };
}

/** Add a dependency edge, refusing one that would close a cycle.
 *
 * The loader rejects cycles outright, so catching it at the gesture keeps the
 * error next to the edge the author just drew instead of surfacing at save.
 */
export function connectNodes(
  state: TemplateEditorState,
  fromId: string,
  toId: string,
): TemplateEditorState | string {
  if (fromId === toId) return "节点不能依赖自己";
  const from = state.nodes.find((node) => node.id === fromId);
  const to = state.nodes.find((node) => node.id === toId);
  if (!from || !to) return "依赖的节点不存在";
  if (to.scheduled_deps.includes(fromId)) return state;
  const next = updateNode(state, toId, {
    scheduled_deps: [...to.scheduled_deps, fromId],
  });
  if (hasCycle(next.nodes)) return "该依赖会形成环";
  return next;
}

/** Remove a dependency edge, dropping the resume link it carried.
 *
 * `resume_from` must also appear in `scheduled_deps` (loader rule), so cutting
 * the dep necessarily cuts the resume link with it.
 */
export function disconnectNodes(
  state: TemplateEditorState,
  fromId: string,
  toId: string,
): TemplateEditorState {
  const to = state.nodes.find((node) => node.id === toId);
  if (!to) return state;
  return updateNode(state, toId, {
    scheduled_deps: to.scheduled_deps.filter((dep) => dep !== fromId),
    resume_from: to.resume_from === fromId ? null : to.resume_from,
  });
}

/** Point a node's resume link at one of its deps, adding the dep if missing. */
export function setResumeFrom(
  state: TemplateEditorState,
  nodeId: string,
  resumeFrom: string | null,
): TemplateEditorState | string {
  const node = state.nodes.find((item) => item.id === nodeId);
  if (!node) return "节点不存在";
  if (!resumeFrom) return updateNode(state, nodeId, { resume_from: null });
  if (resumeFrom === nodeId) return "节点不能从自己恢复会话";
  if (!state.nodes.some((item) => item.id === resumeFrom)) {
    return "恢复来源节点不存在";
  }
  const deps = node.scheduled_deps.includes(resumeFrom)
    ? node.scheduled_deps
    : [...node.scheduled_deps, resumeFrom];
  const next = updateNode(state, nodeId, {
    scheduled_deps: deps,
    resume_from: resumeFrom,
  });
  if (hasCycle(next.nodes)) return "该恢复依赖会形成环";
  return next;
}

function hasCycle(nodes: EditorNode[]): boolean {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const state = new Map<string, "visiting" | "done">();
  const visit = (id: string): boolean => {
    const mark = state.get(id);
    if (mark === "visiting") return true;
    if (mark === "done") return false;
    state.set(id, "visiting");
    for (const dep of internalDeps(byId.get(id)!)) {
      if (byId.has(dep) && visit(dep)) return true;
    }
    state.set(id, "done");
    return false;
  };
  return nodes.some((node) => visit(node.id));
}

/** Order nodes so every dep precedes its dependents, keeping current order
 * where the graph allows it.
 *
 * `lane.yaml` has no explicit ordering field: the loader requires each dep to
 * name an *earlier* entry, so file order carries the topology. Drawing an edge
 * backwards through the node list is a legal graph edit, and sorting here is
 * what keeps it from becoming a spurious save rejection.
 */
export function topologicalOrder(nodes: EditorNode[]): EditorNode[] {
  const ids = new Set(nodes.map((node) => node.id));
  const indexOf = new Map(nodes.map((node, index) => [node.id, index]));
  const pending = new Map(
    nodes.map((node) => [
      node.id,
      internalDeps(node).filter((dep) => ids.has(dep) && dep !== node.id),
    ]),
  );
  const emitted = new Set<string>();
  const out: EditorNode[] = [];
  while (out.length < nodes.length) {
    const ready = nodes
      .filter(
        (node) =>
          !emitted.has(node.id) &&
          pending.get(node.id)!.every((dep) => emitted.has(dep)),
      )
      .sort((a, b) => indexOf.get(a.id)! - indexOf.get(b.id)!);
    if (ready.length === 0) {
      // Cyclic; validation reports it. Append the rest so the payload still
      // round-trips every node and the backend error names a real id.
      for (const node of nodes) if (!emitted.has(node.id)) out.push(node);
      break;
    }
    const next = ready[0];
    emitted.add(next.id);
    out.push(next);
  }
  return out;
}

/* ───────── validation ───────── */

export type EditorValidationIssue = {
  /** Node id, argument name, or port name the issue belongs to. */
  target: string;
  message: string;
};

/** Loader rules that can be checked locally, so a doomed save is blocked.
 *
 * Not a second source of truth: the backend still writes a candidate
 * directory and loads it through `loader._load_from_root`. Anything missing
 * here surfaces as a 400 the editor shows verbatim.
 */
export function validateEditorState(
  state: TemplateEditorState,
): EditorValidationIssue[] {
  const issues: EditorValidationIssue[] = [];
  if (!state.name.trim()) {
    issues.push({ target: "name", message: "模板名不能为空" });
  }
  if (state.nodes.length === 0) {
    issues.push({ target: "nodes", message: "模板至少需要一个节点" });
  }

  const seenIds = new Set<string>();
  for (const node of state.nodes) {
    const id = node.id.trim();
    if (!id) {
      issues.push({ target: node.id, message: "节点 id 不能为空" });
    } else if (seenIds.has(id)) {
      issues.push({ target: id, message: `节点 id ${id} 重复` });
    } else if (id.startsWith(INPUT_DEP_PREFIX)) {
      issues.push({
        target: id,
        message: `节点 id 不能以 ${INPUT_DEP_PREFIX} 开头（保留给输入端口）`,
      });
    }
    seenIds.add(id);

    if (node.kind !== "agent") {
      issues.push({
        target: id,
        message: "用户模板只能包含 agent 节点；请删除 verifier 节点",
      });
    }
    if (node.category === "review") {
      if (!node.subtype) {
        issues.push({ target: id, message: "review 节点必须有 subtype" });
      }
      if (!node.brief) {
        issues.push({ target: id, message: "review 节点必须有 brief" });
      }
    } else {
      if (node.subtype) {
        issues.push({
          target: id,
          message: "非 review 节点不能带 subtype",
        });
      }
      if (node.brief) {
        issues.push({ target: id, message: "非 review 节点不能带 brief" });
      }
    }
  }

  const inputNames = new Set<string>();
  for (const input of state.inputs) {
    if (!PARAM_NAME_RE.test(input.name)) {
      issues.push({
        target: input.name,
        message: `端口名 ${input.name} 必须匹配 [a-z][a-z0-9_]*`,
      });
    }
    if (inputNames.has(input.name)) {
      issues.push({ target: input.name, message: `端口 ${input.name} 重复` });
    }
    inputNames.add(input.name);
  }

  const argumentNames = new Set<string>();
  for (const argument of resolveArguments(state)) {
    if (!PARAM_NAME_RE.test(argument.name)) {
      issues.push({
        target: argument.name,
        message: `参数名 ${argument.name} 必须匹配 [a-z][a-z0-9_]*`,
      });
    }
    if (argumentNames.has(argument.name)) {
      issues.push({
        target: argument.name,
        message: `参数 ${argument.name} 重复`,
      });
    }
    argumentNames.add(argument.name);
  }

  for (const node of state.nodes) {
    for (const dep of internalDeps(node)) {
      if (dep === node.id) {
        issues.push({ target: node.id, message: "节点不能依赖自己" });
      } else if (!seenIds.has(dep)) {
        issues.push({
          target: node.id,
          message: `依赖的节点 ${dep} 不存在`,
        });
      }
    }
    for (const port of inputDeps(node)) {
      if (!inputNames.has(port)) {
        issues.push({
          target: node.id,
          message: `依赖了未声明的输入端口 ${port}`,
        });
      }
    }
    for (const port of scanPlaceholders(node.prompt).inputPorts) {
      if (!inputNames.has(port)) {
        issues.push({
          target: node.id,
          message: `提示词引用了未声明的输入端口 {{input.${port}}}`,
        });
      }
    }
    if (node.resume_from) {
      if (!seenIds.has(node.resume_from)) {
        issues.push({
          target: node.id,
          message: `恢复来源 ${node.resume_from} 不存在`,
        });
      } else if (!internalDeps(node).includes(node.resume_from)) {
        issues.push({
          target: node.id,
          message: `恢复来源 ${node.resume_from} 必须同时是依赖`,
        });
      }
    }
  }

  if (hasCycle(state.nodes)) {
    issues.push({ target: "nodes", message: "依赖关系存在环" });
  }
  return issues;
}

/* ───────── serialization ───────── */

export type TemplateRewriteNode = {
  id: string;
  kind: string;
  category: NodeCategory;
  subtype: ReviewSubtype | null;
  brief: ReviewBrief | null;
  prompt: string;
  motivation: string;
  scheduled_deps: string[];
  resume_from: string | null;
  model_preset_id: string | null;
};

export type TemplateRewritePayload = {
  name: string;
  brief: string;
  nodes: TemplateRewriteNode[];
  arguments: Array<{ name: string; description: string; default: string | null }>;
  inputs: Array<{ name: string; description: string }>;
};

/** Serialize editor state into the `PUT /user-templates/{slug}` body.
 *
 * Nodes go out topologically sorted (see {@link topologicalOrder}). Every
 * resolved argument is sent, including scanned-only ones: persisting them is
 * how a placeholder an author typed becomes a declared, documentable parameter
 * with a description and a default.
 *
 * `lane_mode` and `schema_version` are absent by design — the backend owns
 * them. Each node's `model_preset_id` travels here, so editing a node's model
 * is what changes the model it will be stamped with.
 */
export function buildRewritePayload(
  state: TemplateEditorState,
): TemplateRewritePayload {
  return {
    name: state.name.trim(),
    brief: state.brief.trim(),
    nodes: topologicalOrder(state.nodes).map((node) => ({
      id: node.id.trim(),
      kind: node.kind,
      category: node.category,
      subtype: node.category === "review" ? node.subtype : null,
      brief: node.category === "review" ? node.brief : null,
      prompt: node.prompt,
      motivation: node.motivation,
      scheduled_deps: [...node.scheduled_deps],
      resume_from: node.resume_from,
      model_preset_id: node.model_preset_id,
    })),
    arguments: resolveArguments(state).map((argument) => ({
      name: argument.name,
      description: argument.description.trim(),
      default: argument.default,
    })),
    inputs: state.inputs.map((input) => ({
      name: input.name,
      description: input.description.trim(),
    })),
  };
}

/** Whether the state differs from the last saved payload. */
export function isDirty(
  state: TemplateEditorState,
  savedPayload: TemplateRewritePayload | null,
): boolean {
  if (!savedPayload) return false;
  return (
    JSON.stringify(buildRewritePayload(state)) !== JSON.stringify(savedPayload)
  );
}

/* ───────── canvas layout ───────── */

export type TemplateGraphPosition = { x: number; y: number };

export const TEMPLATE_LAYOUT = {
  columnWidth: 288,
  rowHeight: 148,
  originX: 232,
  originY: 40,
  portColumnX: 24,
  portRowHeight: 92,
};

/** Layered left-to-right positions for the editor canvas.
 *
 * Depth is the longest dependency chain to a node, which puts every dep to the
 * left of its dependents and makes the DAG readable without a solver. Input
 * ports get their own column ahead of the graph so the `⟨in:x⟩` tiles read as
 * the template's signature.
 */
export function layoutTemplateGraph(state: TemplateEditorState): {
  nodes: Record<string, TemplateGraphPosition>;
  ports: Record<string, TemplateGraphPosition>;
} {
  const ordered = topologicalOrder(state.nodes);
  const depth = new Map<string, number>();
  for (const node of ordered) {
    const deps = internalDeps(node).filter((dep) => depth.has(dep));
    depth.set(
      node.id,
      deps.length === 0
        ? inputDeps(node).length > 0
          ? 1
          : 0
        : Math.max(...deps.map((dep) => depth.get(dep)! + 1)),
    );
  }

  const rowByColumn = new Map<number, number>();
  const nodes: Record<string, TemplateGraphPosition> = {};
  for (const node of ordered) {
    const column = depth.get(node.id) ?? 0;
    const row = rowByColumn.get(column) ?? 0;
    rowByColumn.set(column, row + 1);
    nodes[node.id] = {
      x: TEMPLATE_LAYOUT.originX + column * TEMPLATE_LAYOUT.columnWidth,
      y: TEMPLATE_LAYOUT.originY + row * TEMPLATE_LAYOUT.rowHeight,
    };
  }

  const ports: Record<string, TemplateGraphPosition> = {};
  state.inputs.forEach((input, index) => {
    ports[input.name] = {
      x: TEMPLATE_LAYOUT.portColumnX,
      y: TEMPLATE_LAYOUT.originY + index * TEMPLATE_LAYOUT.portRowHeight,
    };
  });
  return { nodes, ports };
}

/* ───────── helpers ───────── */

/** Rewrite placeholder bodies, leaving every non-matching one byte-identical.
 *
 * Returning the original match rather than a re-rendered `{{body}}` preserves
 * whatever spacing the author wrote inside braces we are not renaming.
 */
function rewritePlaceholders(
  text: string,
  map: (body: string) => string | null,
): string {
  return text.replace(PLACEHOLDER_RE, (match, body: string) => {
    const next = map(body);
    return next === null ? match : `{{${next}}}`;
  });
}

/** One-line label for a template node tile. */
export function nodeHeadline(node: EditorNode): string {
  const text = node.prompt.replace(/\s+/g, " ").trim();
  if (text) return text;
  return node.category === "review" && node.brief?.check_what
    ? node.brief.check_what
    : "(空提示词)";
}
