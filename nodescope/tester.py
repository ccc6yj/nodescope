"""测速核心：延迟（TCP 直连 / HTTP 经节点）与下载速度。"""

import contextlib
import ipaddress
import re
import socket
import ssl
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

LATENCY_URL_DEFAULT = "http://www.gstatic.com/generate_204"
# 测速源覆盖 anycast + 亚太 + 欧美。并行探测后按速度选，
# 避免日本节点测到 OVH 法国（跨洲只有几十 Mbps，同城 CDN 能到数百）。
# Cloudflare 对机房 IP 常 403，探测到 0 会自然淘汰；
# 另外它对 bytes >= 100000000 的请求一律 403，必须低于这个值。
# 单个文件拉完时测速线程会自动重新请求，所以文件大小不限制测速窗口。
# （cachefly 的 200mb.test 已下线，只返回一句 "I just served you 200mb"，已移除）
CF_DOWN_URL = "https://speed.cloudflare.com/__down?bytes=90000000"
SPEED_URLS = [
    CF_DOWN_URL,
    "http://speedtest.tokyo2.linode.com/100MB-tokyo.bin",
    "https://hnd-jp-ping.vultr.com/vultr.com.1000MB.bin",
    "http://speedtest.singapore.linode.com/100MB-singapore.bin",
    "https://sgp-ping.vultr.com/vultr.com.1000MB.bin",
    "https://nbg1-speed.hetzner.com/1GB.bin",
    "https://proof.ovh.net/files/1Gb.dat",
]
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
SPEED_HEADERS = {"User-Agent": UA, "Accept-Encoding": "identity"}
GEO_TRACE_URL = "http://www.cloudflare.com/cdn-cgi/trace"

def country_flag(code):
    """把两字母国家代码转为 Flag Emoji，如 HK -> 🇭🇰。"""
    if not code or len(code) != 2:
        return ""
    code = code.upper()
    try:
        return "".join(chr(127397 + ord(c)) for c in code)
    except Exception:
        return ""


COUNTRY_NAMES = {
    "HK": "香港", "TW": "台湾", "JP": "日本", "KR": "韩国",
    "SG": "新加坡", "US": "美国", "GB": "英国", "DE": "德国",
    "FR": "法国", "NL": "荷兰", "RU": "俄罗斯", "CA": "加拿大",
    "AU": "澳大利亚", "IN": "印度", "TH": "泰国", "MY": "马来西亚",
    "VN": "越南", "PH": "菲律宾", "ID": "印尼", "BR": "巴西",
    "ZA": "南非", "AE": "阿联酋", "TR": "土耳其", "UA": "乌克兰",
    "MO": "澳门", "IE": "爱尔兰", "SE": "瑞典", "CH": "瑞士", "IT": "意大利",
}

COLO_CITIES = {
    # 日本
    "NRT": "东京", "HND": "东京", "KIX": "大阪", "ITM": "大阪", "FUK": "福冈", "OKA": "冲绳", "CTS": "札幌",
    # 港澳台
    "HKG": "香港", "TPE": "台北", "KHH": "高雄", "MFM": "澳门",
    # 韩国
    "ICN": "首尔", "GMP": "首尔",
    # 东南亚
    "SIN": "新加坡", "BKK": "曼谷", "KUL": "吉隆坡", "MNL": "马尼拉", "SGN": "胡志明市", "HAN": "河内", "CGK": "雅加达",
    # 北美
    "LAX": "洛杉矶", "SFO": "旧金山", "SJC": "圣何塞", "SEA": "西雅图", "ORD": "芝加哥",
    "JFK": "纽约", "EWR": "纽瓦克", "IAD": "华盛顿", "DFW": "达拉斯", "MIA": "迈阿密", "ATL": "亚特兰大",
    "YVR": "温哥华", "YYZ": "多伦多",
    # 欧洲
    "LHR": "伦敦", "FRA": "法兰克福", "AMS": "阿姆斯特丹", "CDG": "巴黎", "MAD": "马德里", "MXP": "米兰",
    "ZRH": "苏黎世", "ARN": "斯德哥尔摩", "WAW": "华沙", "HEL": "赫尔辛基",
    # 大洋洲
    "SYD": "悉尼", "MEL": "墨尔本", "BNE": "布里斯班", "PER": "珀斯", "AKL": "奥克兰",
}


def _format_geo_name(country, city, country_code):
    loc = (country_code or "").upper()
    if loc == "HK":
        return "香港"
    if loc == "MO":
        return "澳门"
    if loc == "TW":
        c_clean = (city or "").replace("特别行政区", "")
        if c_clean.endswith("都") or (len(c_clean) > 2 and c_clean.endswith("市")):
            c_clean = c_clean[:-1]
        return f"台湾 {c_clean}".strip() if c_clean and c_clean != "台湾" else "台湾"
    if loc == "SG" or country == "新加坡":
        return "新加坡"
    if city and country and city != country:
        c_clean = city.replace("特别行政区", "")
        if c_clean.endswith("都") or (len(c_clean) > 2 and c_clean.endswith("市")):
            c_clean = c_clean[:-1]
        return f"{country} {c_clean}".strip()
    return country or city or loc


def fetch_node_geo(port, timeout=3.5):
    """经节点查询落地 IP、精准城市与国家地区（标准中文输出，如「日本 东京」、「美国 洛杉矶」）。"""
    proxies = _proxies(port)

    # 1) 主引擎：ip-api (直出标准中文物理城市)
    try:
        url_ipapi = "http://ip-api.com/json/?lang=zh-CN&fields=status,country,countryCode,city,query"
        resp = requests.get(url_ipapi, proxies=proxies, timeout=(min(timeout, 2.0), min(timeout, 2.0)))
        if resp.status_code == 200:
            d = resp.json()
            if d.get("status") == "success":
                country = d.get("country") or ""
                city = d.get("city") or ""
                loc = (d.get("countryCode") or "").upper()
                disp = _format_geo_name(country, city, loc)
                return {"ip": d.get("query"), "country": disp, "country_code": loc}
    except Exception:
        pass

    # 2) 备用引擎：Cloudflare Trace (根据 CDN 数据中心代码映射中文城市)
    try:
        resp = requests.get(
            GEO_TRACE_URL,
            proxies=proxies,
            timeout=(timeout, timeout),
            headers={"User-Agent": "curl/7.88.1"},
        )
        if resp.status_code == 200:
            lines = dict(
                line.split("=", 1)
                for line in resp.text.splitlines()
                if "=" in line
            )
            ip = lines.get("ip")
            loc = (lines.get("loc") or "").upper()
            colo = (lines.get("colo") or "").upper()
            cname = COUNTRY_NAMES.get(loc, loc)
            city = COLO_CITIES.get(colo)
            disp = _format_geo_name(cname, city, loc)
            return {"ip": ip, "country": disp, "country_code": loc}
    except Exception:
        pass
    return None


def check_unlocks(port, timeout=2.5, country_code=None):
    """检测节点对常用海外服务（AI与主流流媒体）的解锁情况。"""
    proxies = _proxies(port)
    headers = {
        "User-Agent": UA,
        "Accept-Language": "en-US,en;q=0.9",
    }
    loc = (country_code or "").upper()
    results = {
        "chatgpt": False,
        "claude": False,
        "gemini": False,
        "youtube": False,
        "netflix": False,
        "disney": False,
        "tiktok": False,
        "bilibili": False,
    }

    def _test_chatgpt():
        if loc in ("HK", "CN", "RU", "IR", "KP"):
            return False
        try:
            resp = requests.get(
                "https://api.openai.com/compliance/cookie_requirements",
                proxies=proxies,
                headers=headers,
                timeout=(timeout, timeout),
            )
            return resp.status_code == 200
        except Exception:
            return False

    def _test_claude():
        if loc in ("HK", "CN", "RU", "BY"):
            return False
        try:
            resp = requests.get(
                "https://api.anthropic.com/v1/models",
                proxies=proxies,
                headers={
                    "User-Agent": UA,
                    "anthropic-version": "2023-06-01",
                },
                timeout=(timeout, timeout),
            )
            # 状态码 401 且提示缺少 api-key，证明通过了区域和 IP 风控检测，未被封锁；
            # 若区域不受支持或 IP 被封禁，Anthropic 会返回 403 (Country, region not supported)
            if resp.status_code == 401 and "api-key" in resp.text:
                return True
            return resp.status_code in (200, 301, 302)
        except Exception:
            return False

    def _test_gemini():
        # 若第三方 IP 库已知是明确不支持的地区，直接判定为未解锁
        if loc in ("HK", "CN", "RU", "BY", "IR", "KP", "CU", "SY"):
            return False
        try:
            resp = requests.get(
                "https://gemini.google.com",
                proxies=proxies,
                headers=headers,
                timeout=(timeout, timeout),
                allow_redirects=True,
            )
            if resp.status_code not in (200, 301, 302):
                return False
            url = getattr(resp, "url", "")
            if "unsupported" in url or "sorry" in url:
                return False

            text = resp.text
            # 1. 提取 Google 内部对当前请求 IP 识别的 ISO-3 国家代码（防“Google 送中”）
            # Google 在前端页面中内置了配置，例如 ,2,1,200,"JPN" 或 ,2,1,200,"HKG"
            m = re.search(r',2,1,200,"([A-Z]{3})"', text)
            if m:
                g_country = m.group(1)
                # 若被 Google 内部识别为不支持 Gemini 的国家/地区（常见如中国香港 HKG、中国大陆 CHN、俄罗斯 RUS 等）
                if g_country in {"CHN", "HKG", "MAC", "RUS", "BLR", "IRN", "PRK", "CUB", "SYR"}:
                    return False

            # 2. 检查页面中是否包含明确的地区受限/不支持提示
            text_lower = text.lower()
            unsupported_keywords = (
                "isn't currently supported",
                "is not currently supported",
                "isn't supported in your country",
                "is not supported in your country",
                "unsupported_country",
                "location_unsupported",
                "geographic_restriction",
                "目前不受支持",
                "目前不支持",
                "不支援",
            )
            return not any(kw in text_lower for kw in unsupported_keywords)
        except Exception:
            return False

    def _test_youtube():
        try:
            resp = requests.get(
                "https://www.youtube.com/premium",
                proxies=proxies,
                headers=headers,
                timeout=(timeout, timeout),
            )
            if resp.status_code not in (200, 301, 302):
                return False
            text = resp.text
            # 若包含 google.cn 或是明确的不支持提示，说明被 Google 送中或该区域未开通 Premium
            if "www.google.cn" in text:
                return False
            if "premium is not available in your country" in text.lower():
                return False
            # 确认页面包含订阅支持特征（如 ad-free 或 INNERTUBE 区域标识）
            return "ad-free" in text.lower() or "innertube_context_gl" in text.lower()
        except Exception:
            return False

    def _test_netflix():
        try:
            resp = requests.get(
                "https://www.netflix.com/title/80018499",
                proxies=proxies,
                headers=headers,
                timeout=(timeout, timeout),
                allow_redirects=True,
            )
            url = getattr(resp, "url", "")
            if resp.status_code == 200 and "title/80018499" in url:
                # 若包含 Netflix 的 "Oh no!" 错误页，则说明该 IP 被封禁或重定向
                return "oh no!" not in resp.text.lower()
            return False
        except Exception:
            return False

    def _test_disney():
        try:
            # 只看状态码与最终跳转地址，不读正文；stream=True 时必须关闭响应释放连接
            with requests.get(
                "https://www.disneyplus.com",
                proxies=proxies,
                headers=headers,
                timeout=(timeout, timeout),
                stream=True,
                allow_redirects=True,
            ) as resp:
                return resp.status_code in (200, 301, 302) and "unavailable" not in getattr(resp, "url", "")
        except Exception:
            return False

    def _test_tiktok():
        # TikTok 官方明确退出中国大陆和香港市场，对 HK 和 CN 完全封禁
        if loc in ("HK", "CN"):
            return False
        try:
            # 首页会先下发 WAF 人机验证页（"Please wait..."），判断不了解锁；
            # explore 页直接输出服务端识别的地区 "region":"JP"，以它为准
            resp = requests.get(
                "https://www.tiktok.com/explore",
                proxies=proxies,
                headers=headers,
                timeout=(timeout, timeout),
                allow_redirects=True,
            )
            if resp.status_code != 200:
                return False
            m = re.search(r'"region":"([A-Z]{2})"', resp.text)
            return bool(m) and m.group(1) not in ("CN", "HK")
        except Exception:
            return False

    def _test_bilibili():
        # 分别检测港澳限定与台湾限定番剧端点，任一解锁即为解锁
        endpoints = [
            # 港澳限定
            "https://api.bilibili.com/pgc/player/web/playurl?avid=473502608&cid=845838026&qn=0&type=&otype=json&ep_id=678506&fourk=1&fnver=0&fnval=16&module=bangumi",
            # 台湾限定
            "https://api.bilibili.com/pgc/player/web/playurl?avid=50762638&cid=100279344&qn=0&type=&otype=json&ep_id=268176&fourk=1&fnver=0&fnval=16&module=bangumi",
        ]
        for u in endpoints:
            try:
                resp = requests.get(u, proxies=proxies, headers=headers, timeout=(timeout, timeout))
                if resp.status_code == 200 and resp.json().get("code") == 0:
                    return True
            except Exception:
                continue
        return False

    tests = {
        "chatgpt": _test_chatgpt,
        "claude": _test_claude,
        "gemini": _test_gemini,
        "youtube": _test_youtube,
        "netflix": _test_netflix,
        "disney": _test_disney,
        "tiktok": _test_tiktok,
        "bilibili": _test_bilibili,
    }

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fn): key for key, fn in tests.items()}
        for fut in as_completed(futures):
            key = futures[fut]
            try:
                results[key] = bool(fut.result())
            except Exception:
                results[key] = False

    return results


def _proxies(port):
    if port is None:
        return None  # 直连（本机带宽基线测量用）
    p = f"socks5h://127.0.0.1:{port}"  # h: DNS 也走代理解析
    return {"http": p, "https": p}


def tcp_ping(host, port, samples=3, timeout=3, iface=None, cancel_event=None):
    """直连节点服务器的 TCP 握手延迟，返回毫秒列表（失败为 None）。

    iface: 绑到物理网卡，绕过本机 TUN 默认路由。
    cancel_event: 置位后立即返回已采集到的样本，不再等待剩余采样。
    """
    from .iface import bind_socket_to_iface, resolve_host

    targets = resolve_host(host, iface)
    if not targets:
        return [None] * samples
    port = int(port)
    results = []
    for _ in range(samples):
        if _cancelled(cancel_event):
            break
        hit = None
        timed_out = False
        for family, ip in targets:
            sock = socket.socket(family, socket.SOCK_STREAM)
            try:
                bind_socket_to_iface(sock, iface, target_ip=ip)
                sock.settimeout(timeout)
                t0 = time.monotonic()
                sock.connect((ip, port))
                hit = (time.monotonic() - t0) * 1000
                break
            except socket.timeout:
                timed_out = True
            except OSError:
                continue
            finally:
                with contextlib.suppress(OSError):
                    sock.close()
        results.append(hit)
        # 握手超时说明服务器不可达，剩余采样只会再白等一个完整超时
        if hit is None and timed_out:
            break
    return results


def http_latency(port, url=LATENCY_URL_DEFAULT, timeout=5, samples=2):
    """通过节点请求 204 页面，返回毫秒；失败抛出异常。

    用 Session 复用连接：首次请求仅用于建立连接（预热），
    后续请求测得的是纯往返延迟。
    """
    with requests.Session() as sess:
        sess.proxies.update(_proxies(port))
        # 预热：建立 TCP/TLS 连接
        sess.get(url, timeout=timeout)
        results = []
        for _ in range(samples):
            t0 = time.monotonic()
            resp = sess.get(url, timeout=timeout)
            resp.raise_for_status()
            results.append((time.monotonic() - t0) * 1000)
        return min(results)


def _cancelled(event):
    return event is not None and event.is_set()


def _read_for(port, url, seconds, headers, timeout, budget=None,
              cancel_event=None, limit=None):
    """从 url 拉取数据最多 seconds 秒，返回 (字节数, 墙钟耗时秒)。仅用于探测选源。"""
    got = 0
    wall0 = time.monotonic()
    try:
        with requests.get(url, proxies=_proxies(port), stream=True,
                          timeout=(timeout, timeout), headers=headers) as resp:
            if resp.status_code != 200:
                return 0, 0.001
            for chunk in resp.iter_content(chunk_size=65536):
                if _cancelled(cancel_event):
                    break
                n = len(chunk)
                if not n:
                    continue
                now = time.monotonic()
                got += n
                if budget is not None:
                    with budget["lock"]:
                        budget["left"] -= n
                        exhausted = budget["left"] <= 0
                    if exhausted:
                        break
                if limit is not None and got >= limit:
                    break
                if now - wall0 >= seconds:
                    break
    except requests.RequestException:
        pass
    # 用整段墙钟而不是首末 chunk 间隔：iter_content 可能一次 recv 再瞬间切块，
    # 间隔会被算成 0.001s，所有节点测出同一个天文数字。
    elapsed = max(time.monotonic() - wall0, 0.001)
    return got, elapsed


def _shutdown_pool(pool, cancel=False):
    """关闭线程池；取消场景不等待队列任务，尽量立刻返回。

    默认的 `with ThreadPoolExecutor(...)` 退出时会 shutdown(wait=True)，
    把取消后剩下的排队任务全部跑完，导致「停止」要等最坏 timeout 才生效。
    cancel_futures 需要 Python 3.9+，3.8 上退化为不等待（工作函数开头会自查取消标志）。
    """
    try:
        pool.shutdown(wait=not cancel, cancel_futures=cancel)
    except TypeError:  # Python 3.8 不支持 cancel_futures
        pool.shutdown(wait=False)


def _pick_speed_urls(port, headers, timeout, probe_seconds=1.5,
                     budget=None, cancel_event=None, sources=None):
    """并行探测测速源，返回按 Mbps 降序的可用源 [(url, bytes, dt, mbps), ...]。

    并发限制在 3，避免把弱节点的连接打满导致正式测速全失败。
    """
    sources = list(SPEED_URLS if sources is None else sources)
    if not sources:
        return []
    probe_limit = 768 * 1024
    found = []

    def probe(url):
        got, dt = _read_for(
            port, url, probe_seconds, headers, timeout,
            budget=budget, cancel_event=cancel_event, limit=probe_limit,
        )
        mbps = got * 8 / max(dt, 0.001) / 1e6 if got else 0.0
        return url, got, dt, mbps

    workers = min(3, len(sources))
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = [pool.submit(probe, url) for url in sources]
        for fut in as_completed(futures):
            if _cancelled(cancel_event):
                for f in futures:
                    f.cancel()
                break
            try:
                url, got, dt, mbps = fut.result()
            except Exception:  # noqa: BLE001 - 单个测速源探测失败不影响其他源
                continue
            if got >= 32 * 1024 and mbps > 0:
                found.append((url, got, dt, mbps))
    finally:
        _shutdown_pool(pool, cancel=_cancelled(cancel_event))
    found.sort(key=lambda r: r[3], reverse=True)
    return found


def _download_one(port, url, seconds, threads, headers, timeout,
                  budget, cancel_event, live_cb=None):
    """对单个测速 URL 并发拉取，支持实时速率回调 live_cb(mbps)，返回 (bytes, 传输耗时秒)。

    测速窗口从首字节开始计 seconds 秒，耗时按「首字节 → 停止」计算：
    经代理的 TCP/TLS 握手可能要 1s 以上，算进去会把高延迟节点的速度系统性测低，
    也会让实时速率（同样按首字节起算）和最终结果对不上。
    """
    stop = threading.Event()
    monitor_stop = threading.Event()
    xfer = {"bytes": 0, "t0": None, "t_stop": None, "lock": threading.Lock()}
    req_timeout = (max(timeout, 8), max(timeout, 12))
    # 兜底截止：首字节迟迟不来（握手极慢）时不能无限等下去
    hard_deadline = time.monotonic() + req_timeout[0] + seconds

    def add(n):
        now = time.monotonic()
        with xfer["lock"]:
            if xfer["t0"] is None:
                xfer["t0"] = now
            xfer["bytes"] += n
        return now

    def deadline():
        t0 = xfer["t0"]
        return hard_deadline if t0 is None else min(t0 + seconds, hard_deadline)

    def stop_at(now):
        with xfer["lock"]:
            if xfer["t_stop"] is None:
                xfer["t_stop"] = now
        stop.set()

    def fetch_once():
        """拉一次文件；返回 True 表示文件完整拉完、窗口还没到，可以再拉一次。"""
        with requests.get(url, proxies=_proxies(port), stream=True,
                          timeout=req_timeout, headers=headers) as resp:
            if resp.status_code != 200:
                return False
            for chunk in resp.iter_content(chunk_size=65536):
                if _cancelled(cancel_event) or stop.is_set():
                    return False
                n = len(chunk)
                now = add(n)
                if budget is not None:
                    with budget["lock"]:
                        budget["left"] -= n
                        exhausted = budget["left"] <= 0
                    if exhausted:
                        stop_at(now)
                        return False
                if now >= deadline():
                    stop_at(now)
                    return False
        return True

    def worker():
        # 快链路下单个测速文件可能在窗口内就拉完（如 Cloudflare 单次上限 <100MB），
        # 此时重新请求，否则后半段窗口空转、速度被低估
        with contextlib.suppress(requests.RequestException):
            while fetch_once() and not _cancelled(cancel_event) and not stop.is_set():
                pass

    # 实时速率采样监控（每 0.2 秒向 live_cb 报告首字节以来的平均速率）
    def monitor():
        while not monitor_stop.wait(0.2):
            if stop.is_set() or _cancelled(cancel_event):
                break
            with xfer["lock"]:
                b = xfer["bytes"]
                t0 = xfer["t0"]
            if t0 is None or b <= 0:
                continue
            elapsed = time.monotonic() - t0
            if elapsed > 0 and live_cb:
                with contextlib.suppress(Exception):  # 实时速率回调失败不影响测速
                    live_cb(round(b * 8 / elapsed / 1e6, 1))

    mon_thread = threading.Thread(target=monitor, daemon=True)
    mon_thread.start()

    pool = ThreadPoolExecutor(max_workers=threads)
    try:
        futures = [pool.submit(worker) for _ in range(threads)]
        for f in as_completed(futures):
            f.result()
    finally:
        _shutdown_pool(pool, cancel=_cancelled(cancel_event))

    monitor_stop.set()
    mon_thread.join(timeout=0.3)

    with xfer["lock"]:
        got = xfer["bytes"]
        t0 = xfer["t0"]
        t_stop = xfer["t_stop"]
    if not got or t0 is None:
        return 0, 0.0
    # 没有主动停止（数据中途断流、连接被断）时按全部结束时刻计，
    # 断流的等待时间算进耗时，避免只有前几百毫秒有数据时算出虚假高带宽
    t_end = t_stop if t_stop is not None else time.monotonic()
    return got, max(t_end - t0, 0.2)


def measure_download(port, seconds=5, threads=6, timeout=8, max_bytes=None,
                     cancel_event=None, live_cb=None, hint=None):
    """通过节点并发下载测速文件，按实际传输时间计算 Mbps，返回 (Mbps 或 None, 所用源)。

    hint：优先直接使用的测速源（同落地地区上一个节点选出的源）。能拉到数据就不再
    探测全部源——探测 8 个源每个节点要多花 3~8 秒和数 MB 流量；同地区节点用同一个源，
    结果之间也更可比。hint 失败时回退到完整探测。
    真正没流量时返回 None（界面显示 -），不再用 0.0 冒充测过。
    """
    headers = dict(SPEED_HEADERS)
    budget = None
    if max_bytes:
        budget = {"left": max_bytes, "lock": threading.Lock()}

    def budget_left():
        if budget is None:
            return True
        with budget["lock"]:
            return budget["left"] > 0

    def try_source(url):
        got, elapsed = _download_one(
            port, url, seconds, threads, headers, timeout, budget, cancel_event,
            live_cb=live_cb,
        )
        if got >= 64 * 1024 and elapsed >= 0.2:
            val = got * 8 / elapsed / 1e6
            if val >= 0.05:
                return val
        return None

    if hint:
        val = try_source(hint)
        if val is not None:
            return val, hint
        if _cancelled(cancel_event) or not budget_left():
            return None, None

    ranked = _pick_speed_urls(
        port, headers, timeout, budget=budget, cancel_event=cancel_event,
        sources=[u for u in SPEED_URLS if u != hint],
    )
    if _cancelled(cancel_event):
        return None, None

    # 探测只负责选源，不参与最终 Mbps（探测窗口太短，数字不可信）
    for url, _got, _dt, _mbps in ranked[:3]:
        if _cancelled(cancel_event) or not budget_left():
            break
        val = try_source(url)
        if val is not None:
            return val, url
    return None, None


def download_speed(port, seconds=5, threads=6, timeout=8,
                   max_bytes=None, cancel_event=None, live_cb=None):
    """measure_download 的简化版，只返回 Mbps（或 None）。"""
    val, _url = measure_download(
        port, seconds=seconds, threads=threads, timeout=timeout, max_bytes=max_bytes,
        cancel_event=cancel_event, live_cb=live_cb,
    )
    return val


def _direct_download(host, port, tls, path, seconds, threads, timeout, iface, cancel_event=None):
    """绕过 TUN/fake-ip 的直连下载：resolve_host 拿真实 IP + 绑定物理网卡。

    requests 直连在本机开着 Clash TUN 时会被 fake-ip 劫持（测出的是代理链路
    而非本机带宽），所以用裸 socket。返回 (bytes, 墙钟传输时长秒)。
    """
    from .iface import bind_socket_to_iface, resolve_host

    targets = resolve_host(host, iface)
    if not targets or _cancelled(cancel_event):
        return 0, 0.001
    xfer = {"bytes": 0, "lock": threading.Lock()}
    deadline = time.monotonic() + seconds
    req = (f"GET {path} HTTP/1.1\r\nHost: {host}\r\n"
           "User-Agent: Mozilla/5.0\r\nAccept-Encoding: identity\r\n"
           "Connection: close\r\n\r\n").encode()

    def add(n):
        with xfer["lock"]:
            xfer["bytes"] += n

    def worker(family, ip):
        if _cancelled(cancel_event):
            return
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            bind_socket_to_iface(sock, iface, target_ip=ip)
            sock.settimeout(timeout)
            sock.connect((ip, port))
            if tls:
                ctx = ssl.create_default_context()
                sock = ctx.wrap_socket(sock, server_hostname=host)
            sock.sendall(req)
            while time.monotonic() < deadline and not _cancelled(cancel_event):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                add(len(chunk))
        except OSError:
            pass
        finally:
            with contextlib.suppress(OSError):
                sock.close()

    t_start = time.monotonic()
    workers = []
    for i in range(threads):
        family, ip = targets[i % len(targets)]
        t = threading.Thread(target=worker, args=(family, ip), daemon=True)
        t.start()
        workers.append(t)
    for t in workers:
        t.join(timeout=seconds + timeout)
    with xfer["lock"]:
        got = xfer["bytes"]
    elapsed = max(time.monotonic() - t_start, 0.2)
    return got, elapsed


def local_bandwidth_mbps(seconds=3, threads=4, timeout=6, iface=None, cancel_event=None):
    """直连（不经节点、绕过 TUN/fake-ip）测本机下行带宽基线。

    所有节点速度都接近该值时，说明瓶颈在本机宽带/出口，而非节点差异。
    """
    if _cancelled(cancel_event):
        return None
    if iface is None:
        from .iface import detect_physical_iface
        iface = detect_physical_iface()
    best = 0.0
    for host, port, tls, path in (
        ("speed.cloudflare.com", 443, True, "/__down?bytes=90000000"),
        ("speedtest.tokyo2.linode.com", 80, False, "/100MB-tokyo.bin"),
    ):
        if _cancelled(cancel_event):
            break
        try:
            got, elapsed = _direct_download(
                host, port, tls, path, seconds, threads, timeout, iface, cancel_event=cancel_event)
        except Exception:  # noqa: BLE001
            continue
        if got >= 1024 * 1024 and elapsed >= 0.2:
            best = max(best, got * 8 / elapsed / 1e6)
            if best >= 50:
                break
    return round(best, 1) if best >= 1 else None


def test_node(node, runner=None, options=None):
    """测试单个节点，返回结果字典。线程安全：每个节点独占本地端口。"""
    opts = options or {}
    name = node["name"]
    result = {
        "name": name,
        "type": node.get("type", "?"),
        "server": node.get("server", "?"),
        "port": node.get("port", 0),
        "tcp_ms": None,
        "http_ms": None,
        "speed_mbps": None,
        "ip": None,
        "country": None,
        "country_code": None,
        "unlock": None,
        "ok": False,
        "error": None,
    }

    cancel = opts.get("cancel_event")
    if _cancelled(cancel):
        result["error"] = "已取消"
        return result

    # 1) TCP 直连延迟。UDP 协议（hysteria2 等）端口常无 TCP 监听，
    #    失败只记结果，不阻断后续经节点的 HTTP / 测速。
    samples = tcp_ping(
        node["server"], node["port"],
        samples=opts.get("samples", 3),
        timeout=opts.get("timeout", 5),
        iface=opts.get("iface"),
        cancel_event=cancel,
    )
    ok_samples = [s for s in samples if s is not None]
    if ok_samples:
        result["tcp_ms"] = round(median(ok_samples), 1)

    if _cancelled(cancel):
        result["error"] = "已取消"
        return result

    # 2) 经节点测试（需要 mihomo 端口）
    if runner:
        port = runner.ports.get(name)
        if not port:
            reason = (getattr(runner, "rejected", None) or {}).get(name)
            result["error"] = f"节点配置被 mihomo 拒绝: {reason}" if reason else "缺少本地监听端口"
            return result

        # 测活：请求 204 页面，失败即判定不可用（带一次抖动重试）
        timeout = opts.get("timeout", 5)
        try:
            result["http_ms"] = round(http_latency(port, timeout=timeout), 1)
            result["ok"] = True
        except Exception:
            # 网络抖动单次快速重试，避免好节点因瞬时丢包被误杀
            try:
                result["http_ms"] = round(http_latency(port, timeout=timeout), 1)
                result["ok"] = True
            except Exception as e:  # noqa: BLE001
                result["error"] = f"HTTP 测试失败: {e}"
                return result

        if _cancelled(cancel):
            return result

        # 落地 IP 与国家探测（轻量，消耗不到 1KB 流量）
        loc_code = None
        if opts.get("geo", True):
            geo = fetch_node_geo(port, timeout=min(timeout, 3.5))
            if geo:
                result["ip"] = geo.get("ip")
                result["country"] = geo.get("country")
                loc_code = geo.get("country_code") or None
                result["country_code"] = loc_code

        if _cancelled(cancel):
            return result

        # 流媒体与 AI 解锁探测（默认开启，可在 options 中传入 unlock=False 跳过）
        if opts.get("unlock", True):
            try:
                result["unlock"] = check_unlocks(port, timeout=min(timeout, 2.5), country_code=loc_code)
            except Exception:
                result["unlock"] = None

        # 下载测速不在这里做：由 test_all 的测速队列统一调度（控制同时测速的节点数）
    elif ok_samples:
        result["ok"] = True
        # 无 mihomo（--no-proxy-test）时不存在测速阶段，直接标记完成，
        # 否则上层「等待 _speed_tested」的分支永远不会命中，进度条停在 0%。
        result["_speed_tested"] = True
    else:
        result["error"] = "TCP 直连失败"

    return result


def _run_speed(port, opts, live_cb=None, hint=None):
    """按 options 执行一次测速，返回 (Mbps 或 None, 所用测速源)。"""
    max_bytes = opts.get("max_download_mb")
    if max_bytes:
        max_bytes = int(max_bytes * 1024 * 1024)
    val, url = measure_download(
        port,
        seconds=opts.get("speed_seconds", 5),
        threads=opts.get("speed_threads", 8),
        timeout=opts.get("timeout", 8),
        max_bytes=max_bytes,
        cancel_event=opts.get("cancel_event"),
        live_cb=live_cb,
        hint=hint,
    )
    if val is None:
        return None, None
    return round(val, 2), url


def median(values):
    values = sorted(values)
    n = len(values)
    mid = n // 2
    if n % 2:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2


def check_fake_ip(nodes):
    """检测节点域名是否被本地代理的 fake-ip 污染（198.18.0.0/15）。

    解析限 2 秒，避免 TUN/DNS 卡住导致整个测速页面无响应。
    """
    fake_net = ipaddress.ip_network("198.18.0.0/15")
    for server in list({n.get("server") for n in nodes})[:3]:
        try:
            ipaddress.ip_address(server)
            continue
        except ValueError:
            pass
        infos = _getaddrinfo_timeout(server, 2)
        if infos and ipaddress.ip_address(infos[0][4][0]) in fake_net:
            return True
    return False


def _getaddrinfo_timeout(host, timeout):
    """getaddrinfo 原生无超时，用守护线程 + join(timeout) 兜底。

    之前用 ThreadPoolExecutor + fut.result(timeout)：超时后 with 退出会
    调 shutdown(wait=True)，反而被卡住的解析线程拖死，超时形同虚设。
    """
    box = {}

    def run():
        with contextlib.suppress(OSError):
            box["result"] = socket.getaddrinfo(host, None, socket.AF_INET)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    return box.get("result")


def test_all(nodes, runner=None, options=None, concurrency=8,
             progress_cb=None, speed_concurrency=1, cancel_event=None,
             status_cb=None, on_speed_start=None, on_speed_tick=None):
    """并发测试所有节点：采用生产者-消费者流式流水线。

    探活线程池并发测试节点的延迟与可用性；
    节点一旦探活通过，立即放入测速工作队列，测速工作线程立刻对其执行下载测速并动态推送瞬时速度，
    实现边探活边流式测速，零等待卡顿。
    """
    import queue
    options = dict(options or {})
    if cancel_event is not None:
        options["cancel_event"] = cancel_event
    if options.get("iface") is None:
        if runner is not None and getattr(runner, "iface", None):
            options["iface"] = runner.iface
        else:
            from .iface import detect_physical_iface
            options["iface"] = detect_physical_iface()

    want_speed = bool(options.get("speed", True))
    phase1_opts = dict(options)
    phase1_opts["speed"] = False

    results = {}
    results_lock = threading.Lock()

    def _record(res):
        with results_lock:
            results[res["name"]] = res
        if progress_cb:
            try:
                progress_cb(res["name"], res)
            except (BrokenPipeError, ConnectionResetError, OSError):
                # 客户端断开（SSE/终端管道）：停止后续测试
                if cancel_event is not None:
                    cancel_event.set()
                return False
            except Exception as e:  # noqa: BLE001 - 回调自身缺陷不应中断整场测试
                # 以前这里会让异常逃进 future 并被 as_completed 静默吞掉，
                # 表现为进度条卡死却毫无线索，所以显式打印出来。
                print(f"[warn] 进度回调异常: {type(e).__name__}: {e}", file=sys.stderr)
        return True

    # 落地国家代码 -> 该地区首个测速成功节点选出的测速源，同地区后续节点直接复用
    source_by_cc = {}
    speed_q = queue.Queue()
    speed_workers_count = max(1, speed_concurrency) if want_speed else 0
    speed_threads = []

    def _speed_consumer():
        while True:
            try:
                node = speed_q.get(timeout=0.3)
            except queue.Empty:
                if _cancelled(cancel_event):
                    break
                continue
            if node is None:
                speed_q.task_done()
                break
            if _cancelled(cancel_event):
                speed_q.task_done()
                break

            name = node["name"]
            with results_lock:
                res = results.get(name)
            if res and res.get("ok"):
                port = runner.ports.get(name) if runner else None
                if port:
                    if on_speed_start:
                        with contextlib.suppress(Exception):  # 界面回调失败不影响测速
                            on_speed_start(name)

                    def _live_cb(mbps, _name=name):
                        if on_speed_tick and not _cancelled(cancel_event):
                            with contextlib.suppress(Exception):  # 界面回调失败不影响测速
                                on_speed_tick(_name, mbps)

                    cc = res.get("country_code")
                    with results_lock:
                        hint = source_by_cc.get(cc) if cc else None
                    try:
                        mbps, url = _run_speed(port, options, live_cb=_live_cb, hint=hint)
                        res["speed_mbps"] = mbps
                        if cc and url:
                            with results_lock:
                                source_by_cc.setdefault(cc, url)
                    except Exception as e:  # noqa: BLE001 - 测速失败只影响速度列
                        res["error"] = f"测速失败: {e}"
                    res["_speed_tested"] = True
                    _record(res)
            speed_q.task_done()

    # 启动测速消费者
    if want_speed:
        for _ in range(speed_workers_count):
            t = threading.Thread(target=_speed_consumer, daemon=True)
            t.start()
            speed_threads.append(t)

    if status_cb:
        status_cb(f"正在进行流式测速（共 {len(nodes)} 个节点）…")

    # 探活生产者：并发探活，可用节点立刻送入测速队列
    def _probe_worker(node):
        if _cancelled(cancel_event):
            return
        node_name = node["name"]
        try:
            res = test_node(node, runner, phase1_opts)
        except Exception as e:  # noqa: BLE001 - 单节点异常不应中断整场测试
            res = {"name": node_name, "ok": False, "error": str(e),
                   "tcp_ms": None, "http_ms": None, "speed_mbps": None,
                   "_speed_tested": True}
        _record(res)
        if res.get("ok") and want_speed and not _cancelled(cancel_event):
            speed_q.put(node)

    pool = ThreadPoolExecutor(max_workers=concurrency)
    try:
        futures = [pool.submit(_probe_worker, node) for node in nodes]
        for f in as_completed(futures):
            # 取出结果，避免异常被 as_completed 静默吞掉（历史 bug：进度条卡死无提示）
            try:
                f.result()
            except Exception as e:  # noqa: BLE001 - 已由 _probe_worker 兜底，这里只做提示
                print(f"[warn] 节点测试异常: {type(e).__name__}: {e}", file=sys.stderr)
            if _cancelled(cancel_event):
                for pending_f in futures:
                    pending_f.cancel()
                break
    finally:
        # 取消时不等待仍在跑的任务，否则「停止」要等最坏 timeout 才返回
        _shutdown_pool(pool, cancel=_cancelled(cancel_event))

    # 探活结束，向测速队列发送终止哨兵并等待测速全部完成
    if want_speed:
        for _ in range(speed_workers_count):
            speed_q.put(None)
        for t in speed_threads:
            t.join()

    return [results[n["name"]] for n in nodes if n["name"] in results]
