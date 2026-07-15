# 生成分发辅助文件（UTF-8 BOM 编码）
param([string]$DistDir="dist\WeChatWorkAgent")

$distDir = Resolve-Path $DistDir -ErrorAction SilentlyContinue
if (-not $distDir) {
    $distDir = Join-Path (Get-Location) $DistDir
}
$distDir = $distDir.ToString()

$enc = [System.Text.UTF8Encoding]::new($true)
$nl = [Environment]::NewLine

Write-Host "Generating distribution files in: $distDir"

# ===================== 使用说明.txt =====================
$readme = @'
微信工作日报助手 - 使用说明
============================

【第一步：准备 wx_key 工具】
  将本目录下的 wx_key 文件夹，复制到非 C 盘的根目录下
  （例如：D:\wx_key\ 或 E:\wx_key\）

【第二步：安装依赖环境】
  进入复制后的 wx_key 文件夹，双击运行 vc_redist.x64.exe
  根据安装向导提示，完成 Visual C++ 运行库的安装

【第三步：获取微信密钥】
  在同目录下运行 wx_key.exe，根据程序提示操作
  （程序会自动关闭正在运行的微信，并重启微信）
  正常登录微信即可，随后工具会显示获取到的密钥

【第四步：保存密钥】
  复制 wx_key 工具显示的密钥（passphrase，一串十六进制字符串）
  妥善保存，后续配置需要用到

【第五步：启动软件】
  回到本目录，双击运行 WeChatWorkAgent.exe
  首次启动会弹出激活码输入框，请输入管理员提供的激活码完成激活
  （格式: XXXX-XXXX-XXXX-XXXX）

【第六步：配置密钥】
  进入软件主界面后，点击左侧菜单「密钥配置」
  在「手动密钥配置」区域，将第四步保存的 passphrase 粘贴到输入框
  点击「配置密钥」按钮，等待提示「密钥配置成功」

【第七步：软件设置】
  按下方说明完成基本配置后即可正常使用


----------------------------------------
  软件功能说明
----------------------------------------

▶ 基本设置（左侧菜单第一个）
  - 微信昵称（必填）：填写你自己的微信昵称，用于识别哪些消息是你发出的
  - AI 配置（可选）：填写 API Key + Base URL + 模型名称，启用 AI 智能总结
  - 输出格式：选择日报输出为 Markdown (.md) 或纯文本 (.txt)
  - 屏蔽列表：添加不需要监控的联系人或群聊名称

▶ 手动运行
  立即分析今天的微信聊天记录，生成一份包含工作内容总结和待办事项的日报
  输出文件保存在 output/手动运行_年月日_时分秒/ 目录下

▶ 自动运行
  无需手动操作，软件会在设定的时间自动分析聊天记录并生成日报
  默认时间为每天 17:30（工作日下班前）

▶ 定时任务
  自定义自动运行的时间表和频率
  支持设置每天、工作日、特定时间等

▶ 规则逻辑
  自定义 AI 分析的指令，用自然语言描述你希望 AI 关注的方面
  例如：「重点关注项目进度、客户需求和风险点」

▶ 会话分析
  选择一个或多个群聊/联系人，进行全方位的深度分析
  分析内容包括：话题总结、决策记录、人员动态、待办事项、风险识别

▶ 周报生成
  根据一周的聊天记录，自动生成周报总结
  可在定时任务中设置每周五自动生成


----------------------------------------
  常见问题
----------------------------------------

Q: 微信更新后无法读取消息？
A: 用 wx_key 重新提取 passphrase，在基本设置中重新配置密钥即可

Q: API 调用失败？
A: 检查 API Key 和 Base URL 是否正确，网络是否通畅

Q: 找不到聊天记录？
A: 确认基本设置中「微信昵称」是否填写正确
   确认微信桌面版已登录且有聊天记录

Q: 激活失败？
A: 确认激活码格式为 XXXX-XXXX-XXXX-XXXX，联系管理员获取有效激活码
   如果更换了电脑，需要在新的电脑上重新激活
'@
[System.IO.File]::WriteAllText("$distDir\使用说明.txt", $readme, $enc)
Write-Host "  Generated: 使用说明.txt"

Write-Host "All distribution files generated with UTF-8 BOM encoding."
