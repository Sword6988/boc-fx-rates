# -*- coding: utf-8 -*-
"""抓取与解析层：中行牌价页、秘鲁备用源。

仅标准库，不依赖界面（tkinter）。界面层（app.py）通过
fetch_all() / fmt() 使用本模块。
"""

import html.parser
import json
import logging
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor

# ----------------------------- 配置 -----------------------------

BOC_URL = "https://www.boc.cn/sourcedb/whpj/"

Currency = namedtuple("Currency", "name code display symbol")

# (中行牌价页货币名, 货币代码, 界面显示名, 货币符号)
# 默认选中（界面初始显示）
CURRENCIES = [
    Currency("美元", "USD", "美元", "$"),
    Currency("卢布", "RUB", "卢布", "\u20bd"),          # ₽
    Currency("秘鲁新索尔", "PEN", "秘鲁新索尔", "S/"),
]

# 全部可选币种：中行牌价页挂牌的 29 种（页面货币名原样匹配）+ 秘鲁新索尔（备用源）。
# 符号仅为装饰；个别符号（如 ₮ ₺ ฿）依赖系统字体，缺失时标题仍显示中文名与代码。
ALL_CURRENCIES = CURRENCIES + [
    Currency("澳大利亚元", "AUD", "澳大利亚元", "A$"),
    Currency("加拿大元", "CAD", "加拿大元", "C$"),
    Currency("瑞士法郎", "CHF", "瑞士法郎", "CHF"),
    Currency("丹麦克朗", "DKK", "丹麦克朗", "kr"),
    Currency("欧元", "EUR", "欧元", "\u20ac"),           # €
    Currency("英镑", "GBP", "英镑", "\u00a3"),          # £
    Currency("港币", "HKD", "港币", "HK$"),
    Currency("匈牙利福林", "HUF", "匈牙利福林", "Ft"),
    Currency("印尼卢比", "IDR", "印尼卢比", "Rp"),
    Currency("日元", "JPY", "日元", "\u00a5"),          # ¥
    Currency("韩国元", "KRW", "韩国元", "\u20a9"),      # ₩
    Currency("蒙古图格里克", "MNT", "蒙古图格里克", "\u20ae"),   # ₮
    Currency("澳门元", "MOP", "澳门元", "MOP$"),
    Currency("墨西哥比索", "MXN", "墨西哥比索", "Mex$"),
    Currency("林吉特", "MYR", "马来西亚林吉特", "RM"),
    Currency("挪威克朗", "NOK", "挪威克朗", "kr"),
    Currency("新西兰元", "NZD", "新西兰元", "NZ$"),
    Currency("菲律宾比索", "PHP", "菲律宾比索", "\u20b1"),      # ₱
    Currency("卡塔尔里亚尔", "QAR", "卡塔尔里亚尔", "QR"),
    Currency("塞尔维亚第纳尔", "RSD", "塞尔维亚第纳尔", "din"),
    Currency("沙特里亚尔", "SAR", "沙特里亚尔", "SR"),
    Currency("瑞典克朗", "SEK", "瑞典克朗", "kr"),
    Currency("新加坡元", "SGD", "新加坡元", "S$"),
    Currency("泰国铢", "THB", "泰国铢", "\u0e3f"),      # ฿
    Currency("土耳其里拉", "TRY", "土耳其里拉", "\u20ba"),      # ₺
    Currency("南非兰特", "ZAR", "南非兰特", "R"),
    Currency("阿联酋迪拉姆", "AED", "阿联酋迪拉姆", "AED"),
]

# 币种代码 → 币种定义（供按代码快速索引）
CODE_TO_CURRENCY = {cur.code: cur for cur in ALL_CURRENCIES}

# 秘鲁新索尔备用汇率源（返回 1 PEN 兑人民币的市场参考汇率，按顺序尝试）
# 注意：jsdelivr 上 @latest / @1 写法分别会超时或 404，不加版本号
# 默认取 npm 最新 tag，实测可用
PEN_FALLBACKS = [
    ("open.er-api.com", "https://open.er-api.com/v6/latest/PEN"),
    ("currency-api@jsdelivr",
     "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api/v1/currencies/pen.json"),
]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

MAX_RESPONSE_BYTES = 2 * 1024 * 1024   # 单个响应上限 2MB

# 默认关闭证书降级。仅当用户显式传入 --insecure（入口处置为 True）时，
# 证书校验失败才降级为不校验证书重试一次；否则证书问题直接报错，
# 避免静默跳过证书校验带来的中间人攻击风险。
ALLOW_INSECURE = False

# --------------------------- 网络 ---------------------------

def _read_limited(resp):
    """读取响应并限制最大字节数，防止异常大响应占用过多内存。"""
    try:
        length = int(resp.headers.get("Content-Length", 0))
    except (TypeError, ValueError):
        length = 0
    if length > MAX_RESPONSE_BYTES:
        raise ValueError("响应过大（Content-Length=%d）" % length)
    data = resp.read(MAX_RESPONSE_BYTES + 1)
    if len(data) > MAX_RESPONSE_BYTES:
        raise ValueError("响应超过 %d 字节上限" % MAX_RESPONSE_BYTES)
    return data


def _http_get(url, timeout=15, retries=1):
    """GET 请求，返回 (响应字节, 响应头)。

    - 仅当显式开启 ALLOW_INSECURE 且证书校验失败时，才降级为不校验证书
      重试一次；默认证书问题直接失败，不静默跳过证书校验；
    - 瞬时网络错误（超时/连接中断/SSL 错误）最多重试 retries 次。
      同时捕获 URLError 包装形式与连接/读取阶段直接抛出的裸异常
      （如 resp.read() 阶段的 TimeoutError）；
    - 默认超时 15s（最坏等待 ≈ 15s × 2 次请求 = 30s）。
    """
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return _read_limited(resp), resp.headers
        except (urllib.error.URLError, TimeoutError, ConnectionError,
                ssl.SSLError) as e:
            reason = getattr(e, "reason", e)
            if isinstance(reason, ssl.SSLCertVerificationError):
                if not ALLOW_INSECURE:
                    raise   # 默认不降级：证书问题即失败
                # 仅此场景降级为不校验证书，重试一次
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                    return _read_limited(resp), resp.headers
            if attempt >= retries:
                raise
            if not isinstance(reason, (TimeoutError, socket.timeout, ConnectionError,
                                       ssl.SSLError)):
                raise   # HTTP 4xx/5xx 等非瞬时错误不重试
            time.sleep(0.5)


def _friendly_net_error(e):
    """把网络层异常映射为易懂的中文提示。"""
    reason = getattr(e, "reason", e)
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return "连接超时"
    if isinstance(reason, ssl.SSLCertVerificationError):
        return "网站证书校验失败"
    if isinstance(e, urllib.error.HTTPError):
        return "服务器返回 HTTP %s" % e.code
    if isinstance(reason, (ssl.SSLError, ConnectionError, OSError)):
        return "网络连接失败（%s）" % _brief(reason)
    if isinstance(e, json.JSONDecodeError):
        return "数据解析失败（返回内容不是有效 JSON）"
    return _brief(e)

# --------------------------- 解析 ---------------------------

class _TableParser(html.parser.HTMLParser):
    """一次性提取页面中所有 <tr> 行的单元格文本（td/th 均可）。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("td", "th") and self._cell is not None:
            self._row.append(self._clean("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    @staticmethod
    def _clean(txt):
        txt = txt.replace("\u00a0", " ").replace("\u3000", " ")
        return re.sub(r"\s+", " ", txt).strip()


def _decode_html(raw, headers):
    """按响应头 charset 优先解码 HTML；失败再依次尝试 utf-8-sig / gb18030。"""
    charset = headers.get_content_charset() if headers else None
    for enc in (charset, "utf-8-sig", "gb18030"):
        if not enc:
            continue
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8-sig", "replace")


def parse_boc_html(text, wanted=None):
    """从中行牌价页 HTML 提取现汇买入价（纯解析，可离线测试）。

    wanted：币种名集合；None 时返回页面全部可解析币种。
    返回 {货币名: {"buy": 现汇买入价(每100外币兑人民币), "time": "YYYY/MM/DD HH:MM:SS"}}
    """
    parser = _TableParser()
    parser.feed(text)
    rows = parser.rows

    # 从表头定位「货币名称」与「现汇买入价」列，避免列序变化导致取错
    name_idx, buy_idx = 0, 1
    for cells in rows:
        if "货币名称" in cells:
            name_idx = cells.index("货币名称")
            if "现汇买入价" in cells:
                buy_idx = cells.index("现汇买入价")
            else:
                logging.warning("牌价页表头缺少「现汇买入价」，使用默认列序")
            break
    else:
        logging.warning("牌价页未找到表头「货币名称」，使用默认列序")

    wanted = set(wanted) if wanted is not None else None
    result = {}
    for cells in rows:
        if len(cells) <= max(name_idx, buy_idx):
            continue
        name = cells[name_idx]
        if not name or (wanted is not None and name not in wanted):
            continue
        try:
            buy = float(cells[buy_idx])
        except (TypeError, ValueError):
            continue
        joined = " ".join(cells)
        dm = re.search(r"\d{4}[/-]\d{1,2}[/-]\d{1,2}", joined)
        tm = re.search(r"\d{1,2}:\d{2}(:\d{2})?", joined)
        when = " ".join(p for p in (dm.group(0) if dm else "",
                                    tm.group(0) if tm else "") if p)
        result[name] = {"buy": buy, "time": when}
    return result


def fetch_boc_rates():
    """抓取中行外汇牌价页并解析（返回页面全部挂牌币种）。返回同 parse_boc_html。"""
    raw, headers = _http_get(BOC_URL)
    return parse_boc_html(_decode_html(raw, headers))


def _fetch_pen_source(label, url):
    """尝试单个备用源，返回 (rate, label, date)；失败抛出异常。"""
    data, _headers = _http_get(url)
    data = json.loads(data.decode("utf-8-sig", "replace"))
    rate, date = None, ""
    if "er-api.com" in url:
        if data.get("result") == "success":
            # 新版接口字段为 rates，旧版为 conversion_rates，两者都兼容
            rates_tbl = (data.get("rates") or data.get("conversion_rates") or {})
            rate = rates_tbl.get("CNY")
            m = re.search(r"\d{2} \w{3} \d{4}",
                          data.get("time_last_update_utc") or "")
            date = m.group(0) if m else ""
    else:
        rate = (data.get("pen") or {}).get("cny")
        date = data.get("date", "")
    if rate is None:
        raise ValueError("响应中无 CNY 汇率")
    return float(rate), label, date


def fetch_pen_reference():
    """备用源获取 1 秘鲁新索尔(PEN) 兑人民币的市场参考汇率。

    两个备用源并行请求，但按 PEN_FALLBACKS 顺序固定优先级取结果：
    主源（er-api）成功就只用主源，主源失败才用备源，避免两个源
    参考价不同导致显示值来回跳变。

    主源成功后立即取消/放弃等待备源（wait=False），整体等待时间
    约等于主源单次耗时；备源若已在运行则自然结束，不影响返回。

    返回 (rate, 来源标签, 日期字符串)
    """
    last_exc = None
    pool = ThreadPoolExecutor(max_workers=len(PEN_FALLBACKS))
    futures = {}
    try:
        futures = {label: pool.submit(_fetch_pen_source, label, url)
                   for label, url in PEN_FALLBACKS}
        for label, _url in PEN_FALLBACKS:
            try:
                return futures[label].result()
            except Exception as e:
                last_exc = e
                logging.warning("备用汇率源 %s 失败", label, exc_info=True)
    finally:
        # 主源已成功（或全部失败）后：取消未启动的备源任务，且不阻塞等待
        # 仍在运行的备源（用 with 会 shutdown(wait=True) 白等慢源）
        for fut in futures.values():
            fut.cancel()
        pool.shutdown(wait=False)
    if last_exc is not None:
        raise RuntimeError("备用汇率源均不可用（%s）"
                           % _friendly_net_error(last_exc)) from last_exc
    raise RuntimeError("备用汇率源均不可用")


def _brief(e):
    s = str(e)
    return s if len(s) <= 80 else s[:80] + "..."


def fetch_all(selected=None):
    """抓取所选币种数据。

    selected：币种代码列表（如 ["USD", "PEN"]）；None 时用默认 CURRENCIES。
    中行牌价与秘鲁备用源相互独立，并行抓取以缩短等待时间；
    未选中 PEN 时不会请求备用源。

    返回 (rows, error)：
      rows  {code: {"rate100","rate1","source","time","fallback"}}，失败的币种不在其中
      error None 或错误描述文本
    """
    if selected is None:
        selected = [cur.code for cur in CURRENCIES]
    selected_set = set(selected)
    need_pen = "PEN" in selected_set

    rows, errors = {}, []
    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_boc = pool.submit(fetch_boc_rates)
        fut_pen = pool.submit(fetch_pen_reference) if need_pen else None

        try:
            boc = fut_boc.result()
        except Exception as e:
            boc = {}
            errors.append("中行牌价页获取失败（%s）" % _friendly_net_error(e))
            logging.warning("中行牌价页获取失败", exc_info=True)

        for cur in ALL_CURRENCIES:
            if cur.code not in selected_set:
                continue
            item = boc.get(cur.name)
            if item:
                rows[cur.code] = {
                    "rate100": item["buy"],
                    "rate1": item["buy"] / 100.0,
                    "source": "中国银行官网 · 现汇买入价",
                    "time": item["time"],
                    "fallback": False,
                }
            elif cur.code != "PEN" and boc:
                errors.append("中行牌价页未找到「%s」" % cur.name)

        # 中行无秘鲁新索尔牌价（预期情况），改用备用源
        if need_pen and "PEN" not in rows:
            try:
                rate, label, date = fut_pen.result()
                rows["PEN"] = {
                    "rate100": rate * 100.0,
                    "rate1": rate,
                    "source": "备用汇率源 %s · 市场参考汇率" % label,
                    "time": date,
                    "fallback": True,
                }
            except Exception as e:
                errors.append("秘鲁新索尔备用源失败（%s）" % _friendly_net_error(e))
                logging.warning("秘鲁新索尔备用源失败", exc_info=True)
    return rows, ("；".join(errors) or None)


def fmt(v):
    """按数值量级选择合适的小数位数。"""
    if v is None:
        return "--"
    if v >= 100:
        return "%.2f" % v
    if v >= 0.01:
        return "%.4f" % v
    return "%.6f" % v
