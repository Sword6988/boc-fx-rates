# -*- coding: utf-8 -*-
"""中行汇率换算（Windows 桌面程序）— 程序入口。

数据抓取/解析/缓存：fetcher.py；界面：app.py。
运行：python boc_fx_rates.py [--selftest] [--debug] [--insecure]
"""

from app import main


if __name__ == "__main__":
    main()
