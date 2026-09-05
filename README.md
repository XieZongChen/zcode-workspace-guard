# zcode-workspace-guard

ZCode 插件 **workspace-guard**：在每条命令执行、每次文件写入之前进行规则检查，提供两项能力——**项目围栏**与**危险命令门**。判定为纯规则匹配：不联网、不调用 AI、无第三方依赖，同一条命令总是得到同一结果。

## 功能

**危险命令门**（默认开启）

命中以下模式的命令转为人工确认：`rm -rf` 作用于根目录、家目录或通配整个目录（`/`、`~`、`*` 等）；`dd` 写入 `/dev/…`；`mkfs`；`diskutil eraseDisk` / `eraseVolume` / `deleteContainer`；fork 炸弹；`chmod -R` / `chown -R` 作用于根或家目录；`--no-preserve-root`。批准一次执行一次，拒绝则不执行。该检查与宿主权限模式无关，"完全访问"模式下同样生效。

**项目围栏**（默认开启）

默认可写范围为**当前任务的项目目录 + `/tmp` + `$TMPDIR`**（`/dev/null` 始终合法）。「额外可写根」（`extra_writable_roots`，默认为空）中声明的目录会**追加**到该范围——不配置则不生效，仅在项目含多个文件夹、默认范围不足时才需要：

- 可写范围内的文件写入：放行；在需要确认的权限模式下，同时跳过宿主的例行询问
- 可写范围外的文件写入（桌面、家目录、其他项目）：转为人工确认
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
| `danger_rules` | string | 全部内置规则 | 危险规则清单，分号分隔：内置规则 ID 与自定义正则，如 `fork-bomb;dd-device;git\s+push\s+--force`。删除某段即停用该规则，清空恢复全部默认；仅危险命令门开启时生效 |
| `extra_writable_roots` | string | 空 | 额外可写目录，绝对路径、分号分隔（支持 `~`）。项目下并列多个文件夹（如多个子项目）时使用：围栏的项目根取自宿主注入的任务工作目录，可能只是其中一个子目录，其余文件夹需在此声明 |

清单输入方式（分号 `;` 分隔——设置界面是单行输入框，换行分隔无法在界面保存往返中存活）：

```text
fork-bomb;dd-device;mkfs;diskutil-wipe;no-preserve-root;rm-destructive;chmod-recursive
```

- 删除某段（如 `;mkfs`）即停用对应内置规则；清空恢复全部默认
- 追加自定义正则段：`…;chmod-recursive;git\s+push\s+--force`，命令文本命中即人工确认
- `#` 开头的段为注释；正则内不能包含分号（它是分隔符）

内置规则的完整匹配模式对照表见 [docs/DESIGN.md](docs/DESIGN.md) §6.1。其中 rm / chmod 类判定是 token 级组合逻辑（ rm + 递归旗标 + 危险目标三者同现），无法等价压缩为单行正则，故清单中以 ID 引用而非展示为正则。

### 手动编辑配置文件

三项配置持久化于 `~/.zcode/cli/config.json` 的 `plugins.options` 下，以插件 ID 为键（实测形状：`plugins.options["workspace-guard@zcode-workspace-guard"].danger_rules`）。手动维护 `danger_rules` 时值为清单文本（分号分隔，格式同上；换行分隔亦接受），保存后对下一次工具调用立即生效——守门脚本每次调用现读该文件，无需重启。清空该键等于恢复全部默认规则。

注意：设置界面在启动时载入配置，手动编辑不会即时反映到输入框；且经界面保存会剥掉值中的换行，分号分隔是唯一能在界面往返中保持完整的格式。

## 已知局限

1. shell 命令的越界检测为启发式：仅识别界外**绝对路径**的写操作，相对路径不检测（如先 `cd` 出项目再写）；文件工具的围栏为精确判断。
2. 插件 API 不支持向 ZCode 权限面板添加档位；本插件以常驻检查层的形式与任意模式叠加。
3. 守门脚本故障时放行（fail-open），作为宿主权限体系之外的补充，不影响会话可用性。
4. 无法自动识别项目设置的文件夹：宿主提供给插件的只有**当前任务的工作目录**——实测 PreToolUse 的全部载荷字段与环境变量中，均无项目所配置文件夹的信息（证据见 [docs/DESIGN.md](docs/DESIGN.md) §5.2）。项目下并列多个文件夹（如多个子项目）、且任务工作目录在其中之一时，写入其他文件夹会触发人工确认；在「额外可写根」（`extra_writable_roots`）中声明一次即可覆盖。待宿主提供项目文件夹信息后可升级为自动识别。

规则明细、判定协议与安全模型见 [docs/DESIGN.md](docs/DESIGN.md)。

## 开发

```bash
python3 tests/run_tests.py   # 全量回归（51 例），提交前必须全绿
```

协作约定见 [AGENTS.md](AGENTS.md)，测试规划见 [docs/TEST_PLAN.md](docs/TEST_PLAN.md)。

## License

[MIT](LICENSE)
