# OIT 知识库模块与侧边栏拖拽排序开发文档（桌面端）

> **文档状态**：定稿（供 AI / 开发者直接按文档实现）
> **适用范围**：OIT 桌面端（Windows / Python 3.10+ / tkinter，仅标准库，不引入第三方重型依赖）
> **展示终端**：Obsidian（底层为标准本地 Markdown + YAML Frontmatter + `[[双链]]` 目录）

---

## 1. 需求总览

本次开发包含两个相互独立、可分阶段交付的特性：

1. **侧边栏模块入口拖拽排序 + “黑线”淡化机制**
   - 新增「知识库（`knowledge`）」模块独立侧边栏入口，与 `timer`、`net` 等模块同级。
   - 所有侧边栏模块入口支持鼠标拖拽调整上下顺序。
   - 侧边栏新增一条可相对移动的“黑线”分割线：被拖到**黑线下方**的入口视觉颜色变淡，用于弱化不常用入口；顺序与黑线位置本地持久化。

2. **分学科知识库模块（桌面端先行，Obsidian 为展示终端）**
   - 知识库严格划分为两大类：
     - **概念性（Conceptual）**：仅给人看；**AI 解题绝不读取、不检索**；无频次/权重排序；支持 **AI 补齐**（由标题/笔记碎片/图片补全概念内容）。
     - **技巧性（Tactical / 二级结论与技巧）**：分学科（数学、物理、化学，可扩展）管理二级结论；包含触发特征、结论本体、适用前提与禁区、大题补证、父子衍生关系（DAG）。
   - **排序与检索**：按题目触发特征召回，结合「特化层级（衍生子结论优先级可高于父结论）+ 频次对数 + 最近使用衰减 + 权重 + 置顶」重排。
   - **拟 Git 协作模型**：用户为仓库管理员（Maintainer），OIT 默认为普通贡献者（Contributor）。
     - 正文层（新增/修改/衍生/合并）：OIT 只能提交结构化 PR 提案，经用户审核后合入主库。
     - 统计与权重层（命中频次、最近使用、权重调整、原题命中记录）：独立存储，**默认自动通过（Auto-merge）**。
   - **多渠道录入与解题闭环**：支持从其他软件笔记迁移、手动键入/图片录入，以及接入 OIT 数理化解题链路（解题前检索注入，解题后自动更新权重并静默提交新增/修正 PR）。
   - **结论溯源（Citation Traceability）**：AI 在解题中引用二级结论时，必须接入结构化溯源接口（绑定 `card_id`、父结论推导链与用户熟练度），防止用户因导入了尚未用过的二级结论而“看解答一头雾水”；前端提供可点击的溯源胶囊/浮层，支持一键查看推导链或跳转 OIT 知识库 / Obsidian 原卡片。

---

## 2. 需求一：侧边栏拖拽排序与“黑线”淡化

### 2.1 涉及文件
- `app/app_window.py`：侧边栏构建（`self.nav_labels` / `self.nav_btns`）、页面注册（`self.pages["knowledge"]`）、切页着色（`_show_page`）。
- `app/app_pages.py`：合约页侧边栏配色同步（`_paint_sidebar`）、未读标记刷新。
- `app/app_base.py`：可复用已有颜色混合函数（用于计算淡化前景色）。
- 偏好持久化：存入 `os.path.join(config.app_data_dir(), "ui_prefs.json")`（或 `app_settings.json` 中即时生效的 UI 字段；**不走 48 小时冷静期**）。

### 2.2 入口清单与“黑线”数据模型
- 新增模块键名：`knowledge`，默认显示文案：`tr("📚  知识库")`。
- 使用**单一有序列表 + 哨兵元素 `"__divider__"`** 表示顺序与黑线位置，避免索引越界：
  ```json
  {
    "sidebar_order": [
      "prescript",
      "timer",
      "net",
      "log",
      "cal",
      "stats",
      "contract",
      "knowledge",
      "settings",
      "__divider__",
      "comm",
      "reset"
    ]
  }
  ```
- **归一化规则（Normalize）**：
  1. 若配置缺失或损坏，使用默认顺序（`"__divider__"` 默认位于 `settings` 之后或所有入口末尾，保证默认状态下常用入口在黑线上方）。
  2. 渲染时过滤掉当前未启用的模块（例如未开启都市之意主题时跳过 `prescript`），但保存顺序时尽量保留未知/暂未启用键，避免切换主题后顺序丢失。
  3. 未来新增的模块键若不在已保存列表中，自动插入 `"__divider__"` 之前。
  4. 保证列表中**有且仅有一个** `"__divider__"`。

### 2.3 交互与拖拽行为
- **拖拽对象**：侧边栏中所有模块按钮（`nav_btns`）以及“黑线”本身均可拖动（也可以拖动按钮越过黑线，或直接拖动黑线上下移动）。
- **防误触点击**：
  - 绑定 `<ButtonPress-1>`、`<B1-Motion>`、`<ButtonRelease-1>`。
  - 仅当纵向移动距离 `abs(y - start_y) >= 6`（px）时进入拖拽态；未超过阈值松手则按正常点击触发 `self.show_page(key)`。
  - 注意：`tk.Button` 原生 `command` 在松手时可能与拖拽冲突，建议取消 `command` 改由 `<ButtonRelease-1>` 在非拖拽态下统一触发 `self.show_page(key)`（同时保留键盘回车/空格可达性）。
- **拖拽视觉反馈**：
  - 进入拖拽态后光标改为 `fleur`（或 `sb_v_double_arrow`），被拖拽项高亮边框或背景微变。
  - 根据鼠标相对 `sidebar` 的 `y` 坐标实时计算目标槽位，在目标插入位置显示一条明显的插入指示线（2px 主题强调色横线）。
  - 松手后重排 `sidebar_order`，重新 `pack` 侧边栏子控件，立即持久化，并调用配色刷新。
- **黑线外观**：
  - 一个独立 `tk.Frame` 容器（高度约 `10~14px`，内置一条 `2px` 深色横线，默认 `bg=COLORS.get("text_dim")` 或深灰/黑色 `#333333` / 暗色主题下适当提高对比度），`cursor="sb_v_double_arrow"`，方便直接拖动黑线本身。
  - 移除原先写死在 `key == "comm"` 前的弹性空白 spacer（`tk.Frame(side).pack(fill="both", expand=True)`），改为所有入口与黑线紧凑排列，底部再放一个可选弹性留白。

### 2.4 黑线下方淡化规则
- 定义判定函数 `_is_nav_dimmed(key: str) -> bool`：当 `key` 在 `sidebar_order` 中的位置严格位于 `"__divider__"` 之后时返回 `True`。
- **普通主题着色（`_show_page`）**：
  - 未选中且在黑线上方：`fg = COLORS["text"]`。
  - 未选中且在黑线下方：`fg = blend(COLORS["text"], COLORS["bg_card"], 0.45)`（若存在 `COLORS["text_dim"]` 也可进一步与 `bg_card` 混合，确保肉眼明显更淡但仍可读）。
  - 当前选中项：即使在黑线下方，也保持清晰可辨（可使用 `accent_fill` 正常高亮，或将高亮背景/文字适度柔化，推荐保持高亮可读以防迷失当前页）。
- **合约页主题着色（`app/app_pages.py` 的 `_paint_sidebar`）**：
  - 当合约页自定义侧边栏配色生效（`on == True`）时，对黑线下方未选中项同样执行 `fg = blend(P["side_text"], P["side"], 0.45)`，黑线颜色适配 `P["side_rim"]`。

---

## 3. 需求二：知识库模块架构（桌面端 + Obsidian）

### 3.1 总体分层与目录结构（Obsidian Vault）
默认根目录：`os.path.join(config.app_data_dir(), "knowledge_vault")`，允许用户在设置或知识库顶部栏切换为任意本地目录（例如用户的现有 Obsidian Vault 或其中的子目录）。

```text
<vault_root>/
├── .oit_kb/                    # OIT 内部管理元数据（点目录，Obsidian 默认隐藏不干扰阅读）
│   ├── config.json             # 知识库配置（预览模式、自动放权项、自定义学科、外部导入记录）
│   ├── index.json              # 技巧性卡片检索索引（仅索引 tactical，绝不包含 conceptual）
│   ├── stats.json              # 统计与权重层（freq / weight / last_used / pinned / hits），免审自动写入
│   ├── proposals/              # 拟 Git PR 提案队列（每个 Open PR 一个 <pr_id>.json）
│   └── audit.jsonl             # 操作审计日志（merge / reject / auto_weight / import）
├── 概念性/                     # Conceptual：仅给人看，AI 解题绝不读取、不排序
│   ├── 数学/
│   ├── 物理/
│   └── 化学/
└── 技巧性/                     # Tactical：二级结论与技巧，AI 解题检索与拟 Git PR 目标
    ├── 数学/
    ├── 物理/
    └── 化学/
```

> **单一数据源原则（Single Source of Truth）**：
> Markdown 文件是正文内容的唯一权威来源。用户在 Obsidian 中手动修改或新增 `.md` 文件后，OIT 通过文件 `mtime` 扫描（点击刷新 / 切入知识库页 / 解题检索前增量检查）自动重建 `index.json`，无需维护双向同步。

### 3.2 学科定义
默认内置三个学科（可扩展，配置存于 `.oit_kb/config.json`）：
- `math` → 目录名 `数学`
- `physics` → 目录名 `物理`
- `chemistry` → 目录名 `化学`

### 3.3 Markdown 卡片规范（兼容 Obsidian Properties 与双链）

#### 3.3.1 概念性卡片（`概念性/<学科>/<标题>.md`）
```markdown
---
id: con_math_0001
kind: conceptual
subject: math
topic: 解析几何
title: 椭圆的定义与标准方程
aliases:
  - 椭圆标准方程
ai_completed: true
created_at: "2026-11-24T21:50:00"
updated_at: "2026-11-24T21:50:00"
---

## 核心定义
...

## 基本公式与性质
...

## 直观理解与易混辨析
...

## 基础例题
...
```
- **严格隔离要求**：
  - 索引构建器**跳过** `概念性/` 目录以及任何 `kind: conceptual` 的文件。
  - 检索接口、解题注入、`stats.json` 权重系统**均不触碰**概念性卡片。
  - **AI 补齐**：仅由用户显式触发（新建概念卡片或打开已有概念卡片点击「AI 补齐」）。AI 根据用户输入的标题/碎片笔记/上传图片，补全定义、公式（LaTeX）、辨析与例题，经用户预览确认后直接写入 `概念性/<学科>/`。

#### 3.3.2 技巧性卡片（`技巧性/<学科>/<标题>.md`）
```markdown
---
id: tac_math_0012
kind: tactical
subject: math
topic: 圆锥曲线
title: 椭圆焦点弦定比结论
aliases:
  - 焦点弦定比分点公式
  - 焦半径倒数和延伸
triggers:
  - 过焦点的直线交椭圆于两点
  - 焦点分弦成定比 lambda
  - 离心率与倾斜角关系
parents:
  - "[[椭圆焦半径公式]]"
  - "[[圆锥曲线第二定义]]"
parent_ids:
  - tac_math_0004
  - tac_math_0001
status: verified
source: "外部笔记迁移 / 2025模拟卷压轴"
created_at: "2026-11-24T21:50:00"
updated_at: "2026-11-24T21:50:00"
---

## 触发特征
- 直线过椭圆（或双曲线/抛物线）的焦点 $F$，与曲线交于 $A, B$ 两点
- 题目给出向量关系 $\overrightarrow{AF} = \lambda \overrightarrow{FB}$ 或求离心率 $e$ 与倾角 $\theta$ 关系

## 结论本体
设焦点弦 $AB$ 倾斜角为 $\theta$，$\overrightarrow{AF} = \lambda \overrightarrow{FB}$（$\lambda > 0$），则：
$$e |\cos\theta| = \left|\frac{\lambda - 1}{\lambda + 1}\right|$$

## 适用前提与禁区
- **前提**：直线必须过焦点；$A, B$ 须在双曲线同一支（若跨两支需改用 $\frac{\lambda+1}{\lambda-1}$）。
- **禁区**：不过焦点的普通弦不可直接套用；注意 $\theta$ 是直线与焦轴的夹角而非与 $y$ 轴夹角。

## 大题补证提示
选填题直接秒杀；解答题可用「联立方程 + 韦达定理」或「第二定义作垂线投影」2~3 行补证后使用。

## 衍生与关联
- 父结论：[[椭圆焦半径公式]]、[[圆锥曲线第二定义]]
- 衍生结论：[[焦点弦长与面积速算]]

## 典型例题
- 2025-11-20：已知过右焦点直线倾斜角 $60^\circ$ 且 $|AF|=3|FB|$，秒得 $e = 1$（检验合理性）...
```

> **Obsidian 友好细节**：
> - `parents` 使用 `[[标题]]` 让 Obsidian 的 Graph View 与反向链接直接成图；`parent_ids` 供 OIT 内部稳定计算 DAG 深度，即使改名也不易断链。
> - 数学公式统一使用 `$...$` 和 `$$...$$`（Obsidian 原生 MathJax 语法）。
> - 文件名做安全化处理（去除 `\ / : * ? " < > |`），同一文件保存时保留用户在未识别自定义章节中手写的内容，避免覆盖用户手记。

---

## 4. 统计与权重层 + 排序与检索算法

### 4.1 统计与权重层（`.oit_kb/stats.json`，自动通过）
与 `.md` 正文完全分离，结构示例：
```json
{
  "tac_math_0012": {
    "freq": 7,
    "weight": 1.15,
    "user_pinned": false,
    "last_used": "2026-11-24T21:30:00",
    "success_hits": 6,
    "fail_hits": 1,
    "recent_problems": [
      {"ts": "2026-11-24T21:30:00", "summary": "过右焦点弦定比求离心率", "outcome": "used"}
    ]
  }
}
```
- **自动更新规则（免审直通）**：
  - 每次解题检索并被模型实际采纳的技巧卡片：`freq += 1`，`last_used = now`，`weight = min(3.0, round(weight + 0.05, 4))`，追加 `recent_problems`（保留最近 20 条）。
  - 若模型判定某条被召回的卡片因前提不符被跳过，不加频次，不降权。
  - 若用户反馈或验算发现套错该结论导致出错：`fail_hits += 1`，`weight = max(0.3, round(weight - 0.1, 4))`，并可触发一条 `UPDATE_CARD` PR 补充「禁区」。

### 4.2 检索与重排算法（仅针对 `tactical`）
遵循「**特征匹配优先，特化层级覆盖泛化，频次与权重加权**」原则，彻底避免高频基础结论霸榜：

1. **前置过滤**：
   - 仅检索 `kind == "tactical"` 且 `subject == domain` 的卡片。
   - 若 `preview_unverified_prs == False`（严格模式），仅检索主库 `status == "verified"`；若开启预览模式，允许并入 Open PR 中的 `ADD_CARD` / `ADD_DERIVED` 草稿卡片，但标记 `unverified = True`。

2. **第一阶段：触发特征粗召回（纯标准库离线实现）**
   - 对输入题干（含图片转述/问题文本）做分词与字符 2~3-gram 切分（兼容中文与 LaTeX 符号/希腊字母/变量关键词）。
   - 计算每张卡片的 `match_score`（归一化至 `[0, 1]`）：
     - `triggers` 命中权重：`0.55`（最高，核心召回依据）
     - `title` & `aliases` 命中权重：`0.25`
     - `topic` & 正文关键词命中权重：`0.20`
   - **阈值门控**：`match_score < MIN_MATCH_THRESHOLD`（默认 `0.15`）直接淘汰，不进入重排（保证“不相关但高频”的结论绝不会混进来）。

3. **第二阶段：特化层级（DAG）与频次重排**
   - **DAG 深度与父子覆盖**：
     - 预计算每张卡片在 `parent_ids` DAG 中的 `depth`（根结论 `depth = 0`，一级衍生 `depth = 1`，二级衍生 `depth = 2`，检测环并安全截断）。
     - 若某卡片 `C` 的任意祖先 `P` 也进入了候选集，且 `C.match_score >= 0.75 * P.match_score`，说明题目已匹配到更具体的衍生结论，给 `C` 额外特化加成 `specificity_bonus = 0.25 * min(C.depth, 3)`，使衍生子结论排序高于其父结论。
   - **时间衰减、频次与置顶**：
     - `freq_factor = log1p(freq) / log1p(20)`（对数压缩，防止老结论无限膨胀）
     - `recency_decay = exp(-days_since_last_used / 45.0)`（45 天半衰期）
     - `weight_factor = clamp(weight, 0.3, 3.0)`
     - `pin_boost = 0.25 if user_pinned else 0.0`（作为 `match_score` 的乘法增益而非大额常数加法，避免刚过阈值的弱相关置顶卡挤掉高相关精准卡片）
   - **最终得分**：
     ```text
     final_score = match_score * (1.0 + 0.30 * freq_factor + 0.15 * recency_decay + pin_boost) * weight_factor
                   + specificity_bonus
     ```
   - 取 Top-K（默认 `K = 4`，若含 `unverified` 草稿最多占 1 个名额且排在已验证同分项之后）。

---

## 5. 拟 Git 协作模型（Maintainer vs Contributor）

### 5.1 角色与权限矩阵

| 操作类型 | 用户（Maintainer） | OIT（Contributor） |
| --- | --- | --- |
| 修改/新建/删除技巧卡片正文 | 直接写入主库 | **禁止直写**，必须提 PR |
| 修改父子关系 / 合并重复卡片 | 直接写入主库 | **禁止直写**，必须提 PR |
| 更新频次 / 权重 / 最近使用 / 命中记录 | 直接修改（支持手动调权/置顶） | **默认自动通过**（写 `stats.json`） |
| 概念性卡片的新建/编辑/AI 补齐 | 直接写入（AI 补齐需用户确认后写入） | 仅在用户点击补齐时生成草稿，不主动提 PR |
| 审核 PR（Merge / Edit & Merge / Reject） | 专属权限 | 无权限 |

### 5.2 PR 提案结构（`.oit_kb/proposals/<pr_id>.json`）
```json
{
  "pr_id": "pr_20261124_215501_a1b2",
  "status": "open",
  "action": "ADD_DERIVED",
  "subject": "math",
  "target_card_id": null,
  "parent_ids": ["tac_math_0004"],
  "title": "椭圆焦点弦定比结论",
  "summary": "从焦半径公式衍生出定比与离心率速算关系，可将 8 步联立压缩为 1 步",
  "proposed_card": {
    "topic": "圆锥曲线",
    "aliases": ["焦点弦定比分点公式"],
    "triggers": ["过焦点的直线交椭圆于两点", "焦点分弦成定比"],
    "statement": "...",
    "preconditions": "...",
    "proof_sketch": "...",
    "examples": ["..."]
  },
  "evidence": [
    {
      "ts": "2026-11-24T21:55:01",
      "problem": "已知过椭圆右焦点...",
      "reason": "解题中直接由焦半径推导出该特例，具有通用复用价值"
    }
  ],
  "created_at": "2026-11-24T21:55:01",
  "updated_at": "2026-11-24T21:55:01",
  "squash_count": 1
}
```
支持 5 种 `action`：
- `ADD_CARD`：新增顶级技巧卡片。
- `ADD_DERIVED`：在已有 `parent_ids` 下新增衍生子结论。
- `UPDATE_CARD`：修改已有 `target_card_id` 的字段（如补充 `preconditions` 禁区、完善 `triggers`、修正公式）。
- `MERGE_ALIAS`：将识别到的同义表述合并到已有卡片（追加 `aliases` / `triggers` / `examples`）。
- `REPARENT`：调整已有卡片的 `parent_ids` 衍生层级。

### 5.3 防污染与防刷屏机制
1. **查重优先于新建**：OIT 拟提交 `ADD_CARD` / `ADD_DERIVED` 前，必须先用标题 + 触发特征检索主库 Top-3；相似度过高时改提 `UPDATE_CARD` 或 `MERGE_ALIAS`，避免同义卡片膨胀。
2. **自动 Squash（合并同目标 Open PR）**：若 `proposals/` 中已存在同一 `target_card_id`（或同标题同学科）的 `open` PR，不新建文件，而是合并字段增量、追加 `evidence`、`squash_count += 1`。
3. **静默提交**：解题后在后台线程分析并写入 PR，**绝不弹窗打断专注**，仅在侧边栏或知识库「待审提案」Tab 显示数字徽章。
4. **字段级 Diff 与原子合入**：
   - 审核时按字段（`triggers` / `statement` / `preconditions` / `proof_sketch` / `parents`）生成红删绿增 Diff。
   - 点击 `Merge` 或 `Edit & Merge` 时：先写入临时文件再原子替换目标 `.md`（同时更新相关父卡片的 `## 衍生与关联` 双链），重建 `index.json`，将 PR 标为 `merged` 并记入 `audit.jsonl`。
   - **可选本地 Git 联动**：若配置开启 `git_auto_commit` 且检测到 `<vault_root>/.git` 存在且本机有 `git`，Merge 后异步执行一次 `git add <file> && git commit -m "kb: <action> <title>"`；失败仅记录日志，不影响主流程。

---

## 6. 录入渠道与解题闭环集成

### 6.1 外部笔记迁移（Import Wizard）
- 用户选择本地文件夹或单个/多个文件（支持 `.md`、`.txt`，可扩展 `.html`）。
- 流程：
  1. 扫描文件并切分章节（按 `#` / `##` 标题或分隔线拆成候选知识块）。
  2. 调用 AI（复用 `ai_assist` 配置）对每个知识块做分类与结构化抽取：
     - 判定为 `conceptual` → 生成概念性预览项；
     - 判定为 `tactical` → 抽取 `triggers`、`statement`、`preconditions`、`proof_sketch`、可能的 `parent_ids`。
  3. 展示批量预览列表（允许勾选/改分类/编辑），确认后：
     - 概念性直接写入 `概念性/<学科>/`；
     - 技巧性可选择「作为管理员直接入库」或「批量生成 PR 逐条审核」。

### 6.2 手动添加（键入 / 图片）
- 在知识库页面点击「新建卡片」，选择 `技巧性` 或 `概念性` 及所属学科。
- 支持粘贴/选择本地图片（如手写笔记、教辅截图），调用多模态模型（复用 `ai_assist.ai_http.image_content`）识别并自动填充表单字段（含 LaTeX 公式）。
- 概念性卡片提供「**AI 补齐**」按钮：只需填标题/几句碎片/传图，AI 自动补全结构化概念正文，用户预览确认后保存。

### 6.3 接入 OIT 数理化解题链路
涉及现有文件：`ai_assist/ai_subagent.py`、`comm/comm_tools.py`、`mcp_bridge.py`、`antigravity_plugin/`（MCP Server 与 `oit-subject` 子代理定义）。

1. **升级 `ai_assist/ai_subagent.py` 的 `SYSTEMS["subject"]`（及插件内 `oit-subject` 提示词）**：
   - 增加「两阶段解题策略」与「结论溯源约束」要求：
     - 先阅读注入的「候选二级结论与技巧（来自用户个人技巧库）」，每条卡片附带 `card_id`、父结论链摘要与熟练度标记（如 `freq=0 [用户未用过]`）；
     - **显式核对适用前提与禁区**，若满足且比通法更短则优先采用衍生/二级结论；
     - **强制溯源绑定**：凡在解答中引用了技巧库结论，必须通过结构化引用标记绑定对应 `card_id`（供下游解析为可点击的溯源接口，正文中不得裸露原始 ID）；若引用了**库外**二级结论（用户库中不存在），必须当场给出 1~3 行简短推导或明确标注为库外补充结论，禁止黑盒跳步；
     - **生疏结论自适应衔接**：若引用的卡片标注 `familiarity=unfamiliar`（即 `freq == 0`，用户从未亲自用过，常见于批量导入的笔记）或 `[UNVERIFIED]`，AI 在直接套用该结论时，必须额外补充 1~2 句衔接说明（点明它由哪个父结论/基础公式推出，以及本题为何满足其前提），避免用户一头雾水；
     - 若某条结论标注 `[UNVERIFIED]`，使用前必须用特值/极限/量纲验算；
     - 选填题追求最短路径；解答题用二级结论定方向/验算，并参考「大题补证提示」给出规范得分步骤。
     - 严禁编造或强行套用前提不符的结论。

2. **解题前注入与纯图片题召回（`comm/comm_tools.py`、`mcp_bridge.py`、`antigravity_plugin/`）**：
   - **文字题（`external_api` 模式）**：在 `oit_subject` 执行前，调用 `kb.search_tactical(domain=domain, query=question, top_k=4)`；若命中技巧卡片，将格式化后的 `[个人技巧库参考]` 块（含 `card_id`、结论本体、前提禁区、父链摘要及 `familiarity` 标记）拼入发给 `subject` 子代理的 `task` 前部（不修改用户可见原题）。
   - **纯图片/短文字题（`external_api` 模式）**：当本轮附带图片且文字题干过短（如仅有“看图做第 12 题”，缺乏有效触发词）时，先执行一次极轻量的多模态特征词抽取（仅输出题型、关键对象与结构特征短语）再调用 `search_tactical`；若抽取失败或超时则直接回退正常解题，不阻塞主链路。
   - **宿主原生子代理（`host_subagent` 模式）**：在 `mcp_bridge.py` 与 `antigravity_plugin/` 同步暴露 4 个轻量 MCP 工具（**严格仅暴露技巧库，绝不暴露概念库**）：
     - `oit_kb_search(domain, query, top_k=4)`：供 `oit-subject` 在看图（`read_file`）并提炼出题干关键特征后主动检索技巧库（返回结果自带 `card_id`、`familiarity` 与轻量父链摘要）；
     - `oit_kb_trace(card_id)`：按 `card_id` 查询完整溯源包（祖先推导链、补证提示、前提禁区与 Obsidian URI），供子代理在需要向用户解释生疏结论时即时拉取；
     - `oit_kb_record_use(used_card_ids, failed_card_ids, problem_summary)`：解题后自动更新 `stats.json`（免审直通）；
     - `oit_kb_propose(...)`：提交结构化技巧提案至 `proposals/`（经查重与 Squash，等待用户审核）。

3. **解题后闭环与节流门控（后台异步，不阻塞回复）**：
   - **轻量节流门控**：仅当满足以下任一条件时，才在后台线程触发复盘 LLM 调用，避免对简单问答/一步算术消耗额外 Token 或产生无效 PR 噪音：
     - 本轮检索并注入了至少 1 张技巧卡片（需要确认是否采纳并更新权重）；或
     - 题目/解答达到一定复杂度（如包含多步公式推导、几何/物理模型转化，或解答正文字数超过阈值）。
   - 满足门控后，在后台线程执行一次轻量复盘调用（复用 `subject` 或独立轻量配置，低 temperature）：
     - 输出严格 JSON：
       ```json
       {
         "used_card_ids": ["tac_math_0012"],
         "failed_card_ids": [],
         "proposals": [
           {"action": "ADD_DERIVED", "parent_ids": ["tac_math_0012"], "...": "..."}
         ]
       }
       ```
     - 对 `used_card_ids` / `failed_card_ids`：**自动更新 `stats.json`**（频次 + 权重，默认通过）。
     - 对 `proposals`：经查重与 Squash 后写入 `.oit_kb/proposals/` 等待用户审核（无价值或纯基础常识则输出空数组 `[]`，不强行造 PR）。

### 6.4 结论溯源接口与交互（Citation Traceability）
解决用户“把别人整理的笔记导入后，AI 突然套用一个自己从没用过的二级结论导致看不懂”的核心体验问题：

1. **内部引用协议与解析（`knowledge/kb_trace.py`）**：
   - 解题子代理在输出末尾（或结构化返回字段中）以统一约定声明本轮实际引用的技巧卡 ID 列表（如 `<!-- kb_cite: tac_math_0012, tac_math_0004 -->` 或工具参数 `kb_citations`）。
   - 下游消息组装层（`comm/comm_tools.py`、`mcp_bridge.py`）在展示前剥离原始引用标记，调用 `kb.resolve_citations(card_ids)` 将其转换为结构化溯源元数据挂载到本轮消息（同时校验 `card_id` 合法性：仅允许 `tactical` 卡片，已删除/无效 ID 自动降级忽略，绝不报错或泄露概念库）。

2. **标准溯源数据结构（`kb.resolve_trace(card_id)`）**：
   ```json
   {
     "card_id": "tac_math_0012",
     "title": "椭圆焦点弦定比结论",
     "subject": "math",
     "familiarity": "unfamiliar",
     "freq": 0,
     "unverified": false,
     "statement": "e |cos θ| = |(λ - 1)/(λ + 1)|",
     "preconditions": "直线必须过焦点；双曲线须在同一支...",
     "proof_sketch": "由第二定义作垂线投影 2~3 行可证...",
     "lineage": [
       {"card_id": "tac_math_0004", "title": "椭圆焦半径公式", "depth": 1},
       {"card_id": "tac_math_0001", "title": "圆锥曲线第二定义", "depth": 0}
     ],
     "file_path": "<vault_root>/技巧性/数学/椭圆焦点弦定比结论.md",
     "obsidian_uri": "obsidian://open?path=..."
   }
   ```
   - `familiarity` 判定规则：`freq == 0` → `"unfamiliar"`（从未用过）；`1 <= freq <= 2` → `"learning"`（刚接触）；`freq >= 3` → `"familiar"`（已熟悉）。
   - `lineage` 沿 `parent_ids` 向上回溯至根结论（内置 `visited` 集合防环，按衍生层级从近到远排序）。

3. **展示端溯源交互接口**：
   - **沟通页 / OIT Chat 侧栏**：带有 `kb_citations` 的回复下方渲染紧凑的**「结论溯源」胶囊条**（例如 `📎 引用结论：椭圆焦点弦定比结论 (未用过) ↗`）。
     - 点击胶囊弹出**「结论溯源浮层」**：直接展示结论本体、适用前提与禁区、大题补证/快速推导、以及**可逐级点击的父结论溯源链**（一直追回用户熟悉的基础定理）；底部提供 `在 OIT 知识库打开` 与 `在 Obsidian 中打开` 两个快捷按钮。
   - **解答图片模式（`oit_answer_image`）**：若本轮生成了解答图片且存在引用，可在图片底部附带一行精简脚注（如 `引用技巧：椭圆焦点弦定比结论 ← 椭圆焦半径公式 ← 圆锥曲线第二定义`），同时在聊天区保留可点击的溯源胶囊。

---

## 7. 桌面端 UI 设计（`pages/knowledge_page.py`）

遵循 OIT 既有 tkinter 界面规范（`COLORS`、`config.font`、`pages/widgets.py`、`ScrollArea`、`i18n.tr`、兼容窄窗口与主题切换）：

- **顶部工具栏**：
  - 标题：`📚  知识库`
  - 学科筛选下拉/胶囊：`全部` / `数学` / `物理` / `化学`
  - 快捷按钮：`在 Obsidian 中打开`（尝试 `obsidian://open?path=...` URI，失败则回退 `os.startfile(vault_root)` 打开目录）、`新建卡片`、`导入笔记`、`刷新索引`、`设置`（配置 Vault 路径、预览未审 PR 开关、自动 Git Commit 开关）。
- **三个内部 Tab**：
  1. **技巧库（Tactical）**：
     - 搜索框（按标题/触发词/标签过滤）+ 排序方式切换（综合权重 / 频次 / 最近使用 / 层级视图）。
     - 列表展示：卡片标题、学科标签、父子层级缩进或父标签、频次 `×N`、权重 `w=1.15`、置顶星标按钮。
     - 右侧/弹窗详情预览：展示触发特征、结论本体、**醒目高亮的适用前提与禁区**、补证提示、**可点击的上下游溯源链（向上追回根结论 / 向下看衍生结论）**、在 Obsidian 中打开该文件按钮、手动编辑/删除/调权按钮。
     - 同时向聊天页提供可复用的「结论溯源浮层」组件（接收 `card_id` 直接弹出溯源卡片，支持点击父链逐级回溯与一键跳入知识库页对应卡片）。
  2. **概念库（Conceptual）**：
     - 顶部轻提示：`概念性笔记仅供阅读与复习，AI 解题不会读取，也无权重排序`。
     - 按学科与目录浏览、关键词筛选。
     - 操作：`新建概念`、`AI 补齐`（支持选中已有概念卡片或从零输入标题/图片补齐）、`在 Obsidian 中打开`。
  3. **待审提案（PR 队列，带未审数量徽章）**：
     - 左侧列表：PR 标题、改动类型徽章（`新增` / `衍生` / `修正` / `合并`）、学科、Squash 次数、时间。
     - 右侧审查区：
       - 字段级 Diff 展示（旧值标红、新值标绿）；
       - 关联触发原题证据折叠/展示区；
       - 底部操作栏：`Merge（合入主库）`、`Edit & Merge（编辑后合入）`、`Reject（拒绝）`。
     - 顶部可切换查看「近期自动权重变更日志（来自 `audit.jsonl` / `stats.json`）」。

---

## 8. 代码模块拆分建议

遵循 OIT 现有的“主模块 + 子模块/mixin、单向依赖、零第三方依赖”风格：

| 新增/修改文件 | 职责 |
| --- | --- |
| `knowledge/__init__.py` | `KnowledgeBase` 门面类：统一对外提供检索、溯源、读写、PR 管理、统计更新、导入与 AI 补齐接口 |
| `knowledge/kb_store.py` | Vault 目录初始化、Markdown + YAML Frontmatter 解析与安全写回（仅标准库，不依赖 PyYAML）、`mtime` 增量索引 |
| `knowledge/kb_rank.py` | 技巧库分词/n-gram 特征匹配、DAG 深度计算、特化优先 + 频次衰减重排算法 |
| `knowledge/kb_trace.py` | 结论溯源引擎：引用标记解析与剥离、DAG 祖先链回溯（防环）、熟练度判定（`familiarity`）、Obsidian URI 生成 |
| `knowledge/kb_pr.py` | 拟 Git PR 提案队列：查重、Squash 合并、字段 Diff 生成、原子 Merge/Reject、可选 `git commit` 联动 |
| `knowledge/kb_stats.py` | `stats.json` 读写与自动权重/频次更新、`audit.jsonl` 追加 |
| `knowledge/kb_ai.py` | 解题后复盘提议、概念性知识 AI 补齐、外部笔记迁移拆分、图片 OCR 结构化抽取（复用 `ai_assist`） |
| `pages/knowledge_page.py` | 知识库桌面端主页面（技巧库 / 概念库 / 待审提案 3 个 Tab + 结论溯源浮层 + 新建/导入/设置弹窗） |
| `app/app_window.py` | 注册 `knowledge` 入口与页面；实现侧边栏拖拽排序、黑线渲染与淡化着色 |
| `app/app_pages.py` | `_paint_sidebar` 适配黑线与淡化前景色 |
| `ai_assist/ai_subagent.py` | 更新 `SYSTEMS["subject"]` 支持二级结论先选路 + 禁区核对 + 强制溯源标注 + 生疏结论衔接推导 |
| `comm/comm_tools.py` | `oit_subject` 解题前检索注入技巧库（含纯图片短文本的特征词抽取）、解析挂载 `kb_citations` 溯源元数据、解题后按门控后台触发权重更新与 PR 提案 |
| `mcp_bridge.py` | 同上；为 `host_subagent` 模式暴露 `oit_kb_search`、`oit_kb_trace`、`oit_kb_record_use`、`oit_kb_propose` 技巧库桥接接口，并在 `oit_reply` / `oit_answer_image` 透传 `kb_citations` |
| `antigravity_plugin/` | 同步注册技巧库 MCP 工具定义，更新 `oit-subject` 子代理的看图检索与溯源提示词，并在 `OIT Chat` 侧栏渲染可点击的结论溯源胶囊 |

> **YAML 解析注意**：OIT 仅使用 Python 标准库，不要 `import yaml`。`kb_store.py` 需实现轻量的 Frontmatter 解析/序列化器（支持字符串、布尔、数字、简单字符串列表即可），覆盖所有约定字段并无损保留未知字段。

---

## 9. 分阶段实施顺序（推荐）

1. **阶段 1：侧边栏拖拽排序与黑线淡化**
   - 改造 `app/app_window.py` 与 `app/app_pages.py`，实现拖拽排序、黑线与淡化样式、偏好持久化。
   - 先挂一个占位 `knowledge` 页面验证切页、合约页配色、窄窗口与重启恢复。
   - 编写并通过 `checks/_sidebar_check.py`。
2. **阶段 2：知识库核心引擎（`knowledge/`）**
   - 实现 `kb_store.py`（Obsidian MD 读写 + 概念/技巧严格隔离）、`kb_stats.py`、`kb_rank.py`（特征匹配 + DAG 特化优先 + 频次衰减）、`kb_trace.py`（结论溯源与祖先链回溯）、`kb_pr.py`（拟 Git 查重/Squash/Diff/Merge/Reject）。
   - 编写并通过 `checks/_knowledge_check.py`（纯离线断言测试）。
3. **阶段 3：桌面端 UI（`pages/knowledge_page.py`）与录入/迁移**
   - 实现技巧库、概念库、待审提案 3 个 Tab 与可复用的结论溯源浮层，接入 Obsidian 打开、新建（键入/图片）、AI 补齐、外部笔记迁移向导。
4. **阶段 4：解题闭环与溯源交互接入**
   - 接入 `ai_assist/ai_subagent.py`、`comm/comm_tools.py`、`mcp_bridge.py` 与 `antigravity_plugin/`（含聊天区溯源胶囊、解答图片脚注），补充端到端 mock 断言到 `checks/_knowledge_check.py`。
   - 更新 `AGENTS.md` 中的检查脚本登记表与实现地图，按项目发布流程递增版本号。

---

## 10. 验收标准与检查脚本清单

新增以下两个独立检查脚本（退出码 0 且打印 `… OK`，窗口开在屏幕外，仅使用临时目录，不碰真实数据与网络），并登记到 `AGENTS.md`：

### 10.1 `checks/_sidebar_check.py`
- 归一化逻辑：缺省生成合法顺序、保留且仅保留 1 个 `"__divider__"`、新增未知模块自动插入黑线上方。
- 拖拽重排持久化：拖动入口跨越黑线后，`sidebar_order` 正确落盘且重启恢复。
- 淡化样式断言：黑线上方入口使用正常前景色，黑线下方入口前景色明显淡化；切换至合约页独立主题时 `_paint_sidebar` 同样保持黑线下方淡化且不报错。
- 点击与拖拽互斥：小于阈值的点击正常切换页面，超过阈值的拖拽不误触切页。

### 10.2 `checks/_knowledge_check.py`
- **概念/技巧隔离**：写入多篇 `概念性/` 与 `技巧性/` 卡片，断言 `index.json` 与 `search_tactical` 绝不返回任何概念性卡片，且概念性卡片不产生 `stats.json` 条目。
- **Obsidian 格式兼容**：保存与重新解析 `.md` 后，YAML Frontmatter、`[[双链]]`、LaTeX 公式及用户自定义章节无损保留。
- **检索与特化优先排序**：
  - 不相关但频次极高的卡片因低于 `match_score` 阈值不被召回；
  - 构造 `父结论(freq=20) → 衍生子结论(freq=2)`，当题目命中子结论特征时，断言子结论得分高于父结论；
  - 置顶弱相关卡片（刚过阈值）不会反超高相关精准卡片；
  - 环状 `parent_ids` 不会导致死循环。
- **拟 Git 权限与 PR 流**：
  - 统计/权重更新自动写入 `stats.json`，不生成 PR；
  - OIT 提交 `ADD_DERIVED` / `UPDATE_CARD` 仅写 `proposals/`，不改动主库 `.md`；
  - 同目标多次提案自动 Squash，`squash_count` 递增且保留全部 `evidence`；
  - `Merge` 后主库 `.md` 原子更新、父卡片关联双链更新、索引刷新、PR 标为 `merged`；`Reject` 后主库无变化。
  - 预览模式开/关时，未合入草稿 PR 的召回与 `[UNVERIFIED]` 标记符合预期。
- **结论溯源（`kb_trace`）**：
  - 引用标记从回复正文中被干净剥离，且解析出完整的 `lineage` 祖先链（按衍生层级排序，环状 `parent_ids` 安全截断）；
  - `freq == 0` 的卡片正确标为 `familiarity == "unfamiliar"`，注入模板中携带生疏提示；
  - 传入不存在的 `card_id` 或概念性卡片 `id` 时安全忽略，不报错且绝不泄露概念库内容。
- **AI 补齐与解题闭环（Mock AI）**：
  - 概念卡片 AI 补齐正确填充正文并写入 `概念性/`；
  - 解题闭环 mock 返回 `used_card_ids` 与 `proposals` 后，权重自动更新且提案入队；简单无注入短问答触发节流门控，不发起冗余复盘调用。
