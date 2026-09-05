#!/usr/bin/env python3
"""workspace-guard：ZCode PreToolUse 守门脚本。

设计文档见仓库 docs/DESIGN.md；测试规划见 docs/TEST_PLAN.md。
当前已落地：危险命令门（内置规则）、工作区围栏-文件工具。后续里程碑：
Bash 越界启发式、配置取值链。
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


def check_danger(command):
    """危险命令门：命中返回原因字符串，安全返回 None。"""
    if FORK_BOMB.search(command):
        return "fork 炸弹"
    if DD_TO_DEVICE.search(command):
        return "dd 直写设备"
    if MKFS.search(command):
        return "mkfs 格式化"
    if DISKUTIL_WIPE.search(command):
        return "diskutil 抹卷"
    if "--no-preserve-root" in command:
        return "rm --no-preserve-root"

    tokens = command.split()
    has_dangerous_target = any(DANGEROUS_TARGET.match(t) for t in tokens)

    if has_dangerous_target:
        has_rm = any(t == "rm" or t.endswith("/rm") for t in tokens)
        has_rm_flag = any(RM_FLAG.match(t) for t in tokens)
        if has_rm and has_rm_flag:
            return "rm 递归/强制删除 根目录、家目录或通配整个目录"

        has_chmod = any(
            t in ("chmod", "chown") or t.endswith(("/chmod", "/chown")) for t in tokens
        )
        has_rec_flag = any(CHMOD_FLAG.match(t) for t in tokens)
        if has_chmod and has_rec_flag:
            return "chmod/chown 递归修改根目录或家目录权限"

    return None


# ---- 工作区围栏：可写根与路径规范化（见 DESIGN.md §5.2/§6.3）----


def workspace_root():
    """工作区根来自宿主注入的环境变量；缺失返回 None（围栏降级）。"""
    env = os.environ.get("ZCODE_PROJECT_DIR") or os.environ.get("CLAUDE_PROJECT_DIR")
    if not env:
        return None
    return os.path.realpath(env)


def writable_roots(root):
    """可写根集合：工作区根 + /tmp + $TMPDIR，全部 realpath 规范化去重。"""
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
        return "allow", "工作区内写入，放行"
    return (
        "ask",
        "写入目标在工作区外，需要人工确认。目标：%s。可写根：%s。"
        "请改用工作区内路径，或向用户说明理由并获得批准。" % (target, "、".join(roots)),
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
            return "重定向写入工作区外目标 %s" % target

    for segment in SEGMENT_SPLIT.split(command):
        tokens = segment.split()
        for i, tok in enumerate(tokens):
            verb = tok.rsplit("/", 1)[-1]
            if verb in VERB_DEST_ONLY:
                args = [t for t in tokens[i + 1:] if not t.startswith("-")]
                if args and _abs_outside(args[-1], roots):
                    return "%s 的目的参数在工作区外：%s" % (verb, args[-1])
            elif verb in VERB_ALL_ARGS:
                for arg in tokens[i + 1:]:
                    if not arg.startswith("-") and _abs_outside(arg, roots):
                        return "%s 作用于工作区外路径：%s" % (verb, arg)

    return None


def decide(payload):
    """返回 hookSpecificOutput 决策 dict，或 None（放行，不输出）。"""
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input") or {}

    if tool_name == "Bash":
        command = tool_input.get("command", "")
        reason = check_danger(command)
        if reason:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": (
                        TAG + " 危险命令需要人工确认：%s。请先向用户展示完整命令并获得确认。]" % reason
                    ),
                }
            }
        reason = check_bash_fence(command, workspace_root())
        if reason:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": (
                        TAG + " Bash 命令疑似越界写入（启发式检测，enforcement: heuristic），"
                        "需要人工确认。%s。可写根：%s。请改用工作区内路径，或向用户说明理由。]"
                        % (reason, "、".join(writable_roots(workspace_root())))
                    ),
                }
            }

    if tool_name in FILE_TOOLS:
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
