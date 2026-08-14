import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  getPrinciple,
  getSkill,
  getUserTemplate,
  type PrincipleDetail,
  type PrincipleSummary,
  type SkillDetail,
  type SkillSummary,
} from "../api";
import type { ModelPreset, TemplateDetail, TemplateSummary } from "../types";
import { modelPresetLabel } from "../modelPresets";
import { resolvedTemplateNodeModelPresetId } from "../templateModels";

/** What the dock asks to preview. The modal owns the detail fetch. */
export type LibraryPreviewTarget =
  | { kind: "template"; slug: string; summary: TemplateSummary }
  | { kind: "skill"; slug: string; summary: SkillSummary }
  | { kind: "principle"; slug: string; summary: PrincipleSummary };

export type LibraryPreviewProps = {
  target: LibraryPreviewTarget | null;
  modelPresets: ModelPreset[];
  onClose: () => void;
  /** Stamps the template onto the canvas. Absent in read-only sessions. */
  onApplyTemplate?: (slug: string) => void;
  onEditTemplate: (slug: string) => void;
  /** Attaches to the currently selected virtual node. Absent when the current
   * selection cannot receive an attachment, which disables the button. */
  onAttachToVirtual?: (entryId: string) => void;
  /** Node label shown on the attach button, e.g. `设计 API`. */
  attachTargetLabel?: string | null;
  /** Why attaching is unavailable, shown in place of the button's hint. */
  attachDisabledReason?: string | null;
};

/** Async detail payload, per target kind. */
type DetailState =
  | { status: "loading" }
  | { status: "error" }
  | { status: "ready"; kind: "template"; detail: TemplateDetail }
  | { status: "ready"; kind: "skill"; detail: SkillDetail }
  | { status: "ready"; kind: "principle"; detail: PrincipleDetail };

function Section({
  title,
  trailing,
  children,
}: {
  title: string;
  trailing?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="mb-4">
      <div className="mb-1 flex items-baseline justify-between gap-2 text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
        <span>{title}</span>
        {trailing}
      </div>
      {children}
    </section>
  );
}

function Note({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-md border border-line bg-surface-sunken px-3 py-2.5 text-[12px] text-ink-muted">
      {children}
    </div>
  );
}

/** Long text (SKILL.md, CONTEXT.md) — scrolls inside the modal body so the
 * header and the action bar stay put. */
function Body({ text }: { text: string }) {
  return (
    <div className="md-prose max-h-[42vh] overflow-y-auto rounded-md border border-line bg-surface-sunken px-3 py-2.5 text-[12px] leading-relaxed text-ink">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}

function Chip({ children, title }: { children: React.ReactNode; title?: string }) {
  return (
    <span
      title={title}
      className="rounded border border-line bg-surface px-1.5 py-0.5 font-mono text-[10px] text-ink-muted"
    >
      {children}
    </span>
  );
}

function FileList({ files }: { files: string[] }) {
  return (
    <ul className="max-h-32 overflow-y-auto rounded-md border border-line bg-surface-sunken px-3 py-2 font-mono text-[10.5px] leading-relaxed text-ink-muted">
      {files.map((file) => (
        <li key={file} className="truncate" title={file}>
          {file}
        </li>
      ))}
    </ul>
  );
}

function TemplatePreview({
  detail,
  modelPresets,
}: {
  detail: TemplateDetail;
  modelPresets: ModelPreset[];
}) {
  const nodes = detail.nodes ?? [];
  return (
    <>
      {detail.brief && (
        <Section title="简介">
          <div className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-[12.5px] leading-relaxed text-ink-strong">
            {detail.brief}
          </div>
        </Section>
      )}

      <Section title="节点" trailing={<span className="font-mono normal-case tracking-normal">{detail.node_count}</span>}>
        {nodes.length === 0 ? (
          <Note>这个模板没有节点。</Note>
        ) : (
          <ol className="space-y-1.5">
            {nodes.map((node, index) => {
              const deps = node.scheduled_deps ?? [];
              const resolvedModelPresetId = resolvedTemplateNodeModelPresetId(
                nodes,
                node,
              );
              return (
                <li
                  key={node.id}
                  className="rounded-md border border-line bg-surface-raised px-3 py-2"
                >
                  <div className="flex items-baseline gap-2">
                    <span className="shrink-0 font-mono text-[10px] text-ink-subtle">
                      {index + 1}
                    </span>
                    {/* Motivations are usually a short label but may be a whole
                      * paragraph; two lines keeps a long one legible without
                      * letting it push the node's own metadata off screen. */}
                    <span
                      className="min-w-0 flex-1 line-clamp-2 text-[12px] font-medium leading-snug text-ink-strong"
                      title={node.motivation?.trim() || node.id}
                    >
                      {node.motivation?.trim() || node.id}
                    </span>
                    <span className="shrink-0 font-mono text-[9.5px] text-ink-subtle">
                      {node.id}
                    </span>
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-1">
                    <Chip>{node.kind}</Chip>
                    {node.category && <Chip>{node.category}</Chip>}
                    {node.subtype && <Chip>{node.subtype}</Chip>}
                    {resolvedModelPresetId && (
                      <Chip
                        title={
                          node.resume_from
                            ? `该节点沿用 ${node.resume_from} 的模型 ${resolvedModelPresetId}`
                            : `该节点固定在 ${resolvedModelPresetId} 上运行`
                        }
                      >
                        {modelPresetLabel(modelPresets, resolvedModelPresetId)}
                      </Chip>
                    )}
                    {node.resume_from && (
                      <Chip title={`承接 ${node.resume_from} 的会话`}>
                        resume · {node.resume_from}
                      </Chip>
                    )}
                  </div>
                  {/* Dependencies name the template-local node ids, not the
                    * motivations: a motivation can be a whole paragraph, which
                    * would swamp the row it is meant to annotate. Each id is
                    * echoed at the end of its own row above. */}
                  {deps.length > 0 && (
                    <div className="mt-1 truncate font-mono text-[10px] text-ink-muted">
                      依赖：{deps.join("、")}
                    </div>
                  )}
                  {(node.prompt ?? node.prompt_preview) && (
                    <div className="mt-1 line-clamp-3 whitespace-pre-wrap text-[11px] leading-snug text-ink-muted">
                      {node.prompt ?? node.prompt_preview}
                    </div>
                  )}
                </li>
              );
            })}
          </ol>
        )}
      </Section>

      {detail.arguments.length > 0 && (
        <Section title="参数" trailing={<span className="font-mono normal-case tracking-normal">{detail.arguments.length}</span>}>
          <ul className="space-y-1">
            {detail.arguments.map((argument) => (
              <li
                key={argument.name}
                className="rounded-md border border-line bg-surface-sunken px-3 py-1.5"
              >
                <div className="flex items-baseline gap-2">
                  <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-ink-strong">
                    {argument.name}
                  </span>
                  <span className="shrink-0 text-[9.5px] text-ink-subtle">
                    {argument.required ? "必填" : "可选"}
                  </span>
                  {!argument.declared && (
                    <span
                      className="shrink-0 text-[9.5px] text-state-waiting"
                      title="出现在 prompt 里，但 template.yaml 未声明"
                    >
                      未声明
                    </span>
                  )}
                </div>
                {argument.description && (
                  <div className="mt-0.5 text-[11px] leading-snug text-ink-muted">
                    {argument.description}
                  </div>
                )}
                {argument.default !== null && argument.default !== "" && (
                  <div className="mt-0.5 truncate font-mono text-[10px] text-ink-subtle">
                    默认 {argument.default}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {detail.inputs.length > 0 && (
        <Section title="输入端口" trailing={<span className="font-mono normal-case tracking-normal">{detail.inputs.length}</span>}>
          <ul className="space-y-1">
            {detail.inputs.map((input) => (
              <li
                key={input.name}
                className="rounded-md border border-line bg-surface-sunken px-3 py-1.5"
              >
                <div className="truncate font-mono text-[11px] text-ink-strong">
                  {input.name}
                </div>
                {input.description && (
                  <div className="mt-0.5 text-[11px] leading-snug text-ink-muted">
                    {input.description}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {detail.warnings.length > 0 && (
        <Section title="警告" trailing={<span className="font-mono normal-case tracking-normal">{detail.warnings.length}</span>}>
          <ul className="space-y-1">
            {detail.warnings.map((warning, index) => (
              <li
                key={`${warning.code}-${warning.name}-${index}`}
                className="rounded-md border border-state-waiting/40 bg-state-waiting-soft px-3 py-1.5 text-[11px] leading-snug text-state-waiting"
              >
                {warning.message}
              </li>
            ))}
          </ul>
        </Section>
      )}
    </>
  );
}

function SkillPreview({
  summary,
  detail,
}: {
  summary: SkillSummary;
  detail: SkillDetail;
}) {
  const files = detail.files ?? summary.files;
  return (
    <>
      {detail.description && (
        <Section title="何时使用">
          {/* Full text, no clamp: this is the description the old `<select>`
            * discarded, and it is the main way to tell two neighbours apart. */}
          <div className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-[12.5px] leading-relaxed text-ink-strong">
            {detail.description}
          </div>
        </Section>
      )}

      <Section title="来源">
        <div className="flex flex-wrap gap-1">
          <Chip title={detail.path}>{detail.id}</Chip>
          {detail.version && <Chip>v{detail.version}</Chip>}
          {detail.import_source && <Chip title={detail.import_source}>{detail.import_source}</Chip>}
          {detail.import_kind && <Chip>{detail.import_kind}</Chip>}
          {detail.imported_at && (
            <Chip>{new Date(detail.imported_at * 1000).toLocaleDateString()}</Chip>
          )}
          {detail.package_id && (
            <Chip title={`包 ${detail.package_id}`}>包 · {detail.package_id}</Chip>
          )}
        </div>
        {detail.dependencies && detail.dependencies.length > 0 && (
          <div className="mt-1.5 text-[11px] text-ink-muted">
            依赖：<span className="font-mono">{detail.dependencies.join(", ")}</span>
          </div>
        )}
      </Section>

      <Section title="SKILL.md">
        <Body text={detail.body} />
      </Section>

      {files.length > 0 && (
        <Section title="文件" trailing={<span className="font-mono normal-case tracking-normal">{files.length}</span>}>
          <FileList files={files} />
        </Section>
      )}
    </>
  );
}

function injectionLabel(injection: PrincipleSummary["injection"]): string {
  if (typeof injection === "string") return injection;
  if (injection && typeof injection === "object") {
    const parts = Object.entries(injection).map(([key, value]) => `${key}: ${value}`);
    if (parts.length > 0) return parts.join(" · ");
  }
  return "system";
}

function maxCharsLabel(maxChars: PrincipleSummary["max_chars"]): string {
  if (typeof maxChars === "number") return String(maxChars);
  if (maxChars && typeof maxChars === "object") {
    const parts = Object.entries(maxChars).map(([key, value]) => `${key}: ${value}`);
    if (parts.length > 0) return parts.join(" · ");
  }
  return "默认";
}

function PrinciplePreview({ detail }: { detail: PrincipleDetail }) {
  return (
    <>
      {detail.description && (
        <Section title="说明">
          <div className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-[12.5px] leading-relaxed text-ink-strong">
            {detail.description}
          </div>
        </Section>
      )}

      <Section title="注入">
        <div className="flex flex-wrap gap-1">
          <Chip>{injectionLabel(detail.injection)}</Chip>
          <Chip title="字符上限">上限 {maxCharsLabel(detail.max_chars)}</Chip>
          <Chip title={detail.path}>{detail.id}</Chip>
        </div>
      </Section>

      <Section title="CONTEXT.md">
        {detail.body === null || detail.body.trim().length === 0 ? (
          <Note>
            这个 principle 还没有 CONTEXT.md 正文（
            <span className="font-mono text-[11px]">{detail.body_path}</span>）。
          </Note>
        ) : (
          <Body text={detail.body} />
        )}
      </Section>
    </>
  );
}

const KIND_LABEL: Record<LibraryPreviewTarget["kind"], string> = {
  template: "模板",
  skill: "Skill",
  principle: "Principle",
};

export function LibraryEntryPreviewModal({
  target,
  modelPresets,
  onClose,
  onApplyTemplate,
  onEditTemplate,
  onAttachToVirtual,
  attachTargetLabel,
  attachDisabledReason,
}: LibraryPreviewProps) {
  const [state, setState] = useState<DetailState>({ status: "loading" });
  const closeRef = useRef<HTMLButtonElement | null>(null);

  const targetKey = target ? `${target.kind}:${target.slug}` : null;

  /* Fetch on open and on target change. The guard is the composite key rather
   * than the object identity, so a library refresh handing us a new summary for
   * the same entry does not refetch the body under the reader. */
  useEffect(() => {
    if (!target) return;
    let cancelled = false;
    setState({ status: "loading" });
    const load = async (): Promise<DetailState> => {
      if (target.kind === "template") {
        return {
          status: "ready",
          kind: "template",
          detail: await getUserTemplate(target.slug),
        };
      }
      if (target.kind === "skill") {
        return { status: "ready", kind: "skill", detail: await getSkill(target.slug) };
      }
      return {
        status: "ready",
        kind: "principle",
        detail: await getPrinciple(target.slug),
      };
    };
    void load().then(
      (next) => {
        if (!cancelled) setState(next);
      },
      () => {
        if (!cancelled) setState({ status: "error" });
      },
    );
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetKey]);

  /* Initial focus goes to the close button: there is no search field here, and
   * the body is a scroll region rather than something to type into. A full
   * focus trap is deliberately out of scope (design §6). */
  useEffect(() => {
    if (!target) return;
    window.setTimeout(() => closeRef.current?.focus(), 0);
  }, [targetKey, target]);

  useEffect(() => {
    if (!target) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [target, onClose]);

  const heading = useMemo(() => {
    if (!target) return "";
    if (target.kind === "template") return target.summary.name;
    return target.summary.title || target.slug;
  }, [target]);

  if (!target) return null;

  const entryId = target.kind === "template" ? target.slug : target.summary.id;
  const canAttach = target.kind !== "template" && onAttachToVirtual !== undefined;
  const canApplyTemplate = target.kind === "template" && onApplyTemplate !== undefined;

  /* Portalled for the same reason as the picker: the side panel animates with
   * `transform`, which would otherwise make it the containing block for this
   * fixed scrim and pin it to the 380px column. */
  return createPortal(
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-surface-scrim/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`${KIND_LABEL[target.kind]} ${heading}`}
        onClick={(event) => event.stopPropagation()}
        className="flex max-h-[85vh] w-[520px] max-w-[95vw] flex-col rounded-xl border border-line bg-surface-raised shadow-modal"
      >
        <div className="flex items-start justify-between gap-3 border-b border-line px-5 py-3.5">
          <div className="min-w-0">
            <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
              {KIND_LABEL[target.kind]}
            </div>
            <div className="mt-0.5 truncate font-display text-sm font-semibold text-ink-strong">
              {heading}
            </div>
            <div className="truncate font-mono text-[10.5px] text-ink-muted" title={entryId}>
              {entryId}
            </div>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            className="shrink-0 rounded px-2 py-1 text-[11px] font-medium text-ink-muted transition hover:bg-surface-sunken hover:text-ink"
            aria-label="关闭"
          >
            Esc
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          {state.status === "loading" && <Note>正在加载…</Note>}
          {state.status === "error" && (
            <Note>无法加载详情。它可能已被删除，可刷新 library 后重试。</Note>
          )}
          {state.status === "ready" && state.kind === "template" && (
            <TemplatePreview detail={state.detail} modelPresets={modelPresets} />
          )}
          {state.status === "ready" && state.kind === "skill" && target.kind === "skill" && (
            <SkillPreview summary={target.summary} detail={state.detail} />
          )}
          {state.status === "ready" && state.kind === "principle" && (
            <PrinciplePreview detail={state.detail} />
          )}
        </div>

        <div className="flex items-center justify-between gap-3 border-t border-line px-5 py-3">
          {/* Two lines at most: the action buttons must keep their place
            * regardless of how long the selected node's label is. */}
          <div className="line-clamp-2 min-w-0 text-[10.5px] leading-snug text-ink-subtle">
            {target.kind === "template"
              ? canApplyTemplate
                ? "也可以把条目从 library 直接拖到画布。"
                : "只读会话中不能把模板应用到画布。"
              : canAttach
                ? attachTargetLabel
                  ? `将附加到「${attachTargetLabel}」。`
                  : "将附加到当前选中的虚拟节点。"
                : attachDisabledReason ?? "选中一个虚拟节点后可在此附加，或直接拖到该节点上。"}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {target.kind === "template" ? (
              <>
                <button
                  type="button"
                  onClick={() => onEditTemplate(target.slug)}
                  className="rounded-md border border-line px-2.5 py-1.5 text-[11.5px] text-ink-muted transition hover:border-line-strong hover:text-ink"
                >
                  编辑
                </button>
                <button
                  type="button"
                  disabled={!canApplyTemplate}
                  onClick={() => onApplyTemplate?.(target.slug)}
                  className="rounded-md bg-brand px-2.5 py-1.5 text-[11.5px] font-medium text-white shadow-card transition hover:brightness-[0.95] disabled:cursor-not-allowed disabled:opacity-40"
                >
                  应用到画布
                </button>
              </>
            ) : (
              <button
                type="button"
                disabled={!canAttach}
                onClick={() => onAttachToVirtual?.(entryId)}
                className="rounded-md bg-brand px-2.5 py-1.5 text-[11.5px] font-medium text-white shadow-card transition hover:brightness-[0.95] disabled:cursor-not-allowed disabled:opacity-40"
              >
                附加到选中节点
              </button>
            )}
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}
