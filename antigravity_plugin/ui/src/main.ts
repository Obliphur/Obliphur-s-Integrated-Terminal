import {
  App,
  applyDocumentTheme,
  applyHostStyleVariables,
} from "@modelcontextprotocol/ext-apps";

interface ChatMsg {
  id: string;
  role: "me" | "ai";
  text: string;
  at?: string;
  ts?: number;
  has_voice?: boolean;
  image_id?: string;
  answer_image?: boolean;
  delay_ms?: number;
}

interface UiState {
  persona_id?: string;
  persona_name?: string;
  cursor?: number;
  blocked?: boolean;
  messages?: ChatMsg[];
  pending?: ChatMsg[];
  toolbar?: {
    scenario?: boolean;
    muted?: boolean;
    co_alarm?: boolean;
    share?: boolean;
  };
  banners?: Array<{ kind: string; label: string }>;
  status_kind?: string;
}

const app = new App({ name: "OIT Chat", version: "1.0.0" }, {});

const elMessages = document.getElementById("messages") as HTMLDivElement;
const elPersona = document.getElementById("personaName") as HTMLDivElement;
const elStatus = document.getElementById("statusPill") as HTMLDivElement;
const elAvatar = document.getElementById("avatar") as HTMLImageElement;
const elBanners = document.getElementById("banners") as HTMLDivElement;
const elInput = document.getElementById("input") as HTMLTextAreaElement;
const elSend = document.getElementById("btnSend") as HTMLButtonElement;
const elComposer = document.getElementById("composer") as HTMLDivElement;
const elReadonlyHint = document.getElementById("readonlyHint") as HTMLDivElement;

const renderedIds = new Set<string>();
const scheduledTimers = new Map<string, number>();
let activePersonaId = "";
let cursor = 0;
let polling = false;
let canSendMessage = false;
const elMaxHint = document.getElementById("maxHint") as HTMLSpanElement | null;

function updateMaxHint() {
  if (!elMaxHint) return;
  elMaxHint.style.display = window.innerWidth >= 760 ? "none" : "inline";
}
window.addEventListener("resize", updateMaxHint);

function fmtTime(at?: string, ts?: number): string {
  if (at) {
    const match = at.match(/T(\d{2}:\d{2})/);
    if (match) return match[1];
  }
  if (!ts) return "";
  const d = new Date(ts);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function appendBubble(m: ChatMsg) {
  if (!m.id || renderedIds.has(m.id)) return;
  renderedIds.add(m.id);

  const row = document.createElement("div");
  row.className = `row ${m.role}`;
  row.dataset.id = m.id;

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  if (m.image_id) {
    const image = document.createElement("img");
    image.className = "bubble-image";
    image.alt = m.answer_image ? "解答图片" : "聊天图片";
    image.loading = "lazy";
    image.tabIndex = 0;
    image.onclick = () => { if (image.src) window.open(image.src, "_blank", "noopener"); };
    bubble.appendChild(image);
    void callAppTool("oit_ui_media", { kind: "chat_image", id: m.image_id }).then((media) => {
      const uri = String(media?.data_uri || "");
      if (uri) image.src = uri;
    });
  }
  if (m.text && !(m.image_id && m.text === "[图片]")) {
    const text = document.createElement("div");
    text.className = "bubble-text";
    text.textContent = m.text;
    bubble.appendChild(text);
  }
  row.appendChild(bubble);

  const meta = document.createElement("div");
  meta.className = "meta";
  const t = fmtTime(m.at, m.ts || Date.now());
  if (t) {
    const span = document.createElement("span");
    span.textContent = t;
    meta.appendChild(span);
  }
  if (m.role === "ai" && m.has_voice) {
    const play = document.createElement("button");
    play.className = "tbtn";
    play.textContent = "▶";
    play.onclick = () => playVoice(m.id);
    meta.appendChild(play);
  }
  row.appendChild(meta);

  elMessages.appendChild(row);
  elMessages.scrollTop = elMessages.scrollHeight;
}

async function playVoice(msgId: string) {
  try {
    const res = await callAppTool("oit_ui_media", { kind: "voice", id: msgId });
    const uri = String(res?.data_uri || "");
    if (uri) {
      const audio = new Audio(uri);
      await audio.play();
    }
  } catch {
    // Ignore playback errors in restricted contexts.
  }
}

async function callAppTool(name: string, args: Record<string, unknown>): Promise<Record<string, unknown>> {
  const result = await app.callServerTool({ name, arguments: args });
  if (result.structuredContent && typeof result.structuredContent === "object") {
    return result.structuredContent as Record<string, unknown>;
  }
  const text = result.content?.find((c) => c.type === "text");
  if (text && "text" in text) {
    try {
      return JSON.parse(text.text) as Record<string, unknown>;
    } catch {
      return { text: text.text };
    }
  }
  return {};
}

function clearPendingTimers() {
  for (const timer of scheduledTimers.values()) {
    window.clearTimeout(timer);
  }
  scheduledTimers.clear();
}

function schedulePending(items: ChatMsg[]) {
  let cumulative = 0;
  for (const item of items) {
    if (!item.id || renderedIds.has(item.id) || scheduledTimers.has(item.id)) continue;
    cumulative += Math.max(0, Number(item.delay_ms || 0));
    const delay = cumulative;
    const timer = window.setTimeout(async () => {
      scheduledTimers.delete(item.id);
      try {
        const result = await callAppTool("oit_ui_mark_delivered", { msg_id: item.id });
        if (result.ok) {
          appendBubble({ ...item, role: "ai", at: item.at, ts: item.ts || Date.now() });
        }
      } catch {
        // Retry on next poll if needed.
      }
    }, delay);
    scheduledTimers.set(item.id, timer);
  }
}

function renderToolbar(tb?: UiState["toolbar"]) {
  if (!tb) return;
  const s = document.getElementById("btnScenario");
  const m = document.getElementById("btnMute");
  const a = document.getElementById("btnAlarm");
  const sh = document.getElementById("btnShare");
  if (s) s.classList.toggle("active", !!tb.scenario);
  if (m) {
    m.textContent = tb.muted ? "🔕" : "🔔";
    m.classList.toggle("active", !!tb.muted);
  }
  if (a) {
    a.textContent = tb.co_alarm ? "⏰" : "⏲";
    a.classList.toggle("active", !!tb.co_alarm);
  }
  if (sh) {
    sh.textContent = tb.share ? "👀" : "🙈";
    sh.classList.toggle("active", !!tb.share);
  }
}

function renderBanners(banners?: UiState["banners"]) {
  elBanners.innerHTML = "";
  for (const b of banners || []) {
    const div = document.createElement("div");
    div.className = "banner";
    const span = document.createElement("span");
    span.textContent = b.label;
    const actions = document.createElement("div");
    actions.className = "banner-actions";
    const yes = document.createElement("button");
    yes.className = "tbtn active";
    yes.textContent = "接受";
    yes.onclick = () => triggerAction(`respond_${b.kind}`, { accept: true });
    const no = document.createElement("button");
    no.className = "tbtn";
    no.textContent = "拒绝";
    no.onclick = () => triggerAction(`respond_${b.kind}`, { accept: false });
    actions.append(yes, no);
    div.append(span, actions);
    elBanners.appendChild(div);
  }
}

function applyState(state: UiState) {
  const incomingPid = String(state.persona_id || "");
  if (incomingPid && activePersonaId && incomingPid !== activePersonaId) {
    // 切换到了另一位人设的绑定对话：清空旧 DOM 与去重集，重新拉取新人设完整消息与头像
    activePersonaId = incomingPid;
    clearPendingTimers();
    renderedIds.clear();
    elMessages.innerHTML = "";
    cursor = 0;
    void loadAvatar();
    void pollState(true);
    return;
  }
  if (incomingPid && !activePersonaId) {
    activePersonaId = incomingPid;
  }
  if (state.persona_name) {
    elPersona.textContent = state.persona_name.replace(/\.(txt|md|oitp)$/i, "");
  }
  elStatus.textContent = state.status_kind ? `状态: ${state.status_kind}` : "在线";
  if (typeof state.cursor === "number" && state.cursor > cursor) {
    cursor = state.cursor;
  }
  for (const m of state.messages || []) {
    appendBubble(m);
  }
  if (state.pending && state.pending.length) {
    schedulePending(state.pending);
  }
  renderToolbar(state.toolbar);
  renderBanners(state.banners);
}

async function pollState(forceReset = false) {
  if (polling && !forceReset) return;
  polling = true;
  try {
    const reqCursor = forceReset ? 0 : cursor;
    const state = (await callAppTool("oit_ui_state", { cursor: reqCursor })) as unknown as UiState;
    applyState(state);
  } catch {
    elStatus.textContent = "等待 OIT 连接…";
  } finally {
    polling = false;
  }
}

async function loadAvatar() {
  try {
    const res = await callAppTool("oit_ui_media", { kind: "avatar", id: "" });
    const uri = String(res?.data_uri || "");
    if (uri) {
      elAvatar.src = uri;
      elAvatar.style.display = "block";
    }
  } catch {
    // Optional avatar.
  }
}

async function triggerAction(action: string, params?: Record<string, unknown>) {
  try {
    await callAppTool("oit_ui_action", { action, params: params || {} });
    await pollState();
  } catch {
    // Ignore transient errors.
  }
}

function refreshCapabilities() {
  const caps = app.getHostCapabilities();
  canSendMessage = Boolean(caps && (caps as Record<string, unknown>).message);
  elComposer.style.display = canSendMessage ? "flex" : "none";
  elReadonlyHint.style.display = canSendMessage ? "none" : "block";
}

async function sendUserMessage() {
  const text = elInput.value.trim();
  if (!text || !canSendMessage) return;
  elInput.value = "";
  elSend.disabled = true;
  try {
    clearPendingTimers();
    const res = await callAppTool("oit_ui_interrupt", { user_text: text });
    const realId = String(res?.msg_id || `local_${Date.now()}`);
    appendBubble({ id: realId, role: "me", text, ts: Date.now() });
    await app.sendMessage({
      role: "user",
      content: [{ type: "text", text }],
    });
  } finally {
    elSend.disabled = false;
  }
}

// Register handlers BEFORE connect()
app.ontoolinput = () => {
  elStatus.textContent = "对方正在输入…";
};

app.ontoolresult = (result) => {
  refreshCapabilities();
  const sc = result.structuredContent as Record<string, unknown> | undefined;
  if (sc && Array.isArray(sc.items) && !sc.held) {
    schedulePending(
      sc.items.map((x: any) => ({
        id: String(x.id || ""),
        role: "ai",
        text: String(x.text || ""),
        image_id: typeof x.image_id === "string" ? x.image_id : undefined,
        answer_image: Boolean(x.answer_image),
        at: typeof x.at === "string" ? x.at : undefined,
        ts: typeof x.ts === "number" ? x.ts : undefined,
        delay_ms: Number(x.delay_ms || 0),
      }))
    );
  }
  void pollState();
};

app.onhostcontextchanged = (ctx) => {
  if (ctx.theme) applyDocumentTheme(ctx.theme);
  if (ctx.styles?.variables) applyHostStyleVariables(ctx.styles.variables);
  refreshCapabilities();
};

// Bind toolbar buttons
document.querySelectorAll<HTMLButtonElement>("[data-action]").forEach((btn) => {
  btn.onclick = () => triggerAction(btn.dataset.action || "");
});
document.querySelectorAll<HTMLButtonElement>("[data-dialog]").forEach((btn) => {
  btn.onclick = () => triggerAction("open_native_dialog", { kind: btn.dataset.dialog || "" });
});
document.getElementById("btnClear")!.onclick = () => triggerAction("clear_chat");
elSend.onclick = () => void sendUserMessage();
elInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    void sendUserMessage();
  }
});

async function boot() {
  await app.connect();
  const ctx = app.getHostContext();
  if (ctx?.theme) applyDocumentTheme(ctx.theme);
  if (ctx?.styles?.variables) applyHostStyleVariables(ctx.styles.variables);

  refreshCapabilities();
  updateMaxHint();

  await pollState();
  void loadAvatar();
  window.setInterval(() => void pollState(), 2500);
}

void boot();
