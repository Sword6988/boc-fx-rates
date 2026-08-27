# -*- coding: utf-8 -*-
"""离线测试：牌价页解析（无需联网）。

运行：python tests/test_parse.py
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import fetcher

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "boc_whpj.html")


def test_parse_fixture():
    with open(FIXTURE, "rb") as f:
        raw = f.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("gb18030")

    rows = fetcher.parse_boc_html(text)
    print("解析结果:", json.dumps(rows, ensure_ascii=False))
    assert "美元" in rows, "fixture 未解析出美元"
    assert "卢布" in rows, "fixture 未解析出卢布"
    assert "秘鲁新索尔" not in rows, "中行不应挂牌秘鲁新索尔"
    for name in ("美元", "卢布"):
        assert rows[name]["buy"] > 0, "%s 买入价为 0" % name
        assert rows[name]["time"], "%s 缺少时间" % name
    print("解析测试通过：", " | ".join(
        "%s=%.2f" % (k, v["buy"]) for k, v in rows.items()))


if __name__ == "__main__":
    test_parse_fixture()
    print("全部离线测试通过")
