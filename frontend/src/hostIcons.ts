/* Host icon vocabulary: the render side of `host_profile.py`.
 *
 * Same contract as `projectIcons.ts`: only our own keys are stored, the
 * backend keeps its own copy of the list for validation, and an unknown key
 * (a profile written by a newer client) renders as the fallback instead of
 * throwing. Colors reuse the project hue palette.
 */
import {
  Box, Building, Cloud, Container, Cpu, FlaskConical, House, Laptop, Monitor,
  Rocket, Server, Terminal, type LucideIcon,
} from "lucide-react";

import { HUE_NAMES, PROJECT_HUES, hueSwatchClass, hueTextClass, type ProjectHue } from "./projectIcons";
import type { HostEntry, SessionHost } from "./types";

export type HostGlyph = { key: string; icon: LucideIcon; name: string };

export const HOST_GLYPHS: HostGlyph[] = [
  { key: "laptop", icon: Laptop, name: "笔记本" },
  { key: "monitor", icon: Monitor, name: "台式机" },
  { key: "server", icon: Server, name: "服务器" },
  { key: "cloud", icon: Cloud, name: "云主机" },
  { key: "container", icon: Container, name: "容器" },
  { key: "cpu", icon: Cpu, name: "算力机" },
  { key: "house", icon: House, name: "家里" },
  { key: "building", icon: Building, name: "公司" },
  { key: "flask", icon: FlaskConical, name: "实验室" },
  { key: "rocket", icon: Rocket, name: "发布机" },
  { key: "box", icon: Box, name: "盒子" },
  { key: "terminal", icon: Terminal, name: "终端" },
];

const BY_KEY = new Map(HOST_GLYPHS.map((glyph) => [glyph.key, glyph]));
export const FALLBACK_HOST_GLYPH = "server";

export function hostGlyph(key: string | null | undefined): HostGlyph {
  return (key && BY_KEY.get(key)) || BY_KEY.get(FALLBACK_HOST_GLYPH)!;
}

export const HOST_HUES = PROJECT_HUES;
export { HUE_NAMES, hueSwatchClass, type ProjectHue };

export function hostTextClass(color: string | null | undefined): string {
  return color ? hueTextClass(color) : "text-ink-muted";
}

/** A host's display name: profile label, then whatever snapshot is at hand, then its short id. */
export function hostName(host: Pick<SessionHost, "mid" | "label"> | Pick<HostEntry, "machine_id" | "label">): string {
  const id = "mid" in host ? host.mid : host.machine_id;
  return host.label?.trim() || id.slice(0, 8);
}
