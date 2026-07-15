---
name: monitor-native-file-picker
overview: 会话监控文件选择改为原生资源管理器多选对话框
todos:
  - id: add-pick-files-api
    content: 在 app/server.py 新增 /api/file/pick-files 端点（tkinter askopenfilenames 多选 .md）
    status: completed
  - id: replace-mon-file-ui
    content: 替换 index.html 的 monFileList 区域为按钮+已选数量标签，新增 pickMonitorFiles/analyzePickedFiles 函数，删除 loadMonitorFiles/analyzeMonitorFiles
    status: completed
    dependencies:
      - add-pick-files-api
---

## 用户需求

将会话监控 Tab 中"消息文件 & AI 分析"区域的文件选择方式从当前的复选框列表改为打开 Windows 原生资源管理器多选 .md 文件。

## 核心功能

- 点击按钮弹出原生文件选择对话框，多选 .md 文件
- 对话框默认打开监控输出目录
- 选择完成后显示已选文件数量
- 填写分析要求后点击发送，将文件路径发给后端 LLM 分析
- 移除原有的文件列表加载和复选框逻辑

## 技术方案

### 实现方式

完全复用项目已有的模式：在 `server.py` 的 `/api/file/pick-folder` 旁边新增 `/api/file/pick-files` 端点（使用 `tkinter.filedialog.askopenfilenames`），前端通过 API 调用获取文件路径列表。

### 改动范围（2 个文件）

#### 1. `app/server.py` — 新增 `/api/file/pick-files` 端点

在 `/api/file/pick-folder` 之后（line 1417 后）新增端点，参照已有模式：

- 用 `tkinter.Tk().withdraw()` 隐藏主窗口
- 调用 `filedialog.askopenfilenames(title="选择要分析的消息文件", filetypes=[("Markdown 文件", "*.md")], initialdir=output_dir)`
- 初始目录设为监控输出目录（`_get_output_dir()`），如果存在"会话监控_*"子目录则定位到最新的那个
- 返回 `{success: True, files: [路径数组]}`

#### 2. `app/static/index.html` — 替换 monFileList 区域及 JS 函数

**HTML 改动**（line 342-353）：

- 删除提示语、刷新按钮、monFileList 容器
- 改为一行：`[📂 选择消息文件]` 按钮 + 已选数量标签 + 分析要求输入框 + 发送分析按钮
- 新增隐藏的 `<input type="hidden" id="monSelectedFiles">` 存 JSON 序列化的路径数组

**JS 改动**：

- 删除 `loadMonitorFiles()` 函数
- 删除 `analyzeMonitorFiles()` 函数
- 新增 `pickMonitorFiles()` 函数：调用 `/api/file/pick-files` 获取路径，更新标签显示数量，存到 hidden input
- 新增 `analyzePickedFiles()` 函数：从 hidden input 读取路径，连同分析要求发给 `/api/chat-monitor/analyze`

### 关键设计决策

- 继续使用 tkinter 文件对话框（与已有 pick-folder 一致），保证原生体验
- 文件路径通过 hidden input 暂存，避免全局变量
- 初始目录自动定位到最新的监控输出子目录，减少用户导航成本
- 不兼容浏览器 fallback 模式时用 alert 提示（浏览器无法调 tkinter）