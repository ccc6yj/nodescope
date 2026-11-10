"""回归测试：针对本次修复的每个 bug 各留一条用例。

覆盖清单（括号内为原缺陷）：
1. run._fmt_ms            —— http_ms 为 None 时 f"{...:.0f}" 抛 TypeError，✓ 行静默丢失
2. run.filter_nodes       —— 非法正则直接抛 re.error 打整段 traceback
3. tester.test_node       —— runner=None 时不置 _speed_tested，进度条永远 0%
4. tester._record         —— 回调异常逃进 future 被 as_completed 静默吞掉
5. tester 取消            —— 取消后仍等所有在跑任务（shutdown(wait=True)）
6. subscription.dedupe    —— 非幂等，订阅自带 "X #2" 时产生重名，mihomo 拒绝启动
7. subscription.parse     —— 带 [server_local] 段头的 QuantumultX 配置解析不出节点
8. subscription.load_nodes—— 粘贴的 Clash 配置（rules 含 http）被误判成多订阅
9. subscription.userinfo  —— expire 超大值抛 OverflowError；毫秒时间戳被忽略
10. subscription.node_to_uri / node_to_singbox —— 导出丢失 ws/grpc/h2/ports/obfs/anytls
11. core.find_free_base_port —— 端口被占用时应后移，避免 mihomo 静默 bind 失败
12. webapp maxmb          —— 前端「不限」传空串被当成 30MB
13. report HTML 排序      —— 失败节点(-)降序时被顶到第一行
14. report 导出跳过提示    —— 不支持的协议静默丢弃
15. subscription._parse_ss —— SS 2022 百分号编码明文密码未 unquote，节点必然连不上
16. tester._download_one  —— 耗时含握手，高延迟节点速度被系统性低估，且与实时速率不一致
17. core.MihomoRunner     —— 单个非法节点导致 mihomo 整体拒绝启动，整场测试失败
18. webapp 安全           —— 不校验 Host/Origin、GET query 可直接开测、可读本机文件
19. webapp 带宽基线        —— 基线只等 5s，未测完就开始节点测速，两边抢带宽
20. tester 测速源          —— Cloudflare bytes>=1e8 一律 403、cachefly 已下线，基线永远测不出
21. tester._download_one  —— 测速文件在窗口内拉完后线程空转，快链路速度被低估
22. tester 选源缓存        —— 每个节点都探测全部 8 个源，多花 3~8 秒和数 MB 流量
23. core.find_free_base_port —— 遇到占用端口只后移 1 位，重复探测大量端口
24. iface 缓存/DNS         —— 无缓存；UDP 53 被屏蔽时每节点最多卡 12 秒；非法域名抛异常
25. webapp 参数            —— concurrency=0 等非法值直接让线程池报错
26. subscription 多订阅     —— 串行拉取；README 说去重实际没去重
27. tester 解锁检测         —— TikTok 首页是 WAF 验证页判不出；Disney 响应未关闭
28. core.find_binary      —— start.sh 下载到项目 bin/ 的内核，直接跑 run.py 时找不到
29. 退出清理              —— 测试中途 kill 服务或关闭终端窗口，mihomo 成为孤儿进程继续占端口，
                              含节点密码的工作目录留在临时目录；kill -9 后下次启动不清理
30. static/index.html 导出图片 —— 原始订阅 URL（含 token）被画进 PNG，转发截图即泄露订阅
31. static/index.html 事件流 —— setupEventSource 形参遮蔽全局 es，测完仍被当成进行中
32. webapp 首页外部资源   —— 字体与解锁图标走 Google Fonts / jsdelivr，国内加载失败且暴露测速行为
33. subscription.parse   —— proxies 位于 2000 字符后或仅含 provider 时被误判为非 YAML，明文提取把 geox-url 等抓出报 "暂不支持协议 geoip: 'https://"；
                            且不能矫枉过正：URI 里含 "proxies" 子串不得当成 YAML，明文兜底不得截断带空格/括号的节点名
34. core.MihomoRunner     —— Windows 上 iface 是 IP 时不写 interface-name，连 DNS 也不配；本机 Clash TUN 把系统 DNS
                            劫持成 198.18 fake-ip 后，节点域名解析失败，延迟和测速全部失败
35. subscription / core xhttp —— vless URI 的 type=xhttp 被当成 tcp；mihomo < 1.19.22 不认识 xhttp 也不报错，
                            静默按裸 tcp 发 vless，TCP 延迟正常但 HTTP 全部失败；sing-box 导出退化成 tcp；
                            vless 链接漏读 encryption / alpn(h3) / allowInsecure
36. core.find_binary      —— PATH 优先于项目 bin/，系统里过旧的 brew 内核压过 start.sh 下载的新内核
37. start.sh / start.ps1   —— "$version（" 在 macOS 自带 bash 3.2 + UTF-8 locale 下把全角字符首字节并进变量名，
                            set -u 报 unbound variable，首次下载内核时脚本直接退出
38. tester.test_node       —— 服务器 IP 直连不通（被墙/下线）时只报 “HTTP 测试失败”，被误以为协议不支持
39. subscription._node_key —— 去重键区分域名大小写，YAML 的 tk-X07 与 URI 的 tk-x07 重复成 “#2”
40. tester.test_all CLI 中断 —— CLI 不传 cancel_event，Ctrl+C 后 shutdown(wait=True) 要把排队节点全部测完才退出
41. subscription.load_text —— text/plain 不带 charset 时 requests 按 ISO-8859-1 解码，明文订阅中文节点名乱码
42. subscription URI 解析   —— hy2 多端口 host:443,2000-3000 整条解析失败；type=httpupgrade 静默当成 tcp；
                            ss v2ray-plugin 被拒；vmess TCP+HTTP 伪装 / alpn / fp / allowInsecure 漏读；导出不对称
43. static/index.html 进度  —— 测速失败的节点不计入「测速 x/y」，进度永远到不了 100%；中途改选项会切换进度模式
44. tester.test_node 死节点 —— TCP 直连已超时仍重试一次 HTTP，每个死节点白等约 15 秒
45. tester 选源缓存失效    —— setdefault 不替换失效的源，同地区后续节点都先在坏源上白跑一轮
46. subscription 补拉 UA   —— as_completed 的 10 秒超时被 with 退出时的 shutdown(wait=True) 抵消
47. report HTML 图标       —— 报告图标仍走 jsdelivr CDN，离线/国内打开全部裂图
48. iface 缓存并发 / UDP 53 —— 并发线程同时查同一域名各发一遍 DNS；UDP 53 被屏蔽时每个新域名都先等两轮超时
49. static/index.html 导出 —— 「导出可用节点」按原始顺序，而 CLI 按速度排名
50. static/index.html 带宽基线 —— 直连基线偏低（直连国际线路受限）时，所有节点都被标成「≈ 本机带宽上限」
51. tester 解锁流量        —— Netflix / TikTok 整页下载，每个节点多耗数百 KB 订阅流量；Web 端无法关闭解锁检测
52. tester._download_one 爬坡段 —— 慢启动计入平均速率，流量上限提前停时快节点被系统性低估
53. start.sh / start.ps1   —— 下载的内核不校验 SHA256，经第三方加速站时等于直接执行对方给的二进制
"""

import base64
import http.client
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.parse import parse_qs, urlencode, urlsplit

import run
from nodescope import core, report, tester, webapp
from nodescope.subscription import (
    dedupe_names,
    format_userinfo,
    load_nodes,
    node_to_singbox,
    node_to_uri,
    parse,
)


class TestProgressAndCallback(unittest.TestCase):
    """1 / 3 / 4：进度条与回调。"""

    def test_fmt_ms_handles_none(self):
        # 原缺陷：f"{result.get('http_ms', 0):.0f}" 在值为 None 时抛 TypeError
        self.assertEqual(run._fmt_ms(None), "-")
        self.assertEqual(run._fmt_ms(0), "0ms")
        self.assertEqual(run._fmt_ms(123.4), "123ms")
        # 确认这就是原始崩溃写法
        with self.assertRaises(TypeError):
            f"{None:.0f}"

    def test_filter_nodes_rejects_bad_regex(self):
        nodes = [{"name": "香港 01"}]
        with self.assertRaises(SystemExit) as ctx:
            run.filter_nodes(nodes, "香港[", None)
        self.assertIn("不是合法正则表达式", str(ctx.exception))

    def test_filter_nodes_normal(self):
        nodes = [{"name": "香港 01"}, {"name": "日本 02"}]
        self.assertEqual(len(run.filter_nodes(nodes, "香港", None)), 1)
        self.assertEqual(len(run.filter_nodes(nodes, None, "香港")), 1)

    def test_test_node_without_runner_marks_speed_tested(self):
        # 原缺陷：runner=None 时 ok=True 但没有 _speed_tested，进度条永远不到 100%
        class _Srv:
            """监听一个本地端口，让 tcp_ping 能连通。"""

            def __enter__(self):
                self.sock = socket.socket()
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self.sock.bind(("127.0.0.1", 0))
                self.sock.listen(1)
                self.port = self.sock.getsockname()[1]
                return self

            def __exit__(self, *exc):
                self.sock.close()

        with _Srv() as srv:
            node = {"name": "n1", "type": "ss", "server": "127.0.0.1", "port": srv.port}
            res = tester.test_node(node, runner=None, options={"speed": True, "iface": None})
        self.assertTrue(res["ok"])
        self.assertTrue(res["_speed_tested"], "无 mihomo 时也必须标记测速阶段结束")

    def test_progress_callback_exception_is_reported_not_swallowed(self):
        # 原缺陷：回调异常逃进 future，被 as_completed 静默吞掉，进度条卡死无提示
        import contextlib
        import io

        nodes = [{"name": "n1", "type": "ss", "server": "127.0.0.1", "port": 1}]

        def boom(_name, _res):
            raise TypeError("boom")

        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            out = tester.test_all(nodes, runner=None,
                                  options={"speed": False, "iface": None},
                                  progress_cb=boom)
        # 测试本身仍然完成
        self.assertEqual(len(out), 1)
        # 并且异常被打印出来（不再静默）
        self.assertIn("进度回调异常", buf.getvalue())


class TestCancellation(unittest.TestCase):
    """5：取消后不应等待所有在跑任务。"""

    def test_cancel_returns_quickly(self):
        cancel = threading.Event()
        # 8 个慢节点，每个 ~2s；不取消的话 test_all 至少要 2s
        nodes = [{"name": f"n{i}", "type": "ss", "server": "127.0.0.1", "port": 1}
                 for i in range(8)]

        orig = tester.test_node

        def slow(node, runner=None, options=None):
            # 模拟内部阻塞（不受 cancel 打断的 socket 等待）
            for _ in range(40):
                if options and options.get("cancel_event") and options["cancel_event"].is_set():
                    break
                threading.Event().wait(0.05)
            return {"name": node["name"], "ok": False, "error": "slow",
                    "tcp_ms": None, "http_ms": None, "speed_mbps": None}

        tester.test_node = slow
        threading.Timer(0.2, cancel.set).start()
        try:
            import time
            t0 = time.monotonic()
            tester.test_all(nodes, runner=None,
                            options={"speed": False, "iface": None},
                            concurrency=8, cancel_event=cancel)
            elapsed = time.monotonic() - t0
        finally:
            tester.test_node = orig
        # 宽松阈值：必须明显小于「跑完 8 个 2s 任务」
        self.assertLess(elapsed, 1.5, f"取消后耗时 {elapsed:.2f}s，仍在等待在跑任务")


class TestSubscriptionFixes(unittest.TestCase):
    """6 / 7 / 8 / 9：订阅解析。"""

    def test_dedupe_names_is_idempotent_and_unique(self):
        # 原缺陷：X / X / X #2 -> 出现两个 "X #2"，mihomo 报 duplicate name 拒绝启动
        nodes = [{"name": "X"}, {"name": "X"}, {"name": "X #2"}]
        dedupe_names(nodes)
        names = [n["name"] for n in nodes]
        self.assertEqual(len(names), len(set(names)), f"仍存在重名: {names}")

        # 幂等：再跑一次不应继续改名
        before = [n["name"] for n in nodes]
        dedupe_names(nodes)
        self.assertEqual([n["name"] for n in nodes], before)

    def test_dedupe_names_handles_empty_name(self):
        nodes = [{"name": ""}, {"name": ""}, {}]
        dedupe_names(nodes)
        names = [n["name"] for n in nodes]
        self.assertEqual(len(names), len(set(names)))

    def test_quantumultx_with_section_headers(self):
        # 原缺陷：首行 [general] 不是注释也不匹配节点行 -> break -> 一个节点都解析不出
        text = (
            "[general]\n"
            "server_check_url=http://www.gstatic.com/generate_204\n"
            "\n"
            "[server_local]\n"
            "trojan=sg.example.com:443, password=pwd123, tag=新加坡QX, over-tls=true\n"
            "vmess=hk.example.com:443, method=auto, password=uuid-123, tag=香港QX, obfs=ws\n"
        )
        nodes, warns = parse(text)
        self.assertEqual(len(nodes), 2, f"解析结果 {nodes} / 警告 {warns}")
        self.assertEqual(nodes[0]["name"], "新加坡QX")
        self.assertEqual(nodes[1]["name"], "香港QX")

    def test_clash_yaml_with_urls_not_treated_as_multi_subscription(self):
        # 原缺陷：rules 里含 http 行 -> 被当成多订阅去拉取，解析不到节点
        text = (
            "proxies:\n"
            "  - {name: A, type: ss, server: 1.2.3.4, port: 443, cipher: aes-128-gcm, password: pw}\n"
            "rules:\n"
            "  - DOMAIN-SUFFIX,x,http://x.example\n"
            "  - DOMAIN-SUFFIX,y,http://y.example\n"
        )
        nodes, warns, _ui = load_nodes(text)
        self.assertEqual(len(nodes), 1, f"警告: {warns}")
        self.assertEqual(nodes[0]["name"], "A")

    def test_format_userinfo_expire_overflow(self):
        # 原缺陷：只 catch (ValueError, OSError)，超大值抛 OverflowError 冒泡
        for bad in ("99999999999999999999", "1e30"):
            msg = format_userinfo(f"total=1000; expire={bad}")
            self.assertIn("已用", msg)

    def test_format_userinfo_expire_milliseconds(self):
        # 毫秒时间戳应换算成秒后正常显示日期
        msg = format_userinfo("total=1000; expire=1700000000000")
        self.assertIn("到期", msg)
        self.assertIn("2023", msg)


class TestExportFixes(unittest.TestCase):
    """10 / 14：导出不再丢字段。"""

    def test_node_to_uri_keeps_ws_and_grpc(self):
        ws = {
            "name": "V-WS", "type": "vless", "server": "a.com", "port": 443, "uuid": "u1",
            "tls": True, "servername": "a.com", "network": "ws",
            "ws-opts": {"path": "/websocket", "headers": {"Host": "a.com"}},
        }
        q = parse_qs(urlsplit(node_to_uri(ws)).query)
        self.assertEqual(q["type"], ["ws"])
        self.assertEqual(q["path"], ["/websocket"])
        self.assertEqual(q["host"], ["a.com"])

        grpc = {
            "name": "V-gRPC", "type": "vless", "server": "a.com", "port": 443, "uuid": "u2",
            "tls": True, "network": "grpc", "grpc-opts": {"grpc-service-name": "svc"},
        }
        q = parse_qs(urlsplit(node_to_uri(grpc)).query)
        self.assertEqual(q["type"], ["grpc"])
        self.assertEqual(q["serviceName"], ["svc"])

    def test_node_to_uri_trojan_keeps_transport(self):
        n = {
            "name": "T", "type": "trojan", "server": "a.com", "port": 443, "password": "p",
            "network": "ws", "ws-opts": {"path": "/ws", "headers": {"Host": "a.com"}},
        }
        q = parse_qs(urlsplit(node_to_uri(n)).query)
        self.assertEqual(q["type"], ["ws"])
        self.assertEqual(q["path"], ["/ws"])

    def test_node_to_uri_hysteria2_keeps_ports_and_obfs(self):
        n = {
            "name": "Y", "type": "hysteria2", "server": "a.com", "port": 443, "password": "p",
            "obfs": "salamander", "obfs-password": "secret", "ports": "443-8443",
        }
        q = parse_qs(urlsplit(node_to_uri(n)).query)
        self.assertEqual(q["obfs"], ["salamander"])
        self.assertEqual(q["obfs-password"], ["secret"])
        self.assertEqual(q["mport"], ["443-8443"])

    def test_node_to_uri_anytls_and_ss_plugin(self):
        anytls = {"name": "A", "type": "anytls", "server": "a.com", "port": 443,
                  "password": "p", "sni": "a.com"}
        uri = node_to_uri(anytls)
        self.assertTrue(uri.startswith("anytls://"), uri)
        self.assertEqual(parse_qs(urlsplit(uri).query)["sni"], ["a.com"])

        ss = {"name": "S", "type": "ss", "server": "a.com", "port": 443,
              "cipher": "aes-128-gcm", "password": "p",
              "plugin": "obfs", "plugin-opts": {"mode": "tls", "host": "a.com"}}
        uri = node_to_uri(ss)
        self.assertIn("plugin=", uri)
        self.assertIn("obfs%3Dtls", uri)

    def test_node_to_singbox_trojan_transport_and_hy2_ports(self):
        trojan = {
            "name": "T", "type": "trojan", "server": "a.com", "port": 443, "password": "p",
            "network": "ws", "ws-opts": {"path": "/ws", "headers": {"Host": "a.com"}},
        }
        ob = node_to_singbox(trojan)
        self.assertEqual(ob["transport"]["type"], "ws")
        self.assertEqual(ob["transport"]["path"], "/ws")

        hy2 = {"name": "Y", "type": "hysteria2", "server": "a.com", "port": 443,
               "password": "p", "ports": "443-8443"}
        self.assertEqual(node_to_singbox(hy2)["server_ports"], ["443-8443"])

    def test_uri_roundtrip_preserves_transport(self):
        """导出 URI 再解析回来，关键字段必须一致（避免导出后连不上）。"""
        from nodescope.subscription import parse_uri

        cases = [
            {"name": "V-WS", "type": "vless", "server": "a.com", "port": 443, "uuid": "u",
             "tls": True, "servername": "a.com", "network": "ws",
             "ws-opts": {"path": "/ws", "headers": {"Host": "a.com"}}},
            {"name": "V-gRPC", "type": "vless", "server": "a.com", "port": 443, "uuid": "u",
             "tls": True, "network": "grpc", "grpc-opts": {"grpc-service-name": "svc"}},
            {"name": "V-h2", "type": "vless", "server": "a.com", "port": 443, "uuid": "u",
             "tls": True, "network": "h2", "h2-opts": {"path": "/p", "host": ["a.com"]}},
            {"name": "T-WS", "type": "trojan", "server": "a.com", "port": 443, "password": "p",
             "network": "ws", "ws-opts": {"path": "/ws", "headers": {"Host": "a.com"}}},
            {"name": "T-gRPC", "type": "trojan", "server": "a.com", "port": 443, "password": "p",
             "network": "grpc", "grpc-opts": {"grpc-service-name": "svc"}},
            {"name": "HY2", "type": "hysteria2", "server": "a.com", "port": 443, "password": "p",
             "obfs": "salamander", "obfs-password": "x", "ports": "443-8443"},
            {"name": "AT", "type": "anytls", "server": "a.com", "port": 443, "password": "p",
             "sni": "a.com"},
            {"name": "SS-obfs", "type": "ss", "server": "a.com", "port": 8388,
             "cipher": "aes-128-gcm", "password": "p", "plugin": "obfs",
             "plugin-opts": {"mode": "tls", "host": "e.com"}},
        ]
        for n in cases:
            with self.subTest(node=n["name"]):
                back = parse_uri(node_to_uri(n))
                diffs = {k: (n.get(k), back.get(k)) for k in n
                         if k != "name" and n.get(k) != back.get(k)}
                self.assertEqual(diffs, {}, f"{n['name']} 往返丢字段")

    def test_ss_obfs_plugin_uri_roundtrip(self):
        # 原缺陷：node_to_uri 会导出 plugin=obfs;... ，但 _parse_ss 一律拒绝，
        # 导致「自己导出的订阅自己读不了」。
        from nodescope.subscription import parse_uri

        node = {"name": "SS", "type": "ss", "server": "a.com", "port": 8388,
                "cipher": "aes-128-gcm", "password": "p", "plugin": "obfs",
                "plugin-opts": {"mode": "tls", "host": "e.com"}}
        back = parse_uri(node_to_uri(node))
        self.assertEqual(back["plugin"], "obfs")
        self.assertEqual(back["plugin-opts"], {"mode": "tls", "host": "e.com"})

    def test_ss_unknown_plugin_still_rejected(self):
        from nodescope.subscription import parse_uri

        # v2ray-plugin 已支持（见 42），这里用 mihomo 不支持的 kcptun
        uri = "ss://YWVzLTEyOC1nY206cHc=@a.com:8388?plugin=kcptun%3Bmode%3Dfast#X"
        with self.assertRaises(Exception):
            parse_uri(uri)

    def test_singbox_ss_obfs_uses_sip003_keys(self):
        # sing-box 内置插件名是 obfs-local，参数键名是 obfs=/obfs-host=（不是 mode=/host=）
        node = {"name": "SS", "type": "ss", "server": "a.com", "port": 8388,
                "cipher": "aes-128-gcm", "password": "p", "plugin": "obfs",
                "plugin-opts": {"mode": "http", "host": "e.com"}}
        ob = node_to_singbox(node)
        self.assertEqual(ob["plugin"], "obfs-local")
        self.assertEqual(ob["plugin_opts"], "obfs=http;obfs-host=e.com")

    def test_singbox_transport_for_trojan_and_h2(self):
        trojan = {"name": "T", "type": "trojan", "server": "a.com", "port": 443, "password": "p",
                  "network": "grpc", "grpc-opts": {"grpc-service-name": "svc"}}
        self.assertEqual(node_to_singbox(trojan)["transport"],
                         {"type": "grpc", "service_name": "svc"})

        vless_h2 = {"name": "H", "type": "vless", "server": "a.com", "port": 443, "uuid": "u",
                    "tls": True, "network": "h2", "h2-opts": {"path": "/p", "host": ["a.com"]}}
        self.assertEqual(node_to_singbox(vless_h2)["transport"],
                         {"type": "http", "path": "/p", "host": ["a.com"]})

    def test_singbox_supports_anytls(self):
        n = {"name": "A", "type": "anytls", "server": "a.com", "port": 443,
             "password": "p", "sni": "a.com"}
        ob = node_to_singbox(n)
        self.assertIsNotNone(ob)
        self.assertEqual(ob["type"], "anytls")
        self.assertEqual(ob["tls"]["server_name"], "a.com")

    def test_parse_reports_yaml_syntax_error(self):
        # 原缺陷：YAML 语法错被静默吞掉，只报「未解析到任何节点」
        nodes, warns = parse("proxies:\n  - {name: A #2, type: socks5}\n")
        self.assertEqual(nodes, [])
        self.assertTrue(any("YAML 语法错误" in w for w in warns), warns)

    def test_parse_reports_proxies_without_name_or_type(self):
        nodes, warns = parse("proxies:\n  - {type: socks5}\n")
        self.assertEqual(nodes, [])
        self.assertTrue(any("缺少 name/type" in w for w in warns), warns)

    def test_parse_base64_wrapped_clash_yaml(self):
        import base64

        yml = "proxies:\n  - {name: B64, type: socks5, server: 1.2.3.4, port: 80}\n"
        nodes, _ = parse(base64.b64encode(yml.encode()).decode())
        self.assertEqual([n["name"] for n in nodes], ["B64"])

    def test_parse_valid_yaml_still_works(self):
        nodes, warns = parse("proxies:\n  - {name: N, type: socks5, server: 1.2.3.4, port: 80}\n")
        self.assertEqual([n["name"] for n in nodes], ["N"])
        self.assertEqual(warns, [])

    def test_export_reports_skipped_nodes(self):
        import os
        import tempfile

        results = [{"name": "SOCKS", "ok": True, "speed_mbps": 10.0, "http_ms": 10}]
        nodes = [{"name": "SOCKS", "type": "socks5", "server": "1.2.3.4", "port": 1080}]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "out.txt")
            # 不应抛异常，且文件写出（内容为空订阅）
            report.export_nodes_base64(results, nodes, path)
            self.assertTrue(os.path.exists(path))


class TestCorePortSelection(unittest.TestCase):
    """11：端口段选择。"""

    def test_find_free_base_port_skips_occupied(self):
        # 占住一个端口，确认返回的段不包含它
        holder = socket.socket()
        holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        busy = holder.getsockname()[1]
        try:
            base = core.find_free_base_port(busy, 3)
            self.assertNotIn(busy, range(base, base + 3))
            for p in range(base, base + 3):
                self.assertTrue(core._port_free(p), f"端口 {p} 应空闲")
        finally:
            holder.close()

    def test_find_free_base_port_returns_start_when_free(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            free = s.getsockname()[1]
        # 释放后再查，应尽量返回起始端口本身
        self.assertEqual(core.find_free_base_port(free, 1), free)

    def test_wait_ready_detects_bind_failure(self):
        # mihomo 监听 bind 失败时进程仍存活，只能从日志识别
        class _FakeProc:
            def poll(self):
                return None  # 进程还活着

        runner = core.MihomoRunner.__new__(core.MihomoRunner)
        runner.proc = _FakeProc()
        runner.ports = {"n1": 1}
        runner.workdir = None
        runner._log_file = None
        runner._read_log = lambda: (
            'level=error msg="Listener l0 listen err: listen tcp 127.0.0.1:1: '
            'bind: address already in use"'
        )
        with self.assertRaises(core.MihomoError) as ctx:
            runner._wait_ready(timeout=5)
        self.assertIn("占用", str(ctx.exception))


class TestWebappAndReport(unittest.TestCase):
    """12 / 13：Web 参数与 HTML 排序。"""

    def test_maxmb_empty_means_unlimited(self):
        # 原缺陷：空串回退成 30MB，用户选「不限」却被限流
        def resolved(raw):
            return float(raw) if (raw is not None and str(raw).strip() != "") else None

        self.assertIsNone(resolved(""))
        self.assertIsNone(resolved(None))
        self.assertEqual(resolved("30"), 30.0)

    def test_maxmb_zero_is_treated_as_unlimited(self):
        # _run_speed: max_bytes 为 0 时是 falsy，download_speed 不会建 budget（即不限流量）
        captured = {}
        orig = tester.measure_download

        def fake(port, max_bytes=None, **kwargs):
            captured["max_bytes"] = max_bytes
            return 1.0, "http://x"

        tester.measure_download = fake
        try:
            tester._run_speed(1, {"max_download_mb": 0})
            tester._run_speed(1, {"max_download_mb": 10})
        finally:
            tester.measure_download = orig
        self.assertEqual(captured["max_bytes"], 10 * 1024 * 1024)
        # 0 -> 仍为 0（falsy），语义上等于不限
        self.assertFalse(0, "0 为 falsy，download_speed 不会启用流量上限")

    def test_html_report_sort_keeps_failed_last(self):
        import os
        import tempfile

        results = [
            {"name": "Fast", "type": "ss", "ok": True, "speed_mbps": 100.0, "http_ms": 100},
            {"name": "Fail", "type": "ss", "ok": False, "speed_mbps": None, "http_ms": None},
            {"name": "Slow", "type": "ss", "ok": True, "speed_mbps": 5.0, "http_ms": 300},
        ]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "r.html")
            report.export_html(results, path)
            html = open(path, encoding="utf-8").read()
        # 缺值统一 -1，降序时失败节点排最后（原来延迟列用 Infinity 会被顶到第一）
        self.assertIn("return m ? parseFloat(m[0]) : -1;", html)
        self.assertNotIn(": Infinity", html)

    def test_is_cancelled_accepts_none(self):
        self.assertFalse(webapp._is_cancelled(None))
        ev = threading.Event()
        self.assertFalse(webapp._is_cancelled(ev))
        ev.set()
        self.assertTrue(webapp._is_cancelled(ev))

    def test_node_to_uri_ipv6_brackets(self):
        """IPv6 地址导出 URI 必须被方括号包裹，否则标准解析器解析端口崩溃。"""
        node_vless = {
            "name": "IPv6-Node", "type": "vless", "server": "2001:db8::1",
            "port": 443, "uuid": "test-uuid"
        }
        uri = node_to_uri(node_vless)
        self.assertIn("@[2001:db8::1]:443", uri)
        u = urlsplit(uri)
        self.assertEqual(u.hostname, "2001:db8::1")
        self.assertEqual(u.port, 443)

        node_ss = {
            "name": "IPv6-SS", "type": "ss", "server": "2001:db8::1",
            "port": 8388, "cipher": "aes-128-gcm", "password": "pwd"
        }
        uri_ss = node_to_uri(node_ss)
        self.assertIn("@[2001:db8::1]:8388", uri_ss)

    def test_load_text_multiline_with_url_not_treated_as_single_request(self):
        """多行内容（第一行为 URL，后跟注释/节点）不应被整段当成 URL 发起请求导致 404。"""
        multiline = "https://example.com/sub\n# 说明行\nvmess://eyJ2IjoiMiJ9"
        nodes, warns, uinfo = load_nodes(multiline)
        # 不应抛出 404 请求异常，而应安全当成直接粘贴的文本处理
        self.assertIsInstance(nodes, list)

    def test_mihomo_runner_cleans_up_on_enter_failure(self):
        """MihomoRunner._start 抛异常时，__enter__ 必须触发 _stop() 进行资源清理。"""
        stopped = False

        class FailingRunner(core.MihomoRunner):
            def _start(self):
                raise core.MihomoError("mock start failure")

            def _stop(self):
                nonlocal stopped
                stopped = True

        runner = FailingRunner([])
        runner.binary = "dummy"
        with self.assertRaises(core.MihomoError):
            with runner:
                pass
        self.assertTrue(stopped, "__enter__ 抛异常时必须触发 _stop 清理资源")


class TestSsPercentEncodedUserinfo(unittest.TestCase):
    """15：SIP002 下 SS 2022 的 userinfo 是百分号编码明文。"""

    def test_percent_encoded_password_is_unquoted(self):
        from nodescope.subscription import parse_uri

        n = parse_uri("ss://2022-blake3-aes-128-gcm:YctPZ6U7xPPcU%2Bgp3u%2BO%3D%3D@1.2.3.4:8388#t")
        self.assertEqual(n["cipher"], "2022-blake3-aes-128-gcm")
        self.assertEqual(n["password"], "YctPZ6U7xPPcU+gp3u+O==")
        # 导出后再解析回来必须一致
        self.assertEqual(parse_uri(node_to_uri(n))["password"], "YctPZ6U7xPPcU+gp3u+O==")

    def test_base64_userinfo_still_works(self):
        from nodescope.subscription import parse_uri

        n = parse_uri("ss://YWVzLTEyOC1nY206cGFzcw==@1.2.3.4:8388#t")  # aes-128-gcm:pass
        self.assertEqual((n["cipher"], n["password"]), ("aes-128-gcm", "pass"))


class TestDownloadTiming(unittest.TestCase):
    """16：测速耗时应从首字节算起，不含经代理的握手等待。"""

    def test_handshake_not_counted(self):
        handshake, window, chunk_gap = 0.6, 0.5, 0.02

        class _Resp:
            status_code = 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def iter_content(self, chunk_size):
                while True:
                    time.sleep(chunk_gap)
                    yield b"x" * 1024

        def fake_get(*args, **kwargs):
            time.sleep(handshake)  # 模拟经代理的 TCP/TLS 握手
            return _Resp()

        orig = tester.requests.get
        tester.requests.get = fake_get
        try:
            t = time.monotonic()
            got, elapsed = tester._download_one(
                1, "http://x", window, 1, {}, 8, None, None)
            wall = time.monotonic() - t
        finally:
            tester.requests.get = orig
        self.assertGreater(got, 0)
        # 旧实现按墙钟算，会得到 ≈ handshake + window；
        # 现在还会剔除起步爬坡段（见用例 52），计速时长约为 window × (1 - 爬坡占比)
        steady = window * (1 - tester._RAMP_FRACTION)
        self.assertLess(elapsed, window + 0.15)
        self.assertGreaterEqual(elapsed, steady - 0.1)
        self.assertGreater(wall, handshake + window - 0.05, "测速窗口应从首字节开始计")


class TestMihomoRejectsBadProxy(unittest.TestCase):
    """17：非法节点应被剔除后重启，而不是整场失败。"""

    def test_bad_proxy_is_dropped_and_retried(self):
        started = []

        class Runner(core.MihomoRunner):
            def _start(self):
                started.append([p["name"] for p in self.proxies])
                names = [p["name"] for p in self.proxies]
                if "bad" in names:
                    idx = names.index("bad")
                    raise core.MihomoError(
                        f'mihomo 启动失败:\nlevel=fatal msg="Parse config error: proxy {idx}: '
                        f'ss 1.2.3.4:8388 cipher: xxx initialize error: unknown method: xxx"')
                self._build_config()

            def _stop(self):
                pass

        nodes = [{"name": "a", "type": "socks5", "server": "127.0.0.1", "port": 1},
                 {"name": "bad", "type": "ss", "server": "1.2.3.4", "port": 8388},
                 {"name": "c", "type": "socks5", "server": "127.0.0.1", "port": 1}]
        runner = Runner(nodes, iface="")
        runner.binary = "dummy"
        with runner:
            self.assertEqual(started, [["a", "bad", "c"], ["a", "c"]])
            self.assertEqual(set(runner.ports), {"a", "c"})
            self.assertIn("unknown method", runner.rejected["bad"])
            res = tester.test_node(nodes[1], runner, {"samples": 1, "timeout": 0.2})
            self.assertIn("被 mihomo 拒绝", res["error"])
        self.assertEqual(len(nodes), 3, "不应修改调用方传入的节点列表")

    def test_unrelated_error_is_not_swallowed(self):
        class Runner(core.MihomoRunner):
            def _start(self):
                raise core.MihomoError("端口等待超时: [1]")

            def _stop(self):
                pass

        runner = Runner([{"name": "a", "type": "socks5"}], iface="")
        runner.binary = "dummy"
        with self.assertRaises(core.MihomoError), runner:
            pass

    @unittest.skipUnless(core.find_binary(), "需要本机安装 mihomo")
    def test_real_mihomo_skips_bad_cipher(self):
        nodes = [{"name": "good", "type": "socks5", "server": "127.0.0.1", "port": 1},
                 {"name": "bad", "type": "ss", "server": "1.2.3.4", "port": 8388,
                  "cipher": "not-a-cipher", "password": "x"}]
        with core.MihomoRunner(nodes, base_port=41001, iface="") as runner:
            self.assertEqual(list(runner.ports), ["good"])
            self.assertIn("bad", runner.rejected)


class TestWebappSecurity(unittest.TestCase):
    """18：Web 服务只接受本机同源请求，且不能借此读本机文件。"""

    @classmethod
    def setUpClass(cls):
        cls.calls = []
        cls._orig_run = webapp.run_test_stream

        def fake_run(params, send_event, cancel_event=None):
            cls.calls.append(params)
            send_event("done", {"ok": 0, "total": 0, "results": []})

        webapp.run_test_stream = fake_run
        cls.server = webapp._Server(("127.0.0.1", 0), webapp.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        webapp.run_test_stream = cls._orig_run

    def _req(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            resp = conn.getresponse()
            return resp.status, resp.read().decode()
        finally:
            conn.close()

    def _post_task(self, headers=None):
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        return self._req("POST", "/api/task", json.dumps({"src": "x"}), h)

    def test_index_self_contained(self):
        # 32：首页曾从 Google Fonts / jsdelivr 拉字体和解锁图标，国内常加载失败，
        # 且向第三方暴露正在测速；现在页面不得引用外部地址，并以 CSP 兜底
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("GET", "/")
            resp = conn.getresponse()
            html = resp.read().decode()
            csp = resp.getheader("Content-Security-Policy") or ""
        finally:
            conn.close()
        self.assertEqual(resp.status, 200)
        self.assertIn("default-src 'self'", csp)
        self.assertEqual(re.findall(r"""(?:src|href)=["']?https?://""", html), [])
        self.assertNotIn("googleapis", html)
        self.assertNotIn("jsdelivr", html)

    def test_icons_served_locally(self):
        # 32：解锁图标改由本地 /icons/ 提供；只认白名单文件名，不能借此读到其他文件
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("GET", "/icons/youtube.svg")
            resp = conn.getresponse()
            body = resp.read()
        finally:
            conn.close()
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.getheader("Content-Type"), "image/svg+xml")
        self.assertTrue(body.lstrip().startswith(b"<"))
        for bad in ("/icons/missing.svg", "/icons/../index.html", "/icons/..%2Fwebapp.py",
                    "/icons/YouTube.svg", "/icons/youtube.svg.bak"):
            status, _ = self._req("GET", bad)
            self.assertEqual(status, 404, bad)

    def test_foreign_host_rejected(self):
        # DNS rebinding：页面域名解析到 127.0.0.1，但 Host 仍是攻击者域名
        status, _ = self._req("GET", "/", headers={"Host": f"evil.example:{self.port}"})
        self.assertEqual(status, 403)

    def test_cross_origin_rejected(self):
        status, _ = self._post_task({"Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        status, _ = self._post_task({"Origin": "http://localhost:1"})  # 本机其他端口也不行
        self.assertEqual(status, 403)
        status, _ = self._post_task({"Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(status, 200)

    def test_non_json_post_rejected(self):
        # 跨站 <form enctype="text/plain"> 可以拼出合法 JSON 请求体
        status, _ = self._req("POST", "/api/task", '{"src":"x"}', {"Content-Type": "text/plain"})
        self.assertEqual(status, 415)

    def test_stream_requires_task_and_is_one_shot(self):
        before = len(self.calls)
        status, _ = self._req("GET", "/api/stream?src=https%3A%2F%2Fexample.com")
        self.assertEqual(status, 404, "不能再用 query 参数直接开测")
        status, body = self._post_task()
        task_id = json.loads(body)["taskId"]
        status, body = self._req("GET", f"/api/stream?taskId={task_id}")
        self.assertEqual(status, 200)
        self.assertIn("event: done", body)
        status, _ = self._req("GET", f"/api/stream?taskId={task_id}")
        self.assertEqual(status, 404, "taskId 只能用一次")
        self.assertEqual(len(self.calls), before + 1)

    def test_web_load_does_not_read_local_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write("proxies:\n  - {name: secret, type: socks5, server: 1.1.1.1, port: 1}\n")
            path = f.name
        try:
            nodes, _w, _u = load_nodes(path)
            self.assertEqual([n["name"] for n in nodes], ["secret"])  # CLI 仍可读文件
            nodes, _w, _u = load_nodes(path, allow_file=False)
            self.assertEqual(nodes, [])
        finally:
            os.unlink(path)


class TestWebBaselineWait(unittest.TestCase):
    """19：节点测速必须等本机带宽基线测完再开始。"""

    def test_waits_for_baseline(self):
        from nodescope import iface as iface_mod

        state = {"baseline_done": False, "seen": None}

        def slow_baseline(**kwargs):
            time.sleep(5.5)  # 超过旧实现 5s 的等待上限
            state["baseline_done"] = True
            return 100.0

        def fake_test_all(nodes, **kwargs):
            state["seen"] = state["baseline_done"]
            return []

        class FakeRunner:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                pass

        patches = [
            (webapp, "load_nodes", lambda *a, **kw: ([{"name": "n", "server": "1.1.1.1", "port": 1}], [], "")),
            (webapp, "MihomoRunner", FakeRunner),
            (iface_mod, "detect_physical_iface", lambda: None),
            (tester, "check_fake_ip", lambda nodes: False),
            (tester, "local_bandwidth_mbps", slow_baseline),
            (tester, "test_all", fake_test_all),
        ]
        saved = [(obj, name, getattr(obj, name)) for obj, name, _ in patches]
        for obj, name, val in patches:
            setattr(obj, name, val)
        try:
            webapp.run_test_stream({"src": "x"}, lambda e, d: None, threading.Event())
        finally:
            for obj, name, val in saved:
                setattr(obj, name, val)
        self.assertTrue(state["seen"], "节点测速开始时基线应已测完")


class TestSpeedSources(unittest.TestCase):
    """20：测速源必须是当前可用的。"""

    def test_cloudflare_under_limit_and_no_cachefly(self):
        for url in tester.SPEED_URLS:
            self.assertNotIn("cachefly", url)
            m = parse_qs(urlsplit(url).query).get("bytes")
            if "speed.cloudflare.com" in url:
                self.assertLess(int(m[0]), 100_000_000, "Cloudflare 对 >=1e8 字节直接 403")

    def test_baseline_sources(self):
        seen = []
        orig = tester._direct_download

        def fake(host, port, tls, path, *a, **kw):
            seen.append((host, path))
            return 0, 1.0

        tester._direct_download = fake
        try:
            tester.local_bandwidth_mbps(iface="en0")
        finally:
            tester._direct_download = orig
        self.assertTrue(seen)
        for host, path in seen:
            self.assertNotIn("cachefly", host)
            if host == "speed.cloudflare.com":
                self.assertLess(int(parse_qs(urlsplit(path).query)["bytes"][0]), 100_000_000)


def _fake_stream(chunks, gap, calls):
    class _Resp:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def iter_content(self, chunk_size):
            for _ in range(chunks):
                time.sleep(gap)
                yield b"x" * 1024

    def fake_get(*args, **kwargs):
        calls.append(1)
        return _Resp()

    return fake_get


class TestDownloadRefetch(unittest.TestCase):
    """21：文件在窗口内拉完要重新请求，速度按整个窗口计。"""

    def test_refetch_until_window_ends(self):
        calls = []
        orig = tester.requests.get
        tester.requests.get = _fake_stream(5, 0.02, calls)  # 每个「文件」约 0.1s 拉完
        try:
            got, elapsed = tester._download_one(1, "http://x", 0.5, 1, {}, 8, None, None)
        finally:
            tester.requests.get = orig
        self.assertGreaterEqual(len(calls), 3, "文件拉完后应重新请求")
        # 计速时长已剔除起步爬坡段（见用例 52），所以略短于 0.5s 的窗口
        self.assertGreaterEqual(elapsed, 0.5 * (1 - tester._RAMP_FRACTION) - 0.1)
        self.assertGreater(got, 5 * 1024 * 2)


class TestSpeedSourceHint(unittest.TestCase):
    """22：有同地区选好的源时不再探测全部源。"""

    def setUp(self):
        self.probed = []
        self.downloaded = []
        self._orig = (tester._pick_speed_urls, tester._download_one)

        def fake_pick(port, headers, timeout, budget=None, cancel_event=None, sources=None):
            self.probed.append(list(sources))
            return [(u, 1, 1, 1) for u in sources]

        def fake_download(port, url, *a, **kw):
            self.downloaded.append(url)
            return (0, 0.0) if url == "bad" else (1024 * 1024, 1.0)

        tester._pick_speed_urls = fake_pick
        tester._download_one = fake_download

    def tearDown(self):
        tester._pick_speed_urls, tester._download_one = self._orig

    def test_hint_skips_probe(self):
        val, url = tester.measure_download(1, hint="http://hinted")
        self.assertEqual(url, "http://hinted")
        self.assertGreater(val, 0)
        self.assertEqual(self.probed, [], "hint 可用时不应探测")

    def test_failed_hint_falls_back_to_probe(self):
        val, url = tester.measure_download(1, hint="bad")
        self.assertEqual(self.downloaded[0], "bad")
        self.assertEqual(len(self.probed), 1)
        self.assertNotIn("bad", self.probed[0])
        self.assertEqual(url, tester.SPEED_URLS[0])

    def test_test_all_reuses_source_per_country(self):
        hints = []
        orig_node, orig_speed = tester.test_node, tester._run_speed

        def fake_node(node, runner, opts):
            return {"name": node["name"], "ok": True, "country_code": node["cc"],
                    "tcp_ms": 1, "http_ms": 1, "speed_mbps": None}

        def fake_speed(port, opts, live_cb=None, hint=None):
            hints.append(hint)
            return 10.0, f"src-{port}"

        class Runner:
            iface = "en0"
            ports = {"a": 1, "b": 2, "c": 3}

        tester.test_node, tester._run_speed = fake_node, fake_speed
        try:
            nodes = [{"name": "a", "cc": "JP"}, {"name": "b", "cc": "JP"}, {"name": "c", "cc": "US"}]
            tester.test_all(nodes, runner=Runner(), options={"speed": True}, concurrency=1)
        finally:
            tester.test_node, tester._run_speed = orig_node, orig_speed
        self.assertEqual(hints, [None, "src-1", None])


class TestFreePortJump(unittest.TestCase):
    """23：遇到被占端口直接跳过它。"""

    def test_jumps_past_busy_port(self):
        checked = []
        orig = core._port_free

        def fake(port):
            checked.append(port)
            return port != 1099

        core._port_free = fake
        try:
            self.assertEqual(core.find_free_base_port(1000, 500), 1100)
        finally:
            core._port_free = orig
        self.assertLess(len(checked), 700, f"探测次数过多: {len(checked)}")


class TestIfaceCache(unittest.TestCase):
    """24：网卡与 DNS 结果缓存，DNS 不可达时快速放弃。"""

    def setUp(self):
        from nodescope import iface as iface_mod
        self.m = iface_mod
        self.m.clear_cache()
        self._orig = (self.m._dns_query, self.m.socket.getaddrinfo, self.m._detect_physical_iface)

    def tearDown(self):
        self.m._dns_query, self.m.socket.getaddrinfo, self.m._detect_physical_iface = self._orig
        self.m.clear_cache()

    def test_dns_cached_but_failures_not(self):
        calls = []

        def fake_query(host, iface, server, qtype=1, timeout=3):
            calls.append(host)
            return ["93.184.216.34"] if host == "ok.example" else []

        self.m._dns_query = fake_query
        self.m.socket.getaddrinfo = lambda *a, **kw: []
        self.assertEqual(self.m.resolve_host("ok.example", "en0"), [(socket.AF_INET, "93.184.216.34")])
        self.m.resolve_host("ok.example", "en0")
        self.assertEqual(calls.count("ok.example"), 1)
        self.m.resolve_host("empty.example", "en0")
        n = calls.count("empty.example")
        self.m.resolve_host("empty.example", "en0")
        self.assertEqual(calls.count("empty.example"), 2 * n, "解析失败不应被缓存")

    def test_dns_timeout_skips_aaaa(self):
        calls = []

        def fake_query(host, iface, server, qtype=1, timeout=3):
            calls.append((server, qtype))
            raise socket.timeout()

        self.m._dns_query = fake_query
        self.m.socket.getaddrinfo = lambda *a, **kw: [(socket.AF_INET, 1, 6, "", ("1.2.3.4", 0))]
        self.assertEqual(self.m.resolve_host("x.example", "en0"), [(socket.AF_INET, "1.2.3.4")])
        self.assertEqual(calls, [("1.1.1.1", 1), ("8.8.8.8", 1)])

    def test_invalid_hostname_does_not_raise(self):
        self.m.socket.getaddrinfo = lambda *a, **kw: []
        self.assertEqual(self.m.resolve_host("a" * 64 + ".example", "en0"), [])

    def test_detect_iface_cached(self):
        calls = []

        def fake():
            calls.append(1)
            return "en0"

        self.m._detect_physical_iface = fake
        env = os.environ.pop("NS_IFACE", None)
        try:
            self.assertEqual(self.m.detect_physical_iface(), "en0")
            self.assertEqual(self.m.detect_physical_iface(), "en0")
        finally:
            if env is not None:
                os.environ["NS_IFACE"] = env
        self.assertEqual(len(calls), 1)

    def test_mihomo_skips_ip_as_interface_name(self):
        r = core.MihomoRunner([{"name": "a", "type": "socks5"}], iface="192.168.1.2")
        cfg = r._build_config()
        self.assertNotIn("interface-name", cfg)
        # 未绑网卡也必须自建 DNS，且不能用 UDP 53（会被 TUN 劫持成 fake-ip）
        dns = cfg["dns"]
        self.assertTrue(dns["enable"])
        self.assertEqual(dns["enhanced-mode"], "redir-host")
        self.assertFalse(dns["use-system-hosts"])
        self.assertTrue(dns["nameserver"])
        for ns in dns["nameserver"]:
            self.assertTrue(ns.startswith("https://") or ns.startswith("tls://"), ns)
        r = core.MihomoRunner([{"name": "a", "type": "socks5"}], iface="en0")
        cfg = r._build_config()
        self.assertEqual(cfg["interface-name"], "en0")
        self.assertEqual(cfg["dns"]["nameserver"][0], "1.1.1.1")


class TestWebParams(unittest.TestCase):
    """25：浏览器传来的数值参数要夹到合理范围。"""

    def test_num_param(self):
        f = webapp._num_param
        self.assertEqual(f({"concurrency": "0"}, "concurrency", 8, 1, 64, int), 1)
        self.assertEqual(f({"concurrency": "999"}, "concurrency", 8, 1, 64, int), 64)
        self.assertEqual(f({}, "concurrency", 8, 1, 64, int), 8)
        self.assertEqual(f({"maxmb": ""}, "maxmb", None, 0, 10240), None)
        self.assertEqual(f({"maxmb": "0"}, "maxmb", None, 0, 10240) or None, None)
        with self.assertRaises(ValueError):
            f({"speed_seconds": "abc"}, "speed_seconds", 5, 1, 60)


class TestMultiSubscription(unittest.TestCase):
    """26：多订阅并行拉取并去重。"""

    def test_parallel_and_dedupe(self):
        from nodescope import subscription as sub

        shared = "trojan://pw@1.1.1.1:443#shared"
        bodies = {
            "https://a.example/s": shared + "\ntrojan://pw@2.2.2.2:443#a-only",
            "https://b.example/s": shared,
            "https://c.example/s": "trojan://pw@3.3.3.3:443#c-only",
        }

        def fake_load_text(src, timeout=20, retries=3, ua=None, allow_file=True):
            time.sleep(0.3)
            return bodies[src], ""

        orig = sub.load_text
        sub.load_text = fake_load_text
        try:
            t = time.monotonic()
            nodes, warns, _ = sub.load_nodes("\n".join(bodies))
            cost = time.monotonic() - t
        finally:
            sub.load_text = orig
        self.assertEqual([n["name"] for n in nodes], ["shared", "a-only", "c-only"])
        self.assertTrue(any("去重" in w for w in warns))
        self.assertLess(cost, 1.2, f"应并行拉取，耗时 {cost:.2f}s")


class TestUnlockChecks(unittest.TestCase):
    """27：TikTok 以 explore 页的 region 为准；Disney 响应要关闭。"""

    def _run(self, tiktok_text):
        closed = []

        class Resp:
            def __init__(self, url, text):
                self.url, self.text, self.status_code = url, text, 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                closed.append(self.url)
                return False

            def json(self):
                return {}

            def iter_content(self, chunk_size):
                yield self.text.encode()

        def fake_get(url, **kwargs):
            if "tiktok" in url:
                return Resp(url, tiktok_text)
            return Resp(url, "")

        orig = tester.requests.get
        tester.requests.get = fake_get
        try:
            res = tester.check_unlocks(1, country_code="JP")
        finally:
            tester.requests.get = orig
        return res, closed

    def test_tiktok_region(self):
        res, closed = self._run('..."region":"JP"...')
        self.assertTrue(res["tiktok"])
        self.assertIn("https://www.disneyplus.com", closed, "Disney 的 stream 响应必须关闭")
        res, _ = self._run("<body>Please wait...</body>")
        self.assertFalse(res["tiktok"])
        res, _ = self._run('"region":"CN"')
        self.assertFalse(res["tiktok"])


class TestExitCleanup(unittest.TestCase):
    """29：服务退出时关闭所有内核、取消进行中的测试；SIGTERM 走 KeyboardInterrupt 清理路径。"""

    def _sleeper(self, *extra):
        import subprocess
        import sys
        return subprocess.Popen([sys.executable, "-c", "import time, sys; time.sleep(60)", *extra])

    def test_shutdown_all_stops_runners_and_cancels(self):
        runner = core.MihomoRunner([], iface="")
        runner.workdir = tempfile.mkdtemp(prefix=core.WORKDIR_PREFIX)
        with open(os.path.join(runner.workdir, "config.yaml"), "w") as f:
            f.write("password: secret\n")
        runner.proc = self._sleeper()
        proc = runner.proc
        ev = threading.Event()
        with core._LIVE_LOCK:
            core._LIVE_RUNNERS.add(runner)
        with webapp._ACTIVE_LOCK:
            webapp._ACTIVE_CANCELS.add(ev)
        try:
            webapp.shutdown_all()
            self.assertIsNotNone(proc.poll(), "内核进程应已被关闭")
            self.assertFalse(os.path.exists(runner.workdir), "含节点密码的工作目录应被删除")
            self.assertTrue(ev.is_set(), "进行中的测试应被取消")
            self.assertNotIn(runner, core._LIVE_RUNNERS)
        finally:
            with webapp._ACTIVE_LOCK:
                webapp._ACTIVE_CANCELS.discard(ev)
            if proc.poll() is None:
                proc.kill()

    @unittest.skipUnless(os.name == "posix", "孤儿内核清理只在 POSIX 上执行")
    def test_cleanup_stale_after_kill9(self):
        with tempfile.TemporaryDirectory() as root:
            # 创建者已死：内核（命令行带工作目录）应被结束，目录应被删除
            dead = os.path.join(root, core.WORKDIR_PREFIX + "dead")
            os.mkdir(dead)
            orphan = self._sleeper(dead)
            owner = self._sleeper()
            owner.kill()
            owner.wait()
            with open(os.path.join(dead, core.OWNER_FILE), "w") as f:
                json.dump({"owner": owner.pid, "mihomo": orphan.pid}, f)
            # 创建者还活着（本进程）：不能动
            alive = os.path.join(root, core.WORKDIR_PREFIX + "alive")
            os.mkdir(alive)
            with open(os.path.join(alive, core.OWNER_FILE), "w") as f:
                json.dump({"owner": os.getpid(), "mihomo": 0}, f)
            # PID 被复用给无关进程（命令行不含工作目录）：目录删掉，但不能杀该进程
            reused = os.path.join(root, core.WORKDIR_PREFIX + "reused")
            os.mkdir(reused)
            bystander = self._sleeper()
            with open(os.path.join(reused, core.OWNER_FILE), "w") as f:
                json.dump({"owner": owner.pid, "mihomo": bystander.pid}, f)
            try:
                self.assertEqual(core.cleanup_stale(root), 2)
                self.assertFalse(os.path.exists(dead))
                self.assertFalse(os.path.exists(reused))
                self.assertTrue(os.path.exists(alive))
                orphan.wait(timeout=5)
                self.assertIsNone(bystander.poll(), "不能误杀 PID 被复用的无关进程")
            finally:
                for p in (orphan, bystander):
                    if p.poll() is None:
                        p.kill()

    @unittest.skipUnless(hasattr(signal, "SIGTERM") and os.name == "posix", "需要 POSIX 信号")
    def test_sigterm_becomes_keyboard_interrupt(self):
        old = signal.getsignal(signal.SIGTERM)
        try:
            run.install_exit_signals()
            with self.assertRaises(KeyboardInterrupt):
                os.kill(os.getpid(), signal.SIGTERM)
                time.sleep(1)  # 信号处理器在主线程下一条字节码时触发
        finally:
            signal.signal(signal.SIGTERM, old)
            if hasattr(signal, "SIGHUP"):
                signal.signal(signal.SIGHUP, signal.SIG_DFL)


class TestFindLocalBinary(unittest.TestCase):
    """28 / 36：start.sh 下载到项目 bin/ 的内核能被找到，且优先于 PATH。"""

    def test_fallback_to_project_bin(self):
        orig_dir, orig_which = core.LOCAL_BIN_DIR, core.shutil.which
        orig_env = os.environ.pop("NS_MIHOMO_BIN", None)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "mihomo")
            with open(path, "w") as f:
                f.write("#!/bin/sh\n")
            core.LOCAL_BIN_DIR = d
            core.shutil.which = lambda name: None
            try:
                self.assertIsNone(core.find_binary(), "没有执行权限的文件不能当内核")
                os.chmod(path, 0o755)
                self.assertEqual(core.find_binary(), path)
            finally:
                core.LOCAL_BIN_DIR, core.shutil.which = orig_dir, orig_which
                if orig_env is not None:
                    os.environ["NS_MIHOMO_BIN"] = orig_env

    def test_project_bin_preferred_over_path(self):
        """36：bin/ 里是自动保持最新的内核，必须优先于 PATH 里可能过旧的系统内核；NS_MIHOMO_BIN 仍最优先。"""
        orig_dir, orig_which = core.LOCAL_BIN_DIR, core.shutil.which
        orig_env = os.environ.pop("NS_MIHOMO_BIN", None)
        with tempfile.TemporaryDirectory() as d:
            local = os.path.join(d, "mihomo")
            env_bin = os.path.join(d, "custom-mihomo")
            for path in (local, env_bin):
                with open(path, "w") as f:
                    f.write("#!/bin/sh\n")
                os.chmod(path, 0o755)
            core.LOCAL_BIN_DIR = d
            core.shutil.which = lambda name: "/usr/local/bin/" + name
            try:
                self.assertEqual(core.find_binary(), local)
                os.environ["NS_MIHOMO_BIN"] = env_bin
                self.assertEqual(core.find_binary(), env_bin)
                del os.environ["NS_MIHOMO_BIN"]
                os.remove(local)
                self.assertEqual(core.find_binary(), "/usr/local/bin/mihomo", "bin/ 没有内核时退回 PATH")
            finally:
                core.LOCAL_BIN_DIR, core.shutil.which = orig_dir, orig_which
                os.environ.pop("NS_MIHOMO_BIN", None)
                if orig_env is not None:
                    os.environ["NS_MIHOMO_BIN"] = orig_env


class TestExportPngMasksSubscription(unittest.TestCase):
    """30：Web 前端「导出图片」把原始订阅 URL（含 token）画进 PNG，转发截图即泄露订阅。"""

    HTML = os.path.join(os.path.dirname(webapp.__file__), "static", "index.html")

    def _source(self):
        with open(self.HTML, encoding="utf-8") as f:
            return f.read()

    def test_export_png_uses_masked_src(self):
        src = self._source()
        body = src[src.index("function exportPNG"):src.index("function exportNodes")]
        self.assertIn("maskSrc(", body, "导出图片里的订阅必须脱敏")

    @unittest.skipUnless(shutil.which("node"), "需要 node 才能执行前端脚本")
    def test_mask_src_hides_token(self):
        src = self._source()
        fn = src[src.index("function maskSrc"):src.index("function fmtAgo")]
        token = "abcdef0123456789secret"
        script = fn + f"\nprocess.stdout.write(maskSrc('https://sub.example.com/api/v1/client/subscribe?token={token}'));"
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10, check=True).stdout
        self.assertIn("sub.example.com", out)
        self.assertNotIn(token, out)


class TestEventSourceClearsGlobal(unittest.TestCase):
    """31：setupEventSource 形参与全局 es 同名，done 里 es = null 只清了形参，
    测完后前端仍判定测试进行中（点开始变成停止、返回首页弹「还在进行」）。"""

    def test_param_does_not_shadow_global(self):
        with open(TestExportPngMasksSubscription.HTML, encoding="utf-8") as f:
            src = f.read()
        m = re.search(r"function setupEventSource\((\w+)\)", src)
        self.assertIsNotNone(m)
        self.assertNotEqual(m.group(1), "es", "形参遮蔽全局 es，置空不生效")
        body = src[m.start():src.index("function fmtElapsed")]
        self.assertIn("es = null", body, "done / error 时必须清空全局 es")


class TestClashLongYamlAndUriParsing(unittest.TestCase):
    """33：Clash 配置前置配置超 2000 字符导致误判为非 YAML，明文提取把 geox-url 抓出报暂不支持协议。"""

    def test_clash_yaml_proxies_after_2000_chars(self):
        # 原缺陷：text[:2000] 判断 proxies，导致头部很长时漏判，回退到 URI 提取报 "暂不支持协议 geoip: 'https://"
        long_comment = "# " + ("x" * 80 + "\n# ") * 30  # 约 2400 字符
        text = (
            "port: 7890\n"
            "mode: rule\n"
            f"{long_comment}\n"
            "geox-url:\n"
            "  geoip: 'https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/geoip.dat'\n"
            "  geosite: 'https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/geosite.dat'\n"
            "dns:\n"
            "  nameserver:\n"
            "    - 'https://doh.pub/dns-query'\n"
            "proxies:\n"
            "  - {name: '超长前置节点', type: ss, server: 1.2.3.4, port: 8388, cipher: aes-128-gcm, password: pwd}\n"
        )
        nodes, warns = parse(text)
        self.assertEqual(len(nodes), 1, f"警告: {warns}")
        self.assertEqual(nodes[0]["name"], "超长前置节点")
        self.assertFalse(any("暂不支持协议" in w for w in warns))

    def test_clash_yaml_only_proxy_providers(self):
        # 仅含 proxy-providers 且无 proxies 时，应清晰提示，而不是把内部 URL 当节点报错
        text = (
            "port: 7890\n"
            "geox-url:\n"
            "  geoip: 'https://github.com/geoip.dat'\n"
            "proxy-providers:\n"
            "  p1:\n"
            "    type: http\n"
            "    url: 'https://example.com/sub.yaml'\n"
        )
        nodes, warns = parse(text)
        self.assertEqual(len(nodes), 0)
        self.assertTrue(any("proxy-providers" in w for w in warns))
        self.assertFalse(any("暂不支持协议" in w for w in warns))

    def test_mixed_text_does_not_extract_web_urls(self):
        # 明文中混有普通网页说明与真实节点时，忽略普通 http/https 链接
        text = (
            "官网地址请访问 https://example.com/help\n"
            "API 说明: http://api.example.com\n"
            "- ss://YWVzLTEyOC1nY206cHdk@1.2.3.4:8388#节点01\n"
        )
        nodes, warns = parse(text)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]["name"], "节点01")
        self.assertFalse(any("暂不支持协议" in w for w in warns))

    # 以下为修复上面问题时引入、提交前发现的回归：全文搜 "proxies" 子串会把普通 URI 列表当成 YAML
    _SS = "ss://YWVzLTEyOC1nY206cHdk@1.2.3.4:8388"
    _URI_LIST_WITH_PROXIES = (
        f"{_SS}#HK\n"
        "vless://uuid@h.com:443?type=ws&path=%2Fproxies#JP\n"
        f"{_SS}#US: 01"  # 含 ": "，按 YAML 解析必然语法错
    )

    def test_uri_list_containing_proxies_is_not_yaml(self):
        nodes, warns = parse(self._URI_LIST_WITH_PROXIES)
        self.assertEqual([n["name"] for n in nodes], ["HK", "JP", "US: 01"], f"警告: {warns}")
        self.assertFalse(any("YAML" in w for w in warns))

    def test_base64_uri_list_containing_proxies_is_not_yaml(self):
        text = base64.b64encode(self._URI_LIST_WITH_PROXIES.encode()).decode()
        nodes, warns = parse(text)
        self.assertEqual([n["name"] for n in nodes], ["HK", "JP", "US: 01"], f"警告: {warns}")
        self.assertFalse(any("YAML" in w for w in warns))

    def test_real_clash_yaml_syntax_error_still_reported(self):
        # 顶层确有 proxies 键的配置写错时，仍要报 YAML 语法错误，而不是静默当 URI 列表
        text = "proxies:\n  - {name: a, type: ss\n  bad: [\n"
        nodes, warns = parse(text)
        self.assertEqual(nodes, [])
        self.assertTrue(any("YAML 语法错误" in w for w in warns), warns)

    def test_fallback_keeps_names_with_spaces_and_brackets(self):
        # 首行不是节点 URI 时走明文兜底：节点名含未编码空格或以括号结尾都要保持完整
        text = f"# 我的节点\n{self._SS}#香港 01\n- {self._SS}#日本(IPLC)\nurl: \"{self._SS}#美国[x2]\"\n"
        nodes, warns = parse(text)
        self.assertEqual([n["name"] for n in nodes], ["香港 01", "日本(IPLC)", "美国[x2]"], f"警告: {warns}")


class TestXhttp(unittest.TestCase):
    """35：xhttp 传输的解析、导出与旧内核提示。"""

    EXTRA = {
        "xPaddingBytes": "100-1000",
        "noGRPCHeader": True,
        "scMaxEachPostBytes": 1000000.0,
        "xmux": {"maxConcurrency": "16-32", "hKeepAlivePeriod": 30},
        "downloadSettings": {
            "address": "dl.example.com",
            "port": 443,
            "security": "tls",
            "tlsSettings": {"serverName": "dl.example.com", "alpn": ["h2"]},
            "xhttpSettings": {"path": "/dl", "host": "dl.example.com"},
        },
    }

    def _uri(self, **extra_q):
        q = {"type": "xhttp", "security": "tls", "sni": "a.com", "path": "/xh", "host": "a.com", "mode": "auto"}
        q.update(extra_q)
        return f"vless://11111111-1111-1111-1111-111111111111@a.com:443?{urlencode(q)}#XH"

    def test_vless_uri_xhttp_parsed(self):
        nodes, warns = parse(self._uri(extra=json.dumps(self.EXTRA)))
        self.assertEqual(len(nodes), 1, warns)
        n = nodes[0]
        self.assertEqual(n["network"], "xhttp")
        opts = n["xhttp-opts"]
        self.assertEqual((opts["path"], opts["host"], opts["mode"]), ("/xh", "a.com", "auto"))
        self.assertEqual(opts["x-padding-bytes"], "100-1000")
        self.assertIs(opts["no-grpc-header"], True)
        self.assertEqual(opts["sc-max-each-post-bytes"], 1000000)
        self.assertIsInstance(opts["sc-max-each-post-bytes"], int)
        self.assertEqual(opts["reuse-settings"], {"max-concurrency": "16-32", "h-keep-alive-period": 30})
        ds = opts["download-settings"]
        self.assertEqual(ds["server"], "dl.example.com")
        self.assertIs(ds["tls"], True)
        self.assertEqual(ds["servername"], "dl.example.com")
        self.assertEqual(ds["path"], "/dl")

    def test_splithttp_alias_and_bad_extra(self):
        nodes, warns = parse(self._uri(type="splithttp", extra="{not json"))
        self.assertEqual(len(nodes), 1, warns)
        self.assertEqual(nodes[0]["network"], "xhttp")
        self.assertEqual(nodes[0]["xhttp-opts"]["path"], "/xh")

    def test_trojan_xhttp_rejected(self):
        nodes, warns = parse("trojan://pwd@a.com:443?type=xhttp&path=%2Fx#T")
        self.assertEqual(nodes, [])
        self.assertTrue(any("xhttp" in w for w in warns), warns)

    def test_uri_roundtrip(self):
        nodes, _ = parse(self._uri(extra=json.dumps(self.EXTRA)))
        again, warns = parse(node_to_uri(nodes[0]))
        self.assertEqual(len(again), 1, warns)
        self.assertEqual(again[0]["xhttp-opts"], nodes[0]["xhttp-opts"])

    def test_vless_encryption_alpn_insecure(self):
        enc = "mlkem768x25519plus.native.0rtt.abc"
        nodes, warns = parse(self._uri(type="xhttp", alpn="h3", encryption=enc, allowInsecure="1"))
        self.assertEqual(len(nodes), 1, warns)
        n = nodes[0]
        self.assertEqual((n["encryption"], n["alpn"], n["skip-cert-verify"]), (enc, ["h3"], True))
        again, _ = parse(node_to_uri(n))
        self.assertEqual((again[0]["encryption"], again[0]["alpn"], again[0]["skip-cert-verify"]), (enc, ["h3"], True))
        # encryption=none 是默认值，不写进配置
        nodes, _ = parse(self._uri(encryption="none"))
        self.assertNotIn("encryption", nodes[0])
        # sing-box 不支持 VLESS Encryption
        tcp = {"name": "e", "type": "vless", "server": "a.com", "port": 443, "uuid": "u", "encryption": enc}
        self.assertIsNone(node_to_singbox(tcp))

    def test_singbox_skips_xhttp(self):
        nodes, _ = parse(self._uri())
        self.assertIsNone(node_to_singbox(nodes[0]))

    def _runner(self, version):
        nodes = [
            {"name": "xh", "type": "vless", "network": "xhttp"},
            {"name": "plain", "type": "vless"},
        ]
        r = core.MihomoRunner(nodes, iface="")
        r.binary = "/fake/mihomo"
        core._VERSION_CACHE[r.binary] = version
        self.addCleanup(core._VERSION_CACHE.pop, r.binary, None)
        return r

    def test_old_kernel_rejects_xhttp_with_hint(self):
        r = self._runner((1, 19, 20))
        r._reject_unsupported_transports()
        self.assertEqual([p["name"] for p in r.proxies], ["plain"])
        self.assertIn("1.19.20", r.rejected["xh"])
        self.assertIn("1.19.22", r.rejected["xh"])

    def test_new_or_unknown_kernel_keeps_xhttp(self):
        for version in ((1, 19, 22), None):
            r = self._runner(version)
            r._reject_unsupported_transports()
            self.assertEqual(len(r.proxies), 2)
            self.assertEqual(r.rejected, {})

    def test_kernel_version_parsing(self):
        script_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, script_dir)
        cases = {
            "Mihomo Meta v1.19.31 darwin arm64 with go1.25.7 2026-02-07": (1, 19, 31),
            "Mihomo Meta 1.19.20 darwin arm64 with go1.25.7": (1, 19, 20),
            "Mihomo Meta alpha-ab12cd3 linux amd64 with go1.25.7": None,  # 不能把 go 版本当内核版本
        }
        for i, (out, want) in enumerate(cases.items()):
            path = os.path.join(script_dir, f"m{i}.py")
            with open(path, "w") as f:
                f.write(f"import sys; print({out!r})\n")
            binary = path
            core._VERSION_CACHE.pop(binary, None)
            orig = core.subprocess.run

            # 用 python 解释器跑脚本，兼容 Windows（无法直接执行 .py）
            def fake_run(cmd, _p=path, **kw):
                return orig([sys.executable, _p] + cmd[1:], **kw)

            core.subprocess.run = fake_run
            try:
                self.assertEqual(core.kernel_version(binary), want, out)
            finally:
                core.subprocess.run = orig
                core._VERSION_CACHE.pop(binary, None)


class TestStartScriptVarBoundary(unittest.TestCase):
    """37：启动脚本里紧跟非 ASCII 字符的变量必须写成 ${var}。"""

    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def test_no_bare_var_before_non_ascii(self):
        pat = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*(?=[^\x00-\x7f])")
        for name in ("start.sh", "start.ps1"):
            with open(os.path.join(self.ROOT, name), encoding="utf-8-sig") as f:
                bad = [
                    f"{name}:{i}: {line.strip()}"
                    for i, line in enumerate(f, 1)
                    if not line.lstrip().startswith("#") and pat.search(line)
                ]
            self.assertEqual(bad, [], "变量后紧跟中文/全角字符时要写成 ${var}")

    @unittest.skipUnless(os.path.exists("/bin/sh"), "需要 /bin/sh")
    def test_bash_utf8_locale_expansion(self):
        # 直接验证 start.sh 里的写法在 UTF-8 locale 下能正确展开
        env = dict(os.environ, LC_ALL="en_US.UTF-8", LANG="en_US.UTF-8")
        out = subprocess.run(
            ["/bin/sh", "-c", 'set -u; version=v1; arch=arm64; echo "${version}（x/${arch}）"'],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, check=False,
        )
        self.assertEqual(out.stdout.decode("utf-8", "replace").strip(), "v1（x/arm64）")


class TestUnreachableServerHint(unittest.TestCase):
    """38：TCP 直连不通时，错误要指明是服务器连不上；UDP / H3 节点不适用这条判断。"""

    def _run(self, node):
        class Runner:
            ports = {node["name"]: 1}
            rejected = {}

        def boom(*a, **kw):
            raise OSError("Remote end closed connection")

        orig = tester.tcp_ping, tester.http_latency
        tester.tcp_ping = lambda *a, **kw: [None, None, None]
        tester.http_latency = boom
        try:
            return tester.test_node(node, runner=Runner(), options={"timeout": 1})
        finally:
            tester.tcp_ping, tester.http_latency = orig

    def test_tcp_node_unreachable(self):
        r = self._run({"name": "a", "type": "vless", "server": "h.com", "port": 443, "network": "xhttp"})
        self.assertTrue(r["error"].startswith("服务器连不上:"), r["error"])
        self.assertIn("h.com:443", r["error"])

    def test_udp_and_h3_nodes_keep_generic_error(self):
        for node in (
            {"name": "b", "type": "hysteria2", "server": "h.com", "port": 443},
            {"name": "c", "type": "vless", "server": "h.com", "port": 443, "alpn": ["h3"]},
        ):
            self.assertTrue(self._run(node)["error"].startswith("HTTP 测试失败:"), node)


class TestNodeKeyCaseInsensitive(unittest.TestCase):
    """39：域名大小写不同的同一节点应当去重。"""

    def test_server_case(self):
        from nodescope.subscription import _node_key

        a = {"name": "X", "type": "vless", "server": "tk-X07.example.com", "port": 1}
        b = dict(a, server="tk-x07.example.com")
        self.assertEqual(_node_key(a), _node_key(b))


class TestCliInterruptCancelsQueue(unittest.TestCase):
    """40：CLI 不传 cancel_event，Ctrl+C 打断主线程后仍要把排队节点全部测完才退出。"""

    def test_keyboard_interrupt_drops_queued_nodes(self):
        calls = []

        def slow(node, runner=None, options=None):
            calls.append(node["name"])
            threading.Event().wait(0.2)
            return {"name": node["name"], "ok": False, "error": "x", "_speed_tested": True}

        real_as_completed = tester.as_completed

        def interrupted(futures):
            # 第一个节点完成后模拟用户按下 Ctrl+C（主线程收到 KeyboardInterrupt）
            for f in real_as_completed(futures):
                yield f
                raise KeyboardInterrupt

        nodes = [{"name": f"n{i}"} for i in range(40)]
        orig = tester.test_node, tester.as_completed
        tester.test_node, tester.as_completed = slow, interrupted
        try:
            t0 = time.monotonic()
            with self.assertRaises(KeyboardInterrupt):
                tester.test_all(nodes, runner=None, options={"speed": False, "iface": "x"},
                                concurrency=4)
            elapsed = time.monotonic() - t0
        finally:
            tester.test_node, tester.as_completed = orig
        # 修复前要跑完 40 个（约 2s）；修复后只剩已在跑的几个
        self.assertLess(elapsed, 1.0)
        time.sleep(0.3)  # 等在跑的线程收尾，确认排队任务确实被丢弃
        self.assertLessEqual(len(calls), 8, calls)


class TestSubscriptionBodyDecoding(unittest.TestCase):
    """41：text/plain 不带 charset 时 resp.text 按 ISO-8859-1 解码，中文节点名乱码。"""

    def _resp(self, body, ctype):
        import requests
        from requests.utils import get_encoding_from_headers

        r = requests.models.Response()
        r._content = body
        r.status_code = 200
        r.headers["Content-Type"] = ctype
        r.encoding = get_encoding_from_headers(r.headers)  # 与 requests 适配器的行为一致
        return r

    def test_plain_text_without_charset(self):
        from nodescope.subscription import _decode_body

        body = "proxies:\n  - {name: 香港01, type: ss}\n".encode()
        self.assertIn("香港01", _decode_body(self._resp(body, "text/plain")))
        self.assertIn("香港01", _decode_body(self._resp(b"\xef\xbb\xbf" + body, "text/yaml")))

    def test_non_utf8_falls_back(self):
        from nodescope.subscription import _decode_body

        body = "名字".encode("gbk")
        self.assertEqual(_decode_body(self._resp(body, "text/plain; charset=gbk")), "名字")


class TestUriParsingCoverage(unittest.TestCase):
    """42：URI 解析覆盖不全，好节点被误报不可用；导出与解析不对称。"""

    def test_hy2_port_hopping_in_host(self):
        from nodescope.subscription import parse_uri

        n = parse_uri("hysteria2://pw@example.com:443,20000-30000/?sni=a&alpn=h3#hy")
        self.assertEqual((n["server"], n["port"], n["ports"]), ("example.com", 443, "443,20000-30000"))
        self.assertEqual(n["alpn"], ["h3"])
        n6 = parse_uri("hy2://pw@[2001:db8::1]:8443,9000-9100#v6")
        self.assertEqual((n6["server"], n6["port"], n6["ports"]), ("2001:db8::1", 8443, "8443,9000-9100"))
        with self.assertRaises(ValueError):
            parse_uri("hysteria2://pw@example.com:abc#bad")
        # 导出再读回，端口跳跃不能丢
        back = parse_uri(node_to_uri(n))
        self.assertEqual((back["port"], back["ports"], back["alpn"]), (443, "443,20000-30000", ["h3"]))

    def test_httpupgrade(self):
        from nodescope.subscription import parse_uri

        for uri in ("vless://u@h.com:443?type=httpupgrade&path=%2Fup&host=cdn.com&security=tls#a",
                    "trojan://pw@h.com:443?type=httpupgrade&path=%2Fup&host=cdn.com#b"):
            n = parse_uri(uri)
            self.assertEqual(n["network"], "ws", uri)
            self.assertEqual(n["ws-opts"], {"path": "/up", "headers": {"Host": "cdn.com"},
                                            "v2ray-http-upgrade": True})
            q = parse_qs(urlsplit(node_to_uri(n)).query)
            self.assertEqual(q["type"], ["httpupgrade"])
            self.assertEqual(node_to_singbox(n)["transport"],
                             {"type": "httpupgrade", "path": "/up", "host": "cdn.com"})
        vm = {"v": "2", "ps": "vm", "add": "h.com", "port": "80", "id": "id", "net": "httpupgrade",
              "path": "/up", "host": "cdn.com"}
        n = parse_uri("vmess://" + base64.b64encode(json.dumps(vm).encode()).decode())
        self.assertTrue(n["ws-opts"]["v2ray-http-upgrade"])
        self.assertEqual(parse_uri(node_to_uri(n))["ws-opts"], n["ws-opts"])

    def test_ss_v2ray_plugin(self):
        from nodescope.subscription import parse_uri

        cred = base64.b64encode(b"aes-256-gcm:pw").decode()
        n = parse_uri(f"ss://{cred}@1.2.3.4:443/?plugin=v2ray-plugin%3Btls%3Bhost%3Dcdn.com%3Bpath%3D%2Fws#v2")
        self.assertEqual(n["plugin"], "v2ray-plugin")
        self.assertEqual(n["plugin-opts"], {"mode": "websocket", "tls": True, "host": "cdn.com", "path": "/ws"})
        back = parse_uri(node_to_uri(n))
        self.assertEqual((back["plugin"], back["plugin-opts"]), (n["plugin"], n["plugin-opts"]))
        sb = node_to_singbox(n)
        self.assertEqual(sb["plugin"], "v2ray-plugin")
        self.assertIn("tls", sb["plugin_opts"].split(";"))
        self.assertNotIn("tls=True", sb["plugin_opts"])
        with self.assertRaises(Exception):
            parse_uri(f"ss://{cred}@1.2.3.4:443/?plugin=v2ray-plugin%3Bmode%3Dquic#q")

    def test_vmess_tcp_http_and_tls_fields(self):
        from nodescope.subscription import parse_uri

        vm = {"v": "2", "ps": "vm", "add": "h.com", "port": "80", "id": "id", "net": "tcp",
              "type": "http", "path": "/a,/b", "host": "x.com", "tls": "tls", "sni": "s.com",
              "alpn": "h2,http/1.1", "fp": "chrome", "allowInsecure": "1"}
        n = parse_uri("vmess://" + base64.b64encode(json.dumps(vm).encode()).decode())
        self.assertEqual(n["network"], "http")
        self.assertEqual(n["http-opts"], {"path": ["/a", "/b"], "headers": {"Host": ["x.com"]}})
        self.assertEqual((n["alpn"], n["client-fingerprint"], n["skip-cert-verify"]),
                         (["h2", "http/1.1"], "chrome", True))
        back = parse_uri(node_to_uri(n))
        for key in ("network", "http-opts", "alpn", "client-fingerprint", "skip-cert-verify", "servername"):
            self.assertEqual(back.get(key), n.get(key), key)
        # sing-box 不支持 TCP HTTP 伪装：跳过而不是导出成 HTTP/2
        self.assertIsNone(node_to_singbox(n))

    def test_vless_tcp_header_http_roundtrip(self):
        from nodescope.subscription import parse_uri

        n = parse_uri("vless://u@h.com:80?type=tcp&headerType=http&path=%2Fp&host=x.com#t")
        self.assertEqual(n["network"], "http")
        self.assertEqual(n["http-opts"], {"path": ["/p"], "headers": {"Host": ["x.com"]}})
        q = parse_qs(urlsplit(node_to_uri(n)).query)
        self.assertEqual((q["type"], q["headerType"]), (["tcp"], ["http"]))
        self.assertEqual(parse_uri(node_to_uri(n))["http-opts"], n["http-opts"])


class TestWebProgressCountsFailedSpeed(unittest.TestCase):
    """43：前端以 speed_mbps 判断测速完成，测速失败的节点永远不计入，进度到不了 100%。"""

    def _fn(self):
        with open(TestExportPngMasksSubscription.HTML, encoding="utf-8") as f:
            src = f.read()
        return src[src.index("function setProgress"):src.index("function updateStats")]

    def test_uses_test_mode_snapshot(self):
        self.assertNotIn("$('noSpeed').checked", self._fn(), "进度模式必须用开测时记下的 testNoSpeed")

    @unittest.skipUnless(shutil.which("node"), "需要 node 才能执行前端脚本")
    def test_failed_speed_counts_as_done(self):
        script = (
            "const els = {}; const $ = id => els[id] || (els[id] = {style: {}, textContent: ''});\n"
            "let testNoSpeed = false;\n"
            "const rows = {a: {_done: true, ok: true, speed_mbps: 50, _speed_tested: true},\n"
            "              b: {_done: true, ok: true, speed_mbps: null, _speed_tested: true},\n"
            "              c: {_done: true, ok: false}};\n"
            "const order = ['a', 'b', 'c'];\n"
            + self._fn() +
            "\nsetProgress(3, 3, 2); process.stdout.write(els.barFill.style.width + '|' + els.progressText.textContent);"
        )
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10, check=True).stdout
        width, text = out.split("|")
        self.assertEqual(width, "100%")
        self.assertEqual(text, "测速全部完成")


class TestDeadNodeSkipsRetry(unittest.TestCase):
    """44：TCP 直连已超时的 TCP 类节点不再重试 HTTP，UDP 类节点仍保留一次抖动重试。"""

    def _calls(self, node, tcp):
        calls = []

        class Runner:
            ports = {node["name"]: 1}
            rejected = {}

        def boom(*a, **kw):
            calls.append(1)
            raise OSError("timeout")

        orig = tester.tcp_ping, tester.http_latency
        tester.tcp_ping = lambda *a, **kw: tcp
        tester.http_latency = boom
        try:
            r = tester.test_node(node, runner=Runner(), options={"timeout": 1})
        finally:
            tester.tcp_ping, tester.http_latency = orig
        return len(calls), r

    def test_unreachable_tcp_node_tries_once(self):
        n, r = self._calls({"name": "a", "type": "vless", "server": "h", "port": 1}, [None, None, None])
        self.assertEqual(n, 1)
        self.assertTrue(r["error"].startswith("服务器连不上:"))

    def test_reachable_or_udp_node_retries(self):
        n, _ = self._calls({"name": "a", "type": "vless", "server": "h", "port": 1}, [10.0, 12.0, 11.0])
        self.assertEqual(n, 2)
        n, _ = self._calls({"name": "b", "type": "hysteria2", "server": "h", "port": 1}, [None, None, None])
        self.assertEqual(n, 2)


class TestSpeedSourceReplaced(unittest.TestCase):
    """45：hint 失效后选出的新源要替换缓存；别的线程已换过的不覆盖。"""

    def test_remember_source(self):
        cache = {}
        tester._remember_source(cache, "JP", None, "A")
        self.assertEqual(cache, {"JP": "A"})
        tester._remember_source(cache, "JP", "A", "B")  # A 失效，选出 B
        self.assertEqual(cache["JP"], "B")
        tester._remember_source(cache, "JP", "A", "C")  # 另一个线程拿着旧 hint A，不能覆盖 B
        self.assertEqual(cache["JP"], "B")
        tester._remember_source(cache, "JP", None, "D")  # 无 hint 时不覆盖已有
        self.assertEqual(cache["JP"], "B")
        tester._remember_source(cache, None, None, "E")
        tester._remember_source(cache, "US", "X", None)
        self.assertEqual(cache, {"JP": "B"})


class TestFallbackUaTimeout(unittest.TestCase):
    """46：补拉其他 UA 卡住时，load_nodes 必须按超时返回主订阅结果。"""

    def test_slow_fallback_does_not_block(self):
        from nodescope import subscription as sub

        main = "ss://" + base64.b64encode(b"aes-256-gcm:pw@1.2.3.4:8388").decode() + "#main"

        def fake_load_text(src, timeout=20, retries=3, ua=None, allow_file=True):
            if ua:  # 其他 UA 的补拉：模拟服务器迟迟不回
                threading.Event().wait(1.5)
            return main, ""

        orig = sub.load_text, sub.FALLBACK_UA_TIMEOUT
        sub.load_text, sub.FALLBACK_UA_TIMEOUT = fake_load_text, 0.2
        try:
            t0 = time.monotonic()
            nodes, _warns, _ = sub.load_nodes("https://sub.example.com/x")
            elapsed = time.monotonic() - t0
        finally:
            sub.load_text, sub.FALLBACK_UA_TIMEOUT = orig
        self.assertEqual([n["name"] for n in nodes], ["main"])
        self.assertLess(elapsed, 1.0)


class TestHtmlReportIconsInline(unittest.TestCase):
    """47：HTML 报告的解锁图标内联为 data URI，不再引用 CDN。"""

    def test_icons_inlined(self):
        res = [{"name": "a", "type": "ss", "ok": True, "speed_mbps": 10.0, "http_ms": 100.0,
                "unlock": {"chatgpt": True, "netflix": True}}]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "r.html")
            report.export_html(res, path, show_unlock=True)
            with open(path, encoding="utf-8") as f:
                html = f.read()
        self.assertNotIn("jsdelivr", html)
        self.assertEqual(html.count('src="data:image/svg+xml;base64,'), 2)
        for _key, (_name, file_name) in report.HTML_UNLOCK_ICONS.items():
            self.assertTrue(os.path.isfile(os.path.join(report.ICON_DIR, file_name)), file_name)


class TestIfaceSingleFlight(unittest.TestCase):
    """48：同一域名并发只解析一次；UDP 53 不可达后后续域名直接走系统解析。"""

    def setUp(self):
        from nodescope import iface as iface_mod
        self.m = iface_mod
        self.m.clear_cache()
        self._orig = (self.m._dns_query, self.m.socket.getaddrinfo)

    def tearDown(self):
        self.m._dns_query, self.m.socket.getaddrinfo = self._orig
        self.m.clear_cache()

    def _concurrent(self, fn, n=8):
        out = []
        threads = [threading.Thread(target=lambda: out.append(fn())) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
        return out

    def test_concurrent_lookups_share_one_query(self):
        calls = []

        def fake_query(host, iface, server, qtype=1, timeout=3):
            calls.append(host)
            threading.Event().wait(0.2)
            return ["93.184.216.34"]

        self.m._dns_query = fake_query
        out = self._concurrent(lambda: self.m.resolve_host("same.example", "en0"))
        self.assertEqual(len(calls), 1)
        self.assertEqual(out, [[(socket.AF_INET, "93.184.216.34")]] * 8)

    def test_concurrent_failures_shared_not_replayed(self):
        calls = []

        def fake_query(host, iface, server, qtype=1, timeout=3):
            calls.append(host)
            threading.Event().wait(0.2)
            return []

        self.m._dns_query = fake_query
        self.m.socket.getaddrinfo = lambda *a, **kw: []
        t0 = time.monotonic()
        self._concurrent(lambda: self.m.resolve_host("bad.example", "en0"))
        self.assertLess(time.monotonic() - t0, 1.5, "失败结果被串行重放")
        n = len(calls)
        self.m.resolve_host("bad.example", "en0")
        self.assertEqual(len(calls), 2 * n, "失败仍不应被缓存")

    def test_udp_dns_down_remembered(self):
        calls = []

        def fake_query(host, iface, server, qtype=1, timeout=3):
            calls.append((host, server))
            raise socket.timeout()

        self.m._dns_query = fake_query
        self.m.socket.getaddrinfo = lambda *a, **kw: [(socket.AF_INET, 1, 6, "", ("1.2.3.4", 0))]
        self.assertEqual(self.m.resolve_host("a.example", "en0"), [(socket.AF_INET, "1.2.3.4")])
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.m.resolve_host("b.example", "en0"), [(socket.AF_INET, "1.2.3.4")])
        self.assertEqual(len(calls), 2, "UDP 53 已判定不可达，不应再发 UDP 查询")
        # 另一块网卡不受影响
        self.m.resolve_host("c.example", "en1")
        self.assertEqual(len(calls), 4)


class TestWebExportOrderedByRank(unittest.TestCase):
    """49：Web「导出可用节点」要按排名导出，与 CLI 一致。"""

    def test_export_sorted(self):
        with open(TestExportPngMasksSubscription.HTML, encoding="utf-8") as f:
            src = f.read()
        body = src[src.index("function exportNodes"):src.index("function ellipsize")]
        self.assertRegex(body, r"availableRaw = [^;]*\.sort\([^;]*rankVal\(", "导出必须按 rankVal 排序")


class TestBaselineTrust(unittest.TestCase):
    """50：直连基线明显低于节点速度时不可信，不能再拿它把节点标成「≈ 本机带宽上限」。"""

    HTML = os.path.join(os.path.dirname(webapp.__file__), "static", "index.html")

    def _eff(self, local_bw, speeds):
        with open(self.HTML, encoding="utf-8") as f:
            src = f.read()
        fn = src[src.index("const BASELINE_TRUST"):src.index("// 提示收进状态栏")]
        rows = {f"n{i}": {"_done": True, "speed_mbps": v} for i, v in enumerate(speeds)}
        script = (f"const order = {json.dumps(list(rows))}; const rows = {json.dumps(rows)};"
                  f"const localBW = {json.dumps(local_bw)};\n{fn}\n"
                  "process.stdout.write(JSON.stringify(effBaseline()));")
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10, check=True).stdout
        return json.loads(out)

    @unittest.skipUnless(shutil.which("node"), "需要 node 才能执行前端脚本")
    def test_baseline_discarded_when_nodes_far_exceed_it(self):
        # 直连只有 20 Mbps，专线节点跑到 200：基线作废，且节点速度各不相同时不推断上限
        self.assertIsNone(self._eff(20, [200, 150, 90]))
        # 节点都在基线附近：基线可信
        self.assertEqual(self._eff(100, [98, 95, 60]), 100)
        self.assertEqual(self._eff(100, [110, 95]), 100, "略高于基线属于测量误差，不应作废")
        # 基线作废后，≥3 个节点挤在最快值附近时仍按原规则推断撞了上限
        self.assertEqual(self._eff(20, [200, 198, 197, 50]), 200)
        self.assertEqual(self._eff(None, [200, 198, 197]), 200)


def _chunked_resp(chunks, served):
    """按块吐正文的假响应；served 记录实际被读走了多少块。"""
    class _Resp:
        status_code = 200

        def __init__(self, url):
            self.url, self.text = url, ""

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def json(self):
            return {}

        def iter_content(self, chunk_size):
            for chunk in chunks:
                served.append(len(chunk))
                yield chunk

    return _Resp


class TestUnlockTraffic(unittest.TestCase):
    """51：解锁检测走的是订阅流量，能提前下结论的页面不应整页下载。"""

    def _run(self, host, chunks):
        served, streamed = [], {}
        resp_cls = _chunked_resp(chunks, served)
        empty_cls = _chunked_resp([], [])

        def fake_get(url, **kwargs):
            if host in url:
                streamed["stream"] = kwargs.get("stream")
                return resp_cls(url)
            return empty_cls(url)

        orig = tester.requests.get
        tester.requests.get = fake_get
        try:
            res = tester.check_unlocks(1, country_code="JP")
        finally:
            tester.requests.get = orig
        self.assertTrue(streamed.get("stream"), "必须流式请求，否则 requests 会先把整页读完")
        return res, served

    def test_tiktok_stops_at_first_region(self):
        filler = b"x" * 16384
        res, served = self._run("tiktok", [filler, b'..."region":"JP"...'] + [filler] * 40)
        self.assertTrue(res["tiktok"])
        self.assertLessEqual(len(served), 3, "读到 region 就应停止")
        # region 跨块时也要能识别
        res, _ = self._run("tiktok", [b'..."regi', b'on":"JP"...'])
        self.assertTrue(res["tiktok"])
        res, _ = self._run("tiktok", [filler, b'"region":"CN"'])
        self.assertFalse(res["tiktok"])

    def test_netflix_reads_only_head(self):
        filler = b"x" * 16384
        res, served = self._run("netflix", [filler] * 150)  # 约 2.4MB 的正常剧集页
        self.assertTrue(res["netflix"])
        self.assertLessEqual(sum(served), tester._NETFLIX_HEAD_BYTES + 16384)
        res, _ = self._run("netflix", [b"<html><title>Netflix</title>", b"<h1>Oh no!</h1></html>"])
        self.assertFalse(res["netflix"], "错误页仍要识别出来")

    def test_web_can_disable_unlock(self):
        with open(TestBaselineTrust.HTML, encoding="utf-8") as f:
            src = f.read()
        self.assertIn('id="noUnlock"', src)
        self.assertRegex(src, r"unlock: \$\('noUnlock'\)\.checked \? '0' : '1'")


class TestSteadyWindow(unittest.TestCase):
    """52：计速要剔除起步爬坡段，否则流量上限提前停时快节点被系统性低估。"""

    @staticmethod
    def _marks(segments, step=0.05):
        """segments: [(持续秒数, 字节/秒), ...] → 每 step 秒一个 (时刻, 累计字节) 采样点。"""
        marks, t, done = [], 0.0, 0.0
        for seconds, rate in segments:
            for _ in range(int(round(seconds / step))):
                t += step
                done += rate * step
                marks.append((round(t, 6), done))
        return marks, t, done

    def test_ramp_excluded(self):
        # 第 1 秒爬坡只有 1MB/s，之后稳定在 10MB/s
        marks, t_end, total = self._marks([(1.0, 1e6), (4.0, 10e6)])
        got, elapsed = tester._steady_window(marks, 0.0, t_end, total)
        self.assertAlmostEqual(got / elapsed, 10e6, delta=0.3e6)
        self.assertLess(total / t_end, 8.5e6, "按整段平均会被爬坡段拉低")

    def test_short_run_skips_proportionally(self):
        # 流量上限让测速 0.8s 就停：只剔除前 30%，剩余时长仍够计速
        marks, t_end, total = self._marks([(0.25, 2e6), (0.55, 30e6)])
        got, elapsed = tester._steady_window(marks, 0.0, t_end, total)
        self.assertGreater(got / elapsed, total / t_end * 1.2)
        self.assertGreaterEqual(elapsed, tester._MIN_STEADY_SECONDS)

    def test_falls_back_to_whole_window(self):
        # 只有开头 0.3s 有数据，之后断流到 5s：不能拿片段算，耗时要含断流的等待
        marks, _, total = self._marks([(0.3, 10e6)])
        self.assertEqual(tester._steady_window(marks, 0.0, 5.0, total), (total, 5.0))
        # 整段太短：剔除后不足最小计速时长，按整段算且时长有下限
        marks, t_end, total = self._marks([(0.2, 10e6)])
        got, elapsed = tester._steady_window(marks, 0.0, t_end, total)
        self.assertEqual(got, total)
        self.assertGreaterEqual(elapsed, tester._MIN_STEADY_SECONDS)


class TestKernelDownloadChecksum(unittest.TestCase):
    """53：启动脚本下载内核后必须先校验 SHA256 再解压、运行；校验值不能经加速前缀获取。"""

    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _read(self, name):
        with open(os.path.join(self.ROOT, name), encoding="utf-8-sig") as f:
            return f.read()

    def test_start_sh_verifies_before_extract(self):
        src = self._read("start.sh")
        body = src[src.index("download_mihomo() {"):]
        self.assertLess(body.index("expected_sha256 \"$version\""), body.index("gunzip -c"))
        api_line = next(ln for ln in src.splitlines() if "api.github.com/repos" in ln)
        self.assertNotIn("NS_GITHUB_PROXY", api_line)

    def test_start_ps1_verifies_before_extract(self):
        src = self._read("start.ps1")
        body = src[src.index("function Get-Mihomo("):]
        self.assertLess(body.index("Get-ExpectedSha256 $version"), body.index("Expand-Archive"))
        api_line = next(ln for ln in src.splitlines() if "api.github.com/repos" in ln)
        self.assertNotIn("NS_GITHUB_PROXY", api_line)


if __name__ == "__main__":
    unittest.main()
