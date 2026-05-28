import type { Activity, EventRecord, NodeInfo, ServerEvent } from "./types";

export type TranscriptBlock =
  | { id: string; kind: "text"; text: string }
  | { id: string; kind: "thinking"; text: string }
  | { id: string; kind: "activity"; items: Activity[] }
  | { id: string; kind: "error"; text: string };

export type ChatTurn = {
  id: string;
  role: "user" | "assistant";
  text: string;
  blocks: TranscriptBlock[];
  streaming?: boolean;
  cursor?: "text" | "thinking" | "activity" | null;
};

export function createUserTurn(id: string, text: string): ChatTurn {
  return {
    id,
    role: "user",
    text,
    blocks: [],
    cursor: null,
  };
}

export function createAssistantTurn(id: string, streaming: boolean): ChatTurn {
  return {
    id,
    role: "assistant",
    text: "",
    blocks: [],
    streaming,
    cursor: null,
  };
}

export function appendServerEvent(prev: ChatTurn[], event: ServerEvent): ChatTurn[] {
  if (event.type === "text_delta") {
    return appendAssistantText(prev, event.text);
  }
  if (event.type === "thinking") {
    return appendAssistantThinking(prev, event.text);
  }
  if (event.type === "activity") {
    return mergeAssistantActivity(prev, event);
  }
  if (event.type === "turn_done") {
    return finishAssistantTurn(prev);
  }
  if (event.type === "error") {
    return appendAssistantError(prev, event.message);
  }
  if (event.type === "interaction_request") {
    return markAssistantBoundary(prev);
  }
  return prev;
}

export function buildTurnsFromEvents(node: NodeInfo, records: EventRecord[]): ChatTurn[] {
  let turns: ChatTurn[] = [
    createUserTurn(`${node.id}-user`, node.prompt),
    createAssistantTurn(
      `${node.id}-assistant`,
      node.state === "running" || node.state === "waiting",
    ),
  ];
  for (const record of records) {
    turns = appendServerEvent(turns, record.event);
  }
  return turns;
}

export function appendAssistantText(prev: ChatTurn[], text: string): ChatTurn[] {
  if (text.length === 0) return prev;
  return updateLastAssistantTurn(prev, (turn) => {
    const lastBlock = turn.blocks.at(-1);
    const nextBlocks =
      turn.cursor === "text" && lastBlock?.kind === "text"
        ? replaceLastBlock(turn.blocks, { ...lastBlock, text: lastBlock.text + text })
        : [
            ...turn.blocks,
            {
              id: nextBlockId(turn),
              kind: "text" as const,
              text,
            },
          ];
    return {
      ...turn,
      text: turn.text + text,
      blocks: nextBlocks,
      cursor: "text",
    };
  });
}

export function appendAssistantThinking(prev: ChatTurn[], text: string): ChatTurn[] {
  if (text.length === 0) return markAssistantBoundary(prev);
  return updateLastAssistantTurn(prev, (turn) => {
    const lastBlock = turn.blocks.at(-1);
    const nextBlocks =
      turn.cursor === "thinking" && lastBlock?.kind === "thinking"
        ? replaceLastBlock(turn.blocks, { ...lastBlock, text: lastBlock.text + text })
        : [
            ...turn.blocks,
            {
              id: nextBlockId(turn),
              kind: "thinking" as const,
              text,
            },
          ];
    return {
      ...turn,
      blocks: nextBlocks,
      cursor: "thinking",
    };
  });
}

export function mergeAssistantActivity(prev: ChatTurn[], activity: Activity): ChatTurn[] {
  return updateLastAssistantTurn(prev, (turn) => {
    const lastBlock = turn.blocks.at(-1);
    const nextBlocks =
      turn.cursor === "activity" && lastBlock?.kind === "activity"
        ? replaceLastBlock(turn.blocks, {
            ...lastBlock,
            items: mergeActivityItems(lastBlock.items, activity),
          })
        : [
            ...turn.blocks,
            {
              id: nextBlockId(turn),
              kind: "activity" as const,
              items: [activity],
            },
          ];
    return {
      ...turn,
      blocks: nextBlocks,
      cursor: "activity",
    };
  });
}

export function finishAssistantTurn(prev: ChatTurn[]): ChatTurn[] {
  return updateLastAssistantTurn(prev, (turn) => ({
    ...turn,
    streaming: false,
    cursor: null,
  }));
}

export function appendAssistantError(prev: ChatTurn[], message: string): ChatTurn[] {
  return updateLastAssistantTurn(prev, (turn) => {
    const text = `Error: ${message}`;
    const lastBlock = turn.blocks.at(-1);
    const nextBlocks =
      lastBlock?.kind === "error"
        ? replaceLastBlock(turn.blocks, { ...lastBlock, text: `${lastBlock.text}\n${text}` })
        : [
            ...turn.blocks,
            {
              id: nextBlockId(turn),
              kind: "error" as const,
              text,
            },
          ];
    return {
      ...turn,
      text: turn.text ? `${turn.text}\n\n${text}` : text,
      blocks: nextBlocks,
      streaming: false,
      cursor: null,
    };
  });
}

export function markAssistantBoundary(prev: ChatTurn[]): ChatTurn[] {
  return updateLastAssistantTurn(prev, (turn) =>
    turn.cursor === null
      ? turn
      : {
          ...turn,
          cursor: null,
        },
  );
}

function updateLastAssistantTurn(
  prev: ChatTurn[],
  updater: (turn: ChatTurn) => ChatTurn,
): ChatTurn[] {
  if (prev.length === 0) return prev;
  const last = prev[prev.length - 1];
  if (last.role !== "assistant") return prev;
  const next = updater(last);
  if (next === last) return prev;
  return [...prev.slice(0, -1), next];
}

function nextBlockId(turn: ChatTurn): string {
  return `${turn.id}-block-${turn.blocks.length + 1}`;
}

function replaceLastBlock(
  blocks: TranscriptBlock[],
  block: TranscriptBlock,
): TranscriptBlock[] {
  return [...blocks.slice(0, -1), block];
}

function mergeActivityItems(items: Activity[], next: Activity): Activity[] {
  const index = items.findIndex((item) => item.id === next.id);
  if (index < 0) return [...items, next];
  return items.map((item, i) => (i === index ? next : item));
}
