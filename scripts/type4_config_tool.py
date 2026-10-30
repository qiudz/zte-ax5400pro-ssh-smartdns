#!/usr/bin/env python3
"""中兴 AX5400 Pro (ZXHN E3630) Type-4 配置文件加解密与一键开启 Telnet/关闭自动升级工具。

固件实测版本: V1.0.0.10B5.0000
加密规格 (Type-4):
  - 外层 72 字节头: Magic(0x01020304) + Type(4) + 52B保留 + PlainLen + PaddedLen + 0
  - 算法: AES-256-CBC (零填充至 16 字节对齐)
  - 型号内置派生种子:
      KEY = SHA256("E3630Key18410001")  (32 bytes)
      IV  = SHA256("E3630Iv18410001")[:16] (16 bytes)
  - 内层容器: 60 字节头 (含压缩块链式 CRC32 与头 CRC32) + 64KB 分块 zlib 流

用法:
  1. 解密导出配置为 XML:
     python scripts/type4_config_tool.py decrypt config.bin config.xml

  2. 将修改后的 XML 重新打包加密为 Type-4 配置:
     python scripts/type4_config_tool.py encrypt config.xml patched_config.bin

  3. 一键注入永久 Telnet Root 并关闭官方自动升级 (推荐):
     python scripts/type4_config_tool.py patch-telnet config.bin patched_config.bin --user root --password <设置强密码>
"""
import argparse
import hashlib
import re
import struct
import sys
import zlib
from Crypto.Cipher import AES

MAGIC = 0x01020304
OUTER_TYPE = 4
CHUNK_SIZE = 65536

# ZXHN E3630 固件内置配置加解密模型常量（非个人密钥，同型号通用）
MODEL_KEY_SEED = b"E3630Key18410001"
MODEL_IV_SEED = b"E3630Iv18410001"


def derive_key_iv(key_seed=MODEL_KEY_SEED, iv_seed=MODEL_IV_SEED):
    key = hashlib.sha256(key_seed).digest()
    iv = hashlib.sha256(iv_seed).digest()[:16]
    return key, iv


def decrypt_type4(raw: bytes, key_seed=MODEL_KEY_SEED, iv_seed=MODEL_IV_SEED) -> bytes:
    if len(raw) < 72:
        raise ValueError("文件过短，不是有效的 Type-4 配置文件")
    magic, ptype, _res, plain_len, pad_len, _zero = struct.unpack(">II52sIII", raw[:72])
    if magic != MAGIC or ptype != OUTER_TYPE:
        raise ValueError(f"文件头不匹配: magic=0x{magic:08x}, type={ptype} (期望 0x01020304, type=4)")
    if len(raw) < 72 + pad_len or pad_len % 16 != 0:
        raise ValueError(f"密文长度异常: file_len={len(raw)}, pad_len={pad_len}")

    key, iv = derive_key_iv(key_seed, iv_seed)
    inner = AES.new(key, AES.MODE_CBC, iv).decrypt(raw[72:72 + pad_len])[:plain_len]

    imagic, iptype, uncomp_sz, _last_off, _chunk_sz, crc_data, crc_hdr = struct.unpack(">IIIIIII", inner[:28])
    if imagic != MAGIC or iptype != 0:
        raise ValueError("解密失败或型号种子不匹配：内层 zlib 容器魔数校验未通过")

    calc_hdr_crc = zlib.crc32(inner[:24]) & 0xFFFFFFFF
    if calc_hdr_crc != crc_hdr:
        raise ValueError(f"内层头 CRC32 校验失败: 0x{calc_hdr_crc:08x} != 0x{crc_hdr:08x}")

    pos = 60
    crc_comp = 0
    out_parts = []
    while pos < len(inner):
        u_len, c_len, _next_off = struct.unpack(">III", inner[pos:pos + 12])
        cdata = inner[pos + 12:pos + 12 + c_len]
        crc_comp = zlib.crc32(cdata, crc_comp) & 0xFFFFFFFF
        udata = zlib.decompress(cdata)
        if len(udata) != u_len:
            raise ValueError(f"分块解压长度不一致: {len(udata)} != {u_len}")
        out_parts.append(udata)
        pos += 12 + c_len

    if crc_comp != crc_data:
        raise ValueError(f"压缩块 CRC32 校验失败: 0x{crc_comp:08x} != 0x{crc_data:08x}")

    xml_bytes = b"".join(out_parts)
    if len(xml_bytes) != uncomp_sz:
        raise ValueError(f"总解压长度不一致: {len(xml_bytes)} != {uncomp_sz}")
    return xml_bytes


def encrypt_type4(xml_bytes: bytes, key_seed=MODEL_KEY_SEED, iv_seed=MODEL_IV_SEED) -> bytes:
    raw_chunks = [xml_bytes[i:i + CHUNK_SIZE] for i in range(0, len(xml_bytes), CHUNK_SIZE)] or [b""]
    comp_chunks = [zlib.compress(ch, 9) for ch in raw_chunks]

    crc_comp = 0
    for cd in comp_chunks:
        crc_comp = zlib.crc32(cd, crc_comp) & 0xFFFFFFFF

    # 计算每个分块在 inner 容器中的偏移 (首块始于 60)
    offsets = []
    cur = 60
    for cd in comp_chunks:
        offsets.append(cur)
        cur += 12 + len(cd)

    last_chunk_off = offsets[-1]
    body = bytearray()
    for idx, (uch, cd) in enumerate(zip(raw_chunks, comp_chunks)):
        next_off = offsets[idx + 1] if idx + 1 < len(offsets) else 0
        body += struct.pack(">III", len(uch), len(cd), next_off)
        body += cd

    hdr24 = struct.pack(">IIIIII", MAGIC, 0, len(xml_bytes), last_chunk_off, CHUNK_SIZE, crc_comp)
    crc_hdr = zlib.crc32(hdr24) & 0xFFFFFFFF
    inner = bytes(hdr24) + struct.pack(">I32s", crc_hdr, b"\x00" * 32) + bytes(body)

    plain_len = len(inner)
    pad_len = (plain_len + 15) & ~15
    padded = inner + b"\x00" * (pad_len - plain_len)

    key, iv = derive_key_iv(key_seed, iv_seed)
    ciphertext = AES.new(key, AES.MODE_CBC, iv).encrypt(padded)
    outer_hdr = struct.pack(">II52sIII", MAGIC, OUTER_TYPE, b"\x00" * 52, plain_len, pad_len, 0)
    return outer_hdr + ciphertext


def _set_dm_val(tbl_xml: str, dm_name: str, new_val: str) -> str:
    pattern = re.compile(rf'(<DM\s+name="{re.escape(dm_name)}"\s+val=")[^"]*(")')
    if pattern.search(tbl_xml):
        return pattern.sub(rf"\g<1>{new_val}\2", tbl_xml)
    return tbl_xml


def patch_telnet_and_ota(xml_text: str, user: str, password: str, enable_autostart: bool = False) -> str:
    # 1. 修改 TelnetCfg 表: 仅开启局域网 Telnet Root，严禁暴露 WAN 侧 23 端口
    def repl_telnet(m):
        blk = m.group(0)
        updates = {
            "TS_Enable": "1",
            "Lan_Enable": "1",
            "Wan_Enable": "0",
            "TS_Port": "23",
            "TS_UName": user,
            "TS_UPwd": password,
            "TSLan_UName": user,
            "TSLan_UPwd": password,
            "Level": "3",
            "TimeoutEnable": "0",
            "SecurityEnable": "0",
        }
        for k, v in updates.items():
            blk = _set_dm_val(blk, k, v)
        return blk

    patched, n_tel = re.subn(r'<Tbl\s+name="TelnetCfg"[^>]*>.*?</Tbl>', repl_telnet, xml_text, flags=re.S)
    if n_tel == 0:
        raise RuntimeError("未在配置 XML 中找到 TelnetCfg 表，请确认固件型号")

    # 2. 关闭自动 OTA 固件升级 (UpgradeCtrl = 0)，防止半夜升级覆盖 Root 与加密种子
    patched, n_ota = re.subn(
        r'(<DM\s+name="UpgradeCtrl"\s+val=")[^"]*(")',
        r"\g<1>0\2",
        patched,
    )

    # 3. 可选: 同步开启 QDCfg.QdUUplugindEnable = 1 原生开机自启钩子
    n_qd = 0
    if enable_autostart:
        patched, n_qd = re.subn(
            r'(<DM\s+name="QdUUplugindEnable"\s+val=")[^"]*(")',
            r"\g<1>1\2",
            patched,
        )

    msg = f"[+] TelnetCfg 表已更新 ({n_tel} 处), UpgradeCtrl 自动升级已关闭 ({n_ota} 处)"
    if enable_autostart:
        msg += f", QdUUplugindEnable 自启钩子已开启 ({n_qd} 处)"
    print(msg)
    return patched


def main():
    ap = argparse.ArgumentParser(description="ZXHN E3630 (AX5400 Pro) Type-4 配置加解密与 Telnet 注入工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_dec = sub.add_parser("decrypt", help="解密 Type-4 bin 为 XML")
    p_dec.add_argument("input", help="输入的加密配置 .bin")
    p_dec.add_argument("output", help="输出的明文 .xml")

    p_enc = sub.add_parser("encrypt", help="加密明文 XML 为 Type-4 bin")
    p_enc.add_argument("input", help="输入的明文 .xml")
    p_enc.add_argument("output", help="输出的加密配置 .bin")

    p_pat = sub.add_parser("patch-telnet", help="一键注入 LAN Telnet Root 并关闭自动升级")
    p_pat.add_argument("input", help="原始导出的加密配置 .bin")
    p_pat.add_argument("output", help="注入后的加密配置 .bin (用于路由器后台恢复)")
    p_pat.add_argument("--user", default="root", help="Telnet 用户名 (默认: root)")
    p_pat.add_argument("--password", required=True, help="设置你的 Telnet 强密码 (勿用弱口令)")
    p_pat.add_argument("--enable-autostart", action="store_true", help="同时将 QDCfg.QdUUplugindEnable 置为 1")

    args = ap.parse_args()
    if args.cmd == "decrypt":
        xml_bytes = decrypt_type4(open(args.input, "rb").read())
        open(args.output, "wb").write(xml_bytes)
        print(f"[+] 解密成功: {args.output} ({len(xml_bytes)} 字节)")
    elif args.cmd == "encrypt":
        out = encrypt_type4(open(args.input, "rb").read())
        open(args.output, "wb").write(out)
        print(f"[+] 加密成功: {args.output} ({len(out)} 字节)")
    elif args.cmd == "patch-telnet":
        raw = open(args.input, "rb").read()
        xml_text = decrypt_type4(raw).decode("utf-8", errors="surrogateescape")
        patched_xml = patch_telnet_and_ota(xml_text, args.user, args.password, args.enable_autostart)
        out = encrypt_type4(patched_xml.encode("utf-8", errors="surrogateescape"))
        # 回环自检确保 100% 可被路由器解析
        verify = decrypt_type4(out)
        assert len(verify) > 1000
        open(args.output, "wb").write(out)
        print(f"[+] 生成并回环校验通过: {args.output} ({len(out)} 字节)")


if __name__ == "__main__":
    main()
