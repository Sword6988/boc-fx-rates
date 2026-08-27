# -*- coding: utf-8 -*-
"""离线测试：fetch_all 编排、解码、错误映射、格式化、列偏移解析（无需联网）。

运行：python tests/test_fetchall.py
"""
import json
import os
import socket
import ssl
import sys
import urllib.error
from email.message import Message
from unittest import mock

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import fetcher
from app import _fmt_time


# ------------------------- fetch_all 编排 -------------------------

def test_fetch_all_all_ok():
    def fake_boc():
        return {"美元": {"buy": 671.22, "time": "2026/08/27 08:19:14"},
                "卢布": {"buy": 7.58, "time": "2026/08/27 08:19:14"}}

    def fake_pen():
        return 2.0111, "open.er-api.com", "27 Aug 2026"

    with mock.patch.object(fetcher, "fetch_boc_rates", fake_boc), \
         mock.patch.object(fetcher, "fetch_pen_reference", fake_pen):
        rows, err = fetcher.fetch_all()
    assert err is None, "全成功不应有错误"
    assert set(rows) == {"USD", "RUB", "PEN"}
    assert rows["USD"]["rate1"] == 6.7122
    assert rows["USD"]["rate100"] == 671.22
    assert rows["USD"]["fallback"] is False
    assert rows["PEN"]["fallback"] is True
    assert rows["PEN"]["rate1"] == 2.0111
    print("fetch_all 全成功测试通过")


def test_fetch_all_boc_missing_currency():
    def fake_boc():
        return {"美元": {"buy": 671.22, "time": "2026/08/27 08:19:14"}}

    def fake_pen():
        return 2.0111, "open.er-api.com", "27 Aug 2026"

    with mock.patch.object(fetcher, "fetch_boc_rates", fake_boc), \
         mock.patch.object(fetcher, "fetch_pen_reference", fake_pen):
        rows, err = fetcher.fetch_all()
    assert "未找到「卢布」" in err
    assert "USD" in rows and "RUB" not in rows
    assert "PEN" in rows
    print("fetch_all 中行缺币种测试通过")


def test_fetch_all_boc_fails_pen_succeeds():
    def fake_boc():
        raise TimeoutError("timed out")

    def fake_pen():
        return 2.0111, "open.er-api.com", "27 Aug 2026"

    with mock.patch.object(fetcher, "fetch_boc_rates", fake_boc), \
         mock.patch.object(fetcher, "fetch_pen_reference", fake_pen):
        rows, err = fetcher.fetch_all()
    assert "中行牌价页获取失败" in err
    assert set(rows) == {"PEN"}, "中行失败时仅有备用源数据"
    print("fetch_all 中行失败/PEN 成功测试通过")


def test_fetch_all_pen_fails():
    def fake_boc():
        return {"美元": {"buy": 671.22, "time": "2026/08/27 08:19:14"},
                "卢布": {"buy": 7.58, "time": "2026/08/27 08:19:14"}}

    def fake_pen():
        raise RuntimeError("all fallback sources down")

    with mock.patch.object(fetcher, "fetch_boc_rates", fake_boc), \
         mock.patch.object(fetcher, "fetch_pen_reference", fake_pen):
        rows, err = fetcher.fetch_all()
    assert "秘鲁新索尔备用源失败" in err
    assert set(rows) == {"USD", "RUB"}
    assert "PEN" not in rows
    print("fetch_all PEN 失败测试通过")


# ------------------------- 解码 / 错误映射 / 格式化 -------------------------

def _msg(charset):
    m = Message()
    if charset:
        m["Content-Type"] = "text/html; charset=%s" % charset
    return m


def test_decode_html_charset_priority():
    assert fetcher._decode_html("中文".encode("gb18030"),
                                _msg("gb18030")) == "中文"
    assert fetcher._decode_html("中文".encode("utf-8"), _msg("")) == "中文"
    assert fetcher._decode_html("中文".encode("gb18030"), _msg("")) == "中文"
    assert fetcher._decode_html("中文".encode("utf-8"), None) == "中文"
    print("_decode_html charset 优先级测试通过")


def test_decode_html_garbage_replace():
    out = fetcher._decode_html(b"\xff\xfe\xfa", None)
    assert "\ufffd" in out, "无法解码时应以替换符兜底"
    print("_decode_html 乱码兜底测试通过")


def test_friendly_net_error():
    assert "连接超时" in fetcher._friendly_net_error(
        urllib.error.URLError(socket.timeout()))
    cert = ssl.SSLCertVerificationError("certificate verify failed")
    assert "证书校验失败" in fetcher._friendly_net_error(
        urllib.error.URLError(cert))
    http_err = urllib.error.HTTPError("http://x", 503, "svc", None, None)
    assert "HTTP 503" in fetcher._friendly_net_error(http_err)
    assert "JSON" in fetcher._friendly_net_error(
        json.JSONDecodeError("bad", "doc", 0))
    print("_friendly_net_error 映射测试通过")


def test_fmt():
    assert fetcher.fmt(None) == "--"
    assert fetcher.fmt(671.22) == "671.22"
    assert fetcher.fmt(6.7122) == "6.7122"
    assert fetcher.fmt(0.0758) == "0.0758"
    assert fetcher.fmt(0.000123) == "0.000123"
    print("fmt 小数位测试通过")


def test_fmt_time():
    assert _fmt_time("2026/08/26 20:50:26") == "2026-08-26 20:50"
    assert _fmt_time("2026-08-26 20:50:26") == "2026-08-26 20:50"
    assert _fmt_time("27 Aug 2026") == "2026-08-27"
    assert _fmt_time("") == ""
    assert _fmt_time(" 乱文 ") == "乱文"
    print("_fmt_time 格式测试通过")


# ------------------------- 表头列定位 -------------------------

def test_parse_header_column_offset():
    """表头列序变化时仍能按列名定位（货币名称不在第 0 列）。"""
    html = ("<table>"
            "<tr><td>序号</td><td>货币名称</td><td>现汇买入价</td></tr>"
            "<tr><td>1</td><td>美元</td><td>671.22</td></tr>"
            "<tr><td>2</td><td>卢布</td><td>7.58</td></tr>"
            "</table>")
    rows = fetcher.parse_boc_html(html)
    assert rows["美元"]["buy"] == 671.22
    assert rows["卢布"]["buy"] == 7.58
    print("表头列偏移解析测试通过")


def test_parse_default_column_order():
    """无表头时回退默认列序（货币名称第 0 列、现汇买入价第 1 列）。"""
    html = ("<table>"
            "<tr><td>美元</td><td>671.22</td></tr>"
            "</table>")
    rows = fetcher.parse_boc_html(html)
    assert rows["美元"]["buy"] == 671.22
    print("无表头默认列序测试通过")


def test_parse_missing_currency_skipped():
    html = ("<table>"
            "<tr><td>货币名称</td><td>现汇买入价</td></tr>"
            "<tr><td>欧元</td><td>780.12</td></tr>"
            "</table>")
    rows = fetcher.parse_boc_html(html)
    assert rows == {}, "非目标币种不应出现在结果中"
    print("非目标币种跳过测试通过")


if __name__ == "__main__":
    test_fetch_all_all_ok()
    test_fetch_all_boc_missing_currency()
    test_fetch_all_boc_fails_pen_succeeds()
    test_fetch_all_pen_fails()
    test_decode_html_charset_priority()
    test_decode_html_garbage_replace()
    test_friendly_net_error()
    test_fmt()
    test_fmt_time()
    test_parse_header_column_offset()
    test_parse_default_column_order()
    test_parse_missing_currency_skipped()
    print("全部 fetch_all/工具函数离线测试通过")
