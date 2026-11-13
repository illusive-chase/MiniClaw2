import { useLayoutEffect, useRef, type KeyboardEvent, type MouseEvent, type PointerEvent } from "react";
import { Archive, CloudOff } from "lucide-react";

import type { HomeFolder, SessionInfo } from "../../types";
import { glyphDef, hueTextClass } from "../../projectIcons";
import { prefersReducedMotion } from "../../motion";
import { ProjectGlyph } from "../ProjectGlyph";

export type CellCtx = "root" | "folder" | "archive" | "search";
/** `cancel` retracts a ring released early; `selected` is the sidebar subject. */
export type RingPhase = "idle" | "pressing" | "armed" | "cancel" | "selected";

export type TileFace =
  | { kind: "project"; session: SessionInfo }
  | { kind: "folder"; folder: HomeFolder; members: SessionInfo[] }
  | { kind: "archive"; count: number };

export type CornerMark =
  | { kind: "waiting"; count: number }
  | { kind: "error" }
  | { kind: "unbound" }
  /** Unpushed project work older than the host's threshold. */
  | { kind: "unsynced" }
  | null;

/* A rounded square 6px outside the 64px tile, stroked from 12 o'clock
 * clockwise. `pathLength=1` lets the dash math run on 0..1 progress. */
const RING_PATH =
  "M38 1.25H53A21.75 21.75 0 0 1 74.75 23V53A21.75 21.75 0 0 1 53 74.75H23"
  + "A21.75 21.75 0 0 1 1.25 53V23A21.75 21.75 0 0 1 23 1.25Z";

const EASE_IN = "cubic-bezier(0.4, 0, 1, 1)";
const EASE_OUT = "cubic-bezier(0.22, 1, 0.36, 1)";
const EASE_SPRING = "cubic-bezier(0.34, 1.36, 0.64, 1)";

/**
 * The long-press progress ring, which stays on as the selection ring.
 *
 * Progress is driven through the Web Animations API rather than a CSS class
 * so an early release can retract from wherever the fill had reached instead
 * of jumping to empty, and an armed ring can become the selection ring
 * without being redrawn.
 */
export function PressRing({ phase, holdMs }: { phase: RingPhase; holdMs: number }) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const pathRef = useRef<SVGPathElement | null>(null);
  const fill = useRef<Animation | null>(null);
  const previous = useRef<RingPhase>("idle");

  useLayoutEffect(() => {
    const path = pathRef.current;
    const svg = svgRef.current;
    const from = previous.current;
    previous.current = phase;
    if (!path || !svg) return;
    const reduced = prefersReducedMotion();
    const progress = () => {
      const value = parseFloat(getComputedStyle(path).strokeDashoffset);
      return Number.isFinite(value) ? value : 1;
    };
    if (phase === "pressing") {
      fill.current?.cancel();
      fill.current = path.animate(
        [{ strokeDashoffset: 1 }, { strokeDashoffset: 0 }],
        { duration: holdMs, easing: "linear", fill: "forwards" },
      );
    } else if (phase === "armed") {
      fill.current?.cancel();
      fill.current = path.animate([{ strokeDashoffset: 0 }], { duration: 1, fill: "forwards" });
      if (!reduced) {
        svg.animate(
          [{ transform: "scale(1)" }, { transform: "scale(1.07)", offset: 0.4 }, { transform: "scale(1)" }],
          { duration: 320, easing: EASE_SPRING },
        );
      }
    } else if (phase === "cancel") {
      const reached = progress();
      fill.current?.cancel();
      fill.current = path.animate(
        [
          { strokeDashoffset: reached, opacity: 1 },
          { strokeDashoffset: Math.min(1, reached + 0.2), opacity: 0 },
        ],
        { duration: 140, easing: EASE_IN, fill: "forwards" },
      );
    } else if (phase === "selected") {
      const keep = from === "armed" || from === "selected";
      fill.current?.cancel();
      fill.current = path.animate(
        keep
          ? [{ strokeDashoffset: 0, opacity: 1 }]
          : [{ strokeDashoffset: 0, opacity: 0 }, { strokeDashoffset: 0, opacity: 1 }],
        { duration: keep ? 1 : 140, easing: EASE_OUT, fill: "forwards" },
      );
    }
  }, [phase, holdMs]);

  if (phase === "idle") return null;
  return (
    <svg ref={svgRef} className="home-ring" viewBox="0 0 76 76" aria-hidden="true">
      <path ref={pathRef} d={RING_PATH} pathLength={1} strokeDasharray="1" strokeDashoffset="1" />
    </svg>
  );
}

function FolderFace({ members, large = false }: { members: SessionInfo[]; large?: boolean }) {
  const mini = large ? 19 : 14;
  return (
    <span
      aria-hidden="true"
      className={`home-tile home-folder-tile flex-none ${large ? "h-[88px] w-[88px] rounded-[23px]" : "h-16 w-16 rounded-[17px]"}`}
      style={large ? { gridTemplateColumns: "repeat(3, 19px)", gridAutoRows: "19px", gap: 6, padding: 10 } : undefined}
    >
      {members.slice(0, 9).map((session) => {
        const Glyph = glyphDef(session.icon.glyph).icon;
        return (
          <span key={session.id} className="home-mini" style={large ? { width: mini, height: mini, borderRadius: 5 } : undefined}>
            <Glyph size={large ? 13 : 10} strokeWidth={2.6} className={hueTextClass(session.icon.hue)} />
          </span>
        );
      })}
    </span>
  );
}

export function TileFaceView({ face, large = false }: { face: TileFace; large?: boolean }) {
  if (face.kind === "project") return <ProjectGlyph icon={face.session.icon} size={large ? 88 : 64} />;
  if (face.kind === "folder") return <FolderFace members={face.members} large={large} />;
  return (
    <span aria-hidden="true" className="home-tile home-system-tile h-16 w-16 flex-none rounded-[17px]">
      <Archive size={26} strokeWidth={1.7} />
    </span>
  );
}

export function CornerBadge({ mark }: { mark: CornerMark }) {
  if (!mark) return null;
  if (mark.kind === "unsynced") {
    return <span className="home-badge home-badge-dot bg-state-error" aria-hidden="true" />;
  }
  if (mark.kind === "unbound") {
    return (
      <span className="home-badge" data-kind="unbound" aria-hidden="true">
        <CloudOff size={12} strokeWidth={2.2} />
      </span>
    );
  }
  return (
    <span
      key={mark.kind === "waiting" ? `w${mark.count}` : "e"}
      className={"home-badge " + (mark.kind === "waiting" ? "bg-state-waiting" : "bg-state-error")}
      aria-hidden="true"
    >
      {mark.kind === "waiting" ? mark.count : "!"}
    </span>
  );
}

export type HomeCellProps = {
  slot: string;
  ctx: CellCtx;
  face: TileFace;
  label: string;
  title: string;
  ariaLabel: string;
  mark: CornerMark;
  running: boolean;
  dimmed: boolean;
  draggable: boolean;
  ring: RingPhase;
  holdMs: number;
  placeholder: boolean;
  mergeTarget: boolean;
  jiggleDelayMs: number;
  onPointerDown: (event: PointerEvent<HTMLDivElement>) => void;
  onContextMenu: (event: MouseEvent<HTMLDivElement>) => void;
  onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => void;
};

/**
 * One home-screen slot. Three nested layers, each owning one kind of motion so
 * they never fight over `transform`: the cell glides (FLIP), `.home-pop`
 * plays the arrival, `.home-wobble` jiggles in edit mode and dips under a
 * press.
 */
export function HomeCell({
  slot,
  ctx,
  face,
  label,
  title,
  ariaLabel,
  mark,
  running,
  dimmed,
  draggable,
  ring,
  holdMs,
  placeholder,
  mergeTarget,
  jiggleDelayMs,
  onPointerDown,
  onContextMenu,
  onKeyDown,
}: HomeCellProps) {
  const press = ring === "pressing" || ring === "armed" ? ring : undefined;
  return (
    <div
      data-slot={slot}
      data-ctx={ctx}
      data-draggable={draggable ? "true" : undefined}
      data-unbound={dimmed ? "true" : undefined}
      data-placeholder={placeholder ? "true" : undefined}
      data-merge={mergeTarget ? "true" : undefined}
      data-press={press}
      role="button"
      tabIndex={0}
      aria-label={ariaLabel}
      title={title}
      onPointerDown={onPointerDown}
      onContextMenu={onContextMenu}
      onKeyDown={onKeyDown}
      className="home-cell relative h-[104px] w-24 select-none rounded-[14px] outline-none focus-visible:shadow-[0_0_0_2px_rgb(var(--brand)/0.55)]"
      style={{ ["--jiggle-delay" as string]: `${jiggleDelayMs}ms`, ["--home-hold-ms" as string]: `${holdMs}ms` }}
    >
      <div className="home-pop flex flex-col items-center gap-2 pt-1">
        <div className="home-wobble">
          <PressRing phase={ring} holdMs={holdMs} />
          <TileFaceView face={face} />
          <CornerBadge mark={mark} />
        </div>
        <div className="flex max-w-[94px] items-center justify-center gap-[5px] text-[12px] leading-4">
          {running && (
            <i className="home-run-dot inline-block h-1.5 w-1.5 flex-none rounded-full bg-state-running" aria-hidden="true" />
          )}
          <span
            className={
              "truncate "
              + (dimmed || face.kind === "archive" ? "text-ink-muted" : "text-ink-strong")
            }
          >
            {label}
          </span>
        </div>
      </div>
    </div>
  );
}
