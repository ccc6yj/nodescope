"""订阅解析模块。

支持三种来源：
1. 订阅 URL（http/https）
2. 本地文件路径
3. 直接粘贴的文本（通过 stdin 或文件）

内容格式自动识别：
- Clash YAML（含 proxies: 字段，节点原样透传给 mihomo）
- Base64 编码的节点 URI 列表（机场常见格式）
- 明文节点 URI 列表（ss:// vmess:// vless:// trojan:// hysteria2://）
"""

import base64
import binascii
import contextlib
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit

import requests
import yaml

# 用 mihomo UA 请求：部分机场（SSPanel 系）会按客户端下发协议，
# mihomo UA 能拿到 anytls / hysteria2 等新协议。
# 另用 Clash 系 UA 再拉一次：同一条线路会以下发 trojan 回退节点，
# 名字不带 AT/HY2 后缀，Stash 等客户端列表里看起来会多几条。
SUB_UA = "mihomo/1.19.20"
FALLBACK_UAS = (
    "ClashforWindows/0.20.39",
    "clash.meta/v1.19.0",
)

SUPPORTED_SCHEMES = ("ss", "vmess", "vless", "trojan", "hysteria2", "hy2", "anytls", "tuic", "tuic5")


class UnsupportedNode(Exception):
    """节点包含暂不支持的特性（如 ss plugin）。"""


def _b64decode(text):
    """宽容的 base64 解码：剥离空白与换行，自动补齐 padding，兼容 urlsafe 字符与 UTF-8 BOM。"""
    text = re.sub(r"\s+", "", str(text)).lstrip("\ufeff").replace("-", "+").replace("_", "/")
    pad = "=" * (-len(text) % 4)
    return base64.b64decode(text + pad)


def load_text(src, timeout=20, retries=3, ua=None, allow_file=True):
    """从 URL、本地文件或直接粘贴的文本读取订阅原文（网络源带递增间隔自动重试）。

    返回 (text, userinfo)。本地文件或直接文本的 userinfo 为空字符串。
    allow_file=False 时不把输入当成本地路径（Web 模式：输入来自浏览器，不能借此读本机文件）。
    """
    src_str = str(src).strip()
    if "\n" not in src_str and re.match(r"^https?://", src_str, re.I):
        last_err = None
        headers = {"User-Agent": ua or SUB_UA, "Accept": "*/*"}
        for attempt in range(retries):
            try:
                resp = requests.get(src_str, timeout=timeout, headers=headers)
                resp.raise_for_status()
                userinfo = resp.headers.get("subscription-userinfo", "") or ""
                return resp.text, userinfo
            except requests.RequestException as e:
                last_err = e
                if attempt < retries - 1:
                    time.sleep(3 * (attempt + 1))  # 3s / 6s 递增等待
        raise last_err

    # 检查是否为本地存在的文件路径
    # 如果内容很长或包含换行符，说明是直接粘贴的订阅内容而不是文件路径
    is_file = False
    if allow_file and "\n" not in src_str and len(src_str) < 4096:
        try:
            is_file = os.path.isfile(src_str)
        except OSError:
            is_file = False

    if is_file:
        with open(src_str, encoding="utf-8") as f:
            return f.read(), ""

    # 既非 URL 也非文件，作为直接粘贴的订阅文本（Base64、YAML 或明文 URI）
    return src_str, ""


def _node_key(node):
    return (
        node.get("name"),
        node.get("type"),
        str(node.get("server") or ""),
        str(node.get("port") or ""),
    )


# 机场常塞进 proxies 的说明项，不是可测线路
_INFO_NODE_RE = re.compile(
    r"有效期|到期时间|套餐到期|流量信息|剩余流量|官网地址|刷新订阅|请更新|^剩余[\s:：]?"
)


def _is_info_node(node):
    name = node.get("name") or ""
    if "流量倍率" in name:
        return False
    return bool(_INFO_NODE_RE.search(name))


def _drop_info_nodes(nodes, warns):
    kept, dropped = [], []
    for node in nodes:
        if _is_info_node(node):
            dropped.append(node.get("name") or "?")
        else:
            kept.append(node)
    if dropped:
        warns.append("已忽略说明节点: " + "、".join(dropped))
    return kept, warns


def load_nodes(src, timeout=15, on_status=None, allow_file=True):
    """读取并解析订阅。支持单订阅 URL/文件，也支持多行 URL 批量合并拉取。

    allow_file=False：不读取本地文件路径（见 load_text）。
    """
    src_clean = str(src).strip()
    # 检测多订阅合并（多行输入，且每一行都是 http URL）
    # 只看「含多个 http 行」会把粘贴进来的 Clash 配置（rules 里带 URL）误判成多订阅。
    lines = [line.strip() for line in src_clean.splitlines() if line.strip()]
    url_lines = [line for line in lines if re.match(r"^https?://", line, re.I)]
    if len(url_lines) > 1 and len(url_lines) == len(lines):
        total = len(url_lines)
        if on_status:
            on_status(f"正在并行拉取 {total} 个订阅…")
        # 并行拉取（单个订阅失败重试时最多要等几十秒，串行会成倍放大），结果按输入顺序合并
        outcomes = [None] * total
        with ThreadPoolExecutor(max_workers=min(8, total)) as pool:
            futs = {pool.submit(load_nodes, u, timeout): i for i, u in enumerate(url_lines)}
            for done, fut in enumerate(as_completed(futs), 1):
                i = futs[fut]
                try:
                    outcomes[i] = fut.result()
                except Exception as e:  # noqa: BLE001 - 单个订阅失败不影响其他订阅
                    outcomes[i] = e
                if on_status:
                    on_status(f"订阅拉取进度 {done}/{total}")
        all_nodes, all_warns, all_userinfo = [], [], []
        seen = set()
        dup = 0
        for u, out in zip(url_lines, outcomes):
            if isinstance(out, Exception):
                all_warns.append(f"订阅拉取失败 ({u[:30]}): {out}")
                continue
            sub_nodes, sub_warns, sub_uinfo = out
            for node in sub_nodes:
                key = _node_key(node)
                if key in seen:
                    dup += 1
                    continue
                seen.add(key)
                all_nodes.append(node)
            all_warns.extend(sub_warns)
            if sub_uinfo:
                all_userinfo.append(sub_uinfo)
        if dup:
            all_warns.append(f"多个订阅间有 {dup} 个重复节点，已去重")
        return all_nodes, all_warns, " | ".join(all_userinfo)

    if on_status:
        on_status("读取订阅…")
    text, userinfo = load_text(src, timeout=timeout, retries=2, allow_file=allow_file)
    nodes, warns = parse(text)
    if not re.match(r"^https?://", src, re.I):
        nodes, warns = _drop_info_nodes(nodes, warns)
        return nodes, warns, userinfo

    def fetch_ua(ua):
        extra_text, _ = load_text(src, timeout=min(timeout, 8), retries=1, ua=ua)
        return parse(extra_text)

    if on_status:
        on_status("合并其他客户端节点…")
    extras = []
    with ThreadPoolExecutor(max_workers=len(FALLBACK_UAS)) as pool:
        futs = [pool.submit(fetch_ua, ua) for ua in FALLBACK_UAS]
        try:
            for fut in as_completed(futs, timeout=10):
                try:
                    extras.append(fut.result())
                except Exception:
                    continue
        except FuturesTimeout:
            for fut in futs:
                fut.cancel()

    seen = {_node_key(n) for n in nodes}
    added = 0
    for extra_nodes, _extra_warns in extras:
        for node in extra_nodes:
            key = _node_key(node)
            if key in seen:
                continue
            seen.add(key)
            nodes.append(node)
            added += 1
    if added:
        warns.append(f"已从其他客户端格式补入 {added} 个节点（机场按 UA 下发不同协议）")
    nodes, warns = _drop_info_nodes(nodes, warns)
    return nodes, warns, userinfo


def _plain_float(val):
    """只接受纯数字；'14.20 GB' 这类带单位的返回 None。"""
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def format_userinfo(info):
    """把 subscription-userinfo 头转成友好摘要。

    标准字段为 upload / download / total / expire；
    少数面板另给 used（可能是纯字节或 '14.20 GB'），无法解析则用 upload+download。
    """
    if not info:
        return None
    parts = dict(
        p.strip().split("=", 1)
        for p in info.split(";")
        if "=" in p
    )
    upload = _plain_float(parts.get("upload")) or 0
    download = _plain_float(parts.get("download")) or 0
    used = _plain_float(parts.get("used"))
    if used is None:
        used = upload + download
    total = _plain_float(parts.get("total")) or 0
    expire = parts.get("expire")
    msg = f"已用 {used / 1024**3:.2f} GB / {total / 1024**3:.1f} GB"
    if expire:
        from datetime import datetime
        try:
            ts = float(expire)
            # 部分面板下发毫秒时间戳
            if ts > 1e11:
                ts /= 1000
            msg += f"，到期 {datetime.fromtimestamp(ts).date()}"
        except (ValueError, OSError, OverflowError):
            pass
    return msg


# 顶层 proxies / proxy-providers 键（行首，可带 BOM 或引号）。只认顶层键而不是子串，
# 否则节点 path/名字里含 "proxies" 的 URI 列表会被当成 YAML，语法错后整份订阅解析失败
_CLASH_KEY_RE = re.compile(r"""^\ufeff?["']?(?:proxies|proxy-providers)["']?\s*:""", re.M)


def _parse_clash_yaml(text):
    """尝试按 Clash YAML 解析，返回 (nodes, warns)。

    warns 里的内容用于区分「压根不是 YAML」「YAML 语法错」「有 proxies 但没有合法节点」
    三种情况，否则用户只会看到「未解析到任何节点」而毫无线索。
    """
    if not (_CLASH_KEY_RE.search(text) or text.lstrip().startswith("{")):
        return [], []
    try:
        cfg = yaml.safe_load(text)
    except yaml.YAMLError as e:
        # 语法错就明确告知，别再往下当 URI 列表静默试一遍
        detail = str(e).splitlines()[0] if str(e) else e.__class__.__name__
        return [], [f"Clash 配置 YAML 语法错误: {detail}"]
    if not isinstance(cfg, dict):
        return [], []
    raw = cfg.get("proxies")
    if not raw:
        if "proxy-providers" in cfg:
            return [], ["Clash 配置仅包含 proxy-providers，未包含静态 proxies 节点（暂不支持外部 provider 自动下载）"]
        if "proxies" in cfg:
            return [], ["Clash 配置里 proxies 列表为空"]
        if any(k in cfg for k in ("rules", "proxy-groups", "rule-providers", "dns", "geox-url")):
            return [], ["Clash 配置里没有 proxies 节点列表"]
        return [], []
    if not isinstance(raw, list):
        return [], ["Clash 配置的 proxies 字段不是列表"]
    nodes, skipped = [], 0
    for p in raw:
        if isinstance(p, dict) and p.get("name") and p.get("type"):
            nodes.append(dict(p))
        else:
            skipped += 1
    warns = []
    if not nodes:
        warns.append(f"proxies 里 {skipped} 条记录缺少 name/type，无法识别为节点")
    elif skipped:
        warns.append(f"已跳过 {skipped} 条缺少 name/type 的 proxies 记录")
    return nodes, warns


def parse(text):
    """解析订阅文本，返回 clash proxy 字典列表和警告信息列表。"""
    text = text.strip()
    if not text:
        return [], ["订阅内容为空"]

    # 1) 尝试 Clash YAML（dns/rules 可能很长，全文按顶层键检测）
    nodes, warns = _parse_clash_yaml(text)
    if nodes:
        return nodes, warns
    if warns:
        # 明确识别为 Clash 配置且产生诊断信息（语法错误、缺少/空 proxies 等），不再回退为普通 URI 列表
        return [], warns

    # 1.5) Base64 包裹的 Clash YAML（部分机场把 yaml 整体 base64 下发）
    with contextlib.suppress(Exception):
        decoded = _b64decode(text).decode("utf-8")
        b_nodes, b_warns = _parse_clash_yaml(decoded)
        if b_nodes or b_warns:
            return b_nodes, b_warns

    # 2) QuantumultX 格式（trojan=host:port, password=..., tag=...）
    #    真实配置文件带 [general] / [server_local] 段头与 server_check_url 等键，
    #    所以要跳过这些行继续找节点行，而不是看到第一行不是节点就放弃。
    for line in text.splitlines():
        line = line.strip()
        if (not line or line.startswith("#") or line.startswith(";")
                or (line.startswith("[") and line.endswith("]"))):
            continue
        if re.match(r"^(trojan|vmess|ss|http|socks5)\s*=", line, re.I):
            nodes, warns = _parse_quantumultx_lines(text)
            if nodes:
                return nodes, warns
            break
        # 真正的节点 URI 列表：交给第 3 步处理
        if line.split("://", 1)[0].lower() in SUPPORTED_SCHEMES:
            break
        # 其余是 QX 的普通配置行（server_check_url=... 等），跳过继续找节点行

    # 3) 节点 URI 列表（明文或 Base64）
    warns = []
    nodes = []
    for line in _decode_uri_lines(text):
        try:
            node = parse_uri(line)
        except UnsupportedNode as e:
            warns.append(str(e))
            continue
        except Exception as e:  # noqa: BLE001 - 单条解析失败不应中断整体
            warns.append(f"解析失败 {line[:40]}...: {e}")
            continue
        if node:
            nodes.append(node)
    return nodes, warns


def _parse_quantumultx_lines(text):
    """解析 QuantumultX 配置行格式。

    形如:
        trojan=host:port, password=xxx, tag=xx, over-tls=true, tls-host=xx
        vmess=host:port, method=auto, password=uuid, tag=xx[, obfs=ws, ...]
        ss=host:port, method=xx, password=xx, tag=xx[, obfs=http, ...]
    """
    nodes, warns = [], []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        # 跳过段头与普通配置项（server_check_url=、dns_exclusion_list= 等），
        # 它们带 '=' 但不是节点定义，不应刷成「暂不支持」警告。
        if line.startswith("[") and line.endswith("]"):
            continue
        if not re.match(r"^(trojan|vmess|ss|http|socks5)\s*=", line, re.I):
            continue
        scheme, _, rest = line.partition("=")
        scheme = scheme.strip().lower()
        try:
            if scheme == "trojan":
                nodes.append(_qx_trojan(rest))
            elif scheme == "vmess":
                nodes.append(_qx_vmess(rest))
            elif scheme == "ss":
                nodes.append(_qx_ss(rest))
            else:
                warns.append(f"暂不支持 QX 节点类型: {scheme}={line[:40]}...")
        except Exception as e:  # noqa: BLE001 - 单行解析失败不应中断整体
            warns.append(f"解析失败 {line[:40]}...: {e}")
    return nodes, warns


def _qx_split(rest):
    """拆分 QX 参数段: 首段 host:port，其余为 key=value。"""
    items = [s.strip() for s in rest.split(",") if s.strip()]
    hostport = items[0]
    kv = {}
    for item in items[1:]:
        k, _, v = item.partition("=")
        kv[k.strip().lower()] = v.strip()
    return hostport, kv


def _qx_trojan(rest):
    hostport, kv = _qx_split(rest)
    host, port = _split_hostport(hostport)
    proxy = {
        "name": kv.get("tag") or f"{host}:{port}",
        "type": "trojan",
        "server": host,
        "port": port,
        "password": kv.get("password", ""),
        "udp": kv.get("udp-relay") == "true",
    }
    if kv.get("tls-host"):
        proxy["sni"] = kv["tls-host"]
    if kv.get("tls-verification") == "false":
        proxy["skip-cert-verify"] = True
    if not proxy["password"]:
        raise ValueError("trojan 节点缺少 password")
    return proxy


def _qx_vmess(rest):
    hostport, kv = _qx_split(rest)
    host, port = _split_hostport(hostport)
    proxy = {
        "name": kv.get("tag") or f"{host}:{port}",
        "type": "vmess",
        "server": host,
        "port": port,
        "uuid": kv.get("password", ""),
        "alterId": 0,
        "cipher": kv.get("method") or "auto",
        "udp": kv.get("udp-relay") == "true",
    }
    if not proxy["uuid"]:
        raise ValueError("vmess 节点缺少 uuid")
    if kv.get("obfs") == "ws":
        proxy["network"] = "ws"
        ws_opts = {"path": kv.get("obfs-uri", "/")}
        if kv.get("obfs-host"):
            ws_opts["headers"] = {"Host": kv["obfs-host"]}
        proxy["ws-opts"] = ws_opts
    if kv.get("over-tls") == "true":
        proxy["tls"] = True
        if kv.get("tls-host"):
            proxy["servername"] = kv["tls-host"]
    return proxy


def _qx_ss(rest):
    hostport, kv = _qx_split(rest)
    host, port = _split_hostport(hostport)
    proxy = {
        "name": kv.get("tag") or f"{host}:{port}",
        "type": "ss",
        "server": host,
        "port": port,
        "cipher": kv.get("method", ""),
        "password": kv.get("password", ""),
        "udp": kv.get("udp-relay") == "true",
    }
    if not proxy["cipher"] or not proxy["password"]:
        raise ValueError("ss 节点缺少加密方式或密码")
    obfs = kv.get("obfs")
    if obfs in ("http", "tls"):
        proxy["plugin"] = "obfs"
        proxy["plugin-opts"] = {"mode": obfs}
        if kv.get("obfs-host"):
            proxy["plugin-opts"]["host"] = kv["obfs-host"]
    return proxy


_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*$")


def _decode_uri_lines(text):
    """把订阅文本还原为节点 URI 列表。"""
    text = text.strip().lstrip("\ufeff")
    first = text.split("\n", 1)[0].strip()
    scheme = first.split("://", 1)[0].lower() if "://" in first else ""
    if scheme in SUPPORTED_SCHEMES:
        return [line.strip() for line in text.splitlines() if line.strip()]

    # Base64 整体编码（机场订阅最常见的形态）
    try:
        decoded = _b64decode(text).decode("utf-8")
        if "://" in decoded:
            return [line.strip() for line in decoded.splitlines() if line.strip()]
    except Exception:  # noqa: BLE001 - 非 base64 内容很常见，直接走下一分支
        pass

    # 明文中混杂 URI 的兜底：提取可能包含代理 URI 的行，过滤掉普通网页/规则链接与非法 scheme
    candidates = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";", "//")):
            continue
        # 从 scheme 起取到行尾：节点名可能含未编码的空格或以括号结尾，不能按 \S+ 截断或剥括号
        m = re.search(r"[a-zA-Z][a-zA-Z0-9+.-]*://", line)
        if not m:
            continue
        uri = line[m.start():]
        # 只去掉包住 URI 的那对引号，如 url: "ss://..."
        quote_ch = line[m.start() - 1] if m.start() else ""
        if quote_ch in ("\"", "'") and uri.endswith(quote_ch):
            uri = uri[:-1]
        s = uri.split("://", 1)[0].lower()
        # 忽略普通网页与规则链接（http/https 绝大多数是说明网页、DoH 或规则库）
        if s in ("http", "https"):
            continue
        candidates.append(uri)
    return candidates


def parse_uri(uri):
    if "://" not in uri:
        raise ValueError(f"无法识别 URI 格式: {uri[:40]}")
    scheme_part, _, _ = uri.partition("://")
    scheme = scheme_part.strip().lower()
    if not _SCHEME_RE.match(scheme):
        raise ValueError(f"非法 URI 协议头: {scheme_part[:20]}")
    parser = {
        "ss": _parse_ss,
        "vmess": _parse_vmess,
        "vless": _parse_vless,
        "trojan": _parse_trojan,
        "hysteria2": _parse_hysteria2,
        "hy2": _parse_hysteria2,
        "anytls": _parse_anytls,
        "tuic": _parse_tuic,
        "tuic5": _parse_tuic,
    }.get(scheme)
    if not parser:
        raise UnsupportedNode(f"暂不支持协议 {scheme}://")
    return parser(uri)


def _split_hostport(hostport):
    """拆分 host:port，支持 [IPv6]:port 形式。"""
    hostport = hostport.strip("/")
    m = re.match(r"^\[(.+)\]:(\d+)$", hostport)
    if m:
        return m.group(1), int(m.group(2))
    host, _, port = hostport.rpartition(":")
    if not host:
        raise ValueError(f"无法解析地址: {hostport}")
    return host, int(port)


def _tag_from_fragment(fragment, fallback):
    return unquote(fragment) if fragment else fallback


def _parse_ss(uri):
    body = uri[5:]
    tag = ""
    if "#" in body:
        body, _, tag = body.partition("#")
    query = ""
    if "?" in body:
        body, _, query = body.partition("?")

    if "@" in body:
        userinfo, _, hostport = body.rpartition("@")
        # SIP002: userinfo 是 base64(method:password)；
        # SS 2022（2022-blake3-*）按规范为百分号编码的明文，必须 unquote，否则密码带着 %2B/%3D
        decoded = False
        if re.fullmatch(r"[A-Za-z0-9+/_=-]+", userinfo or ""):
            with contextlib.suppress(ValueError, UnicodeDecodeError, binascii.Error):
                userinfo = _b64decode(userinfo).decode("utf-8")
                decoded = True
        if not decoded:
            userinfo = unquote(userinfo)
    else:
        # 旧格式: 整体 base64(method:password@host:port)
        raw = _b64decode(body).decode("utf-8")
        userinfo, _, hostport = raw.rpartition("@")

    method, _, password = userinfo.partition(":")
    if not method or not password:
        raise ValueError("ss 节点缺少加密方式或密码")
    host, port = _split_hostport(hostport)

    q = parse_qs(query)
    proxy = {
        "name": _tag_from_fragment(tag, f"{host}:{port}"),
        "type": "ss",
        "server": host,
        "port": port,
        "cipher": method,
        "password": password,
        "udp": True,
    }
    # SIP003 插件参数：plugin=obfs;obfs=tls;obfs-host=xxx（node_to_uri 也是这么导出的，
    # 必须能对称解析回来，否则自己导出的订阅自己读不了）。
    if q.get("plugin"):
        _apply_ss_plugin(proxy, q["plugin"][0])
    return proxy


def _apply_ss_plugin(proxy, spec):
    """解析 SIP003 plugin 参数并写入 proxy（目前支持 simple-obfs / obfs-local）。"""
    parts = [p for p in str(spec).split(";") if p]
    if not parts:
        return
    plugin = parts[0].strip()
    kv = {}
    for item in parts[1:]:
        k, _, v = item.partition("=")
        if k.strip():
            kv[k.strip().lower()] = v
    if plugin not in ("obfs", "simple-obfs", "obfs-local"):
        raise UnsupportedNode(f"暂不支持 ss plugin: {spec[:30]}")
    mode = kv.get("obfs") or kv.get("mode")
    if mode not in ("http", "tls"):
        raise UnsupportedNode(f"ss obfs 插件缺少合法 mode(http/tls): {spec[:30]}")
    proxy["plugin"] = "obfs"
    proxy["plugin-opts"] = {"mode": mode}
    host = kv.get("obfs-host") or kv.get("host")
    if host:
        proxy["plugin-opts"]["host"] = host
    return proxy


def _parse_vmess(uri):
    body = uri[8:]
    tag = ""
    if "#" in body:
        body, _, tag = body.partition("#")
    data = json.loads(_b64decode(body).decode("utf-8"))
    host = str(data.get("add") or "")
    port = int(data.get("port") or 0)
    if not host or not port:
        raise ValueError("vmess 节点缺少地址或端口")

    node_name = data.get("ps") or _tag_from_fragment(tag, f"{host}:{port}")
    proxy = {
        "name": node_name,
        "type": "vmess",
        "server": host,
        "port": port,
        "uuid": data.get("id") or "",
        "alterId": int(data.get("aid") or data.get("alterId") or 0),
        "cipher": data.get("scy") or "auto",
        "udp": True,
    }
    net = (data.get("net") or "tcp").lower()
    if net == "ws":
        proxy["network"] = "ws"
        ws_opts = {"path": data.get("path") or "/"}
        if data.get("host"):
            ws_opts["headers"] = {"Host": data["host"]}
        proxy["ws-opts"] = ws_opts
    elif net == "grpc":
        proxy["network"] = "grpc"
        proxy["grpc-opts"] = {"grpc-service-name": data.get("path") or ""}
    elif net in ("h2", "http"):
        proxy["network"] = "h2"
        h2_opts = {}
        if data.get("path"):
            h2_opts["path"] = data["path"]
        if data.get("host"):
            h2_opts["host"] = [data["host"]]
        if h2_opts:
            proxy["h2-opts"] = h2_opts
    if str(data.get("tls") or "").lower() in ("tls", "true", "1"):
        proxy["tls"] = True
        proxy["servername"] = data.get("sni") or data.get("host") or host
    return proxy


def _apply_uri_transport(proxy, q):
    """把 URI query 里的传输层配置写入 proxy（vless/trojan 共用）。"""
    net = q.get("type", "tcp").lower()
    if net == "ws":
        proxy["network"] = "ws"
        ws_opts = {"path": unquote(q.get("path", "/"))}
        if q.get("host"):
            ws_opts["headers"] = {"Host": q["host"]}
        proxy["ws-opts"] = ws_opts
    elif net == "grpc":
        proxy["network"] = "grpc"
        proxy["grpc-opts"] = {"grpc-service-name": unquote(q.get("serviceName", ""))}
    elif net in ("h2", "http"):
        proxy["network"] = "h2"
        h2_opts = {}
        if q.get("path"):
            h2_opts["path"] = unquote(q["path"])
        if q.get("host"):
            h2_opts["host"] = [q["host"]]
        if h2_opts:
            proxy["h2-opts"] = h2_opts


def _parse_vless(uri):
    u = urlsplit(uri)
    if not u.hostname:
        raise ValueError("vless 节点缺少地址")
    host, port = u.hostname, u.port or 443
    q = {k: v[-1] for k, v in parse_qs(u.query).items()}

    proxy = {
        "name": _tag_from_fragment(u.fragment, f"{host}:{port}"),
        "type": "vless",
        "server": host,
        "port": port,
        "uuid": unquote(u.username or ""),
        "udp": True,
    }
    if q.get("flow"):
        proxy["flow"] = q["flow"]
    security = q.get("security", "").lower()
    if security in ("tls", "reality"):
        proxy["tls"] = True
        if q.get("sni"):
            proxy["servername"] = q["sni"]
        if q.get("fp"):
            proxy["client-fingerprint"] = q["fp"]
        if security == "reality":
            if not q.get("pbk"):
                raise ValueError("reality 节点缺少 public-key")
            reality_opts = {
                "public-key": q["pbk"],
                "short-id": q.get("sid", ""),
            }
            if q.get("spx") or q.get("spider-x"):
                reality_opts["spider-x"] = q.get("spx") or q.get("spider-x")
            proxy["reality-opts"] = reality_opts
    pe = q.get("packet-encoding") or q.get("packet_encoding") or q.get("packetEncoding")
    if pe:
        proxy["packet-encoding"] = pe
    _apply_uri_transport(proxy, q)
    return proxy


def _parse_trojan(uri):
    u = urlsplit(uri)
    if not u.hostname:
        raise ValueError("trojan 节点缺少地址")
    host, port = u.hostname, u.port or 443
    q = {k: v[-1] for k, v in parse_qs(u.query).items()}

    proxy = {
        "name": _tag_from_fragment(u.fragment, f"{host}:{port}"),
        "type": "trojan",
        "server": host,
        "port": port,
        "password": unquote(u.username or ""),
        "udp": True,
    }
    if q.get("sni"):
        proxy["sni"] = q["sni"]
    if q.get("allowInsecure") in ("1", "true") or q.get("insecure") in ("1", "true"):
        proxy["skip-cert-verify"] = True
    if q.get("fp"):
        proxy["client-fingerprint"] = q["fp"]
    if q.get("alpn"):
        proxy["alpn"] = [x.strip() for x in q["alpn"].split(",") if x.strip()]
    # trojan 也支持 ws/grpc/h2（以前这里完全没解析，导出后连不上）
    _apply_uri_transport(proxy, q)
    return proxy


def _parse_hysteria2(uri):
    u = urlsplit(uri)
    if not u.hostname:
        raise ValueError("hysteria2 节点缺少地址")
    host, port = u.hostname, u.port or 443
    q = {k: v[-1] for k, v in parse_qs(u.query).items()}

    proxy = {
        "name": _tag_from_fragment(u.fragment, f"{host}:{port}"),
        "type": "hysteria2",
        "server": host,
        "port": port,
        "password": unquote(u.username or ""),
        "udp": True,
    }
    if q.get("sni"):
        proxy["sni"] = q["sni"]
    if q.get("insecure") in ("1", "true"):
        proxy["skip-cert-verify"] = True
    if q.get("obfs"):
        proxy["obfs"] = q["obfs"]
        if q.get("obfs-password"):
            proxy["obfs-password"] = q["obfs-password"]
    if q.get("mport"):
        proxy["ports"] = q["mport"]
    return proxy


def _parse_tuic(uri):
    """解析 tuic / tuic5 协议 URI。"""
    u = urlsplit(uri)
    if not u.hostname:
        raise ValueError("tuic 节点缺少地址")
    host, port = u.hostname, u.port or 8443
    q = {k: v[-1] for k, v in parse_qs(u.query).items()}

    proxy = {
        "name": _tag_from_fragment(u.fragment, f"{host}:{port}"),
        "type": "tuic",
        "server": host,
        "port": port,
        "uuid": unquote(u.username or ""),
        "password": unquote(u.password or ""),
        "udp": True,
    }
    if q.get("sni"):
        proxy["sni"] = q["sni"]
    if q.get("insecure") in ("1", "true") or q.get("allowInsecure") in ("1", "true") or q.get("allow_insecure") in ("1", "true"):
        proxy["skip-cert-verify"] = True
    if q.get("alpn"):
        proxy["alpn"] = [x.strip() for x in q["alpn"].split(",") if x.strip()]
    cc = q.get("congestion_control") or q.get("congestion_controller") or q.get("cc")
    if cc:
        proxy["congestion-controller"] = cc
    udp_mode = q.get("udp_relay_mode") or q.get("udp-relay-mode")
    if udp_mode:
        proxy["udp-relay-mode"] = udp_mode
    return proxy


def _parse_anytls(uri):
    """anytls://password@host:port?sni=...&insecure=1#name"""
    u = urlsplit(uri)
    if not u.hostname:
        raise ValueError("anytls 节点缺少地址")
    host, port = u.hostname, u.port or 443
    q = {k: v[-1] for k, v in parse_qs(u.query).items()}
    proxy = {
        "name": _tag_from_fragment(u.fragment, f"{host}:{port}"),
        "type": "anytls",
        "server": host,
        "port": port,
        "password": unquote(u.username or ""),
        "udp": True,
    }
    if q.get("sni"):
        proxy["sni"] = q["sni"]
    if q.get("insecure") in ("1", "true") or q.get("allowInsecure") in ("1", "true"):
        proxy["skip-cert-verify"] = True
    if q.get("fp"):
        proxy["client-fingerprint"] = q["fp"]
    if q.get("alpn"):
        proxy["alpn"] = [x.strip() for x in q["alpn"].split(",") if x.strip()]
    return proxy


def dedupe_names(nodes):
    """mihomo 要求节点名唯一，重名节点追加序号。

    注意不能用「计数 + 拼后缀」：订阅本身就可能已经带 `X #2`，
    那样会生成两个 `X #2`，mihomo 会直接以 duplicate name 拒绝启动。
    这里基于全局已用名集合递增，保证幂等且结果唯一。
    """
    used = set()
    for node in nodes:
        name = node.get("name") or "node"
        if name in used:
            i = 2
            while f"{name} #{i}" in used:
                i += 1
            name = f"{name} #{i}"
        used.add(name)
        node["name"] = name
    return nodes


def _uri_transport_params(params, node):
    """把 ws/grpc/h2 传输层配置写进 URI query（vless/trojan 导出共用）。"""
    net = (node.get("network") or "").lower()
    if not net:
        return
    params["type"] = net
    if net == "ws":
        ws_opts = node.get("ws-opts")
        if isinstance(ws_opts, dict):
            params["path"] = ws_opts.get("path") or "/"
            headers = ws_opts.get("headers")
            if isinstance(headers, dict) and headers.get("Host"):
                params["host"] = headers["Host"]
    elif net == "grpc":
        gopts = node.get("grpc-opts")
        if isinstance(gopts, dict) and gopts.get("grpc-service-name"):
            params["serviceName"] = gopts["grpc-service-name"]
    elif net in ("h2", "http"):
        h2 = node.get("h2-opts")
        if isinstance(h2, dict):
            if h2.get("path"):
                params["path"] = h2["path"]
            host = h2.get("host")
            if isinstance(host, list) and host:
                params["host"] = host[0]
            elif isinstance(host, str) and host:
                params["host"] = host


def node_to_uri(node):
    """将内部节点配置字典反向转换为通用代理 URI 字符串。"""
    t = (node.get("type") or "").lower()
    name = node.get("name") or "node"
    tag = quote(name)
    server = node.get("server") or ""
    # RFC 3986: IPv6 作为 URI host 必须加方括号，否则客户端解析端口会报错
    server_host = f"[{server}]" if ":" in server and not server.startswith("[") else server
    port = node.get("port") or 443

    if t == "ss":
        cipher = node.get("cipher") or "aes-256-gcm"
        pwd = node.get("password") or ""
        cred = base64.b64encode(f"{cipher}:{pwd}".encode()).decode()
        params = {}
        # QX obfs / ss plugin 不导出会丢失混淆参数，连不上服务端
        plugin = node.get("plugin")
        if plugin:
            plugin_opts = node.get("plugin-opts") if isinstance(node.get("plugin-opts"), dict) else {}
            parts = [plugin]
            if plugin_opts.get("mode"):
                parts.append(f"obfs={plugin_opts['mode']}")
            if plugin_opts.get("host"):
                parts.append(f"obfs-host={plugin_opts['host']}")
            params["plugin"] = ";".join(parts)
        q = ("?" + urlencode(params)) if params else ""
        return f"ss://{cred}@{server_host}:{port}{q}#{tag}"

    if t == "vmess":
        v2 = {
            "v": "2",
            "ps": name,
            "add": server,
            "port": str(port),
            "id": node.get("uuid", ""),
            "aid": str(node.get("alterId", 0)),
            "net": node.get("network", "tcp"),
            "type": "none",
            "tls": "tls" if node.get("tls") else "",
        }
        if node.get("servername"):
            v2["host"] = node["servername"]
            v2["sni"] = node["servername"]
        ws_opts = node.get("ws-opts")
        if ws_opts and isinstance(ws_opts, dict):
            if ws_opts.get("path"):
                v2["path"] = ws_opts["path"]
            headers = ws_opts.get("headers")
            if headers and isinstance(headers, dict) and headers.get("Host"):
                v2["host"] = headers["Host"]
        raw = json.dumps(v2, ensure_ascii=False)
        return "vmess://" + base64.b64encode(raw.encode()).decode()

    if t == "vless":
        uuid = node.get("uuid", "")
        params = {}
        if node.get("tls"):
            params["security"] = "reality" if node.get("reality-opts") else "tls"
        if node.get("servername"):
            params["sni"] = node["servername"]
        if node.get("flow"):
            params["flow"] = node["flow"]
        if node.get("client-fingerprint"):
            params["fp"] = node["client-fingerprint"]
        _uri_transport_params(params, node)
        reality = node.get("reality-opts")
        if reality and isinstance(reality, dict):
            if reality.get("public-key"):
                params["pbk"] = reality["public-key"]
            if reality.get("short-id"):
                params["sid"] = reality["short-id"]
            if reality.get("spider-x"):
                params["spx"] = reality["spider-x"]
        q = ("?" + urlencode(params)) if params else ""
        return f"vless://{uuid}@{server_host}:{port}{q}#{tag}"

    if t == "trojan":
        pwd = quote(str(node.get("password", "")))
        params = {}
        if node.get("sni"):
            params["sni"] = node["sni"]
        if node.get("skip-cert-verify"):
            params["allowInsecure"] = "1"
        if node.get("client-fingerprint"):
            params["fp"] = node["client-fingerprint"]
        if node.get("alpn"):
            params["alpn"] = ",".join(node["alpn"]) if isinstance(node["alpn"], list) else str(node["alpn"])
        _uri_transport_params(params, node)
        q = ("?" + urlencode(params)) if params else ""
        return f"trojan://{pwd}@{server_host}:{port}{q}#{tag}"

    if t in ("hysteria2", "hy2"):
        pwd = quote(str(node.get("password", "")))
        params = {}
        if node.get("sni"):
            params["sni"] = node["sni"]
        if node.get("skip-cert-verify"):
            params["insecure"] = "1"
        if node.get("obfs"):
            params["obfs"] = node["obfs"]
            if node.get("obfs-password"):
                params["obfs-password"] = node["obfs-password"]
        # 多端口（端口跳跃）不导出会丢失，导致只能连单端口
        if node.get("ports"):
            params["mport"] = node["ports"]
        if node.get("alpn"):
            params["alpn"] = ",".join(node["alpn"]) if isinstance(node["alpn"], list) else str(node["alpn"])
        q = ("?" + urlencode(params)) if params else ""
        return f"hysteria2://{pwd}@{server_host}:{port}{q}#{tag}"

    if t in ("tuic", "tuic5"):
        uuid = node.get("uuid", "")
        pwd = node.get("password", "")
        cred = f"{uuid}:{quote(str(pwd))}" if pwd else uuid
        params = {}
        if node.get("sni"):
            params["sni"] = node["sni"]
        if node.get("congestion-controller"):
            params["congestion_control"] = node["congestion-controller"]
        if node.get("alpn"):
            params["alpn"] = ",".join(node["alpn"]) if isinstance(node["alpn"], list) else str(node["alpn"])
        if node.get("skip-cert-verify"):
            params["allow_insecure"] = "1"
        q = ("?" + urlencode(params)) if params else ""
        return f"tuic://{cred}@{server_host}:{port}{q}#{tag}"

    if t == "anytls":
        pwd = quote(str(node.get("password", "")))
        params = {}
        if node.get("sni"):
            params["sni"] = node["sni"]
        if node.get("skip-cert-verify"):
            params["insecure"] = "1"
        if node.get("client-fingerprint"):
            params["fp"] = node["client-fingerprint"]
        if node.get("alpn"):
            params["alpn"] = ",".join(node["alpn"]) if isinstance(node["alpn"], list) else str(node["alpn"])
        q = ("?" + urlencode(params)) if params else ""
        return f"anytls://{pwd}@{server_host}:{port}{q}#{tag}"

    return None


def _singbox_transport(node):
    net = (node.get("network") or "").lower()
    if net == "ws":
        ws_cfg = {"type": "ws"}
        ws_opts = node.get("ws-opts")
        if isinstance(ws_opts, dict):
            if ws_opts.get("path"):
                ws_cfg["path"] = ws_opts["path"]
            headers = ws_opts.get("headers")
            if isinstance(headers, dict) and headers.get("Host"):
                ws_cfg["headers"] = {"Host": headers["Host"]}
        return ws_cfg
    if net == "grpc":
        grpc_cfg = {"type": "grpc"}
        gopts = node.get("grpc-opts")
        if isinstance(gopts, dict) and gopts.get("grpc-service-name"):
            grpc_cfg["service_name"] = gopts["grpc-service-name"]
        return grpc_cfg
    if net in ("h2", "http"):
        h2_cfg = {"type": "http"}
        h2 = node.get("h2-opts")
        if isinstance(h2, dict):
            if h2.get("path"):
                h2_cfg["path"] = h2["path"]
            host = h2.get("host")
            if isinstance(host, list) and host:
                h2_cfg["host"] = host
            elif isinstance(host, str) and host:
                h2_cfg["host"] = [host]
        return h2_cfg
    return None


def node_to_singbox(node):
    """将单个节点配置字典转为 Sing-box outbound 格式。"""
    t = (node.get("type") or "").lower()
    name = node.get("name") or "node"
    server = node.get("server") or ""
    port = int(node.get("port") or 443)

    outbound = {
        "tag": name,
        "server": server,
        "server_port": port,
    }

    if t == "ss":
        outbound["type"] = "shadowsocks"
        outbound["method"] = node.get("cipher", "aes-256-gcm")
        outbound["password"] = node.get("password", "")
        plugin = node.get("plugin")
        if plugin:
            plugin_opts = node.get("plugin-opts") if isinstance(node.get("plugin-opts"), dict) else {}
            # sing-box 内置的是 obfs-local，且参数键名是 SIP003 风格（obfs=/obfs-host=），
            # 不是 mihomo 的 mode=/host=，写错会导致插件解析失败。
            if plugin == "obfs":
                outbound["plugin"] = "obfs-local"
                parts = []
                if plugin_opts.get("mode"):
                    parts.append(f"obfs={plugin_opts['mode']}")
                if plugin_opts.get("host"):
                    parts.append(f"obfs-host={plugin_opts['host']}")
                if parts:
                    outbound["plugin_opts"] = ";".join(parts)
            else:
                outbound["plugin"] = plugin
                if plugin_opts:
                    outbound["plugin_opts"] = ";".join(f"{k}={v}" for k, v in plugin_opts.items())
        return outbound

    if t == "vmess":
        outbound["type"] = "vmess"
        outbound["uuid"] = node.get("uuid", "")
        outbound["alter_id"] = int(node.get("alterId", 0))
        outbound["security"] = node.get("cipher", "auto")
        if node.get("tls"):
            tls_cfg = {"enabled": True}
            if node.get("servername"):
                tls_cfg["server_name"] = node["servername"]
            outbound["tls"] = tls_cfg
        tr = _singbox_transport(node)
        if tr:
            outbound["transport"] = tr
        return outbound

    if t == "vless":
        outbound["type"] = "vless"
        outbound["uuid"] = node.get("uuid", "")
        if node.get("flow"):
            outbound["flow"] = node["flow"]
        if node.get("tls"):
            tls_cfg = {"enabled": True}
            if node.get("servername"):
                tls_cfg["server_name"] = node["servername"]
            reality = node.get("reality-opts")
            if isinstance(reality, dict):
                reality_cfg = {"enabled": True}
                if reality.get("public-key"):
                    reality_cfg["public_key"] = reality["public-key"]
                if reality.get("short-id"):
                    reality_cfg["short_id"] = reality["short-id"]
                tls_cfg["reality"] = reality_cfg
            outbound["tls"] = tls_cfg
        tr = _singbox_transport(node)
        if tr:
            outbound["transport"] = tr
        return outbound

    if t == "trojan":
        outbound["type"] = "trojan"
        outbound["password"] = str(node.get("password", ""))
        tls_cfg = {"enabled": True}
        if node.get("sni"):
            tls_cfg["server_name"] = node["sni"]
        if node.get("skip-cert-verify"):
            tls_cfg["insecure"] = True
        if node.get("alpn"):
            tls_cfg["alpn"] = node["alpn"] if isinstance(node["alpn"], list) else [node["alpn"]]
        outbound["tls"] = tls_cfg
        # trojan 也支持 ws/grpc，漏掉会让导出的节点连不上
        tr = _singbox_transport(node)
        if tr:
            outbound["transport"] = tr
        return outbound

    if t in ("hysteria2", "hy2"):
        outbound["type"] = "hysteria2"
        outbound["password"] = str(node.get("password", ""))
        tls_cfg = {"enabled": True}
        if node.get("sni"):
            tls_cfg["server_name"] = node["sni"]
        if node.get("skip-cert-verify"):
            tls_cfg["insecure"] = True
        if node.get("alpn"):
            tls_cfg["alpn"] = node["alpn"] if isinstance(node["alpn"], list) else [node["alpn"]]
        outbound["tls"] = tls_cfg
        if node.get("obfs"):
            outbound["obfs"] = {
                "type": node.get("obfs"),
                "password": node.get("obfs-password", ""),
            }
        # 端口跳跃（多端口）必须保留，否则只能连单端口
        if node.get("ports"):
            outbound["server_ports"] = [str(node["ports"])]
        return outbound

    if t in ("tuic", "tuic5"):
        outbound["type"] = "tuic"
        outbound["uuid"] = node.get("uuid", "")
        outbound["password"] = str(node.get("password", ""))
        if node.get("congestion-controller"):
            outbound["congestion_controller"] = node["congestion-controller"]
        tls_cfg = {"enabled": True}
        if node.get("sni"):
            tls_cfg["server_name"] = node["sni"]
        if node.get("skip-cert-verify"):
            tls_cfg["insecure"] = True
        if node.get("alpn"):
            tls_cfg["alpn"] = node["alpn"] if isinstance(node["alpn"], list) else [node["alpn"]]
        outbound["tls"] = tls_cfg
        return outbound

    if t == "anytls":
        outbound["type"] = "anytls"
        outbound["password"] = str(node.get("password", ""))
        tls_cfg = {"enabled": True}
        if node.get("sni"):
            tls_cfg["server_name"] = node["sni"]
        if node.get("skip-cert-verify"):
            tls_cfg["insecure"] = True
        if node.get("alpn"):
            tls_cfg["alpn"] = node["alpn"] if isinstance(node["alpn"], list) else [node["alpn"]]
        outbound["tls"] = tls_cfg
        return outbound

    return None
