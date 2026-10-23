"""Web 界面模式：本地 HTTP 服务 + SSE 实时推送测速结果。

- GET  /                         测速页面（单文件 HTML）
- POST /api/task                 提交测试参数（JSON），返回一次性 taskId
- GET  /api/stream?taskId=...    执行该任务，Server-Sent Events 实时返回结果

安全：只接受 Host 为本机回环地址、且（若带 Origin）同源的请求，
防止任意网页借浏览器触发测试，或通过 DNS rebinding 读取结果（含节点密码）。

事件协议:
    meta   {count, userinfo, warns, fake_ip}   解析完成
    status {message}                            阶段性状态
    result {节点结果字典}                        每个节点测完即推送
    done   {ok, total}                          全部结束
"""

import contextlib
import errno
import json
import re
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import report, tester
from .core import MihomoRunner, cleanup_stale, terminate_all
from .subscription import dedupe_names, format_userinfo, load_nodes

STATIC_DIR = Path(__file__).parent / "static"
ICON_DIR = STATIC_DIR / "icons"
# 解锁平台图标：文件名只允许小写字母、数字和连字符，杜绝路径穿越
ICON_PATH_RE = re.compile(r"^/icons/([a-z0-9-]+)\.svg$")
# 首页 CSP：脚本 / 样式都内联在 index.html 里（含 onclick），只放行同源与 data:
INDEX_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; "
             "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
             "connect-src 'self'; font-src 'self'; base-uri 'none'; form-action 'none'")

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")
MAX_TASK_BODY = 20 * 1024 * 1024  # 粘贴的订阅全文上限

# 同一时刻只允许一个测试会话占用 mihomo 端口段
TEST_LOCK = threading.Lock()

# 每个会话的 mihomo 端口段错开，避免残留实例冲突
_port_cursor = 21001
_port_lock = threading.Lock()


# 异步任务参数存储（避免前端通过 GET query 传递大文本引发 414）
TASKS = {}
TASKS_LOCK = threading.Lock()


def _clean_expired_tasks():
    now = time.time()
    with TASKS_LOCK:
        expired = [tid for tid, item in TASKS.items() if now - item.get("created_at", 0) > 600]
        for tid in expired:
            TASKS.pop(tid, None)


def _next_base_port(node_count):
    global _port_cursor
    with _port_lock:
        base = _port_cursor
        _port_cursor += max(1000, node_count + 50)
        if _port_cursor > 60000:
            _port_cursor = 21001
        return base


def _is_cancelled(cancel_event):
    """cancel_event 允许为 None（直接调用 run_test_stream 时）。"""
    return cancel_event is not None and cancel_event.is_set()


def _num_param(params, key, default, lo, hi, cast=float):
    """读取数值参数并夹到 [lo, hi]；缺省/空串用默认值，非数字报清晰的错误。"""
    raw = params.get(key)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        val = cast(float(raw))
    except (TypeError, ValueError):
        raise ValueError(f"参数 {key} 不是合法数字: {raw!r}") from None
    return min(max(val, lo), hi)


def run_test_stream(params, send_event, cancel_event=None):
    """执行一次完整测试，结果通过 send_event(event, data_dict) 实时推送。"""
    src = str(params.get("src", ""))
    no_speed = params.get("nospeed") == "1"
    # 数值参数来自浏览器，夹到合理范围：concurrency=0 会让线程池直接报错，过大则耗尽 fd
    speed_seconds = _num_param(params, "speed_seconds", 5, 1, 60)
    concurrency = _num_param(params, "concurrency", 8, 1, 64, int)
    speed_conc = _num_param(params, "speed_conc", 1, 1, 16, int)
    # 前端「不限」会传空串；空串/None/0 表示不限流量，而不是回退到 30MB。
    max_mb = _num_param(params, "maxmb", None, 0, 10240) or None
    want_unlock = params.get("unlock") != "0"

    disp_src = src if (len(src) < 60 and "\n" not in src) else (src.strip()[:40] + "... (文本内容)")
    send_event("status", {"message": f"读取订阅: {disp_src}"})
    # 输入来自浏览器：不允许当作本机文件路径读取
    nodes, warns, userinfo = load_nodes(
        src, on_status=lambda m: send_event("status", {"message": m}),
        allow_file=False,
    )
    if not nodes:
        send_event("error", {"message": "未解析到任何节点\n" + "\n".join(warns)})
        return
    dedupe_names(nodes)

    from .iface import detect_physical_iface
    from .tester import check_fake_ip

    fake_ip = check_fake_ip(nodes)
    iface = detect_physical_iface()

    send_event("meta", {
        "count": len(nodes),
        "nodes": [{"name": n["name"], "type": n.get("type", "?"),
                   "server": n.get("server", "?"), "port": n.get("port", 0)}
                  for n in nodes],
        "raw_nodes": nodes,
        "userinfo": format_userinfo(userinfo),
        "warns": warns,
        "fake_ip": fake_ip,
        "tun_iface": iface,
        "show_unlock": want_unlock,
    })

    # 本机带宽基线（直连、不经节点）：与 mihomo 启动并行测，
    # 但在节点测速开始前完成，避免抢带宽拉低节点速度；
    # 所有节点速度都接近该值时，说明瓶颈在本机宽带而非节点差异
    def _baseline():
        try:
            mbps = tester.local_bandwidth_mbps(iface=iface, cancel_event=cancel_event)
        except Exception:  # noqa: BLE001 - 基线仅用于提示，失败不影响测试
            return
        if mbps and not _is_cancelled(cancel_event):
            try:
                send_event("localbw", {"mbps": mbps})
            except (BrokenPipeError, ConnectionResetError, OSError):
                if cancel_event is not None:
                    cancel_event.set()

    options = {
        "speed": not no_speed,
        "speed_seconds": speed_seconds,
        "speed_threads": 8,  # 与 CLI --speed-threads 默认值一致
        "max_download_mb": max_mb,
        "samples": 3,
        "timeout": 5,
        "iface": iface,
        "unlock": want_unlock,
    }

    if not TEST_LOCK.acquire(timeout=0.05):
        send_event("status", {"message": "等待上一次测试结束…"})
        TEST_LOCK.acquire()
    ctx = None
    results = []
    bw_thread = None
    try:
        if _is_cancelled(cancel_event):
            return
        send_event("status", {"message": "启动 mihomo 内核..."})
        if not no_speed:
            bw_thread = threading.Thread(target=_baseline, daemon=True)
            bw_thread.start()
        # 「只测延迟」仍启动 mihomo，才能测经节点的 HTTP 延迟
        ctx = MihomoRunner(
            nodes, base_port=_next_base_port(len(nodes)), iface=iface,
        )
        runner = ctx.__enter__()
        if bw_thread is not None and bw_thread.is_alive():
            # 必须等基线测完再开始节点测速，否则两边抢带宽、互相拉低数值；
            # 基线自身有超时上限，这里只需要能响应取消
            send_event("status", {"message": "测本机带宽基线…"})
            while bw_thread.is_alive() and not _is_cancelled(cancel_event):
                bw_thread.join(timeout=0.2)

        def cb(name, res):
            send_event("result", res)

        def on_status(msg):
            send_event("status", {"message": msg})

        def on_speed_start(name):
            send_event("testing", {"name": name, "stage": "speed"})

        def on_speed_tick(name, mbps):
            send_event("speed_tick", {"name": name, "speed_mbps": mbps})

        results = tester.test_all(
            nodes, runner=runner, options=options,
            concurrency=concurrency, progress_cb=cb,
            speed_concurrency=speed_conc,
            cancel_event=cancel_event,
            status_cb=on_status,
            on_speed_start=on_speed_start,
            on_speed_tick=on_speed_tick,
        )
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)
        TEST_LOCK.release()

    if _is_cancelled(cancel_event):
        return

    ok_count = sum(1 for r in results if r.get("ok"))
    send_event("done", {"total": len(results), "ok": ok_count,
                        "results": report.sort_results(results)})


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 静默访问日志
        pass

    def _allowed_netlocs(self):
        port = self.server.server_address[1]
        return {f"{h}:{port}" for h in LOOPBACK_HOSTS}

    def _check_origin(self):
        """Host 必须是本机回环地址（防 DNS rebinding）；带 Origin 时必须同源（防跨站触发）。

        不通过时已回 403，返回 False。
        """
        allowed = self._allowed_netlocs()
        host = (self.headers.get("Host") or "").strip().lower()
        origin = (self.headers.get("Origin") or "").strip().lower()
        if host not in allowed:
            self._send_json(403, {"error": "forbidden host"})
            return False
        if origin and origin not in {f"http://{n}" for n in allowed}:
            self._send_json(403, {"error": "forbidden origin"})
            return False
        return True

    def do_POST(self):
        if not self._check_origin():
            return
        parsed = urlparse(self.path)
        if parsed.path == "/api/task":
            # 只收 JSON：跨站表单只能发 text/plain 等「简单请求」，据此再挡一层
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != "application/json":
                self._send_json(415, {"error": "Content-Type 必须是 application/json"})
                return
            try:
                content_len = int(self.headers.get("Content-Length", 0))
            except ValueError:
                content_len = -1
            if not 0 <= content_len <= MAX_TASK_BODY:
                self._send_json(413, {"error": "请求体过大或长度非法"})
                return
            raw_body = self.rfile.read(content_len).decode("utf-8", errors="replace")
            try:
                params = json.loads(raw_body)
            except ValueError:
                self._send_json(400, {"error": "Invalid JSON body"})
                return
            if not isinstance(params, dict):
                self._send_json(400, {"error": "Invalid JSON body"})
                return
            _clean_expired_tasks()
            task_id = uuid.uuid4().hex
            with TASKS_LOCK:
                TASKS[task_id] = {"params": params, "created_at": time.time()}
            self._send_json(200, {"taskId": task_id})
        else:
            self._send_json(404, {"error": "not found"})

    def do_GET(self):
        if not self._check_origin():
            return
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            self._serve_index()
        elif ICON_PATH_RE.match(parsed.path):
            self._serve_icon(ICON_PATH_RE.match(parsed.path).group(1))
        elif parsed.path == "/api/stream":
            # 只能执行先经 POST /api/task 提交的任务：GET 可被任意网页跨站发起，
            # 不能允许直接用 query 参数开测
            qs = parse_qs(parsed.query)
            task_id = qs.get("task_id", [None])[-1] or qs.get("taskId", [None])[-1]
            item = None
            if task_id:
                with TASKS_LOCK:
                    item = TASKS.pop(task_id, None)  # 一次性，防重放
            if not item:
                self._send_json(404, {"error": "任务不存在或已过期，请重新发起测试"})
                return
            self._serve_stream(item["params"])
        else:
            self._send_json(404, {"error": "not found"})

    def _serve_index(self):
        data = (STATIC_DIR / "index.html").read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        # 页面完全自包含：禁止加载任何外部资源（字体、图标、脚本），
        # 既避免国内 CDN 失败拖慢页面，也不向第三方暴露正在测速
        self.send_header("Content-Security-Policy", INDEX_CSP)
        self.end_headers()
        self.wfile.write(data)

    def _serve_icon(self, name):
        path = ICON_DIR / f"{name}.svg"
        if not path.is_file():
            self._send_json(404, {"error": "not found"})
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/svg+xml")
        self.send_header("Content-Length", str(len(data)))
        # 图标基本不变，让浏览器缓存一天，结果表反复渲染时不再请求
        self.send_header("Cache-Control", "public, max-age=86400")
        # 即使被当成文档直接打开，SVG 里也不能执行脚本或加载外部资源
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_stream(self, params):
        cancel_event = threading.Event()
        with _ACTIVE_LOCK:
            _ACTIVE_CANCELS.add(cancel_event)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        write_lock = threading.Lock()
        stop_hb = threading.Event()

        def _write(raw):
            with write_lock:
                self.wfile.write(raw)
                self.wfile.flush()

        def send_event(event, data):
            if cancel_event.is_set():
                raise BrokenPipeError("cancelled")
            payload = json.dumps(data, ensure_ascii=False)
            try:
                _write(f"event: {event}\ndata: {payload}\n\n".encode())
            except (BrokenPipeError, ConnectionResetError):
                cancel_event.set()
                raise

        def heartbeat():
            while not stop_hb.wait(2):
                if cancel_event.is_set():
                    return
                try:
                    _write(b": ping\n\n")
                except OSError:
                    cancel_event.set()
                    return

        # 禁止 EventSource 自动重连，避免中途再开一场测试
        try:
            _write(b"retry: 86400000\n\n")
        except OSError:
            return
        threading.Thread(target=heartbeat, daemon=True).start()

        try:
            run_test_stream(params, send_event, cancel_event)
        except (BrokenPipeError, ConnectionResetError):
            cancel_event.set()
        except Exception as e:  # noqa: BLE001 - 任何异常都推给前端
            with contextlib.suppress(OSError):
                send_event("error", {"message": str(e)})
        finally:
            stop_hb.set()
            cancel_event.set()
            with _ACTIVE_LOCK:
                _ACTIVE_CANCELS.discard(cancel_event)


# 进行中测试会话的取消信号，服务停止时统一置位，让测试线程尽快收尾
_ACTIVE_CANCELS = set()
_ACTIVE_LOCK = threading.Lock()


def shutdown_all():
    """取消所有进行中的测试并关闭内核：服务退出时不必等整场测试跑完。"""
    with _ACTIVE_LOCK:
        events = list(_ACTIVE_CANCELS)
    for ev in events:
        ev.set()
    terminate_all()


class _Server(ThreadingHTTPServer):
    allow_reuse_address = True


def _listener_hint(port):
    """查占用该端口的进程，失败则返回 None。"""
    try:
        out = subprocess.check_output(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
            text=True, stderr=subprocess.DEVNULL,
        )
        rows = [ln for ln in out.splitlines()[1:] if ln.strip()]
        if not rows:
            return None
        parts = rows[0].split()
        cmd, pid = parts[0], parts[1]
        return f"{cmd} (pid {pid})"
    except (OSError, subprocess.CalledProcessError, IndexError):
        return None


def _bind_error(port):
    hint = _listener_hint(port)
    occupied = f"，当前占用: {hint}" if hint else ""
    return (
        f"端口 {port} 已被占用{occupied}\n"
        f"可直接打开已在运行的页面: http://127.0.0.1:{port}\n"
        f"或换端口启动: python3 run.py --serve --port {port + 1}"
    )


def serve(port=8765, auto_port=True):
    host = "127.0.0.1"
    last_err = None
    server = None
    chosen = port
    attempts = range(port, port + 20) if auto_port else (port,)
    for candidate in attempts:
        try:
            server = _Server((host, candidate), Handler)
            chosen = candidate
            break
        except OSError as e:
            last_err = e
            if e.errno != errno.EADDRINUSE:
                raise
    if server is None:
        raise OSError(_bind_error(port)) from last_err
    # 上次被 kill -9 / 崩溃中断时留下的内核和工作目录，启动时顺手清掉
    cleanup_stale()

    print(f"节点测速 Web 界面已启动: http://{host}:{chosen}")
    if chosen != port:
        hint = _listener_hint(port)
        extra = f"（{hint}）" if hint else ""
        print(f"端口 {port} 已被占用{extra}，已自动改用 {chosen}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        # 终端已关闭（SIGHUP）时 print 可能抛 OSError，不能挡住后面的清理
        with contextlib.suppress(OSError):
            print("\n已停止")
    finally:
        shutdown_all()
        server.server_close()
