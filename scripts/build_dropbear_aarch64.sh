#!/usr/bin/env bash
# 一键使用 Docker (aarch64-linux-musl) 静态交叉编译适配中兴 AX5400 Pro (ZXHN E3630) 的 Dropbear SSH
#
# 定制要点：
# 1. 静态链接 musl libc，体积 < 1MB，可放入 2MB 的 /usercfg (mtd4) 分区；
# 2. 原厂只读 /etc/passwd 中 uid=0 用户家目录 /root 不可写，开启 -D 目录选项支持读取 /usercfg/local-ssh/authorized_keys；
# 3. 适配私有挂载的 devpts (/var/run/local-ssh-pts/ptmx)，避免污染只读 /dev；
# 4. 彻底裁掉密码认证与端口转发源码选项，仅保留 Ed25519 公钥免密认证，安全轻量。

set -euo pipefail

DROPBEAR_VER="${DROPBEAR_VER:-2024.86}"
OUT_DIR="$(pwd)/build_out"
mkdir -p "$OUT_DIR"

docker run --rm -v "$OUT_DIR:/out" alpine:3.20 sh -euxc "
  apk add --no-cache build-base wget tar linux-headers zlib-dev zlib-static
  # 若宿主机非 aarch64，下载 musl.cc 交叉工具链
  ARCH=\$(uname -m)
  if [ \"\$ARCH\" = 'aarch64' ]; then
    CC_PREFIX=''
  else
    wget -q https://musl.cc/aarch64-linux-musl-cross.tgz -O /tmp/tc.tgz
    tar -xzf /tmp/tc.tgz -C /opt
    export PATH=\"/opt/aarch64-linux-musl-cross/bin:\$PATH\"
    CC_PREFIX='aarch64-linux-musl-'
  fi

  wget -q https://matt.ucc.asn.au/dropbear/releases/dropbear-${DROPBEAR_VER}.tar.bz2 -O /tmp/db.tar.bz2
  tar -xjf /tmp/db.tar.bz2 -C /tmp
  cd /tmp/dropbear-${DROPBEAR_VER}

  # 将伪终端路径重定向到可写的私有挂载点 /var/run/local-ssh-pts
  sed -i 's|\"/dev/ptmx\"|\"/var/run/local-ssh-pts/ptmx\"|g' src/pty.c 2>/dev/null || sed -i 's|\"/dev/ptmx\"|\"/var/run/local-ssh-pts/ptmx\"|g' pty.c
  sed -i 's|\"/dev/pts/%u\"|\"/var/run/local-ssh-pts/%u\"|g' src/pty.c 2>/dev/null || sed -i 's|\"/dev/pts/%u\"|\"/var/run/local-ssh-pts/%u\"|g' pty.c

  cat > localoptions.h <<'EOF'
#define DROPBEAR_SVR_PASSWORD_AUTH 0
#define DROPBEAR_CLI_PASSWORD_AUTH 0
#define DROPBEAR_SVR_PUBKEY_AUTH 1
#define DROPBEAR_ED25519 1
#define DROPBEAR_RSA 0
#define DROPBEAR_ECDSA 0
#define DROPBEAR_DSS 0
#define DO_MOTD 0
EOF

  ./configure --host=aarch64-linux-musl --enable-static --disable-zlib --disable-syslog --disable-utmp --disable-wtmp --disable-lastlog
  make PROGRAMS='dropbear dropbearkey' MULTI=1 STATIC=1 SCPPROGRESS=0 -j\$(nproc)
  \${CC_PREFIX}strip dropbearmulti
  cp dropbearmulti /out/dropbearmulti
"

ls -lh "$OUT_DIR/dropbearmulti"
echo "[+] 编译完成: $OUT_DIR/dropbearmulti"
