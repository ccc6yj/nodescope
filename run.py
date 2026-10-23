#!/usr/bin/env python3
"""订阅节点测速工具。

用法:
    python run.py <订阅URL或文件路径> [选项]

示例:
    python run.py https://example.com/api/v1/client/subscribe?token=xxx
    python run.py sub.txt --no-speed
    python run.py sub.txt --top 5 --json result.json
"""

import argparse
import contextlib
import re
import signal
import sys
import threading

from rich.console import Console
from rich.progress import Progress

from nodescope import report, tester
from nodescope.core import MihomoRunner
from nodescope.subscription import (
    dedupe_names,
    format_userinfo,
    load_nodes,
)


def tune_system_limits():
    """尽量提高当前进程的文件描述符上限，防止并发和大量端口时发生 Too many open files。"""
    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        target = min(hard, 65535) if hard > 0 else 65535
        if soft < target:
            resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
    except Exception:  # noqa: BLE001 - 平台不支持/无权限时静默跳过
        pass


def _fmt_ms(value):
    """延迟展示：http_ms 可能为 None（只测 TCP 直连时），避免 :.0f 格式化崩溃。"""
    return f"{value:.0f}ms" if isinstance(value, (int, float)) else "-"


console = Console()


def build_argparser():
    parser = argparse.ArgumentParser(
        prog="nodescope",
        description="订阅节点测试工具：延迟 + 下载速度",
    )
    parser.add_argument("source", nargs="?", help="订阅 URL 或本地文件路径")
    parser.add_argument("--serve", action="store_true", help="启动 Web 界面模式")
    parser.add_argument("--port", type=int, default=None,
                        help="Web 服务端口（默认 8765，被占用时自动换下一个）")
    parser.add_argument("--no-speed", action="store_true", help="只测延迟，不下载测速")
    parser.add_argument("--speed-seconds", type=float, default=5, help="每个节点测速时长（秒）")
    parser.add_argument("--speed-threads", type=int, default=8, help="每个节点测速并发连接数")
    parser.add_argument("--speed-concurrency", type=int, default=1,
                        help="同时进行下载测速的节点数（默认 1，避免抢本地带宽把速度测低）")
    parser.add_argument("--max-download-mb", type=float, default=30, metavar="MB",
                        help="每节点测速（含探测）最大下载流量，到量即停（默认 30MB）")
    parser.add_argument("--concurrency", type=int, default=8, help="同时测试的节点数")
    parser.add_argument("--samples", type=int, default=3, help="TCP 直连延迟采样次数")
    parser.add_argument("--timeout", type=float, default=5, help="单次请求超时（秒）")
    parser.add_argument("--include", help="只测试名称匹配该正则的节点")
    parser.add_argument("--exclude", help="排除名称匹配该正则的节点")
    parser.add_argument("--top", type=int, help="只显示前 N 名")
    parser.add_argument("--json", metavar="PATH", help="导出 JSON 结果到指定文件")
    parser.add_argument("--html", metavar="PATH", help="导出 HTML 报告到指定文件")
    parser.add_argument("--export-nodes", metavar="PATH",
                        help="导出测试通过的节点为 Clash 配置（.yaml）")
    parser.add_argument("--export-base64", metavar="PATH",
                        help="导出测试通过的节点为标准 Base64 订阅文本")
    parser.add_argument("--export-singbox", metavar="PATH",
                        help="导出测试通过的节点为 Sing-box 配置（.json）")
    parser.add_argument("--no-unlock", action="store_true",
                        help="跳过流媒体与 AI 解锁测试（默认开启测试）")
    parser.add_argument("--list-only", action="store_true", help="只列出解析到的节点，不测试")
    parser.add_argument("--no-proxy-test", action="store_true",
                        help="跳过 mihomo，仅做 TCP 直连延迟")
    parser.add_argument("--keep", action="store_true", help="保留 mihomo 临时配置目录（排查用）")
    parser.add_argument("--iface", help="出站网卡（默认自动选物理网卡以绕过 TUN）")
    return parser


def filter_nodes(nodes, include, exclude):
    def _compile(pattern, flag):
        try:
            return re.compile(pattern)
        except re.error as e:
            raise SystemExit(f"{flag} 不是合法正则表达式: {pattern!r}（{e}）") from e

    inc = _compile(include, "--include") if include else None
    exc = _compile(exclude, "--exclude") if exclude else None
    if inc:
        nodes = [n for n in nodes if inc.search(n.get("name", ""))]
    if exc:
        nodes = [n for n in nodes if not exc.search(n.get("name", ""))]
    return nodes


def install_exit_signals():
    """kill（SIGTERM）或关闭终端窗口（SIGHUP）默认直接结束进程，不执行 finally/atexit，
    mihomo 子进程会成为孤儿继续占端口（mihomo 自己收到 SIGHUP 也不退出）。
    统一转成 KeyboardInterrupt，与 Ctrl+C 走同一条清理路径。"""
    def _interrupt(signum, frame):
        raise KeyboardInterrupt

    for name in ("SIGTERM", "SIGHUP"):
        sig = getattr(signal, name, None)  # Windows 没有 SIGHUP
        if sig is not None:
            with contextlib.suppress(ValueError, OSError):  # 非主线程调用时 ValueError
                signal.signal(sig, _interrupt)


def main(argv=None):
    tune_system_limits()
    install_exit_signals()
    args = build_argparser().parse_args(argv)

    if args.serve:
        from nodescope.webapp import serve
        try:
            serve(args.port or 8765, auto_port=args.port is None)
        except OSError as e:
            console.print(f"[red]{e}[/red]")
            return 1
        return 0

    if not args.source:
        build_argparser().error("需要提供订阅 URL/文件路径，或使用 --serve 启动 Web 模式")

    console.print(f"[dim]读取订阅: {args.source}[/dim]")
    try:
        nodes, warns, userinfo = load_nodes(args.source)
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]读取/解析订阅失败: {e}[/red]")
        return 1

    if not nodes:
        console.print("[red]未解析到任何节点[/red]")
        for w in warns:
            console.print(f"[yellow]  {w}[/yellow]")
        return 1

    dedupe_names(nodes)
    nodes = filter_nodes(nodes, args.include, args.exclude)
    if not nodes:
        console.print("[red]过滤后没有节点[/red]")
        return 1

    info = format_userinfo(userinfo)
    if info:
        console.print(f"[cyan]订阅流量: {info}[/cyan]")
    for w in warns:
        console.print(f"[yellow]警告: {w}[/yellow]")
    from nodescope.iface import detect_physical_iface
    iface = args.iface or detect_physical_iface()
    if tester.check_fake_ip(nodes):
        if iface:
            console.print(
                f"[yellow]检测到 TUN/fake-ip，已绑定网卡 {iface} 绕过，"
                f"可继续测速[/yellow]"
            )
        else:
            console.print(
                "[bold red]警告:[/bold red] 节点域名被解析到 fake-ip，"
                "且未能自动选到物理网卡。UDP 类节点（hysteria2）可能失败。"
                "可用 --iface en0 指定网卡，或暂时关掉 TUN。"
            )
    elif iface:
        console.print(f"[dim]出站网卡: {iface}[/dim]")
    console.print(f"[green]解析到 {len(nodes)} 个节点[/green]\n")

    if args.list_only:
        for i, n in enumerate(nodes, 1):
            console.print(f"{i:>3}. [{n.get('type')}] {n.get('name')}")
        return 0

    options = {
        "speed": not args.no_speed,
        "speed_seconds": args.speed_seconds,
        "speed_threads": args.speed_threads,
        "max_download_mb": args.max_download_mb,
        "samples": args.samples,
        "timeout": args.timeout,
        "iface": iface,
        "unlock": not args.no_unlock,
    }

    # 本机带宽基线与 mihomo 启动并行测；在节点测速开始前完成，
    # 避免抢带宽拉低节点速度
    baseline = None

    def _measure_baseline():
        nonlocal baseline
        with contextlib.suppress(Exception):  # 基线仅用于提示，失败不影响测试
            baseline = tester.local_bandwidth_mbps(iface=iface)

    baseline_thread = None
    if options["speed"]:
        baseline_thread = threading.Thread(target=_measure_baseline, daemon=True)
        baseline_thread.start()
    runner_ctx = None if args.no_proxy_test else MihomoRunner(
        nodes, keep_dir="tmp" if args.keep else None, iface=iface,
    )

    try:
        if runner_ctx is not None:
            runner_ctx.__enter__()
            runner = runner_ctx
        else:
            runner = None

        if baseline_thread is not None:
            baseline_thread.join()
            if baseline:
                console.print(
                    f"[dim]本机带宽基线: {baseline:.1f} Mbps[/dim]"
                    f"[dim]（节点速度接近该值时，瓶颈在本机带宽而非节点差异）[/dim]\n"
                )

        with Progress(console=console) as progress:
            task = progress.add_task("[cyan]流式测试中...", total=len(nodes))
            advanced_nodes = set()

            def on_speed_start(name):
                progress.update(task, description=f"[bold green]测速中: {name}")

            def on_speed_tick(name, mbps):
                progress.update(task, description=f"[bold green]测速中: {name} [bold yellow]{mbps:.1f} Mbps[/bold yellow]")

            def on_done(name, result):
                if not result.get("ok"):
                    if name not in advanced_nodes:
                        advanced_nodes.add(name)
                        progress.advance(task)
                        err = result.get("error", "失败")
                        progress.console.print(f"  [red]✗ {name}: {err}[/red]")
                    return

                if not options.get("speed"):
                    if name not in advanced_nodes:
                        advanced_nodes.add(name)
                        progress.advance(task)
                        progress.console.print(
                            f"  [green]✓ {name}[/green]  "
                            f"{_fmt_ms(result.get('http_ms'))}"
                        )
                    return

                # 测速模式下，等待测速阶段结束（有速度或已完成测速）才结转进度
                if (result.get("_speed_tested") or result.get("speed_mbps") is not None) \
                        and name not in advanced_nodes:
                    advanced_nodes.add(name)
                    progress.advance(task)
                    speed = result.get("speed_mbps")
                    speed_str = f"[bold green]{speed:.1f} Mbps[/bold green]" if speed is not None else "[dim]- Mbps[/dim]"
                    progress.console.print(
                        f"  [green]✓ {name}[/green]  "
                        f"{_fmt_ms(result.get('http_ms'))}  "
                        f"{speed_str}"
                    )

            results = tester.test_all(
                nodes, runner=runner, options=options,
                concurrency=args.concurrency, progress_cb=on_done,
                speed_concurrency=args.speed_concurrency,
                on_speed_start=on_speed_start,
                on_speed_tick=on_speed_tick,
            )
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]{e}[/red]")
        return 1
    finally:
        if runner_ctx is not None:
            runner_ctx.__exit__(None, None, None)

    console.print()
    report.print_report(results, top=args.top, show_unlock=not args.no_unlock)

    if args.json:
        report.export_json(results, args.json)
    if args.html:
        report.export_html(
            results, args.html,
            source=args.source,
            userinfo=format_userinfo(userinfo),
            show_unlock=not args.no_unlock,
        )
    if args.export_nodes:
        report.export_nodes_yaml(results, nodes, args.export_nodes, top=args.top)
    if args.export_base64:
        report.export_nodes_base64(results, nodes, args.export_base64, top=args.top)
    if args.export_singbox:
        report.export_nodes_singbox(results, nodes, args.export_singbox, top=args.top)
    return 0


if __name__ == "__main__":
    sys.exit(main())
