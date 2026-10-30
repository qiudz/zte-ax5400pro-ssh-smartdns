#!/usr/bin/env python3
"""中兴 AX5400 Pro (ZXHN E3630) 轻量级 Telnet Root 客户端。

说明：
- Python 3.13+ 已移除标准库 `telnetlib`，本脚本基于原生 `socket` 实现零依赖连接。
- 注意：固件内置 BusyBox 1.17.2 在 Telnet 退出时会向当前会话组发送 `SIGHUP`，
  因此若通过 Telnet 启动后台常驻进程，务必加上 `setsid <cmd> >/dev/null 2>&1 &`。

用法示例：
  python scripts/telnet_client.py -H 192.168.5.1 -p "你的Telnet密码" "uname -a" "df -k"
"""
import argparse
import os
import socket
import sys
import time


class TelnetClient:
    def __init__(self, host: str, user: str, password: str, port: int = 23, timeout: float = 6.0):
        self.host = host
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self._drain(2.0)
        self.sock.sendall((user + "\r\n").encode("utf-8"))
        self._drain(1.2)
        self.sock.sendall((password + "\r\n").encode("utf-8"))
        banner = self._drain(2.0)
        if b"/ #" not in banner and b"BusyBox" not in banner and b"#" not in banner:
            self.close()
            raise RuntimeError(f"Telnet 登录失败，返回内容: {banner[:200]!r}")

    def _drain(self, duration: float) -> bytes:
        buf = bytearray()
        end = time.time() + duration
        self.sock.settimeout(1.0)
        while time.time() < end:
            try:
                chunk = self.sock.recv(8192)
                if not chunk:
                    break
                buf.extend(chunk)
                end = max(end, time.time() + 0.5)
            except socket.timeout:
                pass
            except OSError:
                break
        return bytes(buf)

    def run(self, cmd: str, wait: float = 2.5) -> str:
        self.sock.sendall(cmd.encode("utf-8") + b"\n")
        out = self._drain(wait).decode("utf-8", errors="replace")
        lines = out.split("\r\n")
        if lines and lines[0].strip().endswith(cmd.strip()[:40]):
            lines = lines[1:]
        if lines and lines[-1].strip().endswith("#"):
            lines = lines[:-1]
        return "\n".join(lines).strip()

    def close(self):
        try:
            self.sock.sendall(b"exit\r\n")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser(description="ZXHN E3630 Telnet Root 命令执行工具")
    ap.add_argument("-H", "--host", default="192.168.5.1", help="路由器 IP (默认: 192.168.5.1)")
    ap.add_argument("-u", "--user", default="root", help="Telnet 用户名 (默认: root)")
    ap.add_argument(
        "-p", "--password",
        default=os.environ.get("TELNET_PASSWORD"),
        help="Telnet 密码 (也可通过环境变量 TELNET_PASSWORD 传入)",
    )
    ap.add_argument("-w", "--wait", type=float, default=2.5, help="单条命令等待输出秒数 (默认: 2.5)")
    ap.add_argument("commands", nargs="+", help="要执行的 Shell 命令列表")
    args = ap.parse_args()

    if not args.password:
        print("[-] 错误: 请通过 -p/--password 或环境变量 TELNET_PASSWORD 提供 Telnet 密码", file=sys.stderr)
        sys.exit(1)

    cli = TelnetClient(args.host, args.user, args.password)
    try:
        for cmd in args.commands:
            print(f"[{args.host}] # {cmd}")
            print(cli.run(cmd, wait=args.wait))
    finally:
        cli.close()


if __name__ == "__main__":
    main()
