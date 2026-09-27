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


def test_parse_malformed_html():
    """恶意/畸形 HTML（未闭合标签、嵌套、控制字符、超长输入）不应抛异常。"""
    cases = [
        "",                                            # 空页
        "<html><body>",                                # 全部未闭合
        "<tr><td>美元<td>",                            # 未闭合 td/tr
        "<scr<script>ipt>美元</td></tr>",              # 标签混杂
        "<table><tr><td>美元</td><td>671.22</td>"
        "<td>未闭合行",                                 # 行中途截断
        "<td>\x00\x01\x02</td>",                       # 控制字符
        "<table>" + "<tr><td>美元</td><td>671.22</td></tr>" * 200 +
        "</table>" + "垃圾" * 5000,                    # 超长输入
        "<div>" + "<b>" * 500 + "美元" + "</b>" * 500 + "</div>",  # 深层嵌套
        "<table><tr><th>货币名称</th><th>现汇买入价</th></tr>"
        "<tr><td>美元</td><td>abc</td></tr>"           # 数值列非法
        "<tr><td></td><td>671.22</td></tr></table>",   # 名称为空
    ]
    for text in cases:
        rows = fetcher.parse_boc_html(text)   # 不应抛异常
        assert isinstance(rows, dict)
    print("畸形/恶意 HTML 解析健壮性测试通过")


if __name__ == "__main__":
    test_parse_fixture()
    test_parse_malformed_html()
    print("全部离线测试通过")
