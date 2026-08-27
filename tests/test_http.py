# -*- coding: utf-8 -*-
"""离线测试：_http_get 的重试 / 不重试 / 证书降级 / 大小上限（不联网）。

运行：python tests/test_http.py
"""
import os
import socket
import ssl
import sys
import urllib.error

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import fetcher


class _FakeResp:
    def __init__(self, data=b"ok", headers=None):
        self._data = data
        self.headers = headers or {}

    def read(self, *args):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _patch_urlopen(fake):
    orig = fetcher.urllib.request.urlopen
    fetcher.urllib.request.urlopen = fake
    return orig


def _cert_error():
    try:
        return ssl.SSLCertVerificationError("certificate verify failed")
    except TypeError:
        return ssl.SSLCertVerificationError()


def test_success():
    calls = []

    def fake(req, timeout=None, context=None):
        calls.append(1)
        return _FakeResp()

    orig = _patch_urlopen(fake)
    try:
        data, headers = fetcher._http_get("http://example.com/x")
        assert data == b"ok"
        assert headers == {}
    finally:
        fetcher.urllib.request.urlopen = orig
    assert len(calls) == 1
    print("正常请求测试通过")


def test_timeout_retries_once():
    calls = []

    def fake(req, timeout=None, context=None):
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.URLError(socket.timeout("timed out"))
        return _FakeResp()

    orig = _patch_urlopen(fake)
    try:
        data, _h = fetcher._http_get("http://example.com/x", timeout=5)
        assert data == b"ok"
    finally:
        fetcher.urllib.request.urlopen = orig
    assert len(calls) == 2, "超时应重试 1 次"
    print("超时重试测试通过")


def test_http_error_no_retry():
    calls = []

    def fake(req, timeout=None, context=None):
        calls.append(1)
        raise urllib.error.HTTPError("http://example.com/x", 404, "Not Found",
                                     None, None)

    orig = _patch_urlopen(fake)
    try:
        try:
            fetcher._http_get("http://example.com/x")
            raise AssertionError("应抛出 HTTPError")
        except urllib.error.HTTPError:
            pass
    finally:
        fetcher.urllib.request.urlopen = orig
    assert len(calls) == 1, "HTTP 错误不应重试"
    print("HTTP 404 不重试测试通过")


def test_cert_error_default_no_fallback():
    calls = []

    def fake(req, timeout=None, context=None):
        calls.append(context)
        raise urllib.error.URLError(_cert_error())

    orig = _patch_urlopen(fake)
    try:
        try:
            fetcher._http_get("https://example.com/x")
            raise AssertionError("默认应因证书错误失败")
        except urllib.error.URLError:
            pass
    finally:
        fetcher.urllib.request.urlopen = orig
    assert len(calls) == 1, "默认不应降级重试"
    print("证书错误默认不降级测试通过")


def test_cert_error_falls_back_with_allow_insecure():
    calls = []
    saved = fetcher.ALLOW_INSECURE
    fetcher.ALLOW_INSECURE = True

    def fake(req, timeout=None, context=None):
        calls.append(context)
        if len(calls) == 1:
            raise urllib.error.URLError(_cert_error())
        return _FakeResp()

    orig = _patch_urlopen(fake)
    try:
        try:
            data, _h = fetcher._http_get("https://example.com/x")
        finally:
            fetcher.ALLOW_INSECURE = saved
        assert data == b"ok"
    finally:
        fetcher.urllib.request.urlopen = orig
    assert len(calls) == 2, "开启 --insecure 后证书错误应降级重试一次"
    assert calls[1] is not None and calls[1].verify_mode == ssl.CERT_NONE, \
        "降级应不校验证书"
    print("开启 --insecure 证书降级测试通过")


def test_response_size_limit():
    def fake(req, timeout=None, context=None):
        return _FakeResp(data=b"x" * (fetcher.MAX_RESPONSE_BYTES + 10))

    orig = _patch_urlopen(fake)
    try:
        try:
            fetcher._http_get("http://example.com/x")
            raise AssertionError("应因响应过大失败")
        except ValueError as e:
            assert "响应" in str(e)
    finally:
        fetcher.urllib.request.urlopen = orig
    print("响应大小上限测试通过")


def test_content_length_limit():
    def fake(req, timeout=None, context=None):
        return _FakeResp(headers={"Content-Length": str(fetcher.MAX_RESPONSE_BYTES + 1)})

    orig = _patch_urlopen(fake)
    try:
        try:
            fetcher._http_get("http://example.com/x")
            raise AssertionError("应因 Content-Length 过大失败")
        except ValueError:
            pass
    finally:
        fetcher.urllib.request.urlopen = orig
    print("Content-Length 上限测试通过")


def test_pen_source_priority():
    orig = fetcher._fetch_pen_source

    def fake(label, url):
        calls.append(label)
        if "er-api.com" in url:
            return 2.0097, label, "2026-08-26"
        raise RuntimeError("备源模拟失败")

    calls = []
    fetcher._fetch_pen_source = fake
    try:
        rate, label, _date = fetcher.fetch_pen_reference()
        assert label == "open.er-api.com", "主源成功时应使用主源"
        assert abs(rate - 2.0097) < 1e-6
    finally:
        fetcher._fetch_pen_source = orig
    assert "open.er-api.com" in calls, "应调用主源"

    calls2 = []

    def fake2(label, url):
        calls2.append(label)
        if "er-api.com" in url:
            raise RuntimeError("主源模拟失败")
        return 2.0041, label, "2026-08-26"

    fetcher._fetch_pen_source = fake2
    try:
        rate, label, _date = fetcher.fetch_pen_reference()
        assert label == "currency-api@jsdelivr", "主源失败时才用备源"
        assert abs(rate - 2.0041) < 1e-6
    finally:
        fetcher._fetch_pen_source = orig
    print("备用源优先级测试通过")


if __name__ == "__main__":
    test_success()
    test_timeout_retries_once()
    test_http_error_no_retry()
    test_cert_error_default_no_fallback()
    test_cert_error_falls_back_with_allow_insecure()
    test_response_size_limit()
    test_content_length_limit()
    test_pen_source_priority()
    print("全部 HTTP 逻辑离线测试通过")
