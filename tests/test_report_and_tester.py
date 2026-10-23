import os
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from nodescope import report, tester


class TestReportAndTester(unittest.TestCase):
    def test_export_html_speed_selector(self):
        results = [
            {"name": "Node A", "type": "ss", "ok": True, "speed_mbps": 50.5, "http_ms": 120},
            {"name": "Node B", "type": "ss", "ok": True, "speed_mbps": 10.2, "http_ms": 200},
        ]
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            temp_path = f.name
        try:
            report.export_html(results, temp_path, show_unlock=True)
            with open(temp_path, "r", encoding="utf-8") as f:
                content = f.read()
            # 确保使用精准的选择器而不是写死 th[7]
            self.assertIn("table.querySelector('th[data-k=\"speed\"]')", content)
            self.assertNotIn("table.querySelectorAll('th')[7].click()", content)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_sort_results(self):
        results = [
            {"name": "Node Fail", "ok": False, "speed_mbps": None, "http_ms": None, "tcp_ms": None},
            {"name": "Node Fast", "ok": True, "speed_mbps": 100.0, "http_ms": 150},
            {"name": "Node Slow", "ok": True, "speed_mbps": 5.0, "http_ms": 100},
            {"name": "Node NoSpeed LowLat", "ok": True, "speed_mbps": None, "http_ms": 50},
        ]
        sorted_res = report.sort_results(results)
        self.assertEqual(sorted_res[0]["name"], "Node Fast")
        self.assertEqual(sorted_res[1]["name"], "Node Slow")
        self.assertEqual(sorted_res[2]["name"], "Node NoSpeed LowLat")
        self.assertEqual(sorted_res[3]["name"], "Node Fail")

    def test_local_bandwidth_cancel(self):
        cancel_event = threading.Event()
        cancel_event.set()
        # 已取消时应立刻返回 None
        bw = tester.local_bandwidth_mbps(cancel_event=cancel_event)
        self.assertIsNone(bw)

    @patch("nodescope.tester.requests.get")
    def test_gemini_unlock_detection(self, mock_get):
        # 1. 正常解锁（荷兰物理地区，且 Google 识别为 NLD）
        resp_ok = MagicMock()
        resp_ok.status_code = 200
        resp_ok.url = "https://gemini.google.com/"
        resp_ok.text = '<html>...this.gbar_={CONFIG:[[[0,"",2,1,200,"NLD",null]]]...</html>'

        mock_get.return_value = resp_ok
        res = tester.check_unlocks(None, country_code="NL")
        self.assertTrue(res["gemini"], "Google 识别为 NLD 时应判定为解锁")

        # 2. Google 送中（第三方 IP 库显示 NL，但 Google 识别为 HKG 或 CHN）
        resp_songzhong = MagicMock()
        resp_songzhong.status_code = 200
        resp_songzhong.url = "https://gemini.google.com/"
        resp_songzhong.text = '<html>...this.gbar_={CONFIG:[[[0,"",2,1,200,"HKG",null]]]...</html>'

        mock_get.return_value = resp_songzhong
        res = tester.check_unlocks(None, country_code="NL")
        self.assertFalse(res["gemini"], "Google 送中识别为 HKG 时应判定为未解锁")

        # 3. 页面返回不支持提示（例如 isn't currently supported）
        resp_unsupported = MagicMock()
        resp_unsupported.status_code = 200
        resp_unsupported.url = "https://gemini.google.com/"
        resp_unsupported.text = "<html>Gemini isn't currently supported in your country.</html>"

        mock_get.return_value = resp_unsupported
        res = tester.check_unlocks(None, country_code="NL")
        self.assertFalse(res["gemini"], "页面包含不支持提示时应判定为未解锁")

    @patch("nodescope.tester.requests.get")
    def test_youtube_and_netflix_detection(self, mock_get):
        # 1. YouTube 送中（页面包含 www.google.cn 或 not available）
        resp_yt_fail = MagicMock()
        resp_yt_fail.status_code = 200
        resp_yt_fail.url = "https://www.youtube.com/premium"
        resp_yt_fail.text = '<html>Premium is not available in your country. www.google.cn</html>'

        mock_get.return_value = resp_yt_fail
        res = tester.check_unlocks(None, country_code="NL")
        self.assertFalse(res["youtube"], "YouTube 送中或未提供 Premium 时应判定为未解锁")

        # 2. Netflix 仅自制剧或被封（出现 Oh no!）
        resp_nf_fail = MagicMock()
        resp_nf_fail.status_code = 200
        resp_nf_fail.url = "https://www.netflix.com/title/80018499"
        resp_nf_fail.text = '<html>Oh no! Something went wrong</html>'

        mock_get.return_value = resp_nf_fail
        res = tester.check_unlocks(None, country_code="NL")
        self.assertFalse(res["netflix"], "Netflix 出现错误页应判定为未解锁")


if __name__ == "__main__":
    unittest.main()
