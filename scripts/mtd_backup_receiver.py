#!/usr/bin/env python3
"""全闪存分区 HTTP PUT 流式备份接收端（自动计算 MD5 校验值）。

背景：
AX5400 Pro 内置 `curl`，但注意不能直接 `curl -T /dev/mtdblockN`（块设备 stat 大小会导致传 0 字节），
必须在路由器上使用管道流式上传：
  for i in 0 1 2 3 4 5 6 7 8 9 10 11 12 13 14; do
      cat /dev/mtdblock$i | curl -s -T - http://<本机IP>:18080/mtd$i.bin
  done

用法：
  python scripts/mtd_backup_receiver.py --port 18080 --out-dir ./mtd_backup
"""
import argparse
import hashlib
import os
from http.server import BaseHTTPRequestHandler, HTTPServer


class PutBackupHandler(BaseHTTPRequestHandler):
    out_dir = "./mtd_backup"

    def do_PUT(self):
        filename = os.path.basename(self.path.strip("/"))
        if not filename:
            self.send_response(400)
            self.end_headers()
            return

        os.makedirs(self.out_dir, exist_ok=True)
        dest_path = os.path.join(self.out_dir, filename)
        md5 = hashlib.md5()
        total = 0

        transfer_enc = (self.headers.get("Transfer-Encoding") or "").lower()
        content_len = self.headers.get("Content-Length")

        with open(dest_path, "wb") as f:
            if "chunked" in transfer_enc:
                while True:
                    line = self.rfile.readline().strip()
                    if not line:
                        break
                    chunk_len = int(line.split(b";")[0], 16)
                    if chunk_len == 0:
                        self.rfile.readline()
                        break
                    data = self.rfile.read(chunk_len)
                    self.rfile.readline()  # trailing CRLF
                    f.write(data)
                    md5.update(data)
                    total += len(data)
            elif content_len is not None:
                rem = int(content_len)
                while rem > 0:
                    data = self.rfile.read(min(65536, rem))
                    if not data:
                        break
                    f.write(data)
                    md5.update(data)
                    total += len(data)
                    rem -= len(data)
            else:
                while True:
                    data = self.rfile.read(65536)
                    if not data:
                        break
                    f.write(data)
                    md5.update(data)
                    total += len(data)

        digest = md5.hexdigest()
        sums_file = os.path.join(self.out_dir, "MD5SUMS.txt")
        with open(sums_file, "a", encoding="utf-8") as sf:
            sf.write(f"{digest}  {filename}  ({total} bytes)\n")

        print(f"[+] 已保存 {filename:<16}  {total:>10} bytes  MD5={digest}", flush=True)
        self.send_response(201)
        self.end_headers()
        self.wfile.write(f"OK {digest}\n".encode("utf-8"))

    def log_message(self, fmt, *args):
        return


def main():
    ap = argparse.ArgumentParser(description="路由器 mtd 分区 HTTP PUT 备份接收服务")
    ap.add_argument("--bind", default="0.0.0.0", help="监听地址 (默认: 0.0.0.0)")
    ap.add_argument("--port", type=int, default=18080, help="监听端口 (默认: 18080)")
    ap.add_argument("--out-dir", default="./mtd_backup", help="备份保存目录 (默认: ./mtd_backup)")
    args = ap.parse_args()

    PutBackupHandler.out_dir = os.path.abspath(args.out_dir)
    os.makedirs(PutBackupHandler.out_dir, exist_ok=True)
    srv = HTTPServer((args.bind, args.port), PutBackupHandler)
    print(f"[*] HTTP PUT 备份接收服务已启动: http://{args.bind}:{args.port}/ -> {PutBackupHandler.out_dir}")
    print("[*] 按 Ctrl+C 停止服务")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] 已停止")


if __name__ == "__main__":
    main()
