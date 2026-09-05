#!/usr/bin/env python3
"""workspace-guard：ZCode PreToolUse 守门脚本。

设计文档见仓库 DESIGN.md；测试规划见 TEST_PLAN.md。
当前已落地：危险命令门（内置规则）。后续里程碑：工作区围栏、Bash 启发式、
配置取值链。
"""
import json
import re
import sys

TAG = "[workspace-guard:"

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


def decide(payload):
    """返回 hookSpecificOutput 决策 dict，或 None（放行，不输出）。"""
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input") or {}

    if tool_name == "Bash":
        reason = check_danger(tool_input.get("command", ""))
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
