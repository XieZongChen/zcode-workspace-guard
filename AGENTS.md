# AGENTS.md —— AI 编码代理工作约定

面向在本仓库工作的 AI 编码代理（同样适用于人类贡献者）。改动代码前先读
`DESIGN.md` 对应章节，测试相关决策以 `TEST_PLAN.md` 为准。

## 这个仓库是什么

`workspace-guard` 是一个 ZCode 插件：通过确定性的 PreToolUse hook 给 ZCode
提供两个常驻能力——**工作区围栏**（文件写入限制在工作区与临时目录内）与
**危险命令门**（毁灭性 shell 命令强制人工确认）。纯规则匹配，无 AI 判断、
无网络、无第三方依赖。

## 仓库地图

| 路径 | 内容 |
| --- | --- |
| `DESIGN.md` | 设计文档：能力定义、guard.py 协议、行为矩阵、已知局限（改代码前必读） |
| `TEST_PLAN.md` | 自动化测试规划：用例矩阵、回归纪律、扩展方法（加功能前必读） |
| `plugins/workspace-guard/scripts/guard.py` | 唯一守门脚本，全部判定逻辑在此 |
| `plugins/workspace-guard/hooks/hooks.json` | hook 注册（PreToolUse, matcher `Bash\|Write\|Edit\|ApplyPatch`） |
| `plugins/workspace-guard/.zcode-plugin/plugin.json` | 插件清单与 userConfig 三项声明 |
| `plugins/workspace-guard/skills/workspace-guard/SKILL.md` | 教会宿主 agent 理解拒绝标记 |
| `tests/run_tests.py` | 自动化测试入口（一条命令全量回归） |
| `tests/cases/*.json` | 测试用例数据，与 TEST_PLAN 用例矩阵一一对应 |
| `marketplace.json` | 本地/远端 marketplace 清单（仓库根即可作为 marketplace 添加） |

## 一条命令跑测试

```bash
python3 tests/run_tests.py
```

退出码 0 = 全绿。**任何提交之前必须全绿**，不允许跳过用例或削弱断言来
"让测试通过"。

## 代码约束

- `guard.py` 是唯一脚本：仅用 Python 标准库，兼容 Python 3.9+，不拆分文件
  （hook 以子进程方式调用它，自包含降低故障面）
- **fail-open**：stdin 解析失败、内部异常时静默放行（exit 0、无输出），
  守门脚本自身故障不应阻塞宿主会话。这是有意的设计决策，见 DESIGN.md §7
- **不写入个人信息**：任何路径动态解析（环境变量/临时目录），禁止硬编码
  绝对机器路径、用户名、邮箱；文档使用相对路径或占位符
- 输出协议严格：stdout 只能输出 `hookSpecificOutput` 结构（见 DESIGN.md §5.4），
  多余 key 会被宿主校验拒绝
- 判定规则**宁可漏报不可误报**：只拦确定毁灭性/明确越界的操作，日常开发
  命令（如 `rm -rf node_modules`）必须零干扰

## 工作流程（文档先行 · 测试驱动）

1. 新功能/新规则：先更新 `DESIGN.md` 对应章节，再在 `TEST_PLAN.md` 用例
   矩阵中登记用例，然后在 `tests/cases/` 增加用例数据
2. 实现/修改 `guard.py`
3. `python3 tests/run_tests.py` 全量回归（新用例 + 既有回归集）全绿
4. 提交：conventional commits（`feat(guard): …` / `fix(guard): …` /
   `docs: …` / `test: …` / `chore: …`），一个功能一个提交，随后 push

## 已知硬约束（不要试图绕过）

- ZCode 插件 API **无法**向宿主权限模式面板添加新档位；本插件以
  "常驻行为层与面板任意模式叠加"的方式等价实现（详见 DESIGN.md §3）
- Bash 越界写检测是启发式（enforcement: heuristic），文件工具围栏是
  精确判断（enforcement: full）。如实标注，不要夸大强制能力
- `${user_config.*}` 模板变量在 hook 中的展开行为官方未文档化，配置读取
  走三级取值链（见 DESIGN.md §5.1），以实测为准
