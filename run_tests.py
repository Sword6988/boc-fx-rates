# -*- coding: utf-8 -*-
"""统一测试入口：顺序运行全部离线测试并汇总结果。

运行：python run_tests.py
退出码：全部通过为 0，任一失败为 1（CI 与本地共用）。
"""
import subprocess
import sys

TESTS = [
    "tests/test_parse.py",
    "tests/test_http.py",
    "tests/test_fetchall.py",
]


def main():
    failures = []
    for path in TESTS:
        print("=== %s ===" % path)
        r = subprocess.run([sys.executable, path])
        if r.returncode != 0:
            failures.append(path)
        print()
    if failures:
        print("FAILED: %s" % ", ".join(failures))
        return 1
    print("All %d test suites passed." % len(TESTS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
