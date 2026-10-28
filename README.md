# nodescope 订阅节点测速工具

对机场订阅中的节点批量测试 **延迟（TCP 直连 / HTTP 经节点）**、**下载速度**、**落地地区与出口 IP 质量**，以及 **AI / 流媒体解锁**，输出排名表格并支持 JSON / HTML 报告与可用节点导出。

## 工作原理

```
订阅 URL / 文件 ──► 解析节点(Clash YAML / Base64 URI 列表)
                          │
                          ▼
              生成 mihomo 配置并启动内核
        每个节点绑定一个本地 mixed 端口 (21001+N)
                          │
                          ▼
     ┌────────────────────┴────────────────────┐
     │ TCP 延迟: 直连 server:port 三次取中位数   │
     │ HTTP 延迟: 经节点请求 gstatic 204 页面    │
     │ 落地/IP: 经节点查 ip-api（含机房/代理标记）│
     │ 解锁: 经节点探测 AI 与流媒体平台          │
     │ 下载速度: 经节点并发拉取测速文件按窗计时    │
     └─────────────────────────────────────────┘
```

本地不实现任何代理协议（ss/vmess/vless/trojan/hysteria2…），而是依赖
[mihomo (Clash.Meta) 内核](https://github.com/MetaCubeX/mihomo)落地协议，
所以只要 mihomo 支持的协议都能测。

## 一键启动

```bash
./start.sh                # macOS / Linux，可追加 run.py 参数，如 ./start.sh --port 9000
start.bat                 # Windows，也可以直接双击（内部调用 start.ps1）
```

只需要系统里有 Python 3.8+（macOS / Linux 另需 curl，Windows 用系统自带的 PowerShell 下载）。脚本会自动：

1. 在 `.venv/` 建虚拟环境并安装依赖（`requirements.txt` 没变就跳过）
2. 找 mihomo 内核：`NS_MIHOMO_BIN` → PATH 里的 `mihomo`/`clash-meta` → 项目 `bin/mihomo`；
   都没有就按系统和架构从 GitHub 下载到 `bin/`（版本可用 `NS_MIHOMO_VERSION` 指定）
3. 启动 Web 界面

网络慢时可加速：

```bash
NS_GITHUB_PROXY=https://ghfast.top/ PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple ./start.sh
```

Windows（cmd）：

```bat
set "NS_GITHUB_PROXY=https://ghfast.top/"
set "PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple"
start.bat
```

## 手动安装

```bash
# 1. mihomo 内核（已装可跳过）
brew install mihomo
# 或指定内核路径: export NS_MIHOMO_BIN=/path/to/mihomo

# 2. Python 依赖
pip3 install -r requirements.txt
```

## 使用

```bash
# 启动 Web 界面（推荐）：浏览器粘贴订阅、实时看结果
python3 run.py --serve            # 默认 http://127.0.0.1:8765（占用则自动换下一个）
python3 run.py --serve --port 9000

# 完整测试（延迟 + 速度）
python3 run.py "https://订阅地址/api/v1/client/subscribe?token=xxx"

# 用本地订阅文件
python3 run.py clash-config.yaml

# 只测延迟（快）
python3 run.py sub.txt --no-speed

# 只显示前 5 名 + 导出 JSON
python3 run.py sub.txt --top 5 --json result.json

# 导出 HTML 报告（浏览器直接打开，可点击表头排序）
python3 run.py sub.txt --html report.html

# 只测包含"香港"的节点
python3 run.py sub.txt --include 香港

# 只列出解析到的节点，不测试
python3 run.py sub.txt --list-only
```

### 全部选项

| 选项 | 默认 | 说明 |
|---|---|---|
| `--no-speed` | 关 | 只测延迟，不下载测速 |
| `--speed-seconds N` | 5 | 每节点测速时长（秒） |
| `--speed-threads N` | 8 | 每节点测速并发连接数 |
| `--max-download-mb MB` | 30 | 每节点测速（含探测）最大下载流量，到量即停（默认 30MB） |
| `--concurrency N` | 8 | 同时测试的节点数 |
| `--speed-concurrency N` | 1 | 同时进行下载测速的节点数；>1 时各节点分抢本机带宽，快节点抢得多，适合所有节点速度都撞本机带宽上限时区分真实快慢 |
| `--samples N` | 3 | TCP 直连延迟采样次数 |
| `--timeout N` | 5 | 单次请求超时（秒） |
| `--include REGEX` | - | 只测名称匹配的节点 |
| `--exclude REGEX` | - | 排除名称匹配的节点 |
| `--top N` | 全部 | 只显示前 N 名 |
| `--json PATH` | - | 导出 JSON 结果 |
| `--html PATH` | - | 导出自包含 HTML 报告（点击表头可排序） |
| `--export-nodes PATH` | - | 导出测试通过的可用节点为 Clash 订阅配置（.yaml） |
| `--export-base64 PATH` | - | 导出测试通过的可用节点为标准 Base64 订阅文本 |
| `--export-singbox PATH` | - | 导出测试通过的可用节点为 Sing-box 配置（.json） |
| `--no-unlock` | 关 | 跳过 AI（ChatGPT / Claude / Gemini）与流媒体（YouTube / Netflix / Disney+ / TikTok / B站港澳台）解锁探测（默认开启） |
| `--list-only` | 关 | 只列出节点不测试 |
| `--no-proxy-test` | 关 | 跳过 mihomo，仅测 TCP 直连延迟 |
| `--keep` | 关 | 保留 mihomo 临时配置目录（排查用） |
| `--iface NAME` | 自动 | 出站网卡；默认选物理网卡以绕过本机 TUN |

## Web 界面模式

`python3 run.py --serve` 后浏览器打开 `http://127.0.0.1:8765`：

- 粘贴订阅链接或订阅内容即可测速（支持 Clash / Base64 / QuantumultX 格式）；
  出于安全考虑 Web 模式不读取本机文件路径，本地文件请粘贴内容或用 CLI
- SSE 实时推送：每个节点测完立刻上屏，无需等全部结束
- 可选参数：测速时长、每节点流量上限、同时测速节点数、探活并发、只测延迟（勾选后测速选项置灰，速度列收窄）
- 自动检测 TUN/fake-ip：出站绑定物理网卡 + 独立 DNS，开着 Clash TUN 也能测
- 浅色界面，结果按速度实时排名，点击表头可按任意列升/降序排序（无数据行始终靠后）
- **流式流水线**：探活成功的节点立刻推入测速队列，边探活边测速，零等待卡顿
- **动态瞬时速度**：测速时每 200ms 实时推送并跳动刷新当前下载速度，过程完全可见
- **即时搜索与过滤**：支持根据节点名称、协议、落地国家、IP 类型（家宽/机房/移动）、解锁平台（chatgpt / netflix 等）快速过滤
- **流媒体与 AI 解锁**：每个节点都会轻量探测 ChatGPT、Claude、Gemini、YouTube、Netflix、Disney+、TikTok、B站港澳台限定，表格中以平台图标展示
- **多格式一键导出**：测速完成后支持一键导出可用节点为 Clash 配置（.yaml）、Sing-box 配置（.json）以及 Base64 订阅文本
- **导出图片**：把结果表绘制成 PNG 报告（含 IP 列与解锁平台图标），方便分享
- **多订阅合并支持**：支持直接粘贴多个订阅 URL（每行一个），并行拉取、自动合并去重后进行批量测速
- **真实落地探测**：自动探测各节点出口 IP 与真实落地国家归属，一眼识别机房漂移与虚标
- **出口 IP 质量**：同一次落地查询顺带识别 IP 类型（家宽 / 机房 / 移动）与是否被标记为代理/VPN，归纳为低/中/高风险（机房且被标记为代理风险最高，AI 服务最容易风控），悬停可看 ISP 与 ASN
- 自动直连测本机带宽基线（绕过 TUN/fake-ip，真实 IP + 绑物理网卡）：节点速度
  接近基线时标「≈本机」，表示瓶颈在本机宽带而非节点差异；基线没测到时，
  若 ≥3 个节点速度都落在最快值 3% 以内也自动推断并标记


## 支持的订阅格式

- Clash / Clash.Meta YAML（`proxies:` 字段，节点原样透传给 mihomo）
- Base64 编码的节点 URI 列表（机场订阅最常见形态）
- 明文 URI 列表：`ss://`（SIP002 + 旧格式）、`vmess://`、`vless://`（含 reality/ws/grpc/spiderX）、`trojan://`、`hysteria2://` / `hy2://`、`tuic://`（TUIC v5）、`anytls://`
- ss 插件仅支持 obfs（simple-obfs / obfs-local）；v2ray-plugin 等其他插件会提示跳过
- mihomo 不认的节点（如未知 cipher）会被自动剔除并在结果中标注原因，不影响其他节点
- 订阅请求使用 `mihomo/1.19.20` UA，并再用 Clash 系 UA 补拉一次：
  机场常按客户端下发不同协议（anytls / hy2 vs trojan 回退），合并后两边的节点都会出现

## 输出示例

```
                                     节点测试结果
┏━━━┳━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━┓
┃ # ┃ 节点    ┃ 类型   ┃ 落地      ┃    IP     ┃     解锁      ┃   延迟 ┃   下载速度 ┃ 状态 ┃
┡━━━╇━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━┩
│ 1 │ 香港 01 │ vmess  │ 香港      │   机房    │    YT NF      │  88 ms │ 182.3 Mbps │ ✓    │
│ 2 │ 日本 02 │ trojan │ 日本 东京 │ 机房·代理 │ GPT YT NF Bili│ 210 ms │  95.1 Mbps │ ✓    │
└───┴─────────┴────────┴───────────┴───────────┴───────────────┴────────┴────────────┴──────┘
```

- 延迟列为经节点的 HTTP 端到端延迟，测不到时退回 TCP 直连延迟
- IP 列仅在拿到 IP 质量数据时显示：家宽/移动为绿色（低风险），机房且被标记为代理为黄色（高风险）；
  JSON 结果中对应 `ip_quality` 字段（`type` / `proxy` / `risk` / `isp` / `asn`），备用落地引擎拿不到时为 `null`

排序规则：可测速的按速度降序 → HTTP 延迟升序 → TCP 延迟升序。

## 本地端到端自测

`tests/socks5_server.py` 是一个极简 SOCKS5 服务器，可当作"真实节点"验证全链路：

```bash
python3 tests/socks5_server.py 31111 &
python3 run.py test-local.yaml --speed-seconds 4
```

`test-local.yaml` 指向 `127.0.0.1:31111`，会走 mihomo → 本地 socks5 → 互联网
的完整链路，测出的是本机真实带宽。

## 流量消耗

- 延迟测试几乎不耗流量（KB 级）
- 测速是大头：每节点约消耗 `速度 × (测速窗口 + 探测窗口) `，
  例：100 Mbps 节点 × 5s ≈ 70 MB，50 个快节点跑完约 1 GB 量级
- 省流量方式：`--no-speed`（完全不测速）、`--speed-seconds 3`、
  `--max-download-mb 10`（每节点最多拉 10MB，快节点速度会偏低估）

## 已知限制

- TCP 直连失败不会跳过经节点测试（Hysteria2 等 UDP 协议端口常无 TCP 监听）
- 测速为经节点到 OVH/Hetzner 的下行，和 fast.com（本机到 Netflix CDN）不是同一条路径，数值通常更低
- 速度按「首字节→停止」计时（测速窗口也从首字节起算）；流量上限提前停时仍按实际传输时间计算，不会被握手等待稀释
- 测速为下载单向下行测试；Web 模式默认同一时刻只测速 1 个节点，避免抢本地带宽
  （此时若多数节点速度相同且接近本机带宽基线，说明撞了本机宽带上限；
  用「并发测速 >1」可让各节点分抢带宽，区分真实快慢）
- 测速源含 Cloudflare / 东京 / 新加坡 / Hetzner / OVH，并行探测后按速度选最近源；同一落地地区的后续节点直接复用该源（省去探测，也让同地区结果更可比）；和 fast.com（Netflix 同城 CDN）仍可能有差距，但不应再差一个数量级
- 开着 TUN 时会绑定物理网卡绕过；选错网卡可用 `--iface en0`（或环境变量 `NS_IFACE`）指定
- mihomo 端口从 21001 起递增，注意防火墙/端口占用
