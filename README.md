# zcode-workspace-guard

ZCode 插件 **workspace-guard**：以确定性 PreToolUse hook 为 ZCode 提供两个
常驻防护能力，纯规则匹配，无 AI 判断、无网络、无第三方依赖。

- **工作区围栏**（默认开）：文件写入限制在工作区与临时目录（`/tmp`、
  `$TMPDIR`）内；界内直接放行（并跳过宿主例行询问，日常编辑零打断），
  界外触发人工确认。Bash 命令的越界写入（重定向 / `cp` / `mv` / `rm` /
  `tee` 作用于界外绝对路径）做启发式检出。
- **危险命令门**（默认开）：毁灭性命令（`rm -rf /`、`rm -rf ~`、
  `rm -rf *`、`dd of=/dev/…`、`mkfs`、`diskutil eraseDisk`、fork 炸弹、
  `chmod -R` 根/家目录、`--no-preserve-root` 等）无论宿主处于何种权限
  模式一律强制人工确认。

设计原则：**宁可漏报不可误报**——`rm -rf node_modules`、
`rm -rf <具体路径>`、`chmod -R 755 ./src` 等日常命令零干扰。
完整设计见 [docs/DESIGN.md](docs/DESIGN.md)。

## 安装

仓库根本身就是 marketplace（含 `marketplace.json`）。

**从本地目录**：ZCode → Settings → Plugin Management → Discover →
`+` → 选择本仓库克隆目录 → 安装 `workspace-guard`。

**从 GitHub**：`+` 处填入 `https://github.com/XieZongChen/zcode-workspace-guard`
→ 安装 `workspace-guard`。

安装/启停后需**新建任务**（hook 与 MCP 配置在任务启动时快照）。

## 配置

插件详情 → Advanced，三项配置（持久化于 `~/.zcode/cli/config.json`，
改动对新工具调用即时生效）：

| 配置 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `sandbox_enabled` | boolean | `true` | 工作区围栏开关 |
| `danger_gate_enabled` | boolean | `true` | 危险命令门开关 |
| `custom_danger_rules` | string | 空 | 一行一条正则，Bash 命令文本命中即人工确认；仅门开启时生效。例：`git\s+push\s+--force` |

## 行为矩阵

| 操作 | 门（开） | 围栏（开）：界内 | 围栏（开）：界外 |
| --- | --- | --- | --- |
| `rm -rf *` 等危险命令 | 人工确认 | — | 人工确认 |
| 文件工具写工作区内 | 不适用 | 放行（免例行询问） | — |
| 文件工具写家目录/系统路径 | 不适用 | — | 人工确认 |
| `echo x > /tmp/f` | 放行 | 放行 | — |
| `echo x > ~/某文件` | 放行 | — | 人工确认 |
| `rm -rf node_modules` | 放行 | 放行 | — |

## 已知局限（如实声明）

1. 插件 API 无法向宿主权限模式面板添加新档位；本插件以常驻行为层与
   面板任意模式叠加的方式等价实现
2. Bash 越界检测为启发式 best-effort（enforcement: heuristic）；文件
   工具围栏为精确判断（enforcement: full）
3. 守门脚本自身故障时 fail-open（放行），它是额外防线而非唯一防线

## 开发

```bash
python3 tests/run_tests.py   # 全量回归（51 例），提交前必须全绿
```

工作约定见 [AGENTS.md](AGENTS.md)，测试规划见
[docs/TEST_PLAN.md](docs/TEST_PLAN.md)。

## 从独立 danger_guard hook 迁移

若你此前在 `~/.zcode/cli/config.json` 的 `hooks` 块中配置过独立的
危险命令守门脚本：安装本插件后请删除该 hooks 块，避免同一条命令双重
弹窗。本插件的危险命令门覆盖并取代其全部规则。

## License

[MIT](LICENSE)
