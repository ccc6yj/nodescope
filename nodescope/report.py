"""结果输出：终端表格 + JSON 导出。"""

import json

from rich.console import Console
from rich.table import Table
from rich.text import Text

console = Console()


def _unlock_tags_text(unlock):
    if not isinstance(unlock, dict):
        return Text("-", style="dim")
    tags = []
    if unlock.get("chatgpt"):
        tags.append(Text("GPT", style="bold green"))
    if unlock.get("claude"):
        tags.append(Text("Claude", style="bold rgb(249,115,22)"))
    if unlock.get("gemini"):
        tags.append(Text("Gemini", style="bold blue"))
    if unlock.get("youtube"):
        tags.append(Text("YT", style="bold red"))
    if unlock.get("netflix"):
        tags.append(Text("NF", style="bold red"))
    if unlock.get("disney"):
        tags.append(Text("Disney", style="bold cyan"))
    if unlock.get("tiktok"):
        tags.append(Text("TikTok", style="bold bright_black"))
    if unlock.get("bilibili"):
        tags.append(Text("Bili", style="bold magenta"))
    return Text(" ").join(tags) if tags else Text("-", style="dim")


IP_RISK_NAMES = {"low": "低", "mid": "中", "high": "高"}


def ip_label(q):
    """IP 质量的简短标签：「家宽」「机房」「机房·代理」；无数据返回 None。"""
    if not isinstance(q, dict) or not q.get("type"):
        return None
    return q["type"] + ("·代理" if q.get("proxy") else "")


def ip_tip(q):
    """IP 质量的悬停说明：风险等级 + ISP + ASN。"""
    if not isinstance(q, dict):
        return ""
    parts = [f"风险{IP_RISK_NAMES.get(q.get('risk'), '?')}"]
    if q.get("proxy"):
        parts.append("已被标记为代理/VPN")
    parts += [x for x in (q.get("isp"), q.get("asn")) if x]
    return " | ".join(parts)


def _ip_text(q):
    label = ip_label(q)
    if not label:
        return Text("-", style="dim")
    style = {"low": "green", "high": "yellow"}.get(q.get("risk"), "")
    return Text(label, style=style)


def print_report(results, top=None, show_unlock=False):
    """按速度/延迟排序打印结果表格（默认不展示解锁，勾选/指定时才展示）。"""
    ranked = sort_results(results)
    if top:
        ranked = ranked[:top]

    has_unlock = show_unlock or any(bool(r.get("unlock")) for r in results)
    has_ip = any(r.get("ip_quality") for r in results)

    table = Table(title="节点测试结果", show_lines=False)
    table.add_column("#", justify="right", style="dim")
    table.add_column("节点", overflow="fold")
    table.add_column("类型", style="cyan")
    table.add_column("落地", justify="center")
    if has_ip:
        table.add_column("IP", justify="center")
    if has_unlock:
        table.add_column("解锁", justify="center")
    table.add_column("延迟", justify="right")
    table.add_column("下载速度", justify="right")
    table.add_column("状态")

    for i, r in enumerate(ranked, 1):
        # 下载速度
        speed = r.get("speed_mbps")
        if speed is not None:
            style = "green" if speed >= 50 else ("yellow" if speed >= 10 else "red")
            speed_cell = Text(f"{speed:.1f} Mbps", style=style)
        else:
            speed_cell = Text("-", style="dim")

        # 延迟（端到端延迟优先，直连作为兜底）
        lat_val = r.get("http_ms") if r.get("http_ms") is not None else r.get("tcp_ms")
        if lat_val is not None:
            style = "green" if lat_val < 300 else ("yellow" if lat_val < 800 else "red")
            lat_cell = Text(f"{lat_val:.0f} ms", style=style)
        else:
            lat_cell = Text("-", style="dim")

        # 状态
        if r.get("ok"):
            status_cell = Text("✓", style="green")
        elif r.get("error"):
            status_cell = Text("✗", style="red")
        else:
            status_cell = Text("-", style="dim")

        country = r.get("country")
        country_cell = Text(str(country), style="magenta") if country else Text("-", style="dim")

        row_cells = [
            str(i),
            r.get("name", "?"),
            str(r.get("type", "?")),
            country_cell,
        ]
        if has_ip:
            row_cells.append(_ip_text(r.get("ip_quality")))
        if has_unlock:
            row_cells.append(_unlock_tags_text(r.get("unlock")))
        row_cells.extend([
            lat_cell,
            speed_cell,
            status_cell,
        ])
        table.add_row(*row_cells)

    console.print(table)

    ok_count = sum(1 for r in results if r.get("ok"))
    console.print(
        f"[dim]共 {len(results)} 个节点，可用 {ok_count} 个，"
        f"失败 {len(results) - ok_count} 个[/dim]"
    )


def sort_results(results):
    """可测速的按速度降序，否则按 HTTP 延迟升序，TCP 延迟兜底。"""

    def key(r):
        speed = r.get("speed_mbps")
        if speed is not None:
            return (0, -speed)
        http = r.get("http_ms")
        if http is not None:
            return (1, http)
        tcp = r.get("tcp_ms")
        if tcp is not None:
            return (2, tcp)
        return (3, 0)

    return sorted(results, key=key)


def export_json(results, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    console.print(f"[green]结果已导出: {path}[/green]")


def export_nodes_yaml(results, original_nodes, path, top=None):
    """导出测速可用的节点为 Clash YAML 格式。"""
    import yaml
    ranked = sort_results([r for r in results if r.get("ok")])
    if top:
        ranked = ranked[:top]
    node_map = {n["name"]: n for n in original_nodes}
    exported = [node_map[r["name"]] for r in ranked if r["name"] in node_map]

    cfg = {
        "proxies": exported,
        "proxy-groups": [
            {
                "name": "PROXIES",
                "type": "select",
                "proxies": [n["name"] for n in exported],
            }
        ],
    }
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
    console.print(f"[green]已导出 {len(exported)} 个可用节点到: {path}[/green]")


def export_nodes_base64(results, original_nodes, path, top=None):
    """导出可用节点为标准 Base64 订阅文本（兼容各大代理客户端）。"""
    import base64

    from .subscription import node_to_uri

    ranked = sort_results([r for r in results if r.get("ok")])
    if top:
        ranked = ranked[:top]
    node_map = {n["name"]: n for n in original_nodes}

    uris = []
    skipped = 0
    for r in ranked:
        node = node_map.get(r["name"])
        if node:
            uri = node_to_uri(node)
            if uri:
                uris.append(uri)
            else:
                skipped += 1

    b64_content = base64.b64encode("\n".join(uris).encode("utf-8")).decode("utf-8")
    with open(path, "w", encoding="utf-8") as f:
        f.write(b64_content)
    console.print(f"[green]已导出 {len(uris)} 个可用节点为 Base64 订阅到: {path}[/green]")
    if skipped:
        console.print(f"[yellow]  {skipped} 个节点因协议不支持 URI 导出已跳过[/yellow]")


def export_nodes_singbox(results, original_nodes, path, top=None):
    """导出可用节点为标准 Sing-box 配置格式（JSON）。"""
    from .subscription import node_to_singbox

    ranked = sort_results([r for r in results if r.get("ok")])
    if top:
        ranked = ranked[:top]
    node_map = {n["name"]: n for n in original_nodes}

    outbounds = []
    tags = []
    skipped = 0
    for r in ranked:
        node = node_map.get(r["name"])
        if node:
            ob = node_to_singbox(node)
            if ob:
                outbounds.append(ob)
                tags.append(ob["tag"])
            else:
                skipped += 1

    cfg = {
        "outbounds": [
            {
                "type": "selector",
                "tag": "select",
                "outbounds": tags,
            },
            *outbounds,
        ]
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    console.print(f"[green]已导出 {len(outbounds)} 个可用节点为 Sing-box 配置到: {path}[/green]")
    if skipped:
        console.print(f"[yellow]  {skipped} 个节点因协议不支持 Sing-box 导出已跳过[/yellow]")


def _badge(cls, text):
    return f'<span class="badge {cls}">{text}</span>'


def _speed_badge(v):
    if v is None:
        return '<span class="dim">-</span>'
    cls = "good" if v >= 50 else ("mid" if v >= 10 else "bad")
    return _badge(cls, f"{v:.1f} Mbps")


def _ms_badge(v, green, yellow):
    if v is None:
        return '<span class="dim">-</span>'
    cls = "good" if v < green else ("mid" if v < yellow else "bad")
    return _badge(cls, f"{v:.0f} ms")


ICON_CDN = "https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/svg/"
HTML_UNLOCK_ICONS = {
    "chatgpt": ("OpenAI (ChatGPT)", "openai.svg"),
    "claude": ("Claude (Anthropic)", "claude-ai.svg"),
    "gemini": ("Google Gemini", "google-gemini.svg"),
    "youtube": ("YouTube", "youtube.svg"),
    "netflix": ("Netflix", "netflix.svg"),
    "disney": ("Disney+", "disney-plus.svg"),
    "tiktok": ("TikTok", "tiktok.svg"),
    "bilibili": ("B站港澳台限定", "bilibili.svg"),
}


def _unlock_badges_html(u):
    if not isinstance(u, dict):
        return '<span class="dim">-</span>'
    icons = []
    for k, (name, file_name) in HTML_UNLOCK_ICONS.items():
        if u.get(k):
            src = f"{ICON_CDN}{file_name}"
            icons.append(f'<span class="icon-badge" data-tip="{name}"><img src="{src}" width="18" height="18" alt="{name}" style="display:block;border-radius:3px;"></span>')
    return f'<div style="display:inline-flex;gap:5px;vertical-align:middle;">{"".join(icons)}</div>' if icons else '<span class="dim">-</span>'


def export_html(results, path, source=None, userinfo=None, show_unlock=False):
    """导出自包含的单文件 HTML 报告（支持点击表头排序）。"""
    import html as html_mod
    from datetime import datetime, timezone

    esc = html_mod.escape
    ranked = sort_results(results)
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")

    has_unlock = show_unlock or any(bool(r.get("unlock")) for r in results)
    has_ip = any(r.get("ip_quality") for r in results)

    ok_list = [r for r in results if r.get("ok")]
    speeds = [r["speed_mbps"] for r in results if r.get("speed_mbps") is not None]
    lats = [r["http_ms"] for r in results if r.get("http_ms") is not None]
    cards = [
        ("节点总数", str(len(results)), ""),
        ("可用", str(len(ok_list)), "good"),
        ("失败", str(len(results) - len(ok_list)), "bad"),
        ("最高速度", f"{max(speeds):.1f} Mbps" if speeds else "-", "good"),
        ("最低延迟", f"{min(lats):.0f} ms" if lats else "-", "mid"),
    ]

    rows = []
    for i, r in enumerate(ranked, 1):
        if r.get("ok"):
            status = _badge("good", "\u2713 可用")
        elif r.get("error"):
            status = _badge("bad", "\u2717 失败")
        else:
            status = '<span class="dim">-</span>'
        err = esc(r.get("error") or "")
        error_cell = f'<td class="error" title="{err}">{err}</td>' if err else '<td class="dim">-</td>'
        country = esc(r.get("country") or "-")
        http = r.get("http_ms")
        tcp = r.get("tcp_ms")
        lat_val = http if http is not None else tcp
        http_str = f"HTTP: {http:.0f}ms" if http is not None else ""
        tcp_str = f"TCP: {tcp:.0f}ms" if tcp is not None else ""
        lat_tip = f"{http_str} | {tcp_str}" if (http_str and tcp_str) else (http_str or tcp_str)
        lat_badge = f'<span data-tip="{esc(lat_tip)}">{_ms_badge(lat_val, 300, 800)}</span>' if lat_val is not None else '<span class="dim">-</span>'
        unlock_td = f'<td>{_unlock_badges_html(r.get("unlock"))}</td>' if has_unlock else ''
        ip_td = ''
        if has_ip:
            q = r.get("ip_quality")
            label = ip_label(q)
            ip_cls = {"low": "good", "high": "mid"}.get((q or {}).get("risk"), "plain")
            ip_td = (f'<td><span data-tip="{esc(ip_tip(q))}">{_badge(ip_cls, esc(label))}</span></td>'
                     if label else '<td class="dim">-</td>')
        rows.append(
            "<tr>"
            f'<td class="num">{i}</td>'
            f"<td class='name'>{esc(r.get('name', '?'))}</td>"
            f"<td>{esc(str(r.get('type', '?')))}</td>"
            f"<td>{country}</td>"
            f"{ip_td}"
            f"{unlock_td}"
            f"<td class='num'>{lat_badge}</td>"
            f"<td class='num'>{_speed_badge(r.get('speed_mbps'))}</td>"
            f"<td>{status}</td>"
            f"{error_cell}"
            "</tr>"
        )

    card_html = "\n".join(
        f'<div class="card"><div class="card-num {cls}">{v}</div><div class="card-label">{k}</div></div>'
        for k, v, cls in cards
    )
    info_html = ""
    if userinfo:
        info_html = f'<div class="meta">订阅流量: {esc(userinfo)}</div>'

    body = "\n".join(rows)

    th_unlock = '<th data-k="unlock">解锁<span class="arrow"></span></th>' if has_unlock else ''
    th_ip = '<th data-k="ip" title="出口 IP 类型与风险，悬停看 ISP / ASN">IP<span class="arrow"></span></th>' if has_ip else ''
    page = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>节点测试报告 - {now}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
         background: #0f1117; color: #e6e6e6; padding: 32px 24px; }}
  .wrap {{ max-width: 1080px; margin: 0 auto; }}
  h1 {{ font-size: 22px; font-weight: 600; margin-bottom: 6px; }}
  .meta {{ color: #7c8497; font-size: 13px; margin-bottom: 4px; }}
  .cards {{ display: flex; gap: 12px; margin: 20px 0; flex-wrap: wrap; }}
  .card {{ flex: 1; min-width: 140px; background: #171a23; border: 1px solid #232838;
          border-radius: 10px; padding: 14px 18px; }}
  .card-num {{ font-size: 24px; font-weight: 700; }}
  .card-num.good {{ color: #4ade80; }} .card-num.bad {{ color: #f87171; }}
  .card-num.mid {{ color: #fbbf24; }}
  .card-label {{ color: #7c8497; font-size: 12px; margin-top: 4px; }}
  table {{ width: 100%; border-collapse: collapse; background: #171a23;
          border: 1px solid #232838; border-radius: 10px; overflow: hidden;
          font-size: 13.5px; }}
  th {{ background: #1c2130; text-align: left; padding: 11px 14px; color: #9aa4bb;
       font-weight: 600; cursor: pointer; user-select: none; white-space: nowrap; }}
  th:hover {{ color: #e6e6e6; }}
  th .arrow {{ font-size: 10px; margin-left: 4px; }}
  td {{ padding: 10px 14px; border-top: 1px solid #20263a; }}
  tr:hover td {{ background: #1a1f2e; }}
  .num {{ font-variant-numeric: tabular-nums; }}
  td.num {{ text-align: left; }}
  .dim {{ color: #5b6478; }}
  .name {{ font-weight: 500; }}
  .error {{ color: #f87171; font-size: 12px; max-width: 260px;
           overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .badge {{ display: inline-block; padding: 2px 10px; border-radius: 999px;
           font-size: 12px; font-weight: 600; }}
  .badge.good {{ background: #12301f; color: #4ade80; }}
  .badge.mid {{ background: #332a10; color: #fbbf24; }}
  .badge.bad {{ background: #371a1a; color: #f87171; }}
  .badge.plain {{ background: #1f2937; color: #cbd5e1; }}
  .icon-badge {{ display: inline-flex; border-radius: 4px; cursor: pointer; vertical-align: middle; transition: transform 0.12s ease; }}
  .icon-badge:hover {{ transform: scale(1.2); z-index: 10; }}
  [data-tip] {{ position: relative; cursor: default; }}
  [data-tip]::before {{
    content: ''; position: absolute; bottom: calc(100% + 2px); left: 50%;
    transform: translateX(-50%); border-width: 4px 4px 0 4px; border-style: solid;
    border-color: #0f172a transparent transparent transparent;
    opacity: 0; visibility: hidden; transition: opacity 0.08s ease; z-index: 9999; pointer-events: none;
  }}
  [data-tip]::after {{
    content: attr(data-tip); position: absolute; bottom: calc(100% + 6px); left: 50%;
    transform: translateX(-50%) translateY(2px); background: #0f172a; color: #f8fafc;
    padding: 4px 8px; border-radius: 4px; font-size: 11px; font-weight: 500; line-height: 1.35;
    white-space: nowrap; pointer-events: none; opacity: 0; visibility: hidden;
    transition: opacity 0.08s ease, transform 0.08s ease;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4); z-index: 9999;
  }}
  [data-tip]:hover::before,
  [data-tip]:hover::after {{ opacity: 1; visibility: visible; transform: translateX(-50%) translateY(0); }}
</style>
</head>
<body>
<div class="wrap">
  <h1>节点测试报告</h1>
  <div class="meta">测试时间: {now}</div>
  {f'<div class="meta">订阅源: {esc(source)}</div>' if source else ''}
  {info_html}
  <div class="cards">{card_html}</div>
  <table id="tbl">
    <thead><tr>
      <th data-k="rank">#<span class="arrow"></span></th>
      <th data-k="name">节点<span class="arrow"></span></th>
      <th data-k="type">类型<span class="arrow"></span></th>
      <th data-k="country">落地<span class="arrow"></span></th>
      {th_ip}
      {th_unlock}
      <th data-k="latency" title="经节点端到端真实延迟（鼠标悬停查看直连入口延迟）">延迟<span class="arrow"></span></th>
      <th data-k="speed">下载速度<span class="arrow"></span></th>
      <th data-k="status">状态<span class="arrow"></span></th>
      <th data-k="error">错误信息<span class="arrow"></span></th>
    </tr></thead>
    <tbody>\n{body}\n</tbody>
  </table>
</div>
<script>
// 点击表头排序（数字列按 data-v 排，文本列按内容排）
const table = document.getElementById('tbl');
const tbody = table.querySelector('tbody');
const numKeys = new Set(['latency', 'speed', 'rank']);
let state = {{ key: null, asc: true }};
function val(row, k) {{
  const idx = [...row.parentElement.parentElement.rows[0].cells]
    .findIndex(th => th.dataset.k === k);
  const cell = row.cells[idx];
  if (numKeys.has(k)) {{
    const m = cell.textContent.match(/[\\d.]+/);
    // 缺值统一给 -1（速度列原本就是 -1），降序时失败/无数据节点排最后；
    // 之前延迟列用 Infinity，降序会把 '-' 的失败节点顶到第一行。
    return m ? parseFloat(m[0]) : -1;
  }}
  return cell.textContent.trim();
}}
table.querySelectorAll('th').forEach(th => th.addEventListener('click', () => {{
  const k = th.dataset.k;
  state.asc = state.key === k ? !state.asc : !(k === 'speed' || k === 'status');
  state.key = k;
  table.querySelectorAll('.arrow').forEach(a => a.textContent = '');
  const arrow = th.querySelector('.arrow');
  if (arrow) arrow.textContent = state.asc ? '\u25b2' : '\u25bc';
  const rows = [...tbody.rows].sort((a, b) => {{
    const va = val(a, k), vb = val(b, k);
    const cmp = typeof va === 'number' ? va - vb : va.localeCompare(vb, 'zh');
    return state.asc ? cmp : -cmp;
  }});
  rows.forEach((r, i) => {{
    tbody.appendChild(r);
    r.cells[0].textContent = i + 1;
  }});
}}));
// 默认按速度降序
const speedTh = table.querySelector('th[data-k="speed"]');
if (speedTh) speedTh.click();
</script>
</body>
</html>"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
    console.print(f"[green]HTML 报告已导出: {path}[/green]")
