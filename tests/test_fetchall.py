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
from fetcher import _fmt_time


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


def test_fetch_all_selected_subset():
    """只选部分币种：仅返回所选，且不请求备用源。"""
    def fake_boc():
        return {"美元": {"buy": 671.22, "time": "2026/08/27 08:19:14"},
                "卢布": {"buy": 7.58, "time": "2026/08/27 08:19:14"}}

    with mock.patch.object(fetcher, "fetch_boc_rates", fake_boc) as fb, \
         mock.patch.object(fetcher, "fetch_pen_reference") as fp:
        rows, err = fetcher.fetch_all(selected=["USD"])
    assert set(rows) == {"USD"}, "只应返回所选币种"
    assert err is None
    fp.assert_not_called(), "未选 PEN 时不应请求备用源"
    print("fetch_all 选中子集测试通过")


def test_fetch_all_selected_pen_only():
    """只选 PEN：全部来自备用源。"""
    def fake_boc():
        return {"美元": {"buy": 671.22, "time": "2026/08/27 08:19:14"}}

    def fake_pen():
        return 2.0111, "open.er-api.com", "27 Aug 2026"

    with mock.patch.object(fetcher, "fetch_boc_rates", fake_boc), \
         mock.patch.object(fetcher, "fetch_pen_reference", fake_pen):
        rows, err = fetcher.fetch_all(selected=["PEN"])
    assert set(rows) == {"PEN"}
    assert rows["PEN"]["fallback"] is True
    assert err is None
    print("fetch_all 仅 PEN 测试通过")


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


def test_parse_wanted_filter():
    html = ("<table>"
            "<tr><td>货币名称</td><td>现汇买入价</td></tr>"
            "<tr><td>欧元</td><td>780.12</td></tr>"
            "<tr><td>美元</td><td>671.22</td></tr>"
            "</table>")
    rows = fetcher.parse_boc_html(html, wanted={"美元"})
    assert set(rows) == {"美元"}, "wanted 过滤应只返回指定币种"
    rows_all = fetcher.parse_boc_html(html)
    assert set(rows_all) == {"欧元", "美元"}, "不传 wanted 应全量解析"
    print("parse wanted 过滤/全量解析测试通过")


# ------------------------- 配置读取健壮性 -------------------------

def _write_cfg(content):
    """写入临时 config.json 并返回一个替换 config._config_path 的函数。"""
    import tempfile
    d = tempfile.mkdtemp(prefix="fx_cfg_test_")
    path = os.path.join(d, "config.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

    def fake_path():
        return path
    return fake_path


def _load_with(content):
    import config as config_mod
    with mock.patch.object(config_mod, "_config_path", _write_cfg(content)):
        return config_mod._load_config()


def test_load_config_unhashable_selected():
    """selected 含不可哈希元素（dict/list）时不应崩溃，仅保留合法项。"""
    cfg = _load_with('{"selected": [{"a": 1}, ["USD"], "USD"]}')
    assert cfg["selected"] == ["USD"], "非法元素应被过滤，仅剩合法项"
    # 全部非法时回退默认币种
    cfg2 = _load_with('{"selected": [{"a": 1}, 42]}')
    assert cfg2["selected"] == [cur.code for cur in fetcher.CURRENCIES]
    print("config 不可哈希 selected 防御测试通过")


def test_load_config_dedup_and_filter():
    """selected 去重、过滤非法代码、保留合法项。"""
    cfg = _load_with('{"selected": ["USD", "USD", "XXX", "RUB"]}')
    assert cfg["selected"] == ["USD", "RUB"]
    print("config selected 去重/过滤测试通过")


def test_load_config_malformed_geometry():
    """geometry 为畸形字符串/非字符串时按无记忆处理，合法值保留。"""
    assert _load_config_geometry_is_none('{"geometry": "abc; drop table x"}')
    assert _load_config_geometry_is_none('{"geometry": "9999999999"}')
    assert _load_config_geometry_is_none('{"geometry": "-100x200"}')
    assert _load_config_geometry_is_none('{"geometry": 123}')
    assert _load_config_geometry_is_none('{"geometry": ["640x480"]}')
    assert _load_config_geometry_is_none('{"geometry": "x480"}')
    import config as config_mod
    for geo in ("640x480", "1024x768+10-5", "440x380-20+30"):
        with mock.patch.object(config_mod, "_config_path",
                               _write_cfg('{"geometry": "%s"}' % geo)):
            assert config_mod._load_config()["geometry"] == geo, geo
    print("config 畸形 geometry 防御测试通过")


def _load_config_geometry_is_none(content):
    import config as config_mod
    with mock.patch.object(config_mod, "_config_path", _write_cfg(content)):
        return config_mod._load_config()["geometry"] is None


def test_load_config_broken_json():
    """JSON 损坏时返回默认配置。"""
    cfg = _load_with("{not valid json")
    assert cfg["selected"] == [cur.code for cur in fetcher.CURRENCIES]
    assert cfg["geometry"] is None
    print("config 损坏 JSON 回退默认测试通过")


# ------------------------- geometry 屏外钳制 -------------------------

def test_clamp_geometry():
    """屏外坐标钳回虚拟屏幕内（保留 80px 可见），屏内坐标不变。"""
    from config import _clamp_geometry
    pri = (0, 0, 1920, 1080)
    # 屏外大坐标 → 钳到右/下边界内 80px
    assert _clamp_geometry("440x380+99999+99999", screen=pri) == \
        "440x380+1840+1000"
    # 负方向屏外 → 钳到左/上边界内 80px
    assert _clamp_geometry("440x380-500-500", screen=pri) == \
        "440x380-360-300"
    # 屏内坐标原样返回（正常使用行为不变）
    assert _clamp_geometry("440x380+100+100", screen=pri) == \
        "440x380+100+100"
    assert _clamp_geometry("640x480+1760+960", screen=pri) == \
        "640x480+1760+960"   # 右下角贴边（恰好在 hi 边界内）
    # 多屏：虚拟屏起点非 0（如左侧副屏）时按虚拟屏整体范围钳制
    assert _clamp_geometry("440x380+99999+99999",
                           screen=(-1920, 0, 3840, 1080)) == \
        "440x380+1840+1000"
    # 无位置部分（仅 WxH）/ 畸形格式 → 原样返回
    assert _clamp_geometry("640x480", screen=pri) == "640x480"
    assert _clamp_geometry("garbage", screen=pri) == "garbage"
    print("geometry 屏外钳制测试通过")


if __name__ == "__main__":
    test_fetch_all_all_ok()
    test_fetch_all_boc_missing_currency()
    test_fetch_all_boc_fails_pen_succeeds()
    test_fetch_all_pen_fails()
    test_fetch_all_selected_subset()
    test_fetch_all_selected_pen_only()
    test_decode_html_charset_priority()
    test_decode_html_garbage_replace()
    test_friendly_net_error()
    test_fmt()
    test_fmt_time()
    test_parse_header_column_offset()
    test_parse_default_column_order()
    test_parse_wanted_filter()
    test_load_config_unhashable_selected()
    test_load_config_dedup_and_filter()
    test_load_config_malformed_geometry()
    test_load_config_broken_json()
    test_clamp_geometry()
    print("全部 fetch_all/工具函数离线测试通过")
