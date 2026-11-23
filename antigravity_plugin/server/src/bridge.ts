import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __bridgeDir = path.dirname(fileURLToPath(import.meta.url));

export interface BridgeConfig {
  port: number;
  token: string;
  pid?: number;
  protocol?: number;
}

function candidateDiscoveryPaths(): string[] {
  const out: string[] = [];
  if (process.env.OIT_BRIDGE_FILE) {
    out.push(process.env.OIT_BRIDGE_FILE);
  }
  if (process.env.OIT_DATA_DIR) {
    out.push(path.join(process.env.OIT_DATA_DIR, "mcp_bridge.json"));
  }
  try {
    const marker = path.resolve(__bridgeDir, "..", "..", ".oit_data_dir");
    if (fs.existsSync(marker)) {
      const dir = fs.readFileSync(marker, "utf-8").trim();
      if (dir) out.push(path.join(dir, "mcp_bridge.json"));
    }
  } catch {
    // Ignore unreadable marker.
  }
  const appData = process.env.APPDATA || path.join(os.homedir(), "AppData", "Roaming");
  out.push(path.join(appData, "OIT", "mcp_bridge.json"));
  out.push(path.join(os.homedir(), ".oit", "mcp_bridge.json"));
  return out;
}

export function loadBridgeConfig(): BridgeConfig {
  if (process.env.OIT_BRIDGE_PORT && process.env.OIT_BRIDGE_TOKEN) {
    return {
      port: Number(process.env.OIT_BRIDGE_PORT),
      token: process.env.OIT_BRIDGE_TOKEN,
    };
  }
  for (const p of candidateDiscoveryPaths()) {
    try {
      if (fs.existsSync(p)) {
        const raw = fs.readFileSync(p, "utf-8");
        const parsed = JSON.parse(raw) as BridgeConfig;
        if (parsed && parsed.port && parsed.token) {
          return parsed;
        }
      }
    } catch {
      // Try next path.
    }
  }
  throw new Error(
    "OIT MCP Bridge discovery file (mcp_bridge.json) not found. Ensure OIT is running with Antigravity desktop takeover enabled."
  );
}

export async function bridgeGet<T = Record<string, unknown>>(
  endpoint: string,
  query?: Record<string, string | number | undefined>
): Promise<T> {
  const cfg = loadBridgeConfig();
  const url = new URL(`http://127.0.0.1:${cfg.port}${endpoint}`);
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      if (v !== undefined && v !== "") {
        url.searchParams.set(k, String(v));
      }
    }
  }
  const resp = await fetch(url, {
    method: "GET",
    headers: {
      Authorization: `Bearer ${cfg.token}`,
      Accept: "application/json",
    },
  });
  const text = await resp.text();
  if (!resp.ok) {
    throw new Error(`OIT Bridge GET ${endpoint} failed (${resp.status}): ${text}`);
  }
  return JSON.parse(text) as T;
}

export async function bridgePost<T = Record<string, unknown>>(
  endpoint: string,
  body: Record<string, unknown>
): Promise<T> {
  const cfg = loadBridgeConfig();
  const resp = await fetch(`http://127.0.0.1:${cfg.port}${endpoint}`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${cfg.token}`,
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify(body),
  });
  const text = await resp.text();
  if (!resp.ok) {
    throw new Error(`OIT Bridge POST ${endpoint} failed (${resp.status}): ${text}`);
  }
  return JSON.parse(text) as T;
}
