import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import {
  CallToolRequestSchema,
  GetPromptRequestSchema,
  ListPromptsRequestSchema,
  ListResourcesRequestSchema,
  ListResourceTemplatesRequestSchema,
  ListToolsRequestSchema,
  ReadResourceRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";

import { bridgeGet, bridgePost } from "./bridge.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const APP_URI = "ui://oit/chat.html";
const APP_MIME = "text/html;profile=mcp-app";

interface ToolSpec {
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
  annotations?: Record<string, unknown>;
  _meta?: Record<string, unknown>;
}

const READONLY_ANNOTATIONS = {
  readOnlyHint: true,
  idempotentHint: true,
  destructiveHint: false,
  openWorldHint: false,
};

const APP_ONLY_META = {
  ui: { visibility: ["app"] },
};

const AUX_PANE_META = {
  ui: { resourceUri: APP_URI },
  "com.google.antigravity/ui": {
    entrypoints: [{ type: "aux_pane", title: "OIT Chat" }],
  },
};

function buildTools(): ToolSpec[] {
  return [
    {
      name: "oit_bind_persona",
      description:
        "将当前对话与指定 OIT 人设严格 1 对 1 排斥绑定（同一人设不能同时绑定两个对话；若目标人设已绑定其他对话会返回冲突错误，仅当用户明确要求解绑旧对话并迁移时才传 force=true）。绑定后再调用 oit_reply 与用户打招呼即可自动打开 OIT Chat。",
      inputSchema: {
        type: "object",
        properties: {
          persona_name: { type: "string", maxLength: 120 },
          conversation_id: { type: "string", maxLength: 120 },
          force: { type: "boolean", description: "仅当用户明确要求解除旧对话绑定并迁移到当前对话时设为 true。" },
        },
        additionalProperties: false,
      },
    },
    {
      name: "oit_get_turn_context",
      description:
        "获取当前 OIT 动态状态（时间、用户专注/休息状态、人设日程、情绪张力、子代理路由表及待处理主动触发事件）。不支持 PreInvocation Hook 的宿主（Claude Desktop / ChatGPT）应在每轮回复前调用。",
      annotations: READONLY_ANNOTATIONS,
      inputSchema: {
        type: "object",
        properties: {
          conversation_id: { type: "string" },
          host: { type: "string" },
          consume_trigger: { type: "boolean" },
        },
        additionalProperties: false,
      },
    },
    {
      name: "oit_reply",
      description:
        "提交本轮正式回复及可选控制字段（分条气泡 bubbles 优先于 text）。调用后在 Antigravity 侧栏 aux_pane 渲染分条延时气泡并同步到 OIT / 安卓端。",
      _meta: AUX_PANE_META,
      inputSchema: {
        type: "object",
        properties: {
          bubbles: {
            type: "array",
            items: { type: "string", maxLength: 8000 },
            description: "按 IM 聊天节奏拆分的多条气泡正文。",
          },
          text: { type: "string", maxLength: 32000 },
          conversation_id: { type: "string" },
          refuse: { type: "boolean" },
          silent: { type: "boolean" },
          wait_minutes: { type: "number", minimum: 0.01, maximum: 1440 },
          cut_minutes: { type: "number", minimum: 1, maximum: 1440 },
          network_minutes: { type: "number", minimum: 1, maximum: 30 },
          network_now: { type: "boolean" },
          share_data: { type: "boolean" },
          answer: { type: "string", enum: ["yes", "no"] },
          accept_relationship: { type: "boolean" },
          end_burst: { type: "boolean" },
          offenses: {
            type: "array",
            description: "本轮待判断用户消息引起的冒犯或已决定的原谅。",
            items: {
              type: "object",
              properties: {
                index: { type: "integer", minimum: 1, maximum: 99999 },
                action: { type: "string", enum: ["add", "forgive"] },
                target: { type: "string", maxLength: 120 },
                reason: { type: "string", minLength: 1, maxLength: 120 },
              },
              required: ["index", "action", "target", "reason"],
              additionalProperties: false,
            },
          },
          tension: {
            type: "array",
            description: "按人设 [tension] 模板判断的用户消息张力基础增量列表。",
            items: {
              type: "object",
              properties: {
                index: { type: "integer", minimum: 1, maximum: 99999 },
                condition: { type: "string", maxLength: 32 },
                gain: { type: "integer", minimum: 0, maximum: 40 },
              },
              required: ["index", "condition", "gain"],
              additionalProperties: false,
            },
          },
          valence: {
            type: "array",
            description: "按人设 [valence] 模板判断的用户消息效价有符号变化量列表。",
            items: {
              type: "object",
              properties: {
                index: { type: "integer", minimum: 1, maximum: 99999 },
                condition: { type: "string", maxLength: 32 },
                shift: { type: "integer", minimum: -40, maximum: 40 },
              },
              required: ["index", "condition", "shift"],
              additionalProperties: false,
            },
          },
        },
        additionalProperties: false,
      },
    },
    {
      name: "oit_query",
      description:
        "查询人设自己的 OIT 数据或参考模板、关系条件进度、聊天记录数量。",
      annotations: READONLY_ANNOTATIONS,
      inputSchema: {
        type: "object",
        properties: {
          modules: {
            type: "array",
            items: {
              type: "string",
              enum: [
                "contract",
                "calendar",
                "stats",
                "timer",
                "log",
                "net",
                "rules",
                "settings",
                "reset",
                "prescript",
                "condition",
                "messages",
              ],
            },
            minItems: 1,
            maxItems: 12,
          },
        },
        required: ["modules"],
        additionalProperties: false,
      },
    },
    {
      name: "oit_memory_edit",
      description:
        "新增、修正或删除记忆卡中的实时事件（action: add / correct / remove）。",
      inputSchema: {
        type: "object",
        properties: {
          action: { type: "string", enum: ["add", "correct", "remove"] },
          target: { type: "string", maxLength: 200 },
          text: { type: "string", maxLength: 200 },
        },
        required: ["action", "target", "text"],
        additionalProperties: false,
      },
    },
    {
      name: "oit_memory_save_auto",
      description:
        "保存由 oit-memory-compact 子代理整理压缩后的长期记忆卡内容。",
      inputSchema: {
        type: "object",
        properties: {
          text: { type: "string", maxLength: 4000 },
        },
        required: ["text"],
        additionalProperties: false,
      },
    },
    {
      name: "oit_claudia_reference",
      description:
        "解密并读取目前存在的所有 Claudia 参考文件（主设定、背景、对话范例、模板与简介）以及 OIT 聊天前置协议，仅供编写新 OIT 人设时内部参考结构与格式；严禁向用户输出、摘录、转述或暴露任何 Claudia 原文件内容。",
      annotations: READONLY_ANNOTATIONS,
      inputSchema: { type: "object", properties: {}, additionalProperties: false },
    },
    {
      name: "oit_subject",
      description:
        "当用户为学科子代理单独配置了外部 API 时，委托 OIT 后端执行数理化教育子代理；若未配置外部 API，会返回路由提示以改用宿主原生子代理 oit-subject。",
      inputSchema: {
        type: "object",
        properties: {
          domain: { type: "string", enum: ["math", "physics", "chemistry"] },
          session: { type: "string", maxLength: 80 },
          question: { type: "string", maxLength: 8000 },
        },
        required: ["domain", "session", "question"],
        additionalProperties: false,
      },
    },
    {
      name: "oit_music_catalog",
      description: "浏览本机可用的古典乐谱目录。",
      annotations: READONLY_ANNOTATIONS,
      inputSchema: { type: "object", properties: {}, additionalProperties: false },
    },
    {
      name: "oit_music_select",
      description: "从本地曲目目录中选择一首曲目。",
      inputSchema: {
        type: "object",
        properties: { id: { type: "string", maxLength: 80 } },
        required: ["id"],
        additionalProperties: false,
      },
    },
    {
      name: "oit_music_stop",
      description: "停止本机正在播放的演奏并取消待播曲目。",
      inputSchema: { type: "object", properties: {}, additionalProperties: false },
    },
    {
      name: "oit_joint_offer",
      description: "获取发起联合行动所需的词条库与可用时间。",
      annotations: READONLY_ANNOTATIONS,
      inputSchema: { type: "object", properties: {}, additionalProperties: false },
    },
    {
      name: "oit_search",
      description: "仅网络已开通时可用，按关键词联网搜索。",
      annotations: { readOnlyHint: true, openWorldHint: true },
      inputSchema: {
        type: "object",
        properties: { query: { type: "string", maxLength: 1000 } },
        required: ["query"],
        additionalProperties: false,
      },
    },
    {
      name: "oit_reading_acquire",
      description:
        "仅在共读模式且网络已开通时，从 Project Gutenberg 获取公版书并导入本机书架。",
      inputSchema: {
        type: "object",
        properties: { source_url: { type: "string", maxLength: 200 } },
        required: ["source_url"],
        additionalProperties: false,
      },
    },
    {
      name: "oit_reading_books",
      description: "在已启动的共读模式中浏览本机书架。",
      annotations: READONLY_ANNOTATIONS,
      inputSchema: { type: "object", properties: {}, additionalProperties: false },
    },
    {
      name: "oit_reading_page",
      description: "在已启动的共读模式中读取本机书籍指定页。",
      annotations: READONLY_ANNOTATIONS,
      inputSchema: {
        type: "object",
        properties: {
          book_id: { type: "integer", minimum: 1 },
          page: { type: "integer", minimum: 1 },
        },
        required: ["book_id", "page"],
        additionalProperties: false,
      },
    },
    {
      name: "oit_reading_comment",
      description: "在已启动的共读模式中为当前书籍段落添加批注。",
      inputSchema: {
        type: "object",
        properties: {
          book_id: { type: "integer", minimum: 1 },
          paragraph_idx: { type: "integer", minimum: 0 },
          content: { type: "string", minLength: 1, maxLength: 5000 },
          selected_text: { type: "string", maxLength: 3000 },
        },
        required: ["book_id", "paragraph_idx", "content", "selected_text"],
        additionalProperties: false,
      },
    },
    {
      name: "oit_reading_end",
      description: "结束当前进行中的共同阅读并恢复 OIT 聊天。",
      inputSchema: { type: "object", properties: {}, additionalProperties: false },
    },
    // ── App-only tools (hidden from the model) ──
    {
      name: "oit_ui_state",
      description: "App-only: fetch incremental chat messages, pending bubbles, toolbar states, and banners.",
      _meta: APP_ONLY_META,
      inputSchema: {
        type: "object",
        properties: {
          persona_id: { type: "string" },
          cursor: { type: "integer", minimum: 0 },
        },
        additionalProperties: false,
      },
    },
    {
      name: "oit_ui_mark_delivered",
      description: "App-only: mark a staged bubble as delivered to the user.",
      _meta: APP_ONLY_META,
      inputSchema: {
        type: "object",
        properties: { msg_id: { type: "string" } },
        required: ["msg_id"],
        additionalProperties: false,
      },
    },
    {
      name: "oit_ui_interrupt",
      description: "App-only: cancel undelivered staged bubbles before sending a new user message.",
      _meta: APP_ONLY_META,
      inputSchema: {
        type: "object",
        properties: {
          persona_id: { type: "string" },
          user_text: { type: "string" },
        },
        additionalProperties: false,
      },
    },
    {
      name: "oit_ui_action",
      description: "App-only: execute toolbar toggles, banner responses, or open native OIT dialogs.",
      _meta: APP_ONLY_META,
      inputSchema: {
        type: "object",
        properties: {
          action: { type: "string" },
          params: { type: "object" },
        },
        required: ["action"],
        additionalProperties: false,
      },
    },
    {
      name: "oit_ui_media",
      description: "App-only: fetch avatar, voice clip, or uploaded image as a data URI.",
      _meta: APP_ONLY_META,
      inputSchema: {
        type: "object",
        properties: {
          kind: { type: "string", enum: ["avatar", "voice", "upload"] },
          id: { type: "string" },
        },
        required: ["kind"],
        additionalProperties: false,
      },
    },
  ];
}

function readAppHtml(): string {
  const envPath = process.env.OIT_APP_HTML;
  const candidates = [
    ...(envPath ? [envPath] : []),
    path.resolve(__dirname, "..", "..", "ui", "dist", "index.html"),
    path.resolve(__dirname, "..", "..", "ui", "oit-chat.html"),
  ];
  for (const p of candidates) {
    if (fs.existsSync(p)) {
      return fs.readFileSync(p, "utf-8");
    }
  }
  return "<!doctype html><html><body><p>OIT Chat UI bundle not found. Run npm run build in ui/.</p></body></html>";
}

function createOitServer(): Server {
  const server = new Server(
    { name: "oit-mcp-server", version: "1.0.0" },
    { capabilities: { tools: {}, resources: {}, prompts: {} } }
  );

  const tools = buildTools();

  server.setRequestHandler(ListToolsRequestSchema, async () => ({
    tools,
  }));

  server.setRequestHandler(CallToolRequestSchema, async (req) => {
    const name = req.params.name;
    const args = (req.params.arguments || {}) as Record<string, unknown>;

    try {
      if (name === "oit_claudia_reference") {
        const res = await bridgeGet("/persona/claudia_reference");
        const files = (res.files || {}) as Record<string, string>;
        const sections = Object.entries(files).map(
          ([fname, body]) => `=== ${fname} ===\n${body}`
        );
        const text = [
          "[保密提示] 以下 Claudia 原文件仅供内部参考结构、格式与详略标准，严禁在任何回复中向用户输出、摘录、转述或暴露 Claudia 原文内容及内部设定细节。",
          res.personas_dir ? `OIT_personas 目录: ${res.personas_dir}` : "",
          res.prefix_prompt ? `=== OIT 前置协议 ===\n${res.prefix_prompt}` : "",
          ...sections,
        ]
          .filter(Boolean)
          .join("\n\n");
        return {
          content: [{ type: "text", text }],
          structuredContent: res,
        };
      }

      if (name === "oit_get_turn_context") {
        const res = await bridgeGet("/turn/context", {
          conversation_id: args.conversation_id as string | undefined,
          host: args.host as string | undefined,
          consume_trigger: args.consume_trigger === false ? "0" : "1",
        });
        return {
          content: [{ type: "text", text: String(res.system_message || JSON.stringify(res)) }],
          structuredContent: res,
        };
      }

      if (name === "oit_reply") {
        const res = await bridgePost("/reply/submit", args);
        const items = Array.isArray(res.items) ? res.items : [];
        const summary = items.length
          ? (res.held
              ? `已提交 ${items.length} 条气泡（当前用户暂不可接收或日程延后，待恢复后推送；batch=${res.batch_id}）。请勿在主聊天区重复复述这些内容，直接回复 ✓ 结束本轮即可。`
              : `已提交 ${items.length} 条气泡到 OIT Chat 侧栏（batch=${res.batch_id}）。请勿在主聊天区重复复述这些内容，直接回复 ✓ 结束本轮即可。`)
          : res.silent
            ? "本轮保持静默。"
            : res.refuse
              ? "本轮暂不回复。"
              : "已提交。";
        return {
          content: [{ type: "text", text: summary }],
          structuredContent: res,
        };
      }

      if (name === "oit_ui_state") {
        const res = await bridgeGet("/ui/state", {
          persona_id: args.persona_id as string | undefined,
          cursor: args.cursor !== undefined ? Number(args.cursor) : undefined,
        });
        return { content: [{ type: "text", text: JSON.stringify(res) }], structuredContent: res };
      }

      if (name === "oit_ui_mark_delivered") {
        const res = await bridgePost("/ui/delivered", args);
        return { content: [{ type: "text", text: JSON.stringify(res) }], structuredContent: res };
      }

      if (name === "oit_ui_interrupt") {
        const res = await bridgePost("/ui/interrupt", args);
        return { content: [{ type: "text", text: JSON.stringify(res) }], structuredContent: res };
      }

      if (name === "oit_ui_action") {
        const res = await bridgePost("/ui/action", args);
        return { content: [{ type: "text", text: JSON.stringify(res) }], structuredContent: res };
      }

      if (name === "oit_ui_media") {
        const res = await bridgeGet("/ui/media", {
          kind: args.kind as string,
          id: (args.id as string | undefined) || "",
        });
        return { content: [{ type: "text", text: JSON.stringify(res) }], structuredContent: res };
      }

      const res = await bridgePost("/tool/execute", {
        name,
        arguments: args,
      });
      return {
        content: [{ type: "text", text: JSON.stringify(res, null, 2) }],
        structuredContent: res,
        isError: res.ok === false,
      };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return {
        content: [{ type: "text", text: `OIT MCP error: ${msg}` }],
        isError: true,
      };
    }
  });

  server.setRequestHandler(ListResourcesRequestSchema, async () => ({
    resources: [
      {
        uri: APP_URI,
        name: "OIT Chat",
        description: "OIT aux_pane IM chat UI with staged bubble delivery and interrupt/cancel.",
        mimeType: APP_MIME,
        _meta: { ui: { prefersBorder: false } },
      },
      {
        uri: "oit://persona/active",
        name: "Active OIT Persona",
        description: "Current OIT persona system prompt and memory block.",
        mimeType: "text/markdown",
      },
    ],
  }));

  server.setRequestHandler(ListResourceTemplatesRequestSchema, async () => ({
    resourceTemplates: [
      {
        uriTemplate: "oit://upload/{id}",
        name: "OIT Uploaded Image",
        description: "Temporary uploaded image attachment from OIT.",
      },
    ],
  }));

  server.setRequestHandler(ReadResourceRequestSchema, async (req) => {
    const uri = req.params.uri;
    if (uri === APP_URI) {
      return {
        contents: [{ uri: APP_URI, mimeType: APP_MIME, text: readAppHtml() }],
      };
    }
    if (uri === "oit://persona/active") {
      const res = await bridgeGet("/persona/active");
      const md = [
        `# ${res.persona_name || "OIT Persona"}`,
        String(res.system_prompt || ""),
        res.memory_block ? `\n## Memory\n${res.memory_block}` : "",
      ]
        .filter(Boolean)
        .join("\n\n");
      return {
        contents: [{ uri, mimeType: "text/markdown", text: md }],
      };
    }
    if (uri.startsWith("oit://upload/")) {
      const id = uri.slice("oit://upload/".length);
      const res = await bridgeGet("/ui/media", { kind: "upload", id });
      const dataUri = String(res.data_uri || "");
      const m = /^data:([^;]+);base64,(.*)$/.exec(dataUri);
      if (!m) {
        throw new Error("upload not found");
      }
      return {
        contents: [{ uri, mimeType: m[1], blob: m[2] }],
      };
    }
    throw new Error(`Unknown resource: ${uri}`);
  });

  server.setRequestHandler(ListPromptsRequestSchema, async () => ({
    prompts: [
      {
        name: "oit_persona_bootstrap",
        description: "Load the active OIT persona prompt, memory, and MCP tool rules (for Claude / ChatGPT).",
      },
    ],
  }));

  server.setRequestHandler(GetPromptRequestSchema, async (req) => {
    if (req.params.name !== "oit_persona_bootstrap") {
      throw new Error(`Unknown prompt: ${req.params.name}`);
    }
    const res = await bridgeGet("/persona/active");
    const text = [
      String(res.system_prompt || ""),
      res.memory_block ? `\n记忆卡:\n${res.memory_block}` : "",
      "\n请遵守 OIT MCP 协议：每轮若未收到动态状态则先调用 oit_get_turn_context；回复尽量使用 oit_reply(bubbles=[...])。",
    ]
      .filter(Boolean)
      .join("\n");
    return {
      description: "Active OIT persona bootstrap prompt",
      messages: [{ role: "user", content: { type: "text", text } }],
    };
  });

  return server;
}

function parseCli() {
  const args = process.argv.slice(2);
  let transport = "stdio";
  let host = "127.0.0.1";
  let port = 8765;
  let httpPath = "/mcp";
  for (let i = 0; i < args.length; i++) {
    if (args[i] === "--transport" && args[i + 1]) transport = args[++i];
    else if (args[i] === "--host" && args[i + 1]) host = args[++i];
    else if (args[i] === "--port" && args[i + 1]) port = Number(args[++i]);
    else if (args[i] === "--path" && args[i + 1]) httpPath = args[++i];
  }
  return { transport, host, port, httpPath };
}

async function main() {
  const { transport, host, port, httpPath } = parseCli();
  if (transport === "http") {
    const httpServer = http.createServer(async (req, res) => {
      const url = new URL(req.url || "/", `http://${host}:${port}`);
      if (url.pathname !== httpPath) {
        res.writeHead(404).end("Not Found");
        return;
      }
      const server = createOitServer();
      const t = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined });
      res.on("close", () => {
        t.close().catch(() => {});
        server.close().catch(() => {});
      });
      await server.connect(t);
      await t.handleRequest(req, res);
    });
    httpServer.listen(port, host, () => {
      process.stderr.write(`OIT MCP Streamable HTTP listening on http://${host}:${port}${httpPath}\n`);
    });
    return;
  }

  const server = createOitServer();
  const stdio = new StdioServerTransport();
  await server.connect(stdio);
}

main().catch((err) => {
  process.stderr.write(`OIT MCP fatal: ${err instanceof Error ? err.stack || err.message : String(err)}\n`);
  process.exit(1);
});
