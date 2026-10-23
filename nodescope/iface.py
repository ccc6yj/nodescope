"""物理网卡探测与绑定，用于绕过本机 TUN（Clash / QuantumultX 等）。"""

import ipaddress
import os
import re
import socket
import struct
import subprocess
import sys
import threading
import time

FAKE_NET = ipaddress.ip_network("198.18.0.0/15")
SKIP_IFACE = re.compile(
    r"^(lo|utun|tun|wg|awdl|llw|bridge|stf|gif|ipsec|ppp|zt|"
    r"tailscale|ap\d|vmnet|veth|docker|br-|virbr|cni|flannel|kube|"
    r"clash|meta|Meta)",
    re.I,
)

# Darwin netinet/in.h
_IP_BOUND_IF = 25
_IPV6_BOUND_IF = 125

_DEVNULL = subprocess.DEVNULL

# 网卡探测每次都要起好几个子进程（ifconfig/route/ipconfig），而它在每次测试、
# 每个 socket 绑定（Linux 非 root 时 SO_BINDTODEVICE 失败会回退到查网卡 IP）时都会用到。
# TTL 取短值：Web 服务常驻期间可能切换 Wi-Fi / 有线。
_IFACE_TTL = 30
# 节点常共用同一个服务器域名；DNS 结果缓存 5 分钟
_DNS_TTL = 300
_DNS_TIMEOUT = 2
_cache = {}
_cache_lock = threading.Lock()


def _cached(key, ttl, compute, keep=None):
    """带 TTL 的简单缓存；keep(value) 为假时不缓存（如 DNS 解析失败，下次重试）。"""
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = compute()
    if keep is None or keep(value):
        with _cache_lock:
            _cache[key] = (time.monotonic(), value)
    return value


def clear_cache():
    """清空网卡与 DNS 缓存（测试用）。"""
    with _cache_lock:
        _cache.clear()


def detect_physical_iface():
    """选出一块能出网的物理网卡（非 utun/tun、非 fake-ip）。

    可用环境变量 NS_IFACE 强制指定。
    """
    env = (os.environ.get("NS_IFACE") or "").strip()
    if env:
        return env
    return _cached(("iface",), _IFACE_TTL, _detect_physical_iface)


def _detect_physical_iface():
    names = _list_ifaces()
    default = _default_route_iface()
    if default and _is_physical(default):
        return default
    for name in ("en0", "en1", "eth0", "eth1", "wlan0", "wlp0s20f3"):
        if name in names and _is_physical(name):
            return name
    for name in names:
        if _is_physical(name):
            return name
    return None


def bind_socket_to_iface(sock, iface, target_ip=None):
    """把 socket 绑到指定网卡，使数据包不走 TUN 默认路由。若目标为回环地址则跳过。"""
    if not iface:
        return
    if target_ip:
        try:
            if ipaddress.ip_address(str(target_ip).strip("[]")).is_loopback:
                return
        except ValueError:
            pass
    if sys.platform == "darwin":
        try:
            ifindex = socket.if_nametoindex(iface)
        except OSError:
            ifindex = 0
        if ifindex:
            if sock.family == socket.AF_INET6:
                sock.setsockopt(socket.IPPROTO_IPV6, _IPV6_BOUND_IF, ifindex)
            else:
                sock.setsockopt(socket.IPPROTO_IP, _IP_BOUND_IF, ifindex)
            return
    if hasattr(socket, "SO_BINDTODEVICE"):
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE,
                            iface.encode() + b"\0")
            return
        except OSError:
            pass
    ip = iface_ipv4(iface)
    if ip and sock.family == socket.AF_INET:
        sock.bind((ip, 0))


def resolve_host(host, iface=None):
    """解析主机名，返回 [(family, ip), ...]，过滤 fake-ip。

    指定 iface 时通过该网卡向 1.1.1.1/8.8.8.8 发 DNS，避开 TUN 劫持的系统 DNS。
    结果按 (host, iface) 缓存 _DNS_TTL 秒。
    """
    host = (host or "").strip()
    if not host:
        return []
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
        if ip in FAKE_NET:
            return []
        fam = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
        return [(fam, str(ip))]
    except ValueError:
        pass
    return list(_cached(("dns", host, iface), _DNS_TTL, lambda: _resolve_uncached(host, iface), keep=bool))


def _resolve_uncached(host, iface):
    found = []
    if iface:
        for server in ("1.1.1.1", "8.8.8.8"):
            for qtype, fam in ((1, socket.AF_INET), (28, socket.AF_INET6)):
                try:
                    for ip in _dns_query(host, iface, server, qtype=qtype, timeout=_DNS_TIMEOUT):
                        addr = ipaddress.ip_address(ip)
                        if addr in FAKE_NET or addr.is_loopback:
                            continue
                        found.append((fam, ip))
                except socket.timeout:
                    # 该 DNS 不可达（如 UDP 53 被屏蔽），不必再等它的 AAAA 超时
                    break
                except ValueError:
                    # 非法域名（idna 编码失败 / 标签超长）：自建查询做不了，交给系统解析兜底
                    break
                except OSError:
                    continue
                if found:
                    break  # 有 A 记录就够，不必再等 AAAA 超时
            if found:
                break
    if not found:
        try:
            for info in socket.getaddrinfo(host, None):
                ip = info[4][0]
                try:
                    addr = ipaddress.ip_address(ip)
                except ValueError:
                    continue
                if addr in FAKE_NET or addr.is_loopback:
                    continue
                found.append((info[0], ip))
        except OSError:
            pass

    seen, out = set(), []
    for item in found:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def iface_ipv4(name):
    if not name:
        return None
    return _cached(("ipv4", name), _IFACE_TTL, lambda: _iface_ipv4_uncached(name))


def _iface_ipv4_uncached(name):
    if sys.platform == "darwin":
        try:
            ip = subprocess.check_output(
                ["ipconfig", "getifaddr", name],
                text=True, stderr=_DEVNULL,
            ).strip()
            if ip:
                return ip
        except (OSError, subprocess.CalledProcessError):
            pass
        try:
            out = subprocess.check_output(
                ["ifconfig", name], text=True, stderr=_DEVNULL,
            )
            m = re.search(r"\binet (\d+\.\d+\.\d+\.\d+)", out)
            if m:
                return m.group(1)
        except (OSError, subprocess.CalledProcessError):
            pass
        return None

    if sys.platform == "win32":
        try:
            ipaddress.ip_address(name)
            return name
        except ValueError:
            pass
        try:
            out = subprocess.check_output("ipconfig", text=True, stderr=_DEVNULL)
            m = re.search(r"IPv4[^:]*:\s*(\d+\.\d+\.\d+\.\d+)", out)
            if m:
                return m.group(1)
        except (OSError, subprocess.CalledProcessError):
            pass
        return None

    try:
        out = subprocess.check_output(
            ["ip", "-4", "-o", "addr", "show", "dev", name],
            text=True, stderr=_DEVNULL,
        )
        m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", out)
        if m:
            return m.group(1)
    except (OSError, subprocess.CalledProcessError):
        pass
    return None


def _list_ifaces():
    if sys.platform == "darwin":
        try:
            return subprocess.check_output(
                ["ifconfig", "-l"], text=True, stderr=_DEVNULL,
            ).split()
        except (OSError, subprocess.CalledProcessError):
            return []
    sysfs = "/sys/class/net"
    if os.path.isdir(sysfs):
        try:
            return os.listdir(sysfs)
        except OSError:
            return []
    return []


def _default_route_iface():
    try:
        if sys.platform == "darwin":
            out = subprocess.check_output(
                ["route", "-n", "get", "default"],
                text=True, stderr=_DEVNULL,
            )
            for line in out.splitlines():
                if "interface:" in line:
                    return line.split(":", 1)[-1].strip() or None
            return None

        if sys.platform == "win32":
            out = subprocess.check_output("route print 0.0.0.0", text=True, stderr=_DEVNULL)
            m = re.search(r"0\.0\.0\.0\s+0\.0\.0\.0\s+\S+\s+(\d+\.\d+\.\d+\.\d+)", out)
            if m:
                return m.group(1)
            return None

        out = subprocess.check_output(
            ["ip", "route", "show", "default"],
            text=True, stderr=_DEVNULL,
        )
        m = re.search(r"\bdev\s+(\S+)", out)
        return m.group(1) if m else None
    except (OSError, subprocess.CalledProcessError):
        return None


def _is_physical(name):
    if not name or SKIP_IFACE.match(name):
        return False
    ip = iface_ipv4(name)
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (addr.is_loopback or addr.is_link_local or addr in FAKE_NET)


def _encode_qname(host):
    out = bytearray()
    for label in host.strip().rstrip(".").split("."):
        raw = label.encode("idna")
        if not raw or len(raw) > 63:
            raise ValueError(f"非法域名: {host}")
        out.append(len(raw))
        out.extend(raw)
    out.append(0)
    return bytes(out)


def _skip_name(data, pos):
    while pos < len(data):
        length = data[pos]
        if length == 0:
            return pos + 1
        if length & 0xC0 == 0xC0:
            return pos + 2
        pos += 1 + (length & 0x3F)
    return pos


def _dns_query(host, iface, server, qtype=1, timeout=3):
    """简单 DNS 查询，qtype=1 A / 28 AAAA。"""
    qname = _encode_qname(host)
    tid = os.urandom(2)
    packet = tid + b"\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
    packet += qname + struct.pack("!HH", qtype, 1)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        bind_socket_to_iface(sock, iface)
        sock.settimeout(timeout)
        sock.sendto(packet, (server, 53))
        data, _ = sock.recvfrom(2048)
    finally:
        sock.close()
    if len(data) < 12 or data[:2] != tid:
        return []
    flags = struct.unpack("!H", data[2:4])[0]
    if flags & 0xF:
        return []
    qdcount, ancount = struct.unpack("!HH", data[4:8])
    pos = 12
    for _ in range(qdcount):
        pos = _skip_name(data, pos) + 4
    ips = []
    for _ in range(ancount):
        pos = _skip_name(data, pos)
        if pos + 10 > len(data):
            break
        typ, cls, _ttl, rdlen = struct.unpack("!HHIH", data[pos:pos + 10])
        pos += 10
        rdata = data[pos:pos + rdlen]
        pos += rdlen
        if cls != 1:
            continue
        if typ == 1 and rdlen == 4:
            ips.append(socket.inet_ntoa(rdata))
        elif typ == 28 and rdlen == 16:
            ips.append(socket.inet_ntop(socket.AF_INET6, rdata))
    return ips
