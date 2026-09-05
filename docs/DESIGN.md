# DESIGN.md —— workspace-guard 设计文档

## 1. 背景与目标

ZCode（类 Claude Code 的 AI 编码客户端）在"完全访问"等权限模式下会直接
执行 AI 生成的命令与文件写入。用户需要一道**确定性**的安检层：

- 毁灭性操作（`rm -rf /`、抹盘、fork 炸弹等）无论什么权限模式都必须人工确认；
- 文件修改默认限制在项目内进行（借鉴 deepseek-harness 的
  `workspace-write` 沙箱语义），越界写入需人工确认；
- 判定必须是**纯规则匹配**，不依赖 AI 判断代码语义——避免同类产品
  （如基于 AI 扫描的 guardrail 插件）高误报打断开发流的问题。

设计原则：**宁可漏报不可误报**。守门脚本对日常开发命令零干扰。

## 2. 能力总览

| 能力 | 开关（userConfig） | 默认 | 触发动作 |
| --- | --- | --- | --- |
| 危险命令门 | `danger_gate_enabled` | 开 | Bash 命令命中规则清单（内置规则 + 自定义正则）→ `ask` 人工确认 |
| 项目围栏 | `sandbox_enabled` | 开 | 文件工具界内 → `allow`（免重复询问）；界外 → `ask`；Bash 越界写启发式 → `ask` |

两能力相互独立，各自可关。规则清单 `danger_rules`（分号分隔：内置
规则 ID 与自定义正则）仅在危险命令门开启时生效；默认启用全部内置
规则，用户可删除不想要的段（协议见 §6.1）。围栏另有额外可写根
`extra_writable_roots`（分号分隔目录，项目多文件夹场景，见 §5.2）。

## 3. 与宿主（ZCode）的关系与硬约束

- 实现载体是 ZCode 插件的 **PreToolUse hook**：matcher
  `Bash|Write|Edit|ApplyPatch`（`Write`/`Edit` 是 `ApplyPatch` 的别名），
  `process` 型子进程，每次工具调用前执行 `guard.py`
- hook 可输出 `permissionDecision: allow / ask / deny`，**优先于宿主权限
  模式**：即使在"完全访问"模式下，`ask` 依然强制弹出人工确认——这是本
  插件不依赖面板档位也能生效的机制基础
- **硬约束**：插件 API 无法向宿主权限模式面板添加新档位（manifest 的
  `settings` 等字段"只记录不执行"）。因此本插件不做独立模式体系，而是
  以**常驻行为层**与面板任意模式叠加：面板选完全访问时，围栏就是项目
  边界；面板选常规模式时，界内 `allow` 会跳过宿主例行询问（日常编辑
  零打断），界外仍需确认
- 危险命令门**不受任何模式/开关影响其"存在性"**：要临时全关只能禁用
  整个插件（或关 `danger_gate_enabled` 配置）

## 4. 目录结构

```
zcode-workspace-guard/
├── marketplace.json                     # marketplace 清单（仓库根即 marketplace）
└── plugins/workspace-guard/
    ├── .zcode-plugin/plugin.json        # 清单 + userConfig 三项
    ├── hooks/hooks.json                 # hook 注册
    ├── scripts/guard.py                 # 唯一守门脚本（全部判定逻辑）
    └── skills/workspace-guard/SKILL.md  # 教宿主 agent 理解拒绝标记
```

## 5. guard.py 协议

### 5.1 配置取值链（每次调用现读，改配置即生效）

```
1. 环境变量 ZCODE_WORKSPACE_GUARD_CONFIG   # 指向 JSON 文件（测试/开发注入用）
2. ~/.zcode/cli/config.json 的 plugins 段  # 宿主 userConfig 持久化位置（键名以实测为准）
3. 内置默认值                               # sandbox_enabled=true, danger_gate_enabled=true, danger_rules=""（空=启用全部内置规则）
```

高优先级来源存在某键时覆盖低优先级同名键；全部异常（文件不存在/解析
失败）时落到默认值。项目根从环境变量 `ZCODE_PROJECT_DIR`（兼容
`CLAUDE_PROJECT_DIR`）取，realpath 规范化；缺失时**围栏失效但危险门
照常工作**（降级而非阻塞）。

### 5.2 可写根（围栏判定唯一依据）

```
writable_roots = dedupe([
    realpath(项目根),        # ZCODE_PROJECT_DIR（宿主注入的是会话工作目录）
    realpath("/tmp"),
    realpath($TMPDIR),         # macOS 通常是 /var/folders/...
    *额外可写根               # extra_writable_roots：分号分隔绝对路径，~ 展开；
                             # 默认空，不配置则无此项
])
```

所有路径先规范化再比较。`/dev/null` 额外视为合法写入目标。

**项目多文件夹**：实测（zcode.cjs）宿主将**会话工作目录**注入为
`ZCODE_PROJECT_DIR`，插件拿不到项目在 ZCode 中设置的文件夹清单——
项目下并列多个文件夹（如多个子项目）且任务工作目录在其中之一时，
其余文件夹会被围栏判为界外。此场景由用户在 `extra_writable_roots`
中显式声明（如项目根文件夹），不做自动推断（曾考虑"向上找含
`.zcode` 的祖先"，但`home` 目录同样含 `.zcode`，会把`home` 目录静默纳入
可写根，否决）。

实测补充（宿主 v3.11.2，PreToolUse 载荷全量字段盘点）：stdin 含
`cwd`、`hook_event_name`、`tool_name`、`tool_input`、`tool_use_id`、
`permission_mode`、`session_id`、`transcript_path`、`riskLevel`、
`sideEffectScope`、`timestamp`、`toolCallId`、`traceId`、`turnId`、
`agent_type`——**无「项目所配置文件夹」的任何字段**；环境变量中项目
路径仅 `ZCODE_PROJECT_DIR`（= 会话 cwd）。同一项目下不同任务的 cwd
可以不同（可为子目录），故其余文件夹的纳入只能走
`extra_writable_roots`，待宿主未来提供项目文件夹信息再自动化。

### 5.3 判定分支

**Bash**（`tool_input.command`）：

1. 危险门开启：解析 `danger_rules` 清单（§6.1）——启用的内置规则命中
   → `ask`；自定义正则逐行编译（失败跳过该行），命中 → `ask`
2. 围栏开启：越界写启发式命中 → `ask`
3. 否则不输出决策（放行给宿主按其模式处理）

**文件工具**（Write/Edit/ApplyPatch，`tool_input.file_path`，兼容 `path`）：

1. 围栏关闭：不输出决策
2. 项目根缺失（降级）：不输出决策
3. 目标路径规范化后在可写根内 → `allow`
4. 界外 → `ask`（reason 附可写根列表，引导模型改用界内路径或向用户申请）

### 5.4 输出格式（严格 schema，多余 key 会被宿主拒绝）

```json
{"hookSpecificOutput": {"hookEventName": "PreToolUse",
                        "permissionDecision": "ask|allow",
                        "permissionDecisionReason": "[workspace-guard: …原因…（中文）]"}}
```

不命中时无输出、exit 0。stdin 解析失败或内部异常：无输出、exit 0
（fail-open）。

## 6. 规则明细

### 6.1 危险命令规则清单（`danger_rules`）

危险命令门由**规则清单**驱动。**段以分号 `;` 分隔**——设置界面是
单行输入框，保存会剥掉值中的换行符，换行不能作分隔符；手编
config.json 时换行分隔同样接受（宽容解析）。默认值为单行的全部
内置规则 ID，不含注释与换行，保证 UI 保存往返不损坏清单。

- 空白段与 `#` 开头的注释段跳过（整串 `#` 开头即无任何规则）
- 等于内置规则 ID 的段 → 启用对应内置判定（见下表）
- 其余段 → 自定义正则（编译失败跳过），命中命令文本即 `ask`；
  正则内不能包含分号（分号是分隔符）
- **值为空或缺失 = 启用全部内置规则**（安全默认；清空即重置为全默认）
- 拼错的 ID 会被当作正则处理（通常永不命中），等效于停用该规则

> 历史陷阱（0.2.0 → 0.3.0 修复）：早期默认值用换行分隔并带 `#`
> 注释头，经 UI 保存后换行被剥、整串因 `#` 开头被当注释跳过，危险门
> 静默变为零规则。0.3.0 起默认值改为分号分隔单行，且元测试强制
> 默认值不含换行与 `#`（TEST_PLAN.md M5）。

内置规则（token 级精确匹配，目标须与危险模式完全相等）：

| 规则 ID | 类别 | 模式 |
| --- | --- | --- |
| `fork-bomb` | fork 炸弹 | `:(){ :|:& };:` 变体（容空白） |
| `dd-device` | 抹设备 | `dd` + `of=/dev/…` |
| `mkfs` | 抹设备 | `mkfs(.*)` |
| `diskutil-wipe` | 抹设备 | `diskutil eraseDisk\|eraseVolume\|deleteContainer` |
| `no-preserve-root` | rm 旗标逃逸 | 命令含 `--no-preserve-root` |
| `rm-destructive` | rm 家族 | 命令含 `rm`（或 `*/rm`）+ 递归/强制旗标（`-[rf…]`/`--recursive`/`--force`）+ 危险目标 token：`/`、`//`、`/*`、`~`、`~/`、`~/*`、`~/.`、`$HOME`、`${HOME}`、`*`、`**`、`.*`、`./*`、`*/*`、`.`、`./`、`..`、`../`、`/Users/<name>`（整级`home` 目录） |
| `chmod-recursive` | 权限污染 | `chmod`/`chown` + `-R` + 危险目标 token（根/`home` 目录级） |

日常放行（必须零误报）：`rm -rf node_modules`、`rm -rf <具体路径>`、
`rm -rf packages/*/dist`、`chmod -R 755 ./src` 等。

### 6.2 Bash 越界写启发式（enforcement: heuristic，如实标注）

- 重定向：`>` / `>>` / `&>` 后跟**绝对路径**目标（`/…`、`~/…`、
  `$HOME/…`），规范化后不在可写根且非 `/dev/null` → `ask`
- 命令目标：`cp`/`rsync`/`install` 检查目的参数（末个非旗标参数）；
  `mv`/`rm`/`tee` 检查全部参数；参数为绝对路径且界外 → `ask`
- 相对路径目标不检测（无可靠 cwd，避免误报）；只认显式绝对路径
- 误报代价是 `ask`（人工看一眼），不是 `deny`，与产品原则一致

### 6.3 路径规范化（防 TOCTOU）

目标文件可能尚不存在：自目标起**逐级上溯找最深存在的祖先**，
realpath 后拼回剩余段，再做包含比较（包含比较：`p == root` 或
`p.startswith(root + os.sep)`）。

## 7. 安全模型与 fail-open 决策

- 守门层故障（stdin 异常、内部错误、超时）选择 **fail-open**：守门脚本
  是"额外防线"而非"唯一防线"，它的崩溃不应瘫痪整个编码会话；宿主的
  权限体系仍然在场
- 危险门语义是 `ask`（人工确认）而非 `deny`（直接拒绝）：确认框就是
  升权通道（对应 deepseek-harness 的 approveEscalation），用户批准一次
  放行一次
- 诚实声明：Bash 层是启发式（可被编码/别名绕过），不宣称安全边界；
  文件层是精确判断

## 8. 行为矩阵

| 操作 | 危险门（开） | 围栏（开）：界内 | 围栏（开）：界外 | 全部关闭 |
| --- | --- | --- | --- | --- |
| `rm -rf *` 等危险命令 | `ask` | —（危险门优先） | `ask` | 放行 |
| 文件工具写项目内 | 不适用 | `allow`（跳过宿主例行询问） | — | 放行 |
| 文件工具写`home` 目录/系统路径 | 不适用 | — | `ask` | 放行 |
| `echo x > /tmp/f` | 放行 | 放行（/tmp 在可写根） | — | 放行 |
| `echo x > ~/某文件` | 放行 | — | `ask` | 放行 |
| `rm -rf node_modules` | 放行 | 放行 | — | 放行 |

## 9. 已知局限

1. 无法扩展宿主权限模式面板（插件 API 不支持），以常驻行为层等价实现
2. Bash 越界检测为启发式 best-effort；文件工具围栏为精确判断
3. `${user_config.*}` 对 hook 的展开官方未文档化，配置读取以 config.json
   直读兜底，键名在安装后实测确认
4. 项目根依赖宿主注入的 `ZCODE_PROJECT_DIR` 环境变量；缺失时围栏降级
   （危险门不受影响）
