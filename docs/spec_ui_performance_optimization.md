# OIT 桌面端 UI 性能优化开发文档（阶段 1 & 阶段 2）

> **目标读者**：负责落地桌面端（Windows / Tkinter）UI 性能优化的开发者。
> **范围**：仅覆盖桌面端 Tkinter UI 层与主线程调度（不涉及 Android 端，也不变更业务逻辑与数据格式）。
> **实施策略**：按 **阶段 1（低风险高收益热修复）** → **阶段 2（中度结构优化）** 顺序推进；每完成一项即可独立验证并跑对应 `checks/_*_check.py`。

---

## 1. 背景与瓶颈总览

当前桌面端 UI 卡顿主要集中在四个方面：

1. **同步 GDI 截屏与 `DwmFlush()` 硬阻塞**：`ScrollArea` 的 `_SmoothScroll` + `_RegionCover` 在每次滚轮/拖动时都会执行 `BitBlt` + `UpdateLayeredWindow` + 2 轮 `settle()` + `GdiFlush()` + `DwmFlush()`；切页 `frozen()` 会执行全窗分层窗口截屏 + 最多 6 轮 `settle()` + `RedrawWindow(..., RDW_ALLCHILDREN | RDW_UPDATENOW)` + `DwmFlush()`。单次滚动耗时 30–60ms，帧率严重受限。
2. **主线程高频无效轮询与磁盘 I/O**：
   - `FocusGlow._place()` 每 90ms 无条件对 4 个 Canvas 调用 `place()` 与 `lift()`；
   - `_net_tick()` 每秒在主线程无条件刷新隐藏页（`rules`、`net`），且每秒调用 `linkd.write_hold()` 触发 `link_state.json` 的磁盘读取与解密/解析；
   - `_pump_sync()` 每 100ms 无条件调用 `comm.ai.refresh_devices()`；
   - `app_timer.update()` 每 200ms 直接 `config()` 文本而未过滤重复值；
   - Win32 / COM 系统调用（虚拟桌面、前台窗口检测）直接跑在主线程。
3. **启动全量建页与过重控件树**：启动及换主题时一次性实例化并预排版 10+ 页面，常驻大量 Windows HWND，拖慢布局传播与事件派发。
4. **长列表重型子窗口与递归事件绑定**：沟通页、统计页、知识库等以大量嵌套 `Frame`/`Label` 挂载在 `Canvas.create_window` 中，且每次增删行都递归执行 `ScrollArea.bind_children()` 绑定 `<MouseWheel>`。

---

## 2. 阶段 1：高收益低风险热修复

本阶段改动集中、向后兼容，建议优先合并发布。

### 2.1 修复 `FocusGlow` 每 90ms 无条件重排与置顶

- **涉及文件**：`pages/widgets.py`（`FocusGlow`，约 L997–L1158）
- **现状问题**：
  `_place()` 中虽然用 `self._geom` 缓存了 `_paint()`，但后面的 `c.place(...)` 与 `c.lift()` 落在 `if self._geom != ...:` 之外，导致控件焦点静止时仍每 90ms 对 4 个子窗口执行 `place()` + `lift()`（约 44 次/秒）。
- **修改方案**：
  1. 将 `geom = (x, y, ww, hh, pad, tuple(colors))` 的比较前置；若 `self._geom == geom` 直接 `return`，不再重复调用 `c.place()` 和 `c.lift()`。
  2. 仅在 `self._geom != geom` 时执行 `_paint()`、`c.place()`、`c.lift()` 并更新 `self._geom`。
  3. （可选增强）给 `widget` 及其 `parent` 绑定 `<Configure>` / `<Unmap>` 事件触发 `_place()`，将兜底轮询 `_tick` 从 `90ms` 放宽至 `250ms–300ms`。
- **注意事项**：切换主题（`colors` / `pad` 变化）或滚动容器移动控件时，`geom` 中的 `(x, y, ww, hh, pad, tuple(colors))` 会变化，仍会准确重绘和重定位。

### 2.2 优化 `_SmoothScroll` 与 `_RegionCover` 滚动路径

- **涉及文件**：`pages/widgets.py`（`_RegionCover`、`_SmoothScroll`、`settle`，约 L506–L867）
- **现状问题**：
  每次滚轮 `_flush()` 都走 `_apply_covered()` → `capture()`（`BitBlt` + `UpdateLayeredWindow`）→ `yview_scroll` → `settle(passes=2)` → `release()`（`GdiFlush` + `DwmFlush`）。`DwmFlush()` 会强制主线程同步等待下一次垂直同步，导致高频滚轮严重掉帧、输入延迟明显。
- **修改方案**：
  1. **移除 `_RegionCover.release()` 中的 `DwmFlush()`**（保留 `GdiFlush()` 即可），避免每次滚动硬卡一个 VSync 周期。
  2. **滚轮事件按帧合并（约 16ms 节流）**：将 `_SmoothScroll._schedule()` 从 `after_idle` 调整为带节流窗口的调度（例如滚轮合并窗口 `16ms`，合并期间的 `_pending_wheel` 累加后一次性 `yview_scroll`），避免一帧内连续触发多次盖板与重绘。
  3. **轻量化普通滚轮**：
     - 普通滚轮（单次步长小、连续滚动中）直接执行 `canvas.yview_scroll(...)`，**不启用 `_RegionCover` 截屏盖板和 `settle(passes=2)`**；
     - 仅在大幅跳转（`moveto()`）或非连续大跨度滚动时按需使用 `_RegionCover`，且将 `settle` 降为 `passes=1`。
     - 可增加配置开关（如 `config.py` / `config_local.py` 中的 `SMOOTH_SCROLL_COVER`）便于测试对比视觉撕裂与帧率。
  4. **精简 `frozen()` 的 `settle` 次数**：切页 `frozen()`（L848、L863）里将 `settle(top)` 从默认 `passes=4` + 结束 `passes=2` 评估降为 `passes=2` + `passes=1`，并测试各页面是否出现换行错位。
- **验收要点**：在沟通页（80 条消息）、设置页、统计页、知识库页快速滚动鼠标滚轮和拖动滚动条，无明显卡顿/粘滞，且不残留悬浮盖板窗口（`_ov`）。

### 2.3 消除主线程每秒读盘：`linkd.write_hold()` 加内存缓存

- **涉及文件**：`linkd.py`（`write_hold`，约 L99–L112）
- **现状问题**：
  `app/app_net.py` 的 `_net_tick()` 每秒调用 `linkd.write_hold(self.dm.data_dir, ...)`；`write_hold()` 每次都会调 `hold_until(dd)` 去 `securefile.open` 读取并反序列化 `link_state.json`。
- **修改方案**：
  1. 在 `linkd.py` 中维护模块级缓存 `_HOLD_CACHE: dict[str, int]`（key 为规范化路径 `os.path.abspath(dd)`，value 为上次成功写入或确认的 `until_ms`）。
  2. `write_hold(dd, until_ms)` 先查缓存：若 `_HOLD_CACHE.get(key) == int(until_ms)` 且 `os.path.exists(path)`（或首次确认后跳过 `exists`，仅在文件异常时清缓存），直接 `return`，不再读盘。
  3. 仅在缓存未命中或值变化时读盘/原子写入，并在成功写入后更新 `_HOLD_CACHE[key] = int(until_ms)`。
  4. 注意清理/重置数据目录或单元测试切换临时目录时，不同 `dd` 自然隔离；若同一 `dd` 被外部删文件重建，可保留轻量 `os.path.exists(path)` 或在 `clear_hold_cache(dd=None)` 中提供清理入口供测试使用。

### 2.4 裁剪 `_net_tick()` 中的隐藏页刷新与无效调用

- **涉及文件**：`app/app_net.py`（`_net_tick`，约 L450–L528）、`pages/rules_page.py`（`refresh`，约 L468–L525）
- **现状问题**：
  1. `_net_tick()` 每秒无条件执行 `if "rules" in self.pages: self.pages["rules"].refresh()`，即便规则页根本没打开。
  2. `_net_tick()` 每秒无条件执行 `self.refresh_net()`，即便当前不在 `net` 页。
- **修改方案**：
  1. **分离逻辑推进与 UI 刷新（关键防坑）**：
     `RulesPage.refresh()` 第一行会调用 `self.rules.promote_due()` 推进到期生效的暂存规则。停止隐藏刷新 UI 前，**必须**将 `self.net.proxy.rules.promote_due()` 显式留在 `_net_tick()`（或 `self.net.tick()`）中执行（可按 1–5s 节流），确保不打开规则页时暂存规则仍按时生效。
  2. 仅当规则页实际可见时（包括独立规则弹窗 `self._rules_win` 存在且 mapped，或 `self.current_page == "rules"`）才调用 `rules.refresh()`。
  3. `self.refresh_net()` 仅在 `self.current_page == "net"` 或 `event` 非空（状态切换）时调用；切到 `net` 页时 `show_page("net")` 已有 `self.refresh_net(force=True)` 兜底。
  4. `_sync_bonus_button()` 仅在 `self.current_page == "timer"` 时更新计时页按钮状态（切回 `timer` 页时补调一次）。

### 2.5 降低 `_pump_sync()` 的 `refresh_devices` 频率

- **涉及文件**：`app/app_sync.py`（`_pump_sync`，约 L40–L50）、`ai_assist/ai_config.py`（`refresh_devices`，约 L274–L310）
- **现状问题**：
  `_pump_sync()` 每 100ms 跑一次，每次都调用 `self.comm.ai.refresh_devices(self.sync.device_states())`，每秒 10 次重复构造 `wanted` 字典、遍历 `profiles` 和 `_trim()`。
- **修改方案**：
  1. 在 `SyncMixin` 上缓存上次传给 `refresh_devices` 的设备摘要（例如 `tuple((d.get("id"), d.get("name")) for d in states)`），仅当摘要变化时才调用 `self.comm.ai.refresh_devices(states)`；
  2. 或者将 `refresh_devices` 触发时机从 100ms 轮询移到设备列表变更事件 / 1s 级 `_net_tick()` 中（仍带签名去重）。

### 2.6 `app_timer.update()` 改用 `cfg()` 过滤重复文本更新

- **涉及文件**：`app/app_timer.py`（`update`，约 L870–L929）
- **现状问题**：
  专注状态每 200ms 直接调用 `self.lbl_time.config(text=...)` 和 `self.lbl_sub.config(text=...)`。Tk 对相同文本的 `config()` 仍会触发重排与重画，而 `cur_m:.2f` 每 0.6 秒才变一次。
- **修改方案**：
  将 `self.lbl_time.config(text=...)` 和 `self.lbl_sub.config(text=...)` 统一替换为 `pages.widgets.cfg(...)`（仅在值真正变化时下发 Tk `configure`）。

### 2.7 将 Win32 / COM 系统探测移出主线程

- **涉及文件**：
  - `app/app_contract.py`（`_check_desktop`，约 L60–L79）
  - `app/app_guard.py`（`_schedule_app_rules` / `_check_app_rules` / `_window_focus_tick`，约 L329–L394）
- **现状问题**：
  `window_guard.on_current_desktop(hwnd)`（涉及 Win32 VirtualDesktopManager COM 调用）、`window_guard.check_focus_rules(...)`、`window_guard.foreground_program()`（涉及进程句柄打开与路径查询）都在 Tkinter 主线程同步执行；遇到系统卡顿或高权限进程查询耗时，会直接卡死 UI。
- **修改方案**：
  1. **虚拟桌面检测（`_check_desktop`）**：在主线程只读取 `hwnd`（启动/重建时缓存），每 2s 的 `on_current_desktop(hwnd)` 探测放入后台守护线程（注意在工作线程内独立初始化 COM `CoInitialize` / `CoUninitialize`，且避免与主线程并发访问同一 COM 对象），状态变化时通过 `self.root.after(0, ...)` 回到主线程写状态、弹 toast、触发同步。
  2. **前台应用规则与窗口统计（`_check_app_rules` / `_window_focus_tick`）**：
     - 后台线程按 1s 周期采集 `(hwnd, exe, title)` 快照（只做只读 Win32 查询与进程名解析）；
     - 主线程或规则线程按快照判定黑白名单；命中禁用应用后的关闭窗口与 toast / `contract_violation` 回调仍在主线程安全提交。
     - 确保 `self.shutting_down` 时线程能及时退出，且不破坏现有自检（如 mock `window_guard` 的测试）。

---

## 3. 阶段 2：中度结构优化

阶段 1 落地后，若启动耗时、换肤耗时、切页或长列表滚动仍有明显卡顿，继续推进本阶段。

### 3.1 页面懒加载（Lazy Initialization）

- **涉及文件**：`app/app_window.py`（`_layout_steps`、`rebuild_ui`、`_show_page`，约 L282–L697）及所有直接访问 `self.pages["xxx"]` 的调用点
- **现状问题**：
  `_layout_steps()` 启动时一次性构建并 `_prepare_page()` 全部 10+ 页面（`knowledge`、`prescript`、`cal`、`stats`、`log`、`contract`、`settings`、`ach`、`comm`、`net`、`timer`、`reset`），导致启动慢、换主题/字号重建慢、常驻大量不可见 HWND。
- **修改方案**：
  1. 引入 `self._page_factories: dict[str, Callable]` 与 `self._ensure_page(key: str)`：
     - 启动/重建时**只实例化当前首页**（通常是 `timer`）和轻量壳层（`topbar`、`sidebar`、`content`、`share_dock`、`ai_data_view`）；
     - 其余重页面（`settings`、`stats`、`cal`、`knowledge`、`ach`、`contract`、`comm`、`log`、`net`、`reset`、`prescript`）在首次 `show_page(key)` 或业务代码首次访问时按需创建并缓存进 `self.pages[key]`。
  2. **跨页面依赖审计（必做防坑）**：
     - `app/app_window.py` L441：`self.pages["comm"].open_knowledge_card = self.pages["knowledge"].open_card` → 改为延迟代理 `lambda card_id: self._ensure_page("knowledge").open_card(card_id)`；
     - `_refresh_if_visible(*keys)` 本身已按 `current_page` 与 `key in self.pages` 过滤，未创建的页面天然跳过；
     - 全局搜索 `self.pages[`（`git grep -n 'self\.pages\[' app/ pages/`），逐一分类处理：
       - **仅刷新 UI 的调用**（如 `self.pages["log"].refresh()`、`self.pages["settings"].refresh_sync()`）：改为 `if "xxx" in self.pages:`，未构建时无需触发创建；
       - **需要跳页或设置交互状态的调用**（如 `self.pages["contract"].set_joint_pick(True)`、`self.pages["comm"].ask_joint_time(...)`、`self.pages["timer"]` 等）：改为 `self._ensure_page("xxx")`。
  3. 同步更新 `LAYOUT_STEPS` 与 `LoadingCover` 进度步数，使启动加载条不卡在半途。

### 3.2 根窗口统一滚轮路由，替代 `ScrollArea.bind_children()` 递归绑定

- **涉及文件**：`pages/widgets.py`（`ScrollArea`、`_SmoothScroll`，约 L57–L104、L653–L786）、`pages/comm_page.py`（L92）
- **现状问题**：
  每次往 `ScrollArea.inner` 动态插入控件（如聊天消息、统计行、搜索结果、提前启动清单）都需调用 `bind_children()` 递归遍历整棵子控件树逐个绑定 `<MouseWheel>`，控件越多开销越大，且容易漏绑或重复遍历。
- **修改方案**：
  1. 在 `root` 上安装一次全局滚轮分发器 `root.bind_all("<MouseWheel>", _dispatch_wheel, add="+")`：
     - 从 `event.widget`（若不可靠则辅以 `winfo_containing(event.x_root, event.y_root)`）沿 `master` 链向上查找；
     - 若先遇到自带原生滚动且内容超出可视区的控件（如 `tk.Text`、`tk.Listbox`、`ttk.Treeview` 或打开中的 `Combobox` popdown），直接放行，不拦截；
     - 否则找到最近的带 `_oit_scroller` 属性的 `ScrollArea`（或挂了 `attach_smooth_scroll` 的 `Canvas`），调用其 `on_wheel(event)` 并返回 `"break"`。
  2. 将 `ScrollArea.bind_children()` 保留为空操作（no-op，保持所有调用方接口兼容）。
  3. **滚动后钩子兼容（防坑）**：
     - `CommPage` 目前通过 `self.area.canvas.bind("<MouseWheel>", lambda _e: self.after_idle(self._check_top), add="+")` 触发向上翻页加载历史消息（`pages/comm_page.py` L92）。
     - 在 `_SmoothScroll` 上增加可选回调 `on_after_scroll`（在 `_flush` / `moveto` 完成后调用），由 `CommPage` 注册 `self.area._scroller.on_after_scroll = self._check_top`，避免全局分发返回 `"break"` 后影响加载更早消息。

### 3.3 长列表轻量化与增量复用（减少 `destroy()` 与子 HWND 数量）

- **优先目标页面**：
  1. **沟通页**（`pages/comm_page.py`）：
     - 减少单个气泡行内的嵌套 `Frame` 层级；
     - 将初始 `RENDER_MAX`（当前 80）评估调整为更轻量的首屏窗口（如 40–50，往上滚动再 `LOAD_MORE`）；
     - 对 `_sig` 未变且仅尾部增量变化的情况继续保持增量追加，避免不必要的整页 `winfo_children()` 销毁重建；
     - 长期可将纯文字气泡区评估重构为单个 `tk.Text`（利用 tag/window 混排）或 `Canvas` 自绘，彻底消除大量子 HWND。
  2. **统计页**（`pages/stats_page.py`）：
     - `journal_rows`、`contract_rows`、`violation_rows`（约 L528–L615）增加数据签名缓存（`_journal_sig` / `_contract_sig` / `_violation_sig`），切回同一 tab 且数据未变时直接跳过销毁重建；
     - 超长列表做分页或“加载更多”（默认先渲染最近 30–50 条）。
  3. **知识库页**（`pages/knowledge_page.py`）：
     - 检索列表按页/分批渲染，避免一次性把全部知识卡都生成为 Tk 行控件；
     - 降低 `_poll`（100ms）在无后台任务时的轮询开销（无进行中任务时停止轮询，仅在触发异步任务时启动）。

---

## 4. 验证与回归检查清单

### 4.1 必跑自动化检查脚本

按改动范围运行以下现有检查脚本（全部需退出码 `0` 且输出 `… OK`）：

```bash
python checks/_sidebar_check.py
python checks/_ui_check.py
python checks/_comm_mode_check.py
python checks/_comm_delivery_check.py
python checks/_stats_ui_check.py
python checks/_daily_ui_check.py
python checks/_ai_dialog_check.py
python checks/_prestart_check.py
python checks/_knowledge_check.py
python checks/_focus_check.py
python checks/_pause_check.py
python -m comm
python -m net_guard
```

### 4.2 建议补充的性能基准与手工验收项

建议在开发过程中用轻量计时日志（勿提交到正式代码）对比优化前后的关键指标：

| 场景 | 验收指标 | 预期目标 |
| --- | --- | --- |
| 启动到首屏可交互 | 冷启动耗时（秒） | 阶段 2 懒加载后显著下降（目标降幅 ≥ 40%） |
| 沟通页 / 设置页 / 知识库页滚轮滚动 | 单次 `_flush` 耗时 & 主观跟手度 | 单次处理 < 10ms，连续滚动不卡顿、无残影盖板 |
| 侧边栏切页（计时器 ↔ 设置 ↔ 统计 ↔ 沟通） | `show_page` 耗时 | 首次切页平滑，二次切页 < 30ms |
| 焦点停留在输入框（都市之意主题） | 闲置 CPU 占用与重绘次数 | 静止时 `FocusGlow._place` 零 `place`/`lift` 调用 |
| 后台静置 1 分钟（非规则页/非联网页） | 磁盘读 `link_state.json` 次数 & `refresh_devices` 次数 | 均为 0 次 |
| 隐藏规则页时暂存规则到期 | 到点是否自动生效 | 正常生效（`promote_due` 不受 UI 隐藏影响） |
| 沟通页上滑加载历史消息 | 滚到顶部是否正常加载且视野不跳 | 正常加载，滚轮在气泡文字上同样生效 |

---

## 5. 推荐排期与拆分 PR 建议

为便于 Review 和定位回归，建议拆分为 4 个独立提交/PR：

1. **PR-1（阶段 1A：渲染与滚动热修复）**：`FocusGlow._place` 几何短接 + `_SmoothScroll`/`_RegionCover` 去 `DwmFlush` 与滚轮节流。
2. **PR-2（阶段 1B：主线程轮询与 I/O 清理）**：`linkd.write_hold` 缓存 + `_net_tick` 隐藏页裁剪（保留 `promote_due`）+ `_pump_sync` 去重 + `app_timer.update` 改用 `cfg()` + Win32 探测移至后台线程。
3. **PR-3（阶段 2A：统一滚轮路由与长列表缓存）**：全局 `<MouseWheel>` 分发器 + 统计/知识库/沟通页签名缓存与分批渲染。
4. **PR-4（阶段 2B：页面懒加载）**：`app_window.py` `_page_factories` / `_ensure_page` 改造与全仓库 `self.pages[...]` 调用点审计。
