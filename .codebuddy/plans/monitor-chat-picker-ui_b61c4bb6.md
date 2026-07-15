---
name: monitor-chat-picker-ui
overview: 将「会话监控」页面的监控群聊从手写 textarea 改为和「会话分析」一样的双面板加载+多选 UI
todos:
  - id: replace-textarea
    content: 替换会话监控 Tab 中监控群聊 textarea 为双面板布局（左侧加载+搜索+勾选列表，右侧已选清单+数量）
    status: completed
  - id: add-mon-variables
    content: 新增独立 JS 变量 monAllChats/monSelectedChats 及 6 个选择器函数
    status: completed
    dependencies:
      - replace-textarea
  - id: update-monitor-config-functions
    content: 修改 loadMonitorConfig 从配置恢复勾选状态并自动加载列表，修改 saveMonitorConfig 从勾选数组获取群聊列表
    status: completed
    dependencies:
      - add-mon-variables
---

## 用户需求

将「会话监控」Tab 中"监控群聊"的输入方式从手动填写 textarea 改为与「会话分析」Tab 一致的交互模式：点击加载按钮获取所有会话列表，支持搜索过滤，勾选多选，右侧显示已选清单并可删减。

## 核心功能

- 左侧面板：点击「加载」按钮调用 `/api/sessions/list` 获取会话列表，支持搜索过滤，每行带 checkbox 勾选
- 右侧面板：显示已选中的监控群聊名称及数量，每项带 ✕ 删除按钮
- 保存配置时从右侧已选清单获取群聊列表（替代原来的 textarea 值）
- 进入监控 Tab 时自动加载会话列表并恢复已选状态
- 监控面板的群聊选择状态与会话分析面板完全独立，互不影响

## 技术方案

### 实现方式

复用「会话分析」已沉淀成熟的双面板 UI 模式，以独立变量名前缀（`mon`）隔离状态，避免与现有 `allChatNames`/`selectedChats` 冲突。

### 改动范围

仅修改单个文件 `app/static/index.html`，无后端改动（共用一个已有 API `/api/sessions/list`）。

### 关键设计决策

- **变量隔离**：使用 `monAllChats` / `monSelectedChats` 独立于分析面板的 `allChatNames` / `selectedChats`
- **函数命名**：新增函数均加 `Monitor` 后缀（如 `loadMonitorChatList`）与已有分析面板函数区分
- **自动加载**：进入监控 Tab 时 `loadMonitorConfig()` 自动调用 `loadMonitorChatList()`
- **配置读写**：`saveMonitorConfig()` 改为从 `monSelectedChats` 数组取数据，`loadMonitorConfig()` 将配置中的 `chats` 数组恢复为勾选状态

### 目录结构

```
app/static/index.html  [MODIFY] 唯一改动文件
```

### 具体改动点

#### HTML 改动（line ~299-302）

将：

```html
<textarea id="monChats" placeholder="每行一个群聊名称" ...></textarea>
```

替换为双面板布局（复用分析面板 line 249-266 结构，ID 加 mon 前缀）：

- `monChatSearch` — 搜索输入框
- `monChatSelectList` — 左侧会话列表容器
- `monChatSelectedBox` — 右侧已选清单容器
- `monChatSelectCount` — 已选数量显示

#### JS 改动（line ~678-789）

1. **新增变量声明**（在 `// ====================== 会话监控 ======================` 区块开头）：

- `var monAllChats = [];`
- `var monSelectedChats = [];`

2. **新增 6 个函数**（参照 line 805-876 的分析面板函数）：

- `loadMonitorChatList()` — 调用 API 加载会话列表
- `renderMonitorChatList(sessions)` — 渲染左侧勾选列表
- `onMonitorChatToggle(cb)` — 勾选/取消时更新 monSelectedChats
- `removeMonitorChat(name)` — 从已选清单移除
- `refreshMonitorSelectedBox()` — 刷新右侧已选面板
- `filterMonitorChatList()` — 搜索过滤

3. **修改 `loadMonitorConfig()`**：

- 从配置的 `cfg.chats` 数组恢复 `monSelectedChats`
- 末尾调用 `loadMonitorChatList()` 自动加载会话列表

4. **修改 `saveMonitorConfig()`**：

- 改为 `var chats = monSelectedChats.slice();`（从勾选数组获取）
- 移除对 `document.getElementById('monChats').value` 的依赖