---
name: code-quality-and-ui-overhaul
overview: 修复静默异常、配置验证、Tkinter 清理等代码质量问题，并对前端 UI 进行全面视觉升级（Design Token、排版体系、按钮层级、表单精致化、微交互），不做暗黑模式。
design:
  architecture:
    framework: html
  styleKeywords:
    - 现代简洁
    - 品牌蓝
    - 浅灰白底
    - 微交互
    - 清晰层级
  fontSystem:
    fontFamily: PingFang SC, Microsoft YaHei, -apple-system, BlinkMacSystemFont, sans-serif
    heading:
      size: 15px
      weight: 600
    subheading:
      size: 14px
      weight: 600
    body:
      size: 14px
      weight: 400
  colorSystem:
    primary:
      - "#3b82f6"
      - "#2563eb"
      - "#1d4ed8"
    background:
      - "#f9fafb"
      - "#ffffff"
      - "#fafafa"
    text:
      - "#111827"
      - "#4b5563"
      - "#9ca3af"
    functional:
      - "#10b981"
      - "#f59e0b"
      - "#ef4444"
todos:
  - id: fix-silent-exceptions
    content: 修复 run.py 中 _show_info 和 _show_error 的静默异常吞没，改为 logger.warning + print 降级
    status: completed
  - id: fix-tkinter-cleanup
    content: 修复 run.py 中 JsApi.pick_folder 的 Tkinter root 窗口，用 try/finally 确保 destroy
    status: completed
  - id: add-config-validation
    content: 为 main.py 的 load_config 后添加 validate_config 函数，检查 db_dir/my_name/api_key 等关键字段
    status: completed
  - id: css-design-tokens
    content: 重写 index.html 的 style 块：在开头插入完整 Design Token CSS 变量体系（色阶、字号、间距、阴影）
    status: completed
  - id: css-components-rebuild
    content: 重构 index.html 的组件 CSS：三级按钮体系、form-input、card、focus-visible、微交互动画，删除旧 tbtn 规则
    status: completed
    dependencies:
      - css-design-tokens
  - id: html-class-migration
    content: 批量替换 index.html 中所有 HTML 按钮 class（tbtn → btn--primary/secondary/ghost）和 JS 中动态 className 引用
    status: completed
    dependencies:
      - css-components-rebuild
  - id: verify-and-commit
    content: 本地启动验证所有页面渲染正常，提交并推送
    status: completed
    dependencies:
      - fix-silent-exceptions
      - fix-tkinter-cleanup
      - add-config-validation
      - html-class-migration
---

## 用户需求

根据代码审查报告和 UI 设计优化方案，进行以下改进：

### 功能性修复（3 项）

1. **run.py 静默异常吞没修复**：`_show_info()`、`_show_error()` 中的 `except Exception: pass` 改为 `logger.warning()` + `print()` 降级输出，确保无头环境下开发者有感知
2. **run.py Tkinter root 资源清理**：`JsApi.pick_folder()` 中的 `root = tk.Tk()` 改用 try/finally 确保 `root.destroy()` 始终执行
3. **main.py 配置参数验证**：`load_config()` 后新增 `validate_config()` 函数，启动时检查 `wechat.db_dir`、`wechat.my_name` 等关键字段，提前给出清晰错误提示

### UI 全面视觉重设计

基于 `app/static/index.html`（约 60KB 单文件），进行完整的视觉升级：

- **Design Token 体系**：建立 `:root` CSS 变量，含 10 级品牌蓝色阶、10 级中性灰色阶、语义色（success/warning/error/info）
- **排版体系**：建立 text-xs/sm/base/lg/xl/2xl 标准化字号 + font-weight + line-height 变量
- **间距网格**：4px 基准的 `--space-1` 到 `--space-8`（4px~32px），统一替换所有魔数间距
- **三级按钮体系**：`.btn--primary`（蓝色填充）、`.btn--secondary`（白底边框）、`.btn--ghost`（透明文字），含 hover 上浮 + 阴影的微交互，替换现有 `.tbtn` / `.tbtn.blue` / `.tbtn.green` / `.tbtn.red`
- **表单控件精致化**：统一 `.form-input` 样式，含 focus 蓝色光环（3px 外发光）、error 红色边框、disabled 灰底态
- **卡片组件**：统一 `.card` 样式，替代内联散落的 border-radius/padding/border 组合
- **焦点状态无障碍**：全局 `:focus-visible` 蓝色焦点环 2px + 2px offset
- **微交互动画**：按钮 hover 上浮 1px + 阴影扩散 150ms ease，统一过渡节奏
- **字体回退链**：`"Microsoft YaHei", "PingFang SC"` 中文优先，优先级高于系统默认

**不做**：暗黑模式、SSL 验证、JSON 提取统一、硬编码端口、build_exe.bat 编码、Pyright 规则、logger 命名、单元测试

## 技术栈

- 后端：Python（run.py、main.py 原地修改，不引入新依赖）
- 前端：纯 HTML + CSS + 原生 JS（index.html 约 60KB 单文件），运行在 PyWebView Edge WebView2（Chromium 内核）
- CSS 变量、`:focus-visible` 均由 WebView2 原生支持，无需 polyfill

## 实现策略

### 代码修复（最小改动，精确替换）

**run.py 三处修改**：

1. `_show_info()` 第 68-69 行：`except Exception: pass` → `except Exception as e: logger.warning(...); print(...)`
2. `_show_error()` 第 77-78 行：同上
3. `JsApi.pick_folder()` 第 347-359 行：`root = tk.Tk()` 提升到 try 外部初始为 None，套上 try/finally，finally 中安全 destroy

**main.py 一处新增**：

在 `load_config()` 的 `return config` 之前插入 `validate_config(config)` 调用，函数定义放在 `load_config` 之后，检查：

- `wechat.db_dir` 是否存在且为有效路径
- `wechat.my_name` 是否非空
- 若 `llm_summary_enabled` 且 `local_llm` 未启用，则检查 `llm.api_key` 非空

### UI 重设计（CSS 变量体系 + HTML class 替换）

**核心原则**：不改变任何 JS 逻辑和 API 调用，只改 CSS 样式和 HTML class 名。PyWebView 加载时自动应用新样式。

**分五步执行**：

1. **`:root` 变量块**：在 `<style>` 最开头插入完整的 Design Token（品牌色阶 10 级、灰色阶 10 级、语义色、间距、字号、圆角、阴影变量）
2. **全局重置 + 排版**：基于变量重写 `*`、`html,body`、标题、正文、`textarea` 的 font-size/line-height/font-weight
3. **按钮体系**：新增 `.btn`（基础）、`.btn--primary`、`.btn--secondary`、`.btn--ghost`、`.btn--sm/.btn--md/.btn--lg` 完整样式，删除旧 `.tbtn` 系列规则
4. **表单 + 卡片 + 焦点**：新增 `.form-input`、`.card`、全局 `:focus-visible` 规则，删除旧的 `.f-row input` 等分散选择器
5. **HTML class 批量替换**：将所有 `<button class="tbtn ...">` 替换为对应的新 class（使用正则替换保证不遗漏）

## 实现细节

### 关键执行细节

**按钮 class 映射表**：

| 旧 class | 新 class |
| --- | --- |
| `class="tbtn"` | `class="btn btn--secondary btn--md"` |
| `class="tbtn blue"` | `class="btn btn--primary btn--md"` |
| `class="tbtn green"` | `class="btn btn--primary btn--md"` |
| `class="tbtn red"` | `class="btn btn--ghost btn--md" style="color:var(--error)"` |
| `class="save-btn"` | `class="btn btn--primary btn--md" style="width:100%"` |


**.tbtn 样式删除后，JS 中动态操作 className 的地方需同步修改**（如 `updateManualBtn`、`updateAutoUI` 中 `btn.className = 'tbtn red'` 等）。

**CSS 变量迁移**：硬编码颜色 → 变量的对应关系：

| 旧值 | 新变量 |
| --- | --- |
| `#1976d2` | `var(--brand-500)` |
| `#1a1a2e` | `var(--gray-900)` |
| `#666` | `var(--gray-600)` |
| `#888` | `var(--gray-400)` |
| `#f5f6f9` | `var(--gray-50)` |
| `#e8e8e8` | `var(--gray-200)` |
| `#4CAF50` | `var(--success)` |
| `#f44336` | `var(--error)` |


### 性能考虑

- CSS 变量替换不增加运行时开销，WebView2 原生支持
- 微交互动画统一使用 `transition: all 150ms ease`，GPU 合成，无重排
- 焦点环使用 `outline` 而非 `border`，避免布局抖动

### 回退安全

- 本计划只改 CSS + HTML class，不变 JS 逻辑
- 旧 `.tbtn` 样式全部删除后会验证无残留引用
- 修改后本地启动 PyWebView 验证所有页面渲染正常

## 架构设计

```
修改涉及的文件:

repote2/
├── run.py                    # [MODIFY] 三处修改：_show_info/_show_error 日志降级、pick_folder try/finally
├── main.py                   # [MODIFY] 一处新增：load_config 后插入 validate_config() 函数
└── app/
    └── static/
        └── index.html        # [MODIFY] 大规模 CSS 重写 + HTML class 批量替换（保持 JS 逻辑不变）
```

无新增文件，无新增依赖。

## 设计风格

现代简洁工具面板风格，以浅灰白为底、品牌蓝为主色，强调信息层级清晰和操作直觉。整体视觉从 2015 年风格升级到 2025 年水准。

## 整体布局

保持现有布局结构（顶栏 → 工具栏 → 左侧栏 + 主内容区 → 状态栏），不做结构调整，仅升级视觉品质。

## 各区块设计

### 顶栏

白色背景，右侧状态指示圆点，左侧标题使用 `--text-lg` 字号 + `--font-weight-semibold` 加粗，底部 1px 灰色分割线。

### 侧栏

浅灰底，选中项蓝色高亮 + 左边框标记，hover 态浅灰底过渡 150ms，图标 + 文字间距 8px。

### 工具栏按钮

三级按钮层次分明：主要操作（手动运行、保存、生成周报）使用 `btn--primary` 蓝色填充，常规操作（打开文件、测试密钥）使用 `btn--secondary` 白底边框，危险/取消（退出、停止）使用 `btn--ghost` 配 error 红色。

### 统计卡片

`card` 组件包裹，浅渐变背景 + 1px 边框 + 10px 圆角，数字 28px 加粗深色，标签 12px 灰色。

### 表单控件

`.form-input` 统一样式：8px 12px 内边距、14px 字号、6px 圆角、1px 灰色边框。聚焦时边框变蓝 + 3px 半透明蓝色外发光。disabled 态灰底 + 灰字 + not-allowed 光标。

### 日志区

保持深色底 Terminal 风格，代码区字体回退链 `Consolas, "Cascadia Code", "Source Code Pro", monospace`。

### 微交互

所有按钮 hover 上浮 1px + 阴影扩散，点击 active 态回弹，全局 150ms ease 过渡。焦点环使用 `outline` 避免布局抖动。