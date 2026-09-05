#!/usr/bin/env python3
"""workspace-guard 自动化测试入口。

进程边界测试：以宿主真实调用方式（subprocess + stdin JSON）执行 guard.py，
断言 stdout 决策。用例数据在 tests/cases/*.json，规划见 TEST_PLAN.md。

用法：python3 tests/run_tests.py   （退出码 0=全绿，1=有失败）
"""
import json
import os
import subprocess
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUARD = os.path.join(REPO_ROOT, "plugins", "workspace-guard", "scripts", "guard.py")
CASES_DIR = os.path.join(REPO_ROOT, "tests", "cases")

CONFIG_ENV = "ZCODE_WORKSPACE_GUARD_CONFIG"


class Session(object):
    """每次运行的临时环境：工作区 {WS}、界外目录 {OUT}、真实 /tmp {TMP}。

    基目录建在系统 TMPDIR 下，但 guard 子进程的 TMPDIR 被重定向到
    {SESSION}/tmpdir——否则 {OUT} 会天然落在可写根 $TMPDIR 内，围栏
    用例全部失效（环境隔离，见 TEST_PLAN.md §1）。
    """

    def __init__(self):
        base = tempfile.mkdtemp(prefix="wsg-test-")
        self.ws = os.path.join(base, "ws")
        self.out = os.path.join(base, "out")
        self.tmpdir = os.path.join(base, "tmpdir")
        os.makedirs(self.ws)
        os.makedirs(self.out)
        os.makedirs(self.tmpdir)
        self.placeholders = {
            "{WS}": self.ws,
            "{OUT}": self.out,
            "{TMP}": "/tmp",  # 可写根内的真实 /tmp（子进程 TMPDIR 已被重定向）
            "{HOME}": os.path.expanduser("~"),
        }

    def resolve(self, value):
        if isinstance(value, str):
            for key, real in self.placeholders.items():
                value = value.replace(key, real)
            return value
        if isinstance(value, list):
            return [self.resolve(v) for v in value]
        if isinstance(value, dict):
            return {k: self.resolve(v) for k, v in value.items()}
        return value


def do_setup(session, steps):
    """执行用例前置动作（F8 符号链接等），支持 symlink/mkdir/write_file。"""
    for step in steps or []:
        step = session.resolve(step)
        if "symlink" in step:
            spec = step["symlink"]
            os.symlink(spec["target"], spec["link"])
        elif "mkdir" in step:
            os.makedirs(step["mkdir"], exist_ok=True)
        elif "write_file" in step:
            spec = step["write_file"]
            os.makedirs(os.path.dirname(spec["path"]), exist_ok=True)
            with open(spec["path"], "w") as f:
                f.write(spec.get("content", ""))
        else:
            raise ValueError("未知 setup 动作: %r" % step)


def run_case(session, case, index):
    name = case.get("name", "case-%d" % index)

    do_setup(session, case.get("setup"))

    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "TMPDIR": session.tmpdir,  # 受控 TMPDIR，保证 {OUT} 在可写根之外
        "ZCODE_PROJECT_DIR": session.ws,
    }
    if "config" in case:
        cfg_path = os.path.join(session.ws, "..", "config-%d.json" % index)
        cfg_path = os.path.abspath(cfg_path)
        with open(cfg_path, "w") as f:
            json.dump(case["config"], f)
        env[CONFIG_ENV] = cfg_path

    if "stdin_raw" in case:
        stdin_data = case["stdin_raw"]
    else:
        payload = session.resolve(
            {"tool_name": case["tool_name"], "tool_input": case.get("tool_input", {})}
        )
        stdin_data = json.dumps(payload)

    proc = subprocess.run(
        [sys.executable, GUARD],
        input=stdin_data,
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )

    expect = case.get("expect", {})
    want_decision = expect.get("decision")  # None 表示期望无输出
    want_reason = expect.get("reason_contains")

    stdout = proc.stdout.strip()
    problems = []
    if proc.returncode != 0:
        problems.append("exit=%d stderr=%s" % (proc.returncode, proc.stderr.strip()[:200]))

    if want_decision is None:
        if stdout:
            problems.append("期望无输出，实际: %s" % stdout[:200])
    else:
        if not stdout:
            problems.append("期望 %s，实际无输出" % want_decision)
        else:
            try:
                out = json.loads(stdout)
                got = out.get("hookSpecificOutput", {})
            except ValueError:
                problems.append("stdout 非 JSON: %s" % stdout[:200])
                got = {}
            if got.get("permissionDecision") != want_decision:
                problems.append(
                    "期望 %s，实际 %s"
                    % (want_decision, got.get("permissionDecision"))
                )
            if want_reason:
                reason = got.get("permissionDecisionReason", "")
                if want_reason not in reason:
                    problems.append("reason 缺少关键词 %r: %s" % (want_reason, reason))

    if problems:
        print("FAIL  %s  (%s)" % (name, "; ".join(problems)))
        return False
    print("PASS  %s" % name)
    return True


def load_cases():
    cases = []
    for fname in sorted(os.listdir(CASES_DIR)):
        if fname.endswith(".json"):
            with open(os.path.join(CASES_DIR, fname)) as f:
                data = json.load(f)
            if isinstance(data, list):
                cases.extend(data)
            else:
                cases.append(data)
    return cases


def main():
    cases = load_cases()
    if not cases:
        print("没有用例")
        return 1
    session = Session()
    passed = 0
    for i, case in enumerate(cases):
        if run_case(session, case, i):
            passed += 1
    print("\n%d/%d 通过" % (passed, len(cases)))
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
