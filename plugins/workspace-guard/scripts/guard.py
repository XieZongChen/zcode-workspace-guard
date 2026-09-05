#!/usr/bin/env python3
"""workspace-guard：ZCode PreToolUse 守门脚本。

设计文档见仓库 DESIGN.md；测试规划见 TEST_PLAN.md。
当前为骨架阶段：stdin 解析 + fail-open，判定逻辑随里程碑逐步落地。
"""
import json
import sys


def decide(payload):
    """返回 hookSpecificOutput 决策 dict，或 None（放行，不输出）。

    payload: {"tool_name": str, "tool_input": dict}
    """
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
