#!/usr/bin/env python3
"""workspace-guard：ZCode PreToolUse 守门脚本。

设计文档见仓库 docs/DESIGN.md；测试规划见 docs/TEST_PLAN.md。
能力：危险命令门（规则清单驱动，内置规则可删）、项目围栏（文件工具
精确判断 + Bash 越界写启发式）、配置取值链。
"""
import json
import os
import re
import sys

TAG = "[workspace-guard:"

FILE_TOOLS = ("Write", "Edit", "ApplyPatch")

# ---- 危险命令门：规则定义（token 级精确匹配，宁可漏报不可误报）----

# 危险目标：必须与命令中的某个 token 完全相等才命中
DANGEROUS_TARGET = re.compile(
    r"""^(?:
        /|//
      | /\*|/\*\*
      | ~|~/|~/\*|~/\.
      | \$(?:\{HOME\}|HOME)
      | \*|\*\*|\.\*|\./\*|\*/\*
      | \.|\./|\.\.|\.\./
      | /Users/[^/]+            # 整级用户家目录（macOS 布局，通配任意用户名）
    )$""",
    re.X,
)

RM_FLAG = re.compile(r"^-[a-zA-Z]*[rf][a-zA-Z]*$|^--recursive$|^--force$")
CHMOD_FLAG = re.compile(r"^-R$|^--recursive$")

FORK_BOMB = re.compile(r":\(\)\s*\{[^}]*\|[^}]*&[^}]*\}\s*;\s*:")

DD_TO_DEVICE = re.compile(r"\bdd\b[^;&|]*\bof=/dev/")
MKFS = re.compile(r"\bmkfs(\.\w+)?\b")
DISKUTIL_WIPE = re.compile(r"\bdiskutil\b[^;&|]*\b(eraseDisk|eraseVolume|deleteContainer)\b")


def _rule_rm_destructive(command):
    """rm 家族：rm + 递归/强制旗标 + 危险目标 token 三者同现。"""
    tokens = command.split()
    if not any(DANGEROUS_TARGET.match(t) for t in tokens):
        return None
    has_rm = any(t == "rm" or t.endswith("/rm") for t in tokens)
    has_flag = any(RM_FLAG.match(t) for t in tokens)
    if has_rm and has_flag:
        return "rm 递归/强制删除 根目录、家目录或通配整个目录"
    return None


def _rule_chmod_recursive(command):
    """chmod/chown 递归作用于危险目标 token。"""
    tokens = command.split()
    if not any(DANGEROUS_TARGET.match(t) for t in tokens):
        return None
    has_chmod = any(
        t in ("chmod", "chown") or t.endswith(("/chmod", "/chown")) for t in tokens
    )
    if has_chmod and any(CHMOD_FLAG.match(t) for t in tokens):
        return "chmod/chown 递归修改根目录或家目录权限"
    return None


# 内置规则表：ID → 判定函数（命中返回原因字符串，否则 None）。
# 顺序即清单回落（空值启用全部）时的检查顺序，与历史优先级一致。
BUILTIN_RULES = {
    "fork-bomb": lambda c: "fork 炸弹" if FORK_BOMB.search(c) else None,
    "dd-device": lambda c: "dd 直写设备" if DD_TO_DEVICE.search(c) else None,
    "mkfs": lambda c: "mkfs 格式化" if MKFS.search(c) else None,
    "diskutil-wipe": lambda c: "diskutil 抹卷" if DISKUTIL_WIPE.search(c) else None,
    "no-preserve-root": lambda c: (
        "rm --no-preserve-root" if "--no-preserve-root" in c else None
    ),
    "rm-destructive": _rule_rm_destructive,
    "chmod-recursive": _rule_chmod_recursive,
}


def parse_rules(text):
    """danger_rules 清单解析（协议见 DESIGN.md §6.1）。

    段以分号分隔（设置界面为单行输入框，保存会剥掉换行，故分号是
    标准分隔符；换行分隔同样接受，便于手编 config.json）。空白段与
    # 开头的注释段跳过；内置规则 ID 启用对应判定；其余段为自定义
    正则。空文本或全空白 = 启用全部内置规则（安全默认，清空即重置）。"""
    if not text or not text.strip():
        return list(BUILTIN_RULES), []
    enabled, customs = [], []
    for segment in text.replace("\n", ";").split(";"):
        segment = segment.strip()
        if not segment or segment.startswith("#"):
            continue
        if segment in BUILTIN_RULES:
            enabled.append(segment)
        else:
            customs.append(segment)
    return enabled, customs


# ---- 项目围栏：可写根与路径规范化（见 DESIGN.md §5.2/§6.3）----


def workspace_root():
    """项目根来自宿主注入的环境变量；缺失返回 None（围栏降级）。"""
    env = os.environ.get("ZCODE_PROJECT_DIR") or os.environ.get("CLAUDE_PROJECT_DIR")
    if not env:
        return None
    return os.path.realpath(env)


def writable_roots(root):
    """可写根集合：项目根 + /tmp + $TMPDIR，全部 realpath 规范化去重。"""
    roots = [root, "/tmp", os.environ.get("TMPDIR", "")]
    seen = []
    for r in roots:
        if not r:
            continue
        p = os.path.realpath(r)
        if p not in seen:
            seen.append(p)
    return seen


def canonical_path(path):
    """规范化目标路径：自目标起逐级上溯找最深存在的祖先，realpath 后拼回
    剩余段。防符号链接偷渡（TOCTOU）：解析发生在判定时点。
    注意用 lexists 而非 exists——悬空符号链接（目标不存在）也必须解析
    链接本身，否则会被当作不存在路径而放过。"""
    path = os.path.expanduser(path)
    tail = []
    p = path
    while True:
        if os.path.lexists(p):
            return os.path.join(os.path.realpath(p), *tail) if tail else os.path.realpath(p)
        rest = os.path.basename(p)
        if rest in ("", "/", "."):
            return p  # 上溯到头也不存在，按字面返回
        tail.insert(0, rest)
        p = os.path.dirname(p)


def is_under(path, root):
    return path == root or path.startswith(root + os.sep)


def fence_file_tool(path, root):
    """文件工具围栏：返回 (decision, reason) 或 (None, None) 表示围栏不适用。"""
    if not path or not root:
        return None, None
    roots = writable_roots(root)
    target = canonical_path(path)
    if target == "/dev/null" or any(is_under(target, r) for r in roots):
        return "allow", "项目内写入，放行"
    return (
        "ask",
        "写入目标在项目外，需要人工确认。目标：%s。可写根：%s。"
        "请改用项目内路径，或向用户说明理由并获得批准。" % (target, "、".join(roots)),
    )


# ---- Bash 越界写启发式（enforcement: heuristic，见 DESIGN.md §6.2）----

# 重定向目标：> 、>> 、&> 、2> 等；捕获目标 token
REDIRECT = re.compile(r"(?:\d)?>>?\s*([^\s;|&]+)|&>>?\s*([^\s;|&]+)")

# 命令动词 → 其后绝对路径参数的检查范围（cp/rsync/install 只查目的参数）
VERB_DEST_ONLY = ("cp", "rsync", "install")
VERB_ALL_ARGS = ("mv", "rm", "tee")

SEGMENT_SPLIT = re.compile(r"&&|\|\||;|\|")


def _abs_outside(path, roots):
    """路径为绝对路径且规范化后不在可写根（且非 /dev/null）→ True。"""
    p = os.path.expandvars(os.path.expanduser(path))
    if not p.startswith("/") or p == "/dev/null":
        return False
    canonical = canonical_path(p)
    return not any(is_under(canonical, r) for r in roots)


def check_bash_fence(command, root):
    """Bash 越界写启发式：命中返回原因字符串，否则 None。"""
    if not root:
        return None
    roots = writable_roots(root)

    for m in REDIRECT.finditer(command):
        target = m.group(1) or m.group(2)
        if target and _abs_outside(target, roots):
            return "重定向写入项目外目标 %s" % target

    for segment in SEGMENT_SPLIT.split(command):
        tokens = segment.split()
        for i, tok in enumerate(tokens):
            verb = tok.rsplit("/", 1)[-1]
            if verb in VERB_DEST_ONLY:
                args = [t for t in tokens[i + 1:] if not t.startswith("-")]
                if args and _abs_outside(args[-1], roots):
                    return "%s 的目的参数在项目外：%s" % (verb, args[-1])
            elif verb in VERB_ALL_ARGS:
                for arg in tokens[i + 1:]:
                    if not arg.startswith("-") and _abs_outside(arg, roots):
                        return "%s 作用于项目外路径：%s" % (verb, arg)

    return None


# ---- 配置取值链（见 DESIGN.md §5.1：env 注入 > 宿主 config.json > 默认值）----

CONFIG_KEYS = ("sandbox_enabled", "danger_gate_enabled", "danger_rules")
DEFAULT_CONFIG = {
    "sandbox_enabled": True,
    "danger_gate_enabled": True,
    "danger_rules": "",  # 空 = 启用全部内置规则（DESIGN.md §6.1）
}
CONFIG_ENV = "ZCODE_WORKSPACE_GUARD_CONFIG"


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no", "off", "")
    return bool(value)


def _find_plugin_config(plugins):
    """在宿主 config.json 的 plugins 段中容错查找本插件的 userConfig 值。

    宿主持久化键名未文档化：先探测常见候选键（options/config/pluginConfig
    下键名含 workspace-guard 的分支），再退化为子树扫描（任何包含本插件
    已知配置键的 dict）。找到返回 dict，否则 None。
    """
    candidates = []
    for section in ("options", "config", "pluginConfig", "pluginOptions"):
        sub = plugins.get(section)
        if isinstance(sub, dict):
            for key, value in sub.items():
                if "workspace-guard" in key and isinstance(value, dict):
                    candidates.append(value)

    def scan(node, depth):
        if depth > 4 or not isinstance(node, dict):
            return None
        if any(k in node for k in CONFIG_KEYS):
            return node
        for value in node.values():
            found = scan(value, depth + 1)
            if found is not None:
                return found
        return None

    for cand in candidates:
        if any(k in cand for k in CONFIG_KEYS):
            return cand
    return scan(plugins, 0)


def load_config():
    """三级取值链，高优先级覆盖低优先级；任何异常回落默认值。"""
    cfg = dict(DEFAULT_CONFIG)

    # 第 2 级：宿主 config.json 的插件配置段
    host_path = os.path.join(os.path.expanduser("~"), ".zcode", "cli", "config.json")
    try:
        with open(host_path) as f:
            host = json.load(f)
        found = _find_plugin_config(host.get("plugins", {}))
        if found:
            for k in CONFIG_KEYS:
                if k in found:
                    cfg[k] = found[k]
    except Exception:
        pass

    # 第 1 级：环境变量注入（测试/开发用）
    env_path = os.environ.get(CONFIG_ENV)
    if env_path:
        try:
            with open(env_path) as f:
                override = json.load(f)
            for k in CONFIG_KEYS:
                if k in override:
                    cfg[k] = override[k]
        except Exception:
            pass

    cfg["sandbox_enabled"] = _as_bool(cfg["sandbox_enabled"])
    cfg["danger_gate_enabled"] = _as_bool(cfg["danger_gate_enabled"])
    cfg["danger_rules"] = (
        cfg["danger_rules"] if isinstance(cfg["danger_rules"], str) else ""
    )
    return cfg


def check_custom_rules(command, rule_lines):
    """自定义危险正则：逐行匹配，编译失败跳过该行。命中返回该行。"""
    for line in rule_lines:
        try:
            if re.search(line, command):
                return line
        except re.error:
            continue  # 非法正则跳过，不阻塞会话
    return None


def decide(payload):
    """返回 hookSpecificOutput 决策 dict，或 None（放行，不输出）。"""
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input") or {}
    cfg = load_config()

    if tool_name == "Bash":
        command = tool_input.get("command", "")
        if cfg["danger_gate_enabled"]:
            enabled, custom_lines = parse_rules(cfg["danger_rules"])
            for rule_id in enabled:
                reason = BUILTIN_RULES[rule_id](command)
                if reason:
                    return _ask(
                        "危险命令需要人工确认：%s。请先向用户展示完整命令并获得确认。"
                        % reason
                    )
            rule = check_custom_rules(command, custom_lines)
            if rule:
                return _ask("自定义危险规则命中（%s）。请先向用户展示完整命令并获得确认。" % rule)
        if cfg["sandbox_enabled"]:
            root = workspace_root()
            reason = check_bash_fence(command, root)
            if reason:
                return _ask(
                    "Bash 命令疑似越界写入（启发式检测，enforcement: heuristic），"
                    "需要人工确认。%s。可写根：%s。请改用项目内路径，或向用户说明理由。"
                    % (reason, "、".join(writable_roots(root)) if root else "未知")
                )

    if tool_name in FILE_TOOLS:
        if cfg["sandbox_enabled"]:
            root = workspace_root()
            target = tool_input.get("file_path") or tool_input.get("path")
            decision, reason = fence_file_tool(target, root)
            if decision:
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": decision,
                        "permissionDecisionReason": TAG + " %s]" % reason,
                    }
                }

    return None


def _ask(reason):
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": TAG + " %s]" % reason,
        }
    }


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)  # fail-open：载荷异常时放行，不阻塞会话

    try:
        decision = decide(payload)
    except Exception:
        sys.exit(0)  # fail-open：内部异常放行

    if decision is not None:
        print(json.dumps(decision, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
