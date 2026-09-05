# zcode-workspace-guard

ZCode 插件 **workspace-guard**：在每条命令执行、每次文件写入之前进行规则检查，提供两项能力——**项目围栏**与**危险命令门**。判定为纯规则匹配：不联网、不调用 AI、无第三方依赖，同一条命令总是得到同一结果。

## 功能

**危险命令门**（默认开启）

命中以下模式的命令转为人工确认：`rm -rf` 作用于根目录、家目录或通配整个目录（`/`、`~`、`*` 等）；`dd` 写入 `/dev/…`；`mkfs`；`diskutil eraseDisk` / `eraseVolume` / `deleteContainer`；fork 炸弹；`chmod -R` / `chown -R` 作用于根或家目录；`--no-preserve-root`。批准一次执行一次，拒绝则不执行。该检查与宿主权限模式无关，"完全访问"模式下同样生效。

**项目围栏**（默认开启）

可写范围为当前项目目录 + `/tmp` + `$TMPDIR`，`/dev/null` 始终合法：

- 项目内的文件写入：放行；在需要确认的权限模式下，同时跳过宿主的例行询问
- 项目外的文件写入（桌面、家目录、其他项目）：转为人工确认
- shell 命令的越界写入（重定向、`cp` / `mv` / `rm` / `tee` 的界外绝对路径参数）：启发式识别，命中即人工确认

只拦截确定毁灭性或明确越界的操作；`rm -rf node_modules`、`rm -rf <具体路径>`、`chmod -R 755 ./src` 等日常命令不受影响。

## 行为一览

| 操作 | 结果 |
| --- | --- |
| 修改项目内的文件 | 直接执行 |
| 写入 `/tmp`、`$TMPDIR` | 直接执行 |
| `rm -rf node_modules` 等日常命令 | 直接执行 |
| 写入项目外路径（如 `~/Desktop`） | 人工确认 |
| `echo x > ~/某文件`（重定向写界外） | 人工确认 |
| `rm -rf *`、抹盘、fork 炸弹等危险命令 | 人工确认 |

拦截理由以 `[workspace-guard: …]` 开头，注明原因与可写范围；随附技能文件用于指导 AI 识别该标记，改用项目内路径或向用户请求批准。

## 安装

仓库根目录即 marketplace，两种方式：

- **本地**：Settings → Plugin Management → Discover → `+` → 选择本仓库目录 → 安装 `workspace-guard`
- **GitHub**：`+` 处填 `https://github.com/XieZongChen/zcode-workspace-guard` → 安装

安装或变更启用状态后需**新建任务**生效（hook 配置在任务启动时加载）。

## 配置

Settings → Plugin Management → workspace-guard → Advanced。配置持久化于 `~/.zcode/cli/config.json`，每次工具调用前重新读取，修改立即生效。

| 配置 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `danger_gate_enabled` | boolean | `true` | 危险命令门开关 |
| `sandbox_enabled` | boolean | `true` | 项目围栏开关，关闭后仅剩危险命令门 |
| `custom_danger_rules` | string | 空 | 自定义危险规则，一行一条正则，命令文本命中即人工确认；仅危险命令门开启时生效。例：`git\s+push\s+--force` |

## 已知局限

1. shell 命令的越界检测为启发式：仅识别界外**绝对路径**的写操作，相对路径不检测（如先 `cd` 出项目再写）；文件工具的围栏为精确判断。
2. 插件 API 不支持向 ZCode 权限面板添加档位；本插件以常驻检查层的形式与任意模式叠加。
3. 守门脚本故障时放行（fail-open），作为宿主权限体系之外的补充，不影响会话可用性。

规则明细、判定协议与安全模型见 [docs/DESIGN.md](docs/DESIGN.md)。

## 从独立 danger_guard hook 迁移

若 `~/.zcode/cli/config.json` 中配置过独立的危险命令 hooks，安装本插件后应删除该配置，避免同一命令双重弹窗。本插件的危险命令门覆盖其全部规则。

## 开发

```bash
python3 tests/run_tests.py   # 全量回归（51 例），提交前必须全绿
```

协作约定见 [AGENTS.md](AGENTS.md)，测试规划见 [docs/TEST_PLAN.md](docs/TEST_PLAN.md)。

## License

[MIT](LICENSE)
