#!/usr/bin/env python3
"""中兴 AX5400 Pro (ZXHN E3630) Web API 客户端：登录认证、会话抢占与加密配置导出。

注意事项：
1. 路由器 Web 后台仅允许单管理员会话活跃，运行前请先关闭浏览器中的路由器后台页面及中兴智慧生活 App。
2. 所有 POST 请求自动维护并轮换响应头中的 `x_xsrf_token` (`_sessionTOKEN`)。
3. 自动禁用 `requests` 系统环境变量代理 (`trust_env = False`)，防止本机代理软件劫持局域网请求。

用法示例：
  python scripts/router_api.py export -H 192.168.5.1 -p "你的路由器管理密码" -o config.bin
"""
import argparse
import hashlib
import json
import os
import secrets
import sys
from urllib.parse import urlencode

import requests


class AX5400Client:
    def __init__(self, host: str, password: str, username: str = "admin"):
        self.base_url = f"http://{host}"
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.session.trust_env = False  # 绕过系统 HTTP/SOCKS 代理
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Origin": self.base_url,
            "Referer": self.base_url + "/",
            "X-Requested-With": "XMLHttpRequest",
        })
        self.sess_token = None
        self.xsrf_token = None

    def _update_token(self, resp: requests.Response):
        tok = resp.headers.get("x_xsrf_token")
        if tok:
            self.xsrf_token = tok

    def get(self, path: str, timeout: int = 15) -> requests.Response:
        resp = self.session.get(self.base_url + path, timeout=timeout)
        self._update_token(resp)
        return resp

    def post(self, path: str, form: dict = None, timeout: int = 15, **kwargs) -> requests.Response:
        if form is not None:
            payload = dict(form)
            tok = self.xsrf_token or self.sess_token
            if tok:
                payload["_sessionTOKEN"] = tok
            resp = self.session.post(
                self.base_url + path,
                data=urlencode(payload).encode("utf-8"),
                timeout=timeout,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                **kwargs,
            )
        else:
            resp = self.session.post(self.base_url + path, timeout=timeout, **kwargs)
        self._update_token(resp)
        return resp

    def login(self) -> str:
        self.get("/")
        token_resp = self.get("/?_type=loginsceneData&_tag=login_token_json")
        info = json.loads(token_resp.text)
        digest = hashlib.sha256((self.password + info["logintoken"]).encode("utf-8")).hexdigest()

        resp = self.post("/?_type=loginData&_tag=login_entry", {
            "Username": self.username,
            "Password": digest,
            "action": "login",
            "Frm_Logintoken": "",
            "captchaCode": "",
        })
        reply = json.loads(resp.text)
        if reply.get("login_need_refresh") is not True:
            raise RuntimeError(f"登录失败，请检查管理密码或稍后重试: {reply}")

        self.sess_token = reply["sess_token"]
        if reply.get("loginErrType"):
            # 若存在残留会话占用，自动发起抢占
            self.post("/?_type=loginData&_tag=login_preempt", {"action": "preempt"})
        return self.sess_token

    def logout(self):
        try:
            self.post("/?_type=loginData&_tag=logout_entry", {"action": "logout"})
        except Exception:
            pass

    def export_config(self, output_path: str) -> int:
        """导出路由器 Type-4 加密配置文件 (.bin)。"""
        r1 = self.post("/?_type=vueData&_tag=vue_loadprevent_data", {"sessToken": self.sess_token})
        if b"SUCC" not in r1.content:
            raise RuntimeError(f"预导出校验 (loadprevent) 失败: {r1.text[:120]}")

        boundary = "----WebKitFormBoundary" + secrets.token_hex(8)
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="config"\r\n\r\n'
            f"\r\n--{boundary}--\r\n"
        ).encode("utf-8")

        r2 = self.session.post(
            self.base_url + "/?_type=vueData&_tag=do_download_usercfg",
            data=body,
            timeout=60,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        if r2.content.lstrip().startswith(b"<!DOCTYPE") or len(r2.content) < 100:
            raise RuntimeError(
                f"导出失败（收到 {len(r2.content)} 字节 HTML，可能管理会话被浏览器抢占）。"
                "请关闭所有打开的路由器管理网页与手机 App 后重试。"
            )
        with open(output_path, "wb") as f:
            f.write(r2.content)
        return len(r2.content)


def main():
    ap = argparse.ArgumentParser(description="中兴 AX5400 Pro (ZXHN E3630) Web API 工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_exp = sub.add_parser("export", help="从路由器导出 Type-4 加密配置文件")
    p_exp.add_argument("-H", "--host", default="192.168.5.1", help="路由器 IP 地址 (默认: 192.168.5.1)")
    p_exp.add_argument("-u", "--user", default="admin", help="Web 管理员账号 (默认: admin)")
    p_exp.add_argument(
        "-p", "--password",
        default=os.environ.get("ROUTER_PASSWORD"),
        help="Web 管理密码 (也可通过环境变量 ROUTER_PASSWORD 传入)",
    )
    p_exp.add_argument("-o", "--output", default="config.bin", help="导出文件路径 (默认: config.bin)")

    args = ap.parse_args()
    if not args.password:
        print("[-] 错误: 请通过 -p/--password 或环境变量 ROUTER_PASSWORD 提供路由器管理密码", file=sys.stderr)
        sys.exit(1)

    client = AX5400Client(host=args.host, password=args.password, username=args.user)
    try:
        client.login()
        print(f"[+] 登录成功 ({args.host})")
        size = client.export_config(args.output)
        print(f"[+] 配置导出成功: {args.output} ({size} 字节)")
    finally:
        client.logout()


if __name__ == "__main__":
    main()
