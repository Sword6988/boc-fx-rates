# 外汇现汇买入价查询

Windows 桌面小工具：实时查询中国银行官网外汇牌价页的现汇买入价（美元、卢布），以及秘鲁新索尔（PEN）的市场参考汇率（中行不挂牌，自动使用备用汇率源并标注「备用源参考」）。

- 中行牌价单位为每 100 外币兑人民币，程序自动折算为「1 外币 = X 人民币」显示
- 秘鲁新索尔备用源：open.er-api.com → currency-api@jsdelivr
- 纯标准库，无第三方依赖；需 Python 3.8+、Windows

## 运行

```text
python boc_fx_rates.py
```

命令行参数：

- `--selftest`：启动约 6 秒后自动退出（冒烟测试）
- `--debug`：在程序目录（不可写时回退 `%TEMP%`）写调试日志
- `--insecure`：证书校验失败时降级为不校验证书（默认不降级，谨慎使用）

## 打包

先准备构建环境（首次）：

```powershell
python -m venv .venv
.\.venv\Scripts\pip install pyinstaller
```

然后一键打包：

```powershell
.\build.ps1
```

产物：`dist\外汇现汇买入价查询.exe`（PyInstaller onefile，含图标、版本信息，自动清理中间产物）。

说明：`build.ps1` 优先使用 `.venv`，若 venv 缺失/失效则自动回退系统 python（需装有 PyInstaller）。Python 3.14 环境请使用 PyInstaller 6.22+（6.21 及以下不支持其 Tcl/Tk 9 打包）。

## 测试

离线测试（解析 / HTTP 逻辑，无需联网）：

```text
python tests\test_parse.py
python tests\test_http.py
```

联网抓取测试（量级校验）：

```text
python _test_fetch.py
```

## 数据源

- 中国银行：<https://www.boc.cn/sourcedb/whpj/>
- 备用汇率源：<https://open.er-api.com/v6/latest/PEN>、jsdelivr 的 currency-api
