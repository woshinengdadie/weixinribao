# 更新日志 (CHANGELOG)

## v2.0.1.16 (2026-07-16)

### 变更
- 

## v2.0.1.15 (2026-07-16)

### 新增
- **UI 全面升级（Design Token 体系）**：品牌蓝+中性灰 10 级色阶、4px/8px 间距网格、三级按钮（primary/secondary/ghost）、焦点无障碍环
- **布局重构**：品牌渐变顶栏、侧栏分组折叠（图标 55px ⇄ 展开 200px + tooltip）、快速操作下拉菜单、仪表盘欢迎区（时段问候+日期徽章）、四色统计卡片（蓝💬/绿✅/黄⚠️/紫📊）
- **代码健壮性增强**：静默异常降级日志（`_show_info`/`_show_error`）、Tkinter `pick_folder` try/finally 安全销毁、启动配置验证 `validate_config()`

### 修复
- **🔴 已选清单回空后空状态提示永远消失**：`refreshSelectedBox`/`refreshMonitorSelectedBox` 的 `innerHTML = ''` 销毁了 `#chatSelectedEmpty`/`#monChatSelectedEmpty`，改为 `querySelectorAll` + `remove()` 逐个清理
- **🔴 `main.py` `validate_config` 使用未定义的 `logger`**：改为 `logging.error()`（logging 模块已导入）
- **🔴 手动运行/会话分析存在 2 秒竞态窗口**：`manualRunning`/`analyzeRunning` 改为 API 调用前立即设标志 + 失败回滚，消除双击重复执行
- **🟡 监控区 `save-btn` 100% 宽度挤压同行按钮**：两处改为 `btn btn--primary btn--md`
- **🟡 会话名称含 `\` 触发 onclick 语法错误**：`safeName` 转义 `\\` → `\\\\`，`'` → `\x27`

## v2.0.1.14 (2026-07-14)

### 修复（实测 + 代码审查）
- **[严重] `chat_monitor.py:analyze_files()` for 循环缩进错误**：第 366-367 行的 `for` 循环体内只有一行 `log(...)`，369-454 行的文件读取、LLM 调用、报告生成全部在循环外部，只对最后一个文件执行一次（选择多个文件分析时其余全部被跳过）。已将所有分析逻辑缩进到循环体内
- **[中等] `app/server.py:_save_config()` 非原子写入**：直接覆盖 `config.yaml`，若写入中途程序崩溃或磁盘空间不足，配置文件会被截断损坏。改为先写 `.tmp` 再 `os.replace()` 原子替换
- **[低] `build_app.py` 控制台 emoji 输出 GBK 报错**：`\u2705`/`\u274c`/`\u26a0` 等 Unicode emoji 在中文 Windows 控制台（GBK 编码）下抛出 `UnicodeEncodeError`，打包成功的总结信息被吞掉。已全部替换为 ASCII 安全标记 `[OK]`/`[FAILED]`/`[WARN]`
- **[后端 API] 全部 14 个端点通过测试**：`/api/config` 读写一致性、`/api/run/status`、`/api/chat-monitor/*`、`/api/file/open`、`/api/weekly/list`、`/api/sessions/list` 等核心 API 均返回正确状态和数据

## v2.0.1.13 (2026-07-14)

### 修复
- **打包时 VERSION 与 CHANGELOG 不同步**：`tools/version.py` 的 `update_changelog` 之前用前三段版本号（如 `## v2.0.1`）做去重判断，会把同主次版本号下所有 build 号（如 v2.0.1.12、v2.0.1.10…）误判为已存在，导致每次 build bump 后 CHANGELOG 都被静默跳过。改为用 `format_version()` 生成的完整版本号（`## v2.0.1.13`）作为唯一标识
- **`version.py update-run` 命令未注册到 argparse**：`build_exe.bat` 第 135 行调用时直接报错 `invalid choice: 'update-run'`，导致 run.py 的 `__version__` 在 CI 流程中其实从未被同步过。现已把 `update-run` 加入 subparsers

## v2.0.1.12 (2026-07-14)

### 修复
- **安装包启动弹出多个 cmd 窗口**：`hardware_id.py`、`local_llm.py`、`run.py` 中 `subprocess` 调用均补上 `creationflags=CREATE_NO_WINDOW`，消除 PyInstaller `--windowed` 打包后的黑窗闪烁
- **升级安装后配置丢失**：`installer.iss` 排除 `_internal\config\*`，`config.yaml.example` 单独安装到 `{app}\config\` 并标记 `uninsneveruninstall`，确保卸载/升级不会删除用户配置文件。`_load_config` 兜底查找 example 时支持 `_internal\config\` 路径
- `tools/version.py` 新增 `update-run` CLI 命令

## v2.0.1.10 (2026-07-14)

### 修复
- 构建脚本 `build_exe.bat` 补充 `update-run` 步骤，消除 `run.py` `__version__` 与 `VERSION` 文件不同步
- `build_app.py` 中 wechat-cli 安装补上 PyPI fallback
- `setup.bat` 补充 `pymem`、`psutil` 依赖
- `run_gui.bat` 修复编码损坏，中文恢复

> **关于配置保留**：用户的 `config.yaml` 等运行时配置保存在 `{app}\config\`，由 `app/server.py` 的 `_save_config` / `_load_config` 函数管理。安装包不会覆盖 `{app}\config\`，因为 PyInstaller 的 `--add-data "config;config"` 把源 config 装到 `{app}\_internal\config\`，与用户运行配置位置分离。

## v2.0.1.9 (2026-07-14)

### 修复（代码审查）
- **chat_monitor.py**：`_save_last_run_time` 两次 `datetime.now()` 不一致 → 统一为一个变量
- **chat_monitor.py**：调度器任务执行失败仍标记 `_last_run_time` → 改为成功后更新
- **chat_monitor.py**：`_fetch_messages` 调用 `WeChatReader` 私有方法 → 新增公开 `run_wechat_cli()` 方法
- **chat_monitor.py**：`OpenAI` 客户端未关闭导致连接泄漏 → `try/finally` 关闭
- **chat_monitor.py**：`folder_path` 在循环内重复计算，`_save_messages_md` 内又算一次 → 循环外统一计算，方法新增 `folder_path` 参数
- **wechat_reader.py**：新增 `run_wechat_cli()` 公开接口，替代 `_run_wechat_cli_inprocess` 私有调用
- **index.html**：`escapeHtml` 漏转引号，XSS 风险 → 补上 `&quot;` / `&#39;`
- **index.html**：`api()` 无异常处理，所有调用静默失败 → 加 `try/catch` + HTTP 状态码检查
- **index.html**：`innerHTML +=` 日志追加 O(n²) 性能崩塌 → `insertAdjacentHTML` + 行数上限 2000
- **index.html**：SSE 重连固定 3s 无退避 → 指数退避 1s→30s
- **index.html**：`toggleAuto` 停止失败仍改 UI 状态 → 检查返回值
- **index.html**：`quitApp` fire-and-forget 可能丢失请求 → `navigator.sendBeacon`
- **index.html**：`generateWeekly` 冗余包装 → 移除
- **app/server.py**：`_monitor_scheduler` 类型标注引用延迟导入的类 → 移除标注
- **main.py**：license 文件损坏无恢复引导 → 捕获异常并提示删除 `license.bin`

## v2.0.1.8 (2026-07-14)

### 修复
- **硬件码采集全面重构**：`license_client/hardware_id.py` 多处修复
  - WMIC 在 Windows 11 22H2+ 不可用 → 改用 PowerShell `Get-CimInstance`，兼容所有现代 Windows
  - 编码兼容 GBK/UTF-8/UTF-16，处理 PowerShell 输出的 BOM，`\r` 尾随清理
  - 三项全部采集失败时混入主机名+用户名+C盘卷序列号兜底，防止不同机器生成相同哈希
  - 三个采集函数新增 `logger.debug` 日志，便于排查
- **安装包体积翻倍**：`build_app.py` 中 `shutil.copytree` 复制 models/wx_key 导致 ~500MB 重复文件 → 改为 `shutil.move`，与 `build_exe.bat` 一致

### 新增
- **机器码可见**：激活授权页面新增本机机器码展示；新增 `get_hwid.bat` 独立采集工具
- **机器码变更友善处理**：`HW_MISMATCH` 时显示清晰引导+当前机器码，桌面版自动弹出激活码输入框，CLI 版提示重新输入激活码

## v2.0.1.5 (2026-07-14)

### 修复
- **硬件码编码不稳定**：`_run_wmic` `decode('utf-8', errors='ignore')` 导致中文字节静默丢弃 → GBK 优先解码，兜底 UTF-8

## v2.0.1.4 (2026-07-11)

### 新增
- **原生文件多选对话框**：`app/server.py` 新增 `/api/file/pick-files` 端点，调用 tkinter `askopenfilenames` 弹出 Windows 资源管理器多选 `.md` 文件，对话框默认定位到监控输出目录下的 `会话监控` 子目录

### 变更
- **消息文件选择 UI 重构**：会话监控 Tab 的「消息文件 & AI 分析」区域移除复选框文件列表（`div#monFileList`），改为 `[📂 选择文件]` 按钮 + 已选数量提示标签（`span#monFileCount`）
  - 新增 `pickMonitorFiles()` 函数，调用 `/api/file/pick-files` 获取路径列表并存到 `monSelectedFiles` 数组
  - 重写 `analyzeMonitorFiles()`，从 `monSelectedFiles` 取值而非读取 DOM 复选框
  - 移除 `loadMonitorFiles()` 函数及其相关的后端文件列表请求逻辑
- **监控按钮合并**：启动/停止监控从两个独立按钮（`▶ 启动监控` / `⏹ 停止监控`）合并为一个切换按钮（`id="monToggleBtn"`）
  - 合并 `startMonitor()` 和 `stopMonitor()` 为 `toggleMonitor()`，根据 `monRunning` 状态决定调用 start/stop API
  - `loadMonitorStatus()` 新增按钮状态同步逻辑，根据 `scheduler_running` 动态切换按钮文字和 `monRunning` 变量

## v2.0.1.1 (2026-07-09)

### 新增
- **会话监控功能**：全新的微信会话自动归档 + AI 分析子系统
  - `src/chat_monitor.py` — 会话监控引擎（`ChatMonitor`）+ 后台调度器（`ChatMonitorScheduler`）
    - 支持两种调度模式：固定间隔（interval）和每日定时（scheduled）
    - 首次运行可捞取最近 N 小时历史消息，后续增量拉取
    - 按群聊逐群拉取消息，渲染为时间线 Markdown 文件
    - 状态持久化到 `config/chat_monitor_state.json`
  - `app/server.py` — 新增 9 个 `/api/chat-monitor/*` 端点
    - `GET/POST /config` — 读取/保存监控配置
    - `POST /start`、`POST /stop` — 启动/停止后台调度器
    - `GET /status` — 查询运行状态（调度器、任务执行中、上次/下次运行时间）
    - `POST /run-now` — 立即触发一次监控
    - `POST /stop-task` — 停止当前正在运行的监控任务
    - `GET /files` — 列出已生成的监控 `.md` 文件
    - `POST /analyze` — 选取 `.md` 文件发送 LLM 分析（远程 API 或本地模型）
  - 前端侧栏新增「会话监控」Tab：启用开关、群聊多选列表（支持搜索）、调度参数、操作按钮、实时状态
  - LLM 分析报告 6 维度：核心话题、决策、成员发言要点、待办事项、风险问题、总结
  - 配置项：`chat_monitor.enabled / chats / schedule.mode / schedule.interval_seconds / schedule.scheduled_time / fetch.first_run_hours`

## v2.0.1 (2026-07-06)

### 新增
- **本地小模型集成**：支持 Qwen2.5-0.5B 本地推理，无需 API Key，完全离线运行
  - `src/local_llm.py` — 本地 LLM 推理引擎，基于 llama-cpp-python + GGUF 格式
  - 首次使用自动检测 CPU 指令集、安装依赖、下载模型（缓存到 models/）
  - 预编译 wheel 不兼容时自动从源码编译通用 x86-64 版本
- **三种 AI 模式**：无 AI（仅未回复待办）/ 本地模型（心得+风险）/ 远程 LLM（完整），互不影响
- **UI 日志实时同步**：新增 `LogQueueHandler` 将 Python logger 消息自动推送到前端 SSE 面板
- **模型预置分发**：模型文件（GGUF）随打包脚本自动包含进安装包，客户拿到即用
- **JSON 容错解析**：`_parse_llm_json` 三级降级提取（标准解析 → 常见错误修复 → 正则兜底），小模型输出不稳定也能尽量提取

### 变更
- **本地模型 prompt 优化**：0.5B 模型指令从 ~1200 字精简到 ~300 字，只要求输出心得+风险
- **本地模型 max_tokens 自动裁剪**：读取共享 max_tokens 配置，上限不超过上下文窗口
- **上下文窗口提升**：默认 n_ctx 从 4096 → 16384，避免 prompt 超限
- **批量推理加速**：n_batch=1024, n_ubatch=512

### 修复
- **打包脚本**：build_exe.bat / build_app.py / WeChatWorkAgent.spec 均添加 models/ 目录打包
- **路径兼容**：`_find_model_file()` 多路径查找模型文件（exe 同级 / _internal / 项目根目录）


## v2.0.0.1 (2026-07-03)

### 修复
- **深拷贝污染缓存导致按钮找不到文件**：`engine.py` 和 `server.py` 的配置复制改为 `copy.deepcopy`，防止运行日报时修改全局配置缓存
- **配置缓存 mtime 记录错误**：`_load_config` 从 example 创建 config.yaml 后，重新获取真实 mtime 写入缓存

### 变更
- **完全移除离线授权系统**：删除 `src/license.py`、`tools/gen_license.py`、`tools/batch_gen_license.py` 及对应 bat 脚本，V2.0 仅保留在线激活
- **打包脚本优化**：`build_app.py` 移除 `pycryptodome`、`Crypto` 相关依赖，`--hidden-import` 简化为 `--collect-submodules` 和 `--collect-all`
- **依赖补全**：`requirements_gui.txt`、`setup.bat` 添加 `cryptography`、`requests`（`license_client` 必需）
- **`tools/verify_key.py` 修复**：硬编码 PASSKEY 改为 `--key` 命令行参数
- **打包配置同步**：`WeChatWorkAgent.spec` 同步清理离线授权和 Crypto 依赖
- **`build_exe.bat` 修复**：所有 `pip` 命令改为 `%PYTHON% -m pip`，避免激活后 pip 不在 PATH 中的问题；同步移除 `pycryptodome`、`license`、`Crypto` 依赖，补全 `cryptography`、`requests`
- **`_gen_dist_files.ps1` 更新**：使用说明.txt 同步改为在线激活流程

## v2.0.0 (2026-07-03)

### 重大更新
- **授权系统升级**：从本地 license.dat 文件授权升级为在线激活码验证系统
  - 一机一码：硬件码绑定，不可多机共用
  - 一码一次：激活码激活后自动作废
  - 离线验证：RSA-2048 签名 Token，激活后完全离线运行
  - 多档位有效期：月卡/季卡/年卡/永久可选
- **新增在线激活管理后台**（https://43.143.121.172/admin）
  - 批量生成激活码、查看激活状态、吊销 License、导出 CSV
- **Web界面优化**：激活授权页面改为显示在线激活状态（剩余时间、到期日期、License ID）
- **CLI模式同步支持**：main.py 命令行入口新增激活验证

## v1.2.1.5 (2026-07-03)

### 新增
- **重要联系人功能**：群聊中按发送者名字匹配，重要联系人发言即使未@也保留分析
  - 屏蔽列表 Tab 新增「重要联系人」配置框
  - `src/message_filter.py` — 新增 `_is_from_important` 按发送者匹配
- **自动生成周报**：`config.weekly_auto=True` 时，周五自动运行后生成周报
  - `app/server.py` — 新增 `_maybe_auto_weekly` 函数
- **规则逻辑独立保存**：新增 `POST /api/config/rule` API，规则 Tab 不再耦合基本设置
- **浏览器目录浏览**：数据库目录"浏览"按钮现在正常调用系统文件夹选择对话框
- **"识别ID"按钮**：基本设置中填写数据库目录后，可自动提取 wxid
- **AI 总结失败红色日志**：LLM 无返回或返回为空时，前端日志区红色醒目提示

### 修复
- **屏蔽列表失效**：统一规范化+精确匹配，移除尾部空格/不可见字符干扰
  - `src/wechat_reader.py` + `src/message_filter.py` — 共用模块级 `_normalize_chat_name`
- **屏蔽列表缓存逃脱**：补充缓存后重新应用屏蔽列表过滤，不再假设"已通过无需重复检查"
- **手动/自动运行并发冲突**：新增 `_daily_run_lock` 互斥锁，防止同时执行
- **跨天日期破坏周报**：文件名统一取起始单日期，周报不再匹配不到跨天文件
- **engine.py 输出路径不一致**：绝对路径直接使用，相对路径才拼项目根目录
- **LLM 卡死无超时**：三个 LLM 调用点（日报/周报/会话分析）统一添加 90-120 秒超时
- **`config.yaml` 频繁读盘**：内存缓存 + mtime 失效，调度器 15 秒循环不再反复 IO
- **SSE 日志流空轮询 CPU 占用**：改为 `queue.get(timeout=2)` 阻塞读
- **无时间戳会话全部保留**：改为只保留前 10 个，大量无关会话不再拖慢查询
- **调度器快速重启线程重复**：`stop()` 改为 `join(timeout=3)` 等待线程退出
- **`all_todos.json` 异常损坏**：写临时文件 + `os.replace` 原子操作
- **`ChatAnalyzer` 重复创建 WeChatReader**：改为构造时创建一次实例复用
- **会话分析路径获取脆弱**：新增 `_last_analyze_report` 全局变量 + `GET /api/analyze/chat/latest` API，不再依赖从 SSE 日志正则提取

### 优化
- **统一 EXE 环境调用**：彻底移除 `getattr(sys, 'frozen', False)` 双路径分支，全部使用 `_run_wechat_cli_inprocess`
- **`_extract_json` 优先整体解析**：先 `json.loads(text)` 快路径，失败再降级到字符遍历
- **消息解析阶段多线程**：`_fetch_via_cli` 由 `max_workers=1` 改为 `max_workers=cpu_count()`，wechat_cli 调用串行但解析并行
- **Webpack 和 shutil/subprocess 依赖移除**：不再引入多余库

## v1.2.0 (2026-06-29)

### 新增
- **软件授权系统**：机器码绑定 + 加密 license.dat，防复制分发
  - `src/license.py` — AES-256 加密的授权验证模块
  - `tools/gen_license.py` — 开发者单个授权生成器
  - `tools/batch_gen_license.py` — Excel/CSV 批量授权生成器
  - `gen_license.bat` / `batch_gen_license.bat` — 快速生成脚本
- **输出格式选择**：基本设置可切换 Markdown (.md) / 纯文本 (.txt)，适用于日报、周报、会话分析
- **重点会话分析**：独立于日报，手动选择群聊/私聊进行全方面深度分析
  - `src/chat_analyzer.py` — LLM 全维度分析引擎（话题/决策/人员/待办/风险）
  - 侧栏「🔍 会话分析」Tab，支持多选搜索+已选清单

### 修复
- **密钥解密失败**：wx_key 提取的是 passphrase，需 PBKDF2-HMAC-SHA512 派生后才能用
  - `/api/init/manual-key` 新增 PBKDF2 派生 + HMAC 校验
- **密钥重启后消失**：`GET /api/config` 返回 `keys_configured`，前端显示"已配置 N 个数据库"
- **会话全部被过滤**：`_filter_recent_sessions` 时间字段优先级错误（`time` 字符串覆盖 `timestamp` 整数）
  - 修复：`timestamp` 优先 + 无年份字符串自动补当前年
- **公众号消息未屏蔽**：只检查展示名漏了 `username: gh_xxx` 
  - `_is_valid_chat` 同步检查 username，316 → 230 个有效会话（过滤 83 个公众号）
- **屏蔽规则改为精确匹配**：之前 substring 匹配会误伤（填"优层"拦所有含"优层"的），改为全名精确匹配
- **消息正则漏掉 [图片] / [撤回]**：新增格式B正则匹配无发送者的系统消息
- **私聊消息被忽略**：私聊无 @ 机制，`_mentions_me` 永远不触发 → 私聊自动检测未回复
  - 新增 `is_group` 字段传递，私聊全保留，未回复自动标记待办
- **AI 总结不调用 LLM**：`my_count == 0` 时直接返回空话，现在有未回复/提到我的消息也会调 LLM
- **LLM JSON 解析失败**：兼容 ````json ... ```` 包裹 + 记录原始返回用于诊断
- **EXE 环境 history 全部超时**：子进程启动开销过大，30s 不够
  - 新增 `_run_wechat_cli_inprocess` 进程内直接调用 wechat_cli，EXE 模式串行 2 线程
- **对话详情缺未回复消息**：`_write_chat_detail` 未回复部分从一行摘要 → 完整对话上下文
- **群聊总结带无关消息**：群聊只保留 @我 + 我发的消息，其余丢弃
- **--limit 100 太小**：history 查询改为 500，超限时日志告警
- **执行测试信息误导**："密钥有效（3+ 个会话）"改为"数据库解密成功，已读取到真实聊天内容"
- **时间组件兼容性**：`datetime-local` 在 PyWebView 嵌入浏览器中异常 → 改为日期+时间分离输入

### 优化
- 全面日志增强：wechat-cli 退出码+完整错误、会话数量、过滤详情、每会话无消息原因、发送者样本、my_name 匹配诊断
- 会话分析 Tab：进度实时显示 + 自动出现"打开最新报告"按钮
- 上报头部增加未回复计数
- 报告文件已移除临时预览路由 `/preview`

---

## v1.1.0 (2026-06-26)

### 新增
- **全新 UI 设计**：撑满窗口、等高分 Tab、首页仪表盘、侧栏 8 个功能 Tab
- **密钥配置独立 Tab**：重启后自动显示配置状态
- **首页独立仪表盘**：操作按钮 + 统计卡片 + 运行日志，不显配置参数

### 修复
- 首次打包在目标电脑密钥初始化失败（wechat-cli 不支持微信 4.1.10.53）
- 打包环境子进程调用失败（--windowed 模式 stdout=None）
- `all_keys.json` 格式与 wechat-cli 不兼容
- 时间过滤导致 309 个会话全被过滤
- 497 个会话串行查询极慢 → 并行 + 时间预过滤
