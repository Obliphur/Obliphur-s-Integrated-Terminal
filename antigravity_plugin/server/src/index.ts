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
      name: "oit_kb_search", description: "查询个人技巧库（绝不读取概念库）。先判断题型并传 problem_mode；卡片按通用性及思路参考设置筛选。返回技巧溯源、模式与不考虑的技巧。看图提取特征后再检索。",
      annotations: READONLY_ANNOTATIONS,
      inputSchema: { type: "object", properties: { domain: { type: "string" }, query: { type: "string", maxLength: 12000 }, top_k: { type: "integer", minimum: 1, maximum: 20 }, problem_mode: { type: "string", enum: ["answer_only", "requires_working"] }, conversation_id: { type: "string" } }, required: ["domain", "query"], additionalProperties: false },
    },
    {
      name: "oit_kb_trace", description: "查询技巧卡片的完整祖先推导链、熟练度、前提禁区与 Obsidian URI。无权查询概念卡。",
      annotations: READONLY_ANNOTATIONS,
      inputSchema: { type: "object", properties: { card_id: { type: "string", maxLength: 120 }, conversation_id: { type: "string" } }, required: ["card_id"], additionalProperties: false },
    },
    {
      name: "oit_kb_record_use", description: "解题后记录实际采纳或确认套错的技巧，更新独立统计。未使用的候选不计数。",
      inputSchema: { type: "object", properties: { used_card_ids: { type: "array", items: { type: "string" }, maxItems: 20 }, failed_card_ids: { type: "array", items: { type: "string" }, maxItems: 20 }, problem_summary: { type: "string", maxLength: 600 }, conversation_id: { type: "string" } }, additionalProperties: false },
    },
    {
      name: "oit_kb_propose", description: "提交待用户审核的技巧正文提案（禁止直写）。先参考 oit_kb_search 的 excluded_tactics 并查重，符合排除描述的不得提交；exclusions_considered 原样填写该设置。遵循「以补充为主，新增为辅」：库内已有核心相同或相近卡片时优先用 UPDATE_CARD / MERGE_ALIAS 补充原卡，严禁重复建卡。title 须规范命名：有公认专名或定理名时原样保留；无公认专名时尽量8字以内浓缩核心对象与方法词，不写完整句子、不带题号/具体数字/临时符号、不加冗余后缀、不含 Markdown/LaTeX；summary 仅用纯自然语言简短说明。",
      inputSchema: { type: "object", properties: { action: { type: "string", enum: ["ADD_CARD", "ADD_DERIVED", "UPDATE_CARD", "MERGE_ALIAS", "REPARENT"] }, subject: { type: "string", maxLength: 60 }, title: { type: "string", maxLength: 300, description: "规范短标题：有公认专名/定理名时原样保留；否则尽量8字以内浓缩核心对象与方法词，不写完整句子、不带题号/具体数字/临时符号、不含 Markdown/LaTeX。" }, target_card_id: { type: "string", maxLength: 120 }, parent_ids: { type: "array", items: { type: "string" }, maxItems: 20 }, proposed_card: { type: "object", properties: { category: { type: "string", maxLength: 120 }, topic: { type: "string", maxLength: 300 }, aliases: { type: "array", items: { type: "string", maxLength: 300 }, maxItems: 40 }, triggers: { type: "array", items: { type: "string", maxLength: 600 }, maxItems: 40, description: "6-12条2-10字简短具体的审题触发词，覆盖已知条件、模型对象、图像/式子特征、设问词与同义表述。" }, statement: { type: "string", maxLength: 6000 }, preconditions: { type: "string", maxLength: 6000 }, proof_sketch: { type: "string", maxLength: 6000 }, generality: { type: "string", enum: ["universal", "explain_first", "non_universal"] }, examples: { type: "array", items: { type: "string", maxLength: 1500 }, maxItems: 20 }, parent_ids: { type: "array", items: { type: "string", maxLength: 120 }, maxItems: 20 } }, additionalProperties: false }, summary: { type: "string", maxLength: 1500, description: "给人看的简短审核说明，仅用纯自然语言，严禁 Markdown/LaTeX。" }, evidence: { type: "array", items: { type: "object", properties: { ts: { type: "string", maxLength: 40 }, problem: { type: "string", maxLength: 1200 }, reason: { type: "string", maxLength: 1200 } }, additionalProperties: false }, maxItems: 20 }, exclusions_considered: { type: "string", maxLength: 8000 }, conversation_id: { type: "string" } }, required: ["action", "subject", "title"], additionalProperties: false },
    },
    {
      name: "oit_bind_persona",
      description:
        "将当前对话与指定 OIT 人设严格 1 对 1 排斥绑定（同一人设不能同时绑定两个对话；若目标人设已绑定其他对话会返回冲突错误，仅当用户明确要求解绑旧对话并迁移时才传 force=true）。完成绑定后不需要调用 oit_reply 打招呼，直接输出 ✓ 结束本轮即可（除非本轮同时附带了待回复的用户消息或主动触发）。",
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
        "获取当前 OIT 完整单轮上下文（完整人设、记忆卡、近期完整对话记录、动态状态、情绪判定、子代理路由与本轮待回复消息/主动触发）。无状态单轮模式请传入 turn_id。",
      annotations: READONLY_ANNOTATIONS,
      inputSchema: {
        type: "object",
        properties: {
          turn_id: { type: "string", maxLength: 120, description: "无状态单轮请求 ID（来自 [OIT单轮请求 turn_id=...]）。" },
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
        "提交本轮正式回复及可选控制字段（分条气泡 bubbles 优先于 text），直接同步到 OIT 原生聊天界面与安卓端。",
      inputSchema: {
        type: "object",
        properties: {
          turn_id: { type: "string", maxLength: 120, description: "无状态单轮请求 ID（来自 [OIT单轮请求 turn_id=...]）。" },
          bubbles: {
            type: "array",
            items: { type: "string", maxLength: 8000 },
            description: "按 IM 聊天节奏拆分的多条气泡正文。",
          },
          text: { type: "string", maxLength: 32000 },
          kb_citations: { type: "array", items: { type: "string" }, maxItems: 20 },
          conversation_id: { type: "string" },
          refuse: { type: "boolean" },
          silent: { type: "boolean" },
          wait_minutes: { type: "number", minimum: 0.01, maximum: 1440 },
          cut_minutes: { type: "number", minimum: 1, maximum: 1440 },
          network_minutes: { type: "number", minimum: 1, maximum: 30 },
          network_now: { type: "boolean" },
          share_data: { type: "boolean" },
          scenario_action: {
            type: "string",
            enum: ["request", "end"],
            description: "申请开启情景模拟填 request（需用户接受且须已解锁）；随时结束当前情景模拟填 end。",
          },
          answer: { type: "string", enum: ["yes", "no"] },
          accept_relationship: { type: "boolean" },
          user_adult: {
            type: "boolean",
            description: "仅当 USER_ADULT=UNKNOWN 且用户在当前对话中明确确认自己已成年(18+)时填 true，否则省略。",
          },
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
          turn_id: { type: "string", maxLength: 120 },
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
        "委托解题子代理，统一负责各学科解题（不限于数理化）和知识库处理；分类、整理、补齐、复盘与提案始终交给它。桌面端自动检索本机技巧库并返回探索/保守模式及排除技巧；宿主原生 oit-subject 应先看图提取特征并调用 oit_kb_search。遵守其模式和排除设置，实际引用卡片通过 kb_citations 传给 oit_reply。解答图片模式开启时，风格化后必须调用 oit_answer_image，再用自然简洁且不含 Markdown / LaTeX 源码的可见正文提交 oit_reply。",
      inputSchema: {
        type: "object",
        properties: {
          domain: { type: "string", maxLength: 60, description: "学科领域标识，如 math、physics、chemistry、biology、cs、english、history、general 等。" },
          session: { type: "string", maxLength: 80 },
          question: { type: "string", maxLength: 8000 },
          turn_id: { type: "string", maxLength: 120 },
          conversation_id: { type: "string", maxLength: 120 },
        },
        required: ["domain", "session", "question"],
        additionalProperties: false,
      },
    },
    {
      name: "oit_answer_image",
      description:
        "在 oit_subject 或原生 oit-subject 完成且聊天 AI 已按人设风格化改写后生成解答图片（原生子代理路由可直接调用，无需先调 oit_subject；styled_answer 可用 Markdown/LaTeX 排版）。成功后必须随即调用 oit_reply（气泡正文不含 Markdown/LaTeX 源码），图片会作为独立气泡发送，可在 bubbles 中用 \"<oit_answer_image>\" 指定发图位置，前后均可加一句话；关闭解答图片模式时无需调用。",
      inputSchema: {
        type: "object",
        properties: {
          domain: { type: "string", maxLength: 60, description: "学科领域标识，与本次解题对应。" },
          session: { type: "string", maxLength: 80 },
          answer: { type: "string", maxLength: 8000, description: "学科子代理返回的原始答案。" },
          styled_answer: { type: "string", maxLength: 8000, description: "聊天 AI 按人设风格化改写后的完整解答版本。" },
          kb_citations: { type: "array", items: { type: "string" }, maxItems: 20 },
          turn_id: { type: "string", maxLength: 120 },
          conversation_id: { type: "string", maxLength: 120 },
        },
        required: ["domain", "session", "answer", "styled_answer"],
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
      name: "oit_reading_memory",
      description:
        "读取或更新共读书库中的章节讨论记忆。共读中按章节实时保存讨论摘要和批注；非共读聊天提到书籍时先按书名检索，再依据新观点修订。只记录实际讨论内容。",
      inputSchema: {
        type: "object",
        properties: {
          action: { type: "string", enum: ["lookup", "save"] },
          book_id: { type: "integer", minimum: 1 },
          book_title: { type: "string", maxLength: 200 },
          chapter: { type: "string", maxLength: 120 },
          chapter_title: { type: "string", maxLength: 200 },
          summary: { type: "string", maxLength: 6000 },
          annotations: { type: "string", maxLength: 6000 },
          mode: { type: "string", enum: ["replace", "append"] },
        },
        required: ["action"],
        additionalProperties: false,
      },
    },
    {
      name: "oit_reading_acquire",
      description:
        "仅在共读模式且网络已开通时，从 Project Gutenberg 或备用 Standard Ebooks（https://standardebooks.org/ebooks）获取公版书并导入本机书架；古登堡不可用时改搜 Standard Ebooks。",
      inputSchema: {
        type: "object",
        properties: { source_url: { type: "string", maxLength: 200, description: "Project Gutenberg 或 Standard Ebooks 官方书目 URL。" } },
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
          turn_id: args.turn_id as string | undefined,
          conversation_id: args.conversation_id as string | undefined,
          host: args.host as string | undefined,
          consume_trigger: args.consume_trigger === false ? "0" : "1",
          bootstrap: "1",
        });
        const { system_message: _sm, ...meta } = res as Record<string, unknown>;
        return {
          content: [{ type: "text", text: String(res.system_message || JSON.stringify(res)) }],
          structuredContent: meta,
        };
      }

      if (name === "oit_reply") {
        const res = await bridgePost("/reply/submit", args);
        const items = Array.isArray(res.items) ? res.items : [];
        const summary = items.length
          ? (res.held
              ? `已提交 ${items.length} 条气泡到 OIT（延后送达），请回复 ✓ 结束本轮。`
              : `已提交 ${items.length} 条气泡到 OIT，请回复 ✓ 结束本轮。`)
          : res.silent
            ? "静默。"
            : res.refuse
              ? "暂不回复。"
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
      if (name === "oit_bind_persona" && res.ok !== false) {
        const pname = String(res.persona_name || "");
        const hint = `已完成与人设「${pname}」的 1 对 1 绑定。若本轮仅为绑定/换会话且无待回复的用户聊天消息或主动触发，请勿调用 oit_reply，直接输出 ✓ 结束本轮即可。`;
        return {
          content: [{ type: "text", text: hint }],
          structuredContent: res,
          isError: false,
        };
      }
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
        res.recent_history ? `\n## Recent History\n${res.recent_history}` : "",
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
      res.recent_history ? `\n近期对话上下文:\n${res.recent_history}` : "",
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
