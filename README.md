# 微信工作日报助手

> 自动读取微信聊天记录，整理当日工作内容和待办事项，生成结构化日报。

## 功能

- 📥 **自动读取** — 从微信本地数据库读取当天的聊天消息
- 🔍 **智能过滤** — 自动识别"与我相关"的消息（@我、关键词、重要联系人）
- 🧠 **AI 摘要** — 支持 DeepSeek / OpenAI 等大模型智能提炼工作内容
- ✅ **待办提取** — 自动识别聊天中的任务和待办事项
- 📋 **日报生成** — 输出 Markdown 格式的工作日报
- ⏰ **定时运行** — 通过 Windows 任务计划程序每日自动执行

## 快速开始

### 1. 安装

双击运行 `setup.bat`，或手动执行：

```bash
python -m venv venv
venv\Scripts\pip install wechat-cli pyyaml openai
```

### 2. 初始化

确保**微信桌面版正在运行并登录**，然后：

```bash
venv\Scripts\python main.py --init
```

这会自动从微信进程内存中提取数据库解密密钥。

### 3. 配置

编辑 `config/config.yaml`，主要配置项：

```yaml
# LLM 配置（用于智能摘要，推荐）
llm:
  enabled: true
  provider: "deepseek"  # openai / deepseek / azure / custom
  api_key: "sk-xxxx"     # 你的 API Key
  model: "deepseek-chat"

# 重要联系人
monitor:
  important_contacts:
    - "老板"
    - "项目经理"
  exclude_chats:
    - "文件传输助手"
```

> 不配置 LLM 也能运行，会使用关键词规则模式。

### 4. 手动运行

```bash
venv\Scripts\python main.py
```

### 5. 设置定时任务

以**管理员身份**运行 `install_scheduler.bat`，配置每天下午6点自动生成日报。

或手动创建：

1. 打开"任务计划程序"
2. 创建基本任务
3. 触发器：每天，时间选下班前（如 18:00）
4. 操作：启动程序 → `venv\Scripts\python.exe main.py`
5. 起始于：项目根目录

## 命令参考

```bash
# 生成今日日报
python main.py

# 指定日期
python main.py --date 2026-06-17

# 自定义配置
python main.py --config my_config.yaml

# 初始化 wechat-cli
python main.py --init

# 持续监听模式
python main.py --watch --interval 30

# 查看帮助
python main.py --help
```

## 输出示例

生成的文件在 `output/` 目录下，格式为 Markdown，内容包含：

```
# 📋 微信工作日报 - 2026-06-18（周三）

## 📝 今日摘要
今天处理了项目A的需求确认，和产品团队讨论了UI方案...

## ✅ 待办事项
1. 完成项目A的需求文档
2. 回复客户关于版本发布的邮件

## 💬 关键讨论
- 项目A UI方案敲定使用新组件库

## ⏱ 消息时间线
| 时间 | 会话 | 发送者 | 消息 |
| 10:30 | 项目群 | 张三 | @你 需求确认好了吗？ |
```

## 技术原理

微信 Windows 版将聊天记录存储在 SQLCipher 加密的 SQLite 数据库中。`wechat-cli` 会从 WeChat.exe 进程内存中提取解密密钥，然后透明解密数据库供查询。

- 所有数据均在本地处理，**不会上传云端**
- 需要微信桌面版正在登录运行
- 如更换微信版本，只需重新运行 `--init`

## 目录结构

```
wechat-work-agent/
├── main.py              # 主入口
├── config/
│   └── config.yaml      # 配置文件
├── src/
│   ├── wechat_reader.py  # 微信消息读取
│   ├── message_filter.py # 消息过滤分类
│   ├── summarizer.py     # AI/规则摘要
│   └── report_generator.py # 日报生成
├── output/              # 日报输出目录
├── logs/                # 日志
├── setup.bat            # 一键安装脚本
└── install_scheduler.bat # 定时任务安装脚本
```

## 注意事项

1. **仅供个人使用**，请合理使用
2. 需要微信桌面版保持登录状态
3. 微信更新后可能需要重新 `--init`
4. API Key 请保管好，不要提交到代码仓库
5. 建议在 config.yaml 中排除不必要的群聊以减少噪声
