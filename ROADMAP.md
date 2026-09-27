# 优化路线图（2026-09-27，基于 v1.3.5.0 代码现状）

> 审查范围：app.py / fetcher.py / config.py / boc_fx_rates.py / tests/ / build.ps1 / 仓库与发布流程
> 六维评分：架构 A- · 正确性 A- · 性能 A · 安全 A- · 可维护性 B+ · 工程化 B

---

## 一、发现的问题清单

### P1-1 【正确性】抓取进行中切换币种，新增币种拿不到数据
- **现象**：`_worker` 发出的是选择快照（`list(self._selected)`）。抓取期间用户勾选新币种时，`_on_currency_toggle` 里的 `refresh()` 被 `self._fetching` 拦截直接返回；在途请求又不含新币种 → 卡片停留在 `--`，提示「可点击刷新重试」，需用户手动再刷一次。
- **目标**：选择在抓取期间发生变化时，本次结果落地后自动补一次刷新。
- **预期收益**：消除一个真实的 UX 断层（多选币种用户必踩）。
- **实施成本**：小（记录请求时的快照，`_apply_result` 末尾对比当前选择，不一致则 `self.after(0, self.refresh)`，约 10 行）。
- **潜在风险**：极低；注意补刷也要防抖（仅补一次，避免连续切换堆积请求）。

### P2-1 【架构】币种领域数据寄生在网络层，config.py 反向依赖 fetcher
- **现象**：`Currency`/`CURRENCIES`/`ALL_CURRENCIES`/`CODE_TO_CURRENCY` 定义在 `fetcher.py`（网络模块），而 `config.py`（配置层）`from fetcher import CURRENCIES, CODE_TO_CURRENCY`——分层倒挂：改网络实现要动领域数据文件，配置测试隐式 import 了整个网络栈。
- **目标**：新建 `currencies.py` 承载领域模型，fetcher/config/app 三方都从它导入。
- **预期收益**：依赖方向清晰（app → fetcher → currencies，config → currencies）；`fetch_all`/`fmt` 签名不变的约定不受影响。
- **实施成本**：小（纯搬家 + 改 import，一次提交）。
- **潜在风险**：低；需同步更新三个测试文件的 import 并全量回归。

### P2-2 【工程化】无 CI——而现在 CI 的前提条件恰好已具备
- **现象**：v1.3.5 把 `config.py` 抽离后，三套测试已彻底不依赖 tkinter（纯标准库），但仓库没有 GitHub Actions。上次架构审查判 YAGNI 的理由（测试要 GUI）已失效。
- **目标**：一条 workflow：push/PR 时 `python tests/test_parse.py`、`test_http.py`、`test_fetchall.py` 全绿。
- **预期收益**：防回归零成本化；配合 tag 推送可在同一 workflow 里自动创建 Release + 传附件（本轮手工做了一遍，正需要固化）。
- **实施成本**：小（一个 yaml，约 30 行；Release job 可复用 softprops/action-gh-release）。
- **潜在风险**：低；Actions 用 `on: push: tags: ['v*']` 时注意与普通 push 分开触发。

### P2-3 【工程化】发布流程全手工，版本号无单一来源
- **现象**：本轮发布 = 手动 build.ps1 → 手动打 tag → 手动 API 建 Release → 手动把中文名 exe 改 ASCII 名上传。且版本号只存在于 `version_info.txt`，代码/README 靠人肉同步。
- **目标**：`release.ps1 <版本>` 一键完成构建 + 自检（--selftest）+ 提交 tag + 创建 Release 附件；版本号从 `version_info.txt` 提取供 README 校验。
- **预期收益**：发布从约 10 步手工降为 1 步，杜绝忘传附件/版本漏改。
- **实施成本**：中（脚本 + 凭据从 git credential 取用，参考本轮 API 调用方式）。
- **潜在风险**：中低；脚本含 push 与网络上传，失败中断点需明确（建议分阶段输出，构建成功后再动远端）。

### P3-1 【性能/体验】切换币种触发全量重新抓网
- **现象**：`_on_currency_toggle` 每次都 `refresh()` 走完整网络流程（中行 15s 超时上限），即使本次会话刚抓过同样的数据。
- **目标**：内存中保留最近一次 `rows`（会话级，非磁盘），切换币种时先用旧数据即时渲染，再后台刷新补最新。
- **预期收益**：切换即时显示，弱网下体验显著提升。
- **实施成本**：中（App 增加 `self._last_rows`；红线允许——这是内存态，不落 json、不自动定时刷新）。
- **潜在风险**：低；需保证旧数据带「上次数据」语义标记，避免误读为刚抓取。

### P3-2 【健壮性】crash.log 无限增长
- **现象**：`_log_crash` 只追加无上限。仅在异常时写、单条很小，但长期置之不理理论可膨胀。
- **目标**：写入前检查，超过 1MB 时重命名轮转（保留一代）。
- **预期收益**：消除长尾风险。
- **实施成本**：极小（5 行）。
- **潜在风险**：几乎无。

### P3-3 【可维护性】测试无统一入口
- **现象**：三套测试需逐个 `python tests/test_xxx.py` 运行，CI 化之前手感差。
- **目标**：`run_tests.py` 顺序跑三套并汇总退出码（CI 与本地共用）。
- **实施成本**：极小。
- **潜在风险**：无。

### 记录为「接受现状」（不建议动）
- `_poll_queue` 100ms 轮询：空转开销可忽略，事件化收益不抵复杂度。
- `fetch_all` 每次新建 `ThreadPoolExecutor`：每次刷新 ≤4 线程、秒级周期，无复用价值。
- `ALLOW_INSECURE` 模块级全局：仅入口一处置位，参数化收益小。
- app.py 757 行单类：界面组件强耦合 Tk 生命周期，拆分是负收益（维持上轮 YAGNI 结论）。
- 不做磁盘缓存/自动刷新/深色模式/涨跌对比：产品红线，维持。

---

## 二、推荐实施顺序

| 顺序 | 事项 | 类型 | 成本 | 理由 |
|---|---|---|---|---|
| 1 | P1-1 抓取中切换币种补刷 | 正确性 | 小 | 唯一的行为缺陷，优先清掉 |
| 2 | P3-3 run_tests.py + P3-2 crash.log 轮转 | 收尾 | 极小 | 为 CI 铺路，顺手完成 |
| 3 | P2-1 currencies.py 领域抽离 | 架构 | 小 | 独立提交，回归验证后进入工程化阶段 |
| 4 | P2-2 GitHub Actions CI | 工程化 | 小 | 测试已无 GUI 依赖，防线先立起来 |
| 5 | P2-3 release.ps1 + 版本单一来源 | 工程化 | 中 | CI 就绪后把发布挂到 tag 触发 |
| 6 | P3-1 会话级数据复用 | 体验 | 中 | 可选；前五项稳定后再做 |

节奏建议：1–3 项一次提交（v1.3.6），4–5 项一次提交（v1.3.7，首个带自动 Release 的版本），第 6 项视使用体验决定是否做。
