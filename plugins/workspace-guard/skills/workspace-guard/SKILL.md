---
name: workspace-guard
description: Use when a tool call is denied or questioned with a "[workspace-guard: …]" marker in the reason, or when planning file writes and shell commands that may touch paths outside the current workspace. Explains the workspace fence (writes allowed inside the workspace and temp dirs) and the dangerous-command gate (destructive commands always require human confirmation), and how to respond correctly instead of retrying blindly.
---

# workspace-guard 行为指南

本环境运行 workspace-guard 守门插件：所有 Bash 命令与文件写入在执行前
经过确定性规则检查。当你的操作被附上 `[workspace-guard: …]` 标记时，
按以下语义响应，**不要盲目重试同一操作**。

## 可写范围（项目围栏）

- 允许写入：当前项目根目录、`/tmp`、`$TMPDIR`
- 越界写入（家目录、系统路径、其他项目）会触发 `ask` 人工确认——确认框
  就是用户的审批通道：用户批准则这一次放行，拒绝则你必须换方案
- 收到"写入目标在项目外"时，首选做法是**改用项目内路径**（如把
  输出写到项目下的临时文件）；确需越界时向用户说明理由，由确认框决定
- Bash 中 `>` 重定向、`cp`/`mv`/`rm`/`tee` 作用于项目外绝对路径同样
  会被检出（启发式）

## 危险命令门（常开）

毁灭性命令（`rm -rf /`、`rm -rf ~`、`rm -rf *`、`dd of=/dev/…`、
`mkfs`、`diskutil eraseDisk`、fork 炸弹、`chmod -R` 作用于根/家目录等）
一律触发人工确认，与权限模式无关。这类操作没有绕过方式——向用户展示
完整命令并等待确认结果。

## 标记速查

| reason 关键词 | 含义 | 正确响应 |
| --- | --- | --- |
| `危险命令需要人工确认` | 命中毁灭性规则 | 展示完整命令，等用户在确认框决定 |
| `自定义危险规则命中` | 用户配置的规则命中 | 同上 |
| `写入目标在项目外` | 文件工具越界 | 改用项目内路径，或请用户批准 |
| `疑似越界写入（启发式）` | Bash 越界写启发式命中 | 核实目标路径；改界内或请用户批准 |
| `项目内写入，放行` | 界内正常写入 | 无需任何动作 |

注意：围栏对文件工具是精确判断，对 Bash 是启发式（best-effort）。即便
启发式未命中，也应当遵守"写入尽量留在项目内"的约定。
