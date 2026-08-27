# -*- coding: utf-8 -*-
"""联网抓取测试：验证三种货币数据解析与量级合理性（需联网）。

运行：python _test_fetch.py
离线解析测试见 tests/test_parse.py。
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import fetcher

# 各币种 1 外币兑人民币的合理区间（量级校验，防解析错位）
RANGES = {"USD": (5.0, 9.0), "RUB": (0.03, 0.3), "PEN": (1.0, 3.0)}


def main():
    rows, err = fetcher.fetch_all()
    print("error:", err)
    for code in ("USD", "RUB", "PEN"):
        d = rows.get(code)
        print(code, "->", json.dumps(d, ensure_ascii=False) if d else None)

    missing = [c for c in ("USD", "RUB", "PEN") if c not in rows]
    if missing:
        print("失败：以下币种未获取到数据：%s" % ", ".join(missing))
        sys.exit(1)

    for code, (lo, hi) in RANGES.items():
        v = rows[code]["rate1"]
        assert lo <= v <= hi, "%s 超出合理区间 [%s, %s]: %s" % (code, lo, hi, v)

    disp = {cur.code: cur.display for cur in fetcher.CURRENCIES}
    print()
    print("量级校验通过：" + "  ".join(
        "1%s=%.4f元" % (disp[code], rows[code]["rate1"])
        for code in ("USD", "RUB", "PEN")))


if __name__ == "__main__":
    main()
