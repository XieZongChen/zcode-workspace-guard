# TEST_PLAN.md —— 自动化测试规划

## 1. 测试架构

- 入口：`python3 tests/run_tests.py`（唯一测试命令，退出码 0/1）
- 方式：**进程边界测试**——用 `subprocess` 以真实 hook 调用方式执行
  `plugins/workspace-guard/scripts/guard.py`，stdin 喂 JSON 载荷，断言
  stdout 的 `hookSpecificOutput` 决策与 reason 关键词。不 import 被测
  模块，测的就是宿主实际运行的东西
- 用例数据：`tests/cases/*.json`，与本文用例矩阵一一对应；每个用例：

```json
{"name": "危险rm根目录", "tool_name": "Bash",
 "tool_input": {"command": "rm -rf /"},
 "expect": {"decision": "ask", "reason_contains": "危险"}}
```

- **路径占位符**（运行时解析，用例文件不含任何真实机器路径/个人信息）：
  - `{WS}` —— 运行时创建的临时项目目录（同时注入
    `ZCODE_PROJECT_DIR`）
  - `{OUT}` —— 项目之外的临时目录（界外路径构造用）
  - `{TMP}` —— 真实 `/tmp`（可写根内路径构造用）
  - `{HOME}` —— 运行机器的真实家目录（界外路径构造用，不落盘进用例文件）
- **配置注入**：用例可带 `"config": {...}`，运行器写入临时 JSON 并设置
  `ZCODE_WORKSPACE_GUARD_CONFIG`（走取值链第 1 级，测试封闭、不触碰
  宿主真实配置）
- **环境隔离**：guard 子进程的 `TMPDIR` 被重定向到运行期受控目录
  （否则 `{OUT}` 会天然落在可写根 `$TMPDIR` 内导致围栏用例失效）；
  测试进程注入最小 env（PATH/HOME/受控 TMPDIR +
  ZCODE_PROJECT_DIR/ZCODE_WORKSPACE_GUARD_CONFIG），不继承无关变量

## 2. 用例矩阵

### M1 冒烟（cases/smoke.json）

| 编号 | 用例 | 期望 |
| --- | --- | --- |
| S1 | 合法载荷、非目标工具（如 `Read`） | 无输出，exit 0 |
| S2 | stdin 非法 JSON | 无输出，exit 0（fail-open） |
| S3 | Bash 空命令 | 无输出，exit 0 |
| S4 | 文件工具载荷但缺 file_path | 无输出，exit 0 |

### M2 危险命令门（cases/danger_gate.json）

| 编号 | 用例 | 期望 |
| --- | --- | --- |
| D1–D13 | `rm -rf /`、`rm -rf *`、`sudo rm -rf ~`、`rm -rf ~/*`、`rm -rf .`、`rm -rf $HOME`、`cd /tmp && rm -rf /*`、`dd if=/dev/zero of=/dev/disk3`、`mkfs.apfs /dev/disk2s1`、`diskutil eraseDisk APFS T /dev/disk4`、`:(){ :\|:& };:`、`sudo chmod -R 777 /`、`rm -rf --no-preserve-root /` | `ask`，reason 含"危险" |
| D14 | `rm -rf /Users/<某用户名>`（整级家目录） | `ask` |
| D15–D20 | 日常放行回归：`rm -rf node_modules && pnpm install`、`rm -rf {TMP}/test-dir`、`rm -rf apps/daemon/tmp`、`rm -rf packages/*/dist`、`chmod -R 755 ./src`、`git push --force origin main` | 无输出 |

### M3 项目围栏-文件工具（cases/file_fence.json）

| 编号 | 用例 | 期望 |
| --- | --- | --- |
| F1 | Write 界内文件 `{WS}/src/a.ts` | `allow` |
| F2 | Edit 界内深层且父目录不存在 `{WS}/a/b/c/new.ts` | `allow`（逐级上溯规范化） |
| F3 | Write 界外 `{OUT}/evil.txt` | `ask`，reason 含"项目"与可写根提示 |
| F4 | Write 界外家目录路径（`~/xx` 展开为真实家目录，非 `{WS}` 内） | `ask` |
| F5 | Write `/tmp/scratch.txt` | `allow` |
| F6 | tool_input 用 `path` 键而非 `file_path` | 同 F1 语义 |
| F7 | ApplyPatch 工具名 | 同 F1 语义 |
| F8 | 界内符号链接指向界外（运行器创建） | `ask`（TOCTOU 防护） |

### M4 Bash 越界启发式（cases/bash_heuristic.json）

| 编号 | 用例 | 期望 |
| --- | --- | --- |
| B1 | `echo x > {OUT}/f` | `ask` |
| B2 | `echo x >> ~/desktop-note` | `ask` |
| B3 | `echo x > {TMP}/f` | 无输出（可写根内） |
| B4 | `cmd 2>/dev/null > {WS}/log` | 无输出（/dev/null 白名单 + 界内） |
| B5 | `cp {WS}/a {OUT}/b` | `ask`（界外目的） |
| B6 | `cp {OUT}/a {WS}/b` | 无输出（界外仅作源，读不拦） |
| B7 | `mv {WS}/a {OUT}/b` | `ask`（mv 双侧都算写） |
| B8 | `rm {OUT}/somefile` | `ask` |
| B9 | `tee {OUT}/log` | `ask` |
| B10 | `cp src/a.ts dist/b.js`（相对路径） | 无输出（不检测相对路径） |
| B11 | `npm run build > {WS}/out.log` | 无输出（界内） |

### M5 配置取值链（cases/config_chain.json）

| 编号 | 用例 | 期望 |
| --- | --- | --- |
| C1 | config 注入 `danger_gate_enabled=false` + fork 炸弹（无路径特征） | 无输出（门关，围栏无路径可查） |
| C2 | config 注入 `danger_gate_enabled=false` + `sandbox_enabled=true` + 界外文件写入 | 仍 `ask`（两能力独立） |
| C3 | config 注入 `custom_danger_rules` 含 `git\s+push\s+--force` + 该命令 | `ask`，reason 含"自定义" |
| C4 | 自定义规则含一行非法正则 + 一行合法规则，命令命中合法行 | `ask`（非法行跳过不崩） |
| C5a | config 注入 `sandbox_enabled=false` + 界外文件写入 | 无输出（围栏关） |
| C5b | config 注入 `sandbox_enabled=false` + 界外重定向 | 无输出（围栏关） |
| C6 | `ZCODE_WORKSPACE_GUARD_CONFIG` 指向不存在文件 + 危险命令 | 仍 `ask`（回落默认值） |
| C7 | config 注入 `danger_gate_enabled=false` + `rm -rf /` | 仍 `ask`（围栏拦：`/` 在界外，两层独立叠加） |

## 3. 回归纪律

- **提交前必跑** `python3 tests/run_tests.py`，全绿才能提交（写入
  AGENTS.md 的工作流程）
- 每个功能里程碑：先在本文矩阵登记用例 → `tests/cases/` 落数据 →
  实现 → 全量回归（新用例 + 全部既有用例）绿 → commit + push
- 修 bug 流程：先把触发 bug 的最小用例加入矩阵（先红）→ 修复 →
  回归全绿（后绿）→ 提交信息引用用例编号

## 4. 扩展方法

1. 在 §2 对应分组表格登记新用例（给编号、写期望）
2. `tests/cases/` 对应 JSON 文件追加用例对象（路径一律用占位符）
3. 需要新占位符时在 `run_tests.py` 的 `PLACEHOLDERS` 处登记并在本文
   §1 补文档
4. 跑全量回归，绿后提交
