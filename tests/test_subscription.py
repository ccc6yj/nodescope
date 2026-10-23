import base64
import json
import unittest

from nodescope.subscription import (
    _b64decode,
    _drop_info_nodes,
    dedupe_names,
    format_userinfo,
    node_to_singbox,
    node_to_uri,
    parse,
    parse_uri,
)


class TestSubscription(unittest.TestCase):
    def test_b64decode_with_bom_and_padding(self):
        raw = "hello world"
        encoded = base64.b64encode(raw.encode()).decode().rstrip("=")
        # 测试自动补齐 padding 与带 BOM
        with_bom = "\ufeff" + encoded
        self.assertEqual(_b64decode(with_bom).decode(), raw)

    def test_parse_ss(self):
        # ss://method:password@server:port#tag
        userinfo = base64.b64encode(b"aes-256-gcm:pass123").decode()
        uri = f"ss://{userinfo}@1.2.3.4:8388#%E9%A6%99%E6%B8%AF01"
        node = parse_uri(uri)
        self.assertEqual(node["type"], "ss")
        self.assertEqual(node["name"], "香港01")
        self.assertEqual(node["server"], "1.2.3.4")
        self.assertEqual(node["port"], 8388)
        self.assertEqual(node["cipher"], "aes-256-gcm")
        self.assertEqual(node["password"], "pass123")

    def test_parse_vmess(self):
        cfg = {
            "v": "2",
            "ps": "日本节点",
            "add": "jp.example.com",
            "port": "443",
            "id": "uuid-1234-5678",
            "aid": "0",
            "net": "ws",
            "type": "none",
            "host": "cdn.example.com",
            "path": "/ws",
            "tls": "tls",
        }
        uri = "vmess://" + base64.b64encode(json.dumps(cfg).encode()).decode()
        node = parse_uri(uri)
        self.assertEqual(node["type"], "vmess")
        self.assertEqual(node["name"], "日本节点")
        self.assertEqual(node["server"], "jp.example.com")
        self.assertEqual(node["port"], 443)
        self.assertEqual(node["uuid"], "uuid-1234-5678")
        self.assertEqual(node["network"], "ws")
        self.assertTrue(node["tls"])

    def test_parse_vless_reality(self):
        uri = (
            "vless://user-uuid@us.example.com:443"
            "?security=reality&sni=yahoo.com&fp=chrome&pbk=publicKey123"
            "&sid=abcd&spx=%2Fsearch&type=grpc&serviceName=test-grpc#%E7%BE%8E%E5%9B%BDReality"
        )
        node = parse_uri(uri)
        self.assertEqual(node["type"], "vless")
        self.assertEqual(node["name"], "美国Reality")
        self.assertEqual(node["server"], "us.example.com")
        self.assertEqual(node["port"], 443)
        self.assertTrue(node["tls"])
        self.assertEqual(node["client-fingerprint"], "chrome")
        self.assertEqual(node["reality-opts"]["public-key"], "publicKey123")
        self.assertEqual(node["reality-opts"]["short-id"], "abcd")
        self.assertEqual(node["reality-opts"]["spider-x"], "/search")
        self.assertEqual(node["network"], "grpc")

    def test_parse_trojan(self):
        uri = "trojan://password123@sg.example.com:443?sni=sg.example.com#%E6%96%B0%E5%8A%A0%E5%9坡"
        node = parse_uri(uri)
        self.assertEqual(node["type"], "trojan")
        self.assertEqual(node["server"], "sg.example.com")
        self.assertEqual(node["password"], "password123")
        self.assertEqual(node["sni"], "sg.example.com")

    def test_parse_hysteria2(self):
        uri = "hy2://pwd123@hy.example.com:8443?sni=hy.example.com&insecure=1#HY2Node"
        node = parse_uri(uri)
        self.assertEqual(node["type"], "hysteria2")
        self.assertEqual(node["server"], "hy.example.com")
        self.assertEqual(node["port"], 8443)
        self.assertEqual(node["password"], "pwd123")
        self.assertTrue(node["skip-cert-verify"])

    def test_parse_tuic(self):
        uri = "tuic://myuuid:mypass@tuic.example.com:8443?sni=tuic.example.com&congestion_control=bbr&alpn=h3#TUICNode"
        node = parse_uri(uri)
        self.assertEqual(node["type"], "tuic")
        self.assertEqual(node["server"], "tuic.example.com")
        self.assertEqual(node["port"], 8443)
        self.assertEqual(node["uuid"], "myuuid")
        self.assertEqual(node["password"], "mypass")
        self.assertEqual(node["congestion-controller"], "bbr")
        self.assertEqual(node["alpn"], ["h3"])

    def test_drop_info_nodes(self):
        raw_nodes = [
            {"name": "剩余流量：50GB", "type": "ss"},
            {"name": "香港 01 - 流量倍率:1.0", "type": "ss"},
            {"name": "套餐到期：2026-12-31", "type": "vmess"},
        ]
        kept, warns = _drop_info_nodes(raw_nodes, [])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["name"], "香港 01 - 流量倍率:1.0")
        self.assertTrue(any("说明节点" in w for w in warns))

    def test_dedupe_names(self):
        nodes = [
            {"name": "香港 01"},
            {"name": "香港 01"},
            {"name": "香港 01"},
        ]
        dedupe_names(nodes)
        self.assertEqual(nodes[0]["name"], "香港 01")
        self.assertEqual(nodes[1]["name"], "香港 01 #2")
        self.assertEqual(nodes[2]["name"], "香港 01 #3")

    def test_format_userinfo(self):
        header = "upload=1073741824; download=10737418240; total=107374182400"
        msg = format_userinfo(header)
        self.assertIn("11.00 GB", msg)
        self.assertIn("100.0 GB", msg)

    def test_node_to_uri_and_singbox(self):
        node = {
            "name": "HK01",
            "type": "ss",
            "server": "1.2.3.4",
            "port": 8388,
            "cipher": "aes-128-gcm",
            "password": "pwd",
        }
        uri = node_to_uri(node)
        self.assertTrue(uri.startswith("ss://"))
        self.assertIn("HK01", uri)

        ob = node_to_singbox(node)
        self.assertEqual(ob["type"], "shadowsocks")
        self.assertEqual(ob["tag"], "HK01")
        self.assertEqual(ob["method"], "aes-128-gcm")

    def test_b64decode_with_newlines_and_unpadded(self):
        # 带有换行符且未对齐 padding 的 base64 字符串
        raw = b"vmess://test-node-sample-content"
        b64 = base64.b64encode(raw).decode().rstrip("=")
        # 插入中间换行
        b64_multiline = b64[:10] + "\r\n  " + b64[10:]
        decoded = _b64decode(b64_multiline)
        self.assertEqual(decoded, raw)

    def test_parse_vmess_with_hash_tag(self):
        cfg = {
            "v": "2",
            "add": "hk.example.com",
            "port": 443,
            "id": "uuid-000",
            "net": "tcp",
        }
        b64_cfg = base64.b64encode(json.dumps(cfg).encode()).decode()
        uri = f"vmess://{b64_cfg}#%E9%A6%99%E6%B8%AFTag"
        node = parse_uri(uri)
        self.assertEqual(node["name"], "香港Tag")
        self.assertEqual(node["server"], "hk.example.com")
        self.assertEqual(node["port"], 443)

    def test_parse_quantumultx_with_comments(self):
        text = """# Quantumult X Server Config
; another comment line

trojan=sg.example.com:443, password=pwd123, tag=新加坡QX, over-tls=true
vmess=hk.example.com:443, method=auto, password=uuid-123, tag=香港QX, obfs=ws
"""
        nodes, warns = parse(text)
        self.assertEqual(len(nodes), 2)
        self.assertEqual(nodes[0]["name"], "新加坡QX")
        self.assertEqual(nodes[0]["type"], "trojan")
        self.assertEqual(nodes[1]["name"], "香港QX")
        self.assertEqual(nodes[1]["type"], "vmess")

    def test_node_to_singbox_vless_websocket_and_grpc(self):
        # 测试 VLESS WebSocket 导出
        node_ws = {
            "name": "VLESS-WS",
            "type": "vless",
            "server": "cf.example.com",
            "port": 443,
            "uuid": "uuid-111",
            "tls": True,
            "servername": "cf.example.com",
            "network": "ws",
            "ws-opts": {
                "path": "/websocket",
                "headers": {"Host": "cf.example.com"}
            }
        }
        ob_ws = node_to_singbox(node_ws)
        self.assertEqual(ob_ws["type"], "vless")
        self.assertIn("transport", ob_ws)
        self.assertEqual(ob_ws["transport"]["type"], "ws")
        self.assertEqual(ob_ws["transport"]["path"], "/websocket")
        self.assertEqual(ob_ws["transport"]["headers"]["Host"], "cf.example.com")

        # 测试 VLESS gRPC 导出
        node_grpc = {
            "name": "VLESS-gRPC",
            "type": "vless",
            "server": "grpc.example.com",
            "port": 443,
            "uuid": "uuid-222",
            "tls": True,
            "servername": "grpc.example.com",
            "network": "grpc",
            "grpc-opts": {
                "grpc-service-name": "my-service"
            }
        }
        ob_grpc = node_to_singbox(node_grpc)
        self.assertEqual(ob_grpc["type"], "vless")
        self.assertIn("transport", ob_grpc)
        self.assertEqual(ob_grpc["transport"]["type"], "grpc")
        self.assertEqual(ob_grpc["transport"]["service_name"], "my-service")


if __name__ == "__main__":
    unittest.main()
