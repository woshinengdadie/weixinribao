---
name: git-init-and-push
overview: 配置 Git 身份、排除大文件、首次 commit 并推送到 GitHub 仓库 woshinengdadie/weixinribao
todos:
  - id: git-config
    content: 配置 Git 提交身份：user.name=woshinengdadie, user.email=woshinengdadie@users.noreply.github.com
    status: pending
  - id: gitignore-update
    content: 更新 .gitignore，追加 models/*.gguf 和 installer_output/ 排除规则
    status: pending
  - id: rm-cached
    content: 从 Git 索引移除大文件：git rm --cached models/qwen2.5-0.5b-instruct-q4_k_m.gguf 和 installer_output/WeChatWorkAgent_Setup_v2.0.1.14.exe
    status: pending
    dependencies:
      - gitignore-update
  - id: commit
    content: "首次提交：git commit -m \"Initial commit: WeChatWorkAgent v2.0.1.14\""
    status: pending
    dependencies:
      - rm-cached
  - id: remote-and-push
    content: 关联远程仓库、拉取合并（保留本地 README）、推送到 main 分支
    status: pending
    dependencies:
      - commit
---

## 任务概述

将项目代码首次提交到 Git 并推送到 GitHub 远程仓库 `woshinengdadie/weixinribao`。

## 前置条件

- 远程仓库已存在，仅有 1 个初始 README commit
- 本地所有文件已 `git add`，但从未 commit
- 暂存区包含大文件需排除

## 核心操作

- 配置 Git 提交身份
- 更新 `.gitignore` 排除大文件（models/*.gguf、installer_output/）
- 从暂存区移除大文件
- 首次 commit
- 关联远程仓库、拉取合并、推送