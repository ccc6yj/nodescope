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
"""

import http.client
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from urllib.parse import parse_qs, urlsplit

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

        uri = "ss://YWVzLTEyOC1nY206cHc=@a.com:8388?plugin=v2ray-plugin%3Btls#X"
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
        # 旧实现按墙钟算，会得到 ≈ handshake + window
        self.assertLess(elapsed, window + 0.15)
        self.assertGreaterEqual(elapsed, window - 0.05)
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
        self.assertGreaterEqual(elapsed, 0.45)
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
        self.assertNotIn("interface-name", r._build_config())
        r = core.MihomoRunner([{"name": "a", "type": "socks5"}], iface="en0")
        self.assertEqual(r._build_config()["interface-name"], "en0")


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
    """28：PATH 里没有内核时，回退到项目根目录 bin/ 下由 start.sh 下载的 mihomo。"""

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



if __name__ == "__main__":
    unittest.main()
