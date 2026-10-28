# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

nodescope：对机场订阅节点批量测延迟（TCP 直连 / HTTP 经节点）、下载速度、落地地区与流媒体/AI 解锁。本地**不实现任何代理协议**，全部交给 mihomo (Clash.Meta) 内核落地。`core.find_binary` 查找顺序：`NS_MIHOMO_BIN` → PATH → 项目根目录 `bin/`（`start.sh` 自动下载的位置，已 gitignore）。

## 常用命令

```bash
./start.sh                                # 一键启动：建 .venv、装依赖、缺内核时下载到 bin/mihomo，再 --serve
start.bat                                 # Windows 一键启动（调用 start.ps1，须保持 UTF-8 BOM + CRLF，见 .gitattributes）
pip3 install -r requirements.txt          # 或 pip3 install -e .（console script: nodescope = run:main）

python3 run.py --serve                    # Web 模式，默认 127.0.0.1:8765，端口被占用时自动顺延
python3 run.py <订阅URL或文件> [--no-speed] [--list-only] [--include REGEX] ...

ruff check .                              # lint 配置见 pyproject.toml
python3 -m pytest                         # 全部测试（pythonpath=., testpaths=tests）
python3 -m pytest tests/test_regressions.py -k <关键字>   # 单个测试

# 端到端自测：本地 SOCKS5 服务器充当“真实节点”
python3 tests/socks5_server.py 31111 &
python3 run.py test-local.yaml --speed-seconds 4
```

## 架构

入口 `run.py`（CLI，位于根目录，不在包内；pyproject 通过 `py-modules = ["run"]` 打包）→ 包 `nodescope/`：

- **subscription.py** — 订阅读取与解析。`load_nodes()` 返回 `(nodes, warns, userinfo)`，节点统一为 **Clash proxy dict**（后续所有模块都用这个格式）。支持 Clash YAML、Base64/明文 URI（ss/vmess/vless/trojan/hy2/tuic/anytls）、QuantumultX。对 URL 订阅会再用 `FALLBACK_UAS` 补拉一次并按 `_node_key` 去重合并（机场会按 UA 下发不同协议）；多行纯 URL 输入视为多订阅合并。反向导出 `node_to_uri` / `node_to_singbox` 也在这里。`dedupe_names()` 必须幂等，否则重名会导致 mihomo 拒绝启动。
- **core.py** — `MihomoRunner`（上下文管理器）：把所有节点写进一份 mihomo 配置，**每个节点对应一个 `mixed` listener 端口**（从 base_port 起连续分配，`runner.ports[name] -> port`），测速代码只需走 `127.0.0.1:端口` 的 HTTP/SOCKS。mihomo 端口 bind 失败时不会退出，所以启动前用 `find_free_base_port` 确保整段端口空闲。任一节点非法时 mihomo 会整体拒绝启动（日志 `Parse config error: proxy N:`），`_start_skipping_bad_proxies` 会剔除该节点后重试，被剔除的节点记在 `runner.rejected`。
  - **残留清理**：工作目录 `nodescope-*` 的 config.yaml 含节点密码，必须删干净。三层保障：正常路径 `_stop()`（加锁，可重复调用）；进程退出时 `atexit` → `terminate_all()` 对所有 `_LIVE_RUNNERS` 执行 `_stop()`（`run.py` 把 SIGTERM/SIGHUP 转成 KeyboardInterrupt，才能走到 atexit）；`kill -9`/崩溃则靠下次启动时的 `cleanup_stale()`，按目录里的 `owner.json` 判断创建者是否已死，只在内核命令行含该目录时才结束它（防 PID 复用误杀）。
- **iface.py** — 绕过本机 TUN/fake-ip：自动选物理网卡（`--iface` / `NS_IFACE`），在绑定网卡的 socket 上做独立 DNS 查询；mihomo 配置里同时设置 `interface-name` 和独立 DNS。网卡探测与 DNS 结果带 TTL 缓存（`_cached`，测试里用 `clear_cache()` 重置）。
- **tester.py** — `test_all()` 是**生产者-消费者流水线**：探活线程池（`concurrency`）跑 `test_node`（TCP ping、HTTP 延迟、geo、解锁，此阶段 `speed=False`；geo 走 ip-api，同一请求顺带取机房/移动/代理标记，由 `ip_quality()` 归纳成 `result["ip_quality"]`），探活成功的节点立即推入 `speed_q`，由 `speed_concurrency` 个测速线程执行下载测速。结果经 `progress_cb(name, result)` 回调推出；同一节点会回调**两次**（探活完成、测速完成后置 `_speed_tested=True`），CLI 进度条和 Web 前端都依赖这个标记推进进度。全程通过 `cancel_event` 支持取消。`local_bandwidth_mbps` 直连测本机带宽基线，用于标记“≈本机”。测速源探测较贵，`test_all` 按落地国家代码缓存首个成功的源并作为 `hint` 传给 `measure_download`，同地区后续节点跳过探测。Cloudflare `__down` 对 `bytes >= 1e8` 返回 403，改测速源时注意。
- **webapp.py** — 基于标准库 `ThreadingHTTPServer`，无框架。`POST /api/task`（仅 `application/json`）存参数并返回一次性 taskId，`GET /api/stream?taskId=` 以 SSE 推送 `meta/status/result/done/error` 事件；不能用 query 参数直接开测。所有请求都经 `_check_origin` 校验 Host 为本机回环地址、Origin 同源（防跨站触发与 DNS rebinding，`meta` 事件里带完整节点密码）；Web 模式调用 `load_nodes(..., allow_file=False)`，不读取本机文件。客户端断开时 `BrokenPipeError` 会置位 `cancel_event`，停止整场测试。每个会话用 `_next_base_port` 错开 mihomo 端口段。
- **static/index.html** — Web 前端是单个 HTML 文件（样式、脚本内联），不引用任何外部资源（首页带 CSP 兜底）。解锁平台图标放在 `static/icons/*.svg`，由 webapp 的 `/icons/<名>.svg` 路由提供（文件名白名单）。两者都靠 package-data 配置打包。
- **report.py** — rich 终端表格、JSON/HTML 报告，以及 Clash YAML / Base64 / Sing-box 可用节点导出。排序：速度降序 → HTTP 延迟升序 → TCP 延迟升序，无数据的行始终排在最后。

## 约定

- 网络/测速代码里刻意使用宽泛的 `except Exception` 兜底（单个节点出错不能中断整场测试），并以 `# noqa: BLE001 - 原因` 注明理由；新增时沿用这个写法。
- `tests/test_regressions.py` 是每个历史 bug 各一条的回归用例（文件头有清单），修 bug 时往这里追加用例并更新清单。
- 目标 Python 3.8（ruff `target-version = "py38"`），不要使用更新版本才有的语法。
