/* Whether the remote-answer bridge (Feishu or WeCom) has pushed (or is about
 * to push) a pending ask_user gate to the user's phone, keyed by gate id.
 *
 * The state arrives as ephemeral `gate_relay_updated` events (seq 0) and is
 * read by `AskUserDialog`, which renders in both the side panel and the
 * canvas tile overlay. A module store keeps it out of the props of every
 * layer in between, the same singleton shape as `hoverStore`. A reload or
 * reconnect re-seeds it from GET /sessions/{id}/gate-relays, because
 * ephemeral events are never replayed. */

import { useSyncExternalStore } from "react";
import type { GateRelayUpdated } from "./types";

type Listener = () => void;

const listeners = new Set<Listener>();
let relays: Record<string, GateRelayUpdated> = {};

function emit(): void {
  for (const listener of listeners) listener();
}

export function applyGateRelayEvent(event: GateRelayUpdated): void {
  if (event.state === "cancelled") {
    if (!(event.gate_id in relays)) return;
    const { [event.gate_id]: _removed, ...rest } = relays;
    relays = rest;
  } else {
    relays = { ...relays, [event.gate_id]: event };
  }
  emit();
}

export function replaceGateRelays(next: Record<string, GateRelayUpdated>): void {
  relays = { ...next };
  emit();
}

function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useGateRelay(gateId: string): GateRelayUpdated | null {
  const read = () => relays[gateId] ?? null;
  return useSyncExternalStore(subscribe, read, read);
}

/** One line for the badge, or null when there is nothing to say. */
export function gateRelayLabel(relay: GateRelayUpdated | null): string | null {
  if (!relay) return null;
  const clock = (seconds: number) =>
    new Date(seconds * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const name = relay.channel === "wecom" ? "企业微信" : "飞书";
  if (relay.state === "sent") return `已推送到${name} · ${clock(relay.at)}`;
  if (relay.state === "scheduled") {
    return relay.send_at ? `将于 ${clock(relay.send_at)} 推送到${name}` : `即将推送到${name}`;
  }
  if (relay.state === "failed") {
    return relay.detail ? `${name}推送失败：${relay.detail}` : `${name}推送失败`;
  }
  return null;
}
