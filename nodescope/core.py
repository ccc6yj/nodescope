"""mihomo (Clash.Meta) 内核管理。

本地不实现 ss/vmess/vless 等协议，而是：
1. 把解析出的节点写成一个 mihomo 配置
2. 为每个节点启动一个绑定到该节点的本地 mixed(HTTP+SOCKS) 监听端口
3. 测速代码只需要通过 127.0.0.1:端口 发请求即可

这样任何协议都能用统一的 HTTP/SOCKS 客户端测试。
"""

import atexit
import contextlib
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import yaml

from .iface import detect_physical_iface

LOCAL_BIN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")
CANDIDATE_BINARIES = ("mihomo", "clash-meta", "clash", "mihomo.exe", "clash-meta.exe", "mihomo-linux-amd64")

WORKDIR_PREFIX = "nodescope-"
OWNER_FILE = "owner.json"
# 没有 owner.json 的工作目录（旧版本留下的）超过这个秒数才视为残留，避免误删刚创建的目录
_ORPHAN_DIR_MIN_AGE = 600

# 所有尚未清理的 MihomoRunner。进程被 SIGTERM/SIGHUP 结束、或测试线程来不及走到
# __exit__ 时，由 atexit 兜底统一关闭内核并删除工作目录（config.yaml 里有节点密码）
_LIVE_RUNNERS = set()
_LIVE_LOCK = threading.Lock()


def terminate_all():
    """关闭本进程启动的所有 mihomo 内核并删除其工作目录（可重复调用）。"""
    with _LIVE_LOCK:
        runners = list(_LIVE_RUNNERS)
    for r in runners:
        with contextlib.suppress(Exception):
            r._stop()


atexit.register(terminate_all)


def _pid_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    if sys.platform == "win32":
        # Windows 上 os.kill(pid, 0) 会直接结束目标进程，只能用 API 查询
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _kill_orphan_mihomo(pid, workdir):
    """结束上次残留的内核。PID 可能已被系统复用给无关进程，
    只有命令行里带着这个工作目录时才动手；Windows 上关闭终端会连带结束内核，这里不处理。"""
    if sys.platform == "win32" or not _pid_alive(pid):
        return
    try:
        cmd = subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "command="], text=True, stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return
    if workdir not in cmd:
        return
    with contextlib.suppress(OSError):
        os.kill(pid, 15)  # SIGTERM
    for _ in range(20):
        if not _pid_alive(pid):
            return
        time.sleep(0.1)
    with contextlib.suppress(OSError):
        os.kill(pid, 9)  # SIGKILL


def cleanup_stale(tmp_root=None):
    """清理之前被 kill -9、崩溃等方式中断后留下的工作目录和孤儿内核。

    只处理创建者进程已不存在的目录；同时运行的其他 nodescope 实例不受影响。
    返回清理掉的目录数。
    """
    tmp_root = tmp_root or tempfile.gettempdir()
    try:
        names = [n for n in os.listdir(tmp_root) if n.startswith(WORKDIR_PREFIX)]
    except OSError:
        return 0
    removed = 0
    for name in names:
        workdir = os.path.join(tmp_root, name)
        if not os.path.isdir(workdir):
            continue
        try:
            with open(os.path.join(workdir, OWNER_FILE), encoding="utf-8") as f:
                owner = json.load(f)
        except (OSError, ValueError):
            owner = None
        if owner is None:
            try:
                age = time.time() - os.path.getmtime(workdir)
            except OSError:
                continue
            if age < _ORPHAN_DIR_MIN_AGE:
                continue
        elif _pid_alive(owner.get("owner")):
            continue
        else:
            _kill_orphan_mihomo(owner.get("mihomo"), workdir)
        shutil.rmtree(workdir, ignore_errors=True)
        if not os.path.exists(workdir):
            removed += 1
    return removed

# 监听 bind 失败时 mihomo 不会退出（只在日志里报错），必须主动识别
_BIND_ERR_RE = "address already in use"
# 配置里任一节点非法时 mihomo 整体拒绝启动，日志形如
# "Parse config error: proxy 1: ss 1.2.3.4:8388 cipher: xxx initialize error: ..."
_BAD_PROXY_RE = re.compile(r"Parse config error: proxy (\d+): ([^\"\n]*)")
# 剔除坏节点后重启的次数上限（每次只能从日志里识别出一个坏节点）
MAX_BAD_PROXY_RETRIES = 30


def _port_free(port):
    """端口是否空闲（可被 bind）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def find_free_base_port(start, count):
    """从 start 起找到一段连续 count 个空闲端口，返回起始端口。

    mihomo 在监听端口被占用时不会退出，只在日志里报 bind 失败，
    旧版本据此会误判「已就绪」并把流量发到别的进程（测速结果全错）。
    所以启动前先确保整段端口可用。
    """
    count = max(1, count)
    port = start
    limit = start + 20000
    while port + count - 1 < limit:
        busy = next((port + i for i in range(count) if not _port_free(port + i)), None)
        if busy is None:
            return port
        # 包含 busy 的起点都不可能成功，直接跳到它后面
        port = busy + 1
    raise MihomoError(f"找不到连续 {count} 个空闲端口（从 {start} 开始）")


def _bw_mbps(val):
    """把 hy2 的 up/down 尽量解析成 Mbps；解析不了返回 None。"""
    if val in (None, "", 0):
        return 0
    s = str(val).strip().lower().replace(" ", "")
    for suffix, div in (("gbps", 0.001), ("mbps", 1), ("kbps", 1000), ("kbs", 1000),
                        ("mbs", 1), ("gbs", 0.001)):
        if s.endswith(suffix):
            try:
                return float(s[: -len(suffix)]) / div if div != 1 else float(s[: -len(suffix)])
            except ValueError:
                return None
    try:
        return float(s)  # 无单位时 hysteria 按 Mbps
    except ValueError:
        return None


def _tune_proxy(node):
    """测速前补全协议参数。

    Hysteria2 的 Brutal 拥塞控制依赖客户端申报上下行带宽，
    订阅里不写或写得很低时，内核会把吞吐压死，表现为「能打开网页但测速接近 0」。
    测速场景按容量测，给一个足够高的申报值。
    """
    p = dict(node)
    t = (p.get("type") or "").lower()
    if t == "hy2":
        p["type"] = "hysteria2"
        t = "hysteria2"
    if t == "hysteria2":
        p.setdefault("udp", True)
        down = _bw_mbps(p.get("down"))
        up = _bw_mbps(p.get("up"))
        if down is None or down < 100:
            p["down"] = "1000 Mbps"
        if up is None or up < 20:
            p["up"] = "100 Mbps"
    return p


def _is_ip(value):
    try:
        ipaddress.ip_address(str(value))
        return True
    except ValueError:
        return False


class MihomoError(Exception):
    pass


def find_binary():
    """按优先级查找可用的 mihomo 内核。"""
    env_bin = os.environ.get("NS_MIHOMO_BIN")
    if env_bin and os.path.isfile(env_bin) and os.access(env_bin, os.X_OK):
        return env_bin
    for name in CANDIDATE_BINARIES:
        path = shutil.which(name)
        if path:
            return path
    # start.sh 自动下载的内核放在项目根目录 bin/ 下，直接运行 run.py 时也能找到
    for name in ("mihomo", "mihomo.exe"):
        path = os.path.join(LOCAL_BIN_DIR, name)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


class MihomoRunner:
    """为一批节点启动 mihomo 实例，节点名 -> 本地端口 的映射。"""

    BASE_PORT = 21001

    def __init__(self, proxies, keep_dir=None, base_port=None, iface=None, auto_port=True):
        self.proxies = proxies
        self.binary = find_binary()
        self.keep_dir = keep_dir
        self.base_port = base_port or self.BASE_PORT
        self.iface = iface if iface is not None else detect_physical_iface()
        self.auto_port = auto_port  # True: 端口被占时自动后移（默认）
        self.workdir = None
        self.proc = None
        self._log_file = None
        self.ports = {}  # 节点名 -> 本地端口
        self.rejected = {}  # 被 mihomo 拒绝的节点名 -> 原因（已从配置中剔除）
        # 测试线程的 __exit__ 与 atexit 兜底可能同时调用 _stop
        self._stop_lock = threading.RLock()

    def __enter__(self):
        if not self.binary:
            raise MihomoError(
                "未找到 mihomo 内核。请安装：brew install mihomo\n"
                "或通过环境变量 NS_MIHOMO_BIN 指定内核路径"
            )
        cleanup_stale()
        try:
            self._start_skipping_bad_proxies()
        except Exception:
            self._stop()
            raise
        return self

    def _start_skipping_bad_proxies(self):
        """启动内核；遇到单个非法节点导致整体启动失败时，剔除该节点后重试。

        否则订阅里一个字段写错（如 mihomo 不认识的 cipher）的节点会拖垮整场测试。
        """
        for _ in range(MAX_BAD_PROXY_RETRIES + 1):
            try:
                self._start()
                return
            except MihomoError as e:
                bad = self._bad_proxy(str(e))
                if bad is None:
                    raise
                idx, reason = bad
                name = self.proxies[idx].get("name") or f"#{idx}"
                self.rejected[name] = reason
                self.proxies = self.proxies[:idx] + self.proxies[idx + 1:]
                self._stop()
                if not self.proxies:
                    raise MihomoError(f"所有节点都被 mihomo 拒绝，最后一个原因: {reason}") from e
        raise MihomoError(f"非法节点过多（已剔除 {len(self.rejected)} 个），放弃启动")

    def _bad_proxy(self, message):
        """从启动失败信息中识别出非法节点，返回 (下标, 原因) 或 None。"""
        m = _BAD_PROXY_RE.search(message)
        if not m:
            return None
        idx = int(m.group(1))
        if not 0 <= idx < len(self.proxies):
            return None
        return idx, m.group(2).strip()

    def __exit__(self, *exc):
        self._stop()

    def _build_config(self):
        proxies = [_tune_proxy(p) for p in self.proxies]
        self.ports = {}
        listeners = []
        for i, node in enumerate(proxies):
            self.ports[node["name"]] = self.base_port + i
            listeners.append(
                {
                    "name": f"nt{i}",
                    "type": "mixed",
                    "listen": "127.0.0.1",
                    "port": self.ports[node["name"]],
                    "proxy": node["name"],  # 关键：把该端口流量绑定到指定节点
                    "udp": True,
                }
            )
        cfg = {
            "mode": "global",  # 避免规则/GeoIP 加载
            "log-level": "warning",
            "ipv6": True,
            "proxies": proxies,
            "listeners": listeners,
        }
        # 绑定物理网卡 + 独立 DNS，避免本机 TUN 把出站/解析劫持进 fake-ip。
        # Windows 上探测到的是网卡 IP 而非名字，mihomo 的 interface-name 只认名字，此时不绑定
        if self.iface and not _is_ip(self.iface):
            cfg["interface-name"] = self.iface
            cfg["dns"] = {
                "enable": True,
                "ipv6": True,
                "use-system-hosts": False,
                "enhanced-mode": "redir-host",
                "nameserver": ["1.1.1.1", "8.8.8.8"],
            }
        return cfg

    def _start(self):
        # 先确保端口段空闲，避免残留实例占端口导致 mihomo 静默 bind 失败
        if self.auto_port:
            self.base_port = find_free_base_port(self.base_port, len(self.proxies))
        self.workdir = self.keep_dir or tempfile.mkdtemp(prefix=WORKDIR_PREFIX)
        os.makedirs(self.workdir, exist_ok=True)
        with _LIVE_LOCK:
            _LIVE_RUNNERS.add(self)
        config_path = os.path.join(self.workdir, "config.yaml")
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(self._build_config(), f, allow_unicode=True)

        # 把内核日志写到文件，避免 stderr=PIPE 缓冲区写满导致 mihomo 堵死。
        # 句柄需在整个内核生命周期内保持打开（不是 with 作用域），由 _stop() 负责关闭。
        log_path = os.path.join(self.workdir, "mihomo.log")
        self._log_file = open(log_path, "wb")  # noqa: SIM115
        self.proc = subprocess.Popen(
            [self.binary, "-f", config_path, "-d", self.workdir],
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
        )
        # 记录归属，供下次启动时识别残留（见 cleanup_stale）
        with open(os.path.join(self.workdir, OWNER_FILE), "w", encoding="utf-8") as f:
            json.dump({"owner": os.getpid(), "mihomo": self.proc.pid}, f)
        self._wait_ready()

    def _read_log(self):
        if self._log_file:
            with contextlib.suppress(OSError):
                self._log_file.flush()
        log_path = os.path.join(self.workdir, "mihomo.log") if self.workdir else None
        if log_path and os.path.isfile(log_path):
            with open(log_path, encoding="utf-8", errors="replace") as f:
                return f.read()
        return ""

    def _wait_ready(self, timeout=20):
        """并发轮询所有监听端口直到全部可连接。"""
        pending = set(self.ports.values())
        deadline = time.monotonic() + timeout

        def _check(port):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    return port
            except OSError:
                return None

        with ThreadPoolExecutor(max_workers=max(1, min(32, len(pending)))) as pool:
            while pending and time.monotonic() < deadline:
                exited = self.proc.poll() is not None
                log = self._read_log()
                if exited:
                    raise MihomoError(f"mihomo 启动失败:\n{log[-800:]}")
                # mihomo 监听 bind 失败时进程仍然存活，只能从日志识别，
                # 否则会把「端口被别的进程占着」误判为就绪。
                if _BIND_ERR_RE in log:
                    raise MihomoError(
                        f"mihomo 监听端口被占用（{sorted(pending)}）:\n{log[-800:]}"
                    )

                for ok_port in pool.map(_check, list(pending)):
                    if ok_port:
                        pending.discard(ok_port)

                if pending:
                    time.sleep(0.1)
        if pending:
            raise MihomoError(f"端口等待超时: {sorted(pending)}")

    def _stop(self):
        with self._stop_lock:
            self._stop_locked()
        with _LIVE_LOCK:
            _LIVE_RUNNERS.discard(self)

    def _stop_locked(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    self.proc.wait(timeout=2)
        self.proc = None
        if self._log_file:
            with contextlib.suppress(OSError):
                self._log_file.close()
            self._log_file = None
        # 临时目录清理（显式 keep 时保留供排查）
        if self.workdir and not self.keep_dir:
            shutil.rmtree(self.workdir, ignore_errors=True)
