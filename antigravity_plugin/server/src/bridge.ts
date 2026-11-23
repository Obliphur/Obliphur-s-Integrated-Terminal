import fs from "node:fs";
import os from "node:os";
import path from "node:path";

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
