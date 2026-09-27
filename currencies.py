# -*- coding: utf-8 -*-
"""币种领域模型：币种定义与索引，不依赖网络/UI。

fetcher.py（抓取）、config.py（配置校验）、app.py（界面）均从本模块
导入币种数据，保证依赖方向清晰：领域模型在最底层，不反向依赖任何
上层模块。
"""

from collections import namedtuple

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
