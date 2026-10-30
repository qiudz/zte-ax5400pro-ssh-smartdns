#!/bin/sh
# Dropbear SSH 安全启动脚本 (部署路径: /usercfg/local-ssh/start.sh)
# 仅放行 lan.conf 中指定的局域网网段，并严格封禁 IPv6 入站 22 端口防止公网暴露
PATH=/sbin:/bin:/usr/bin:/usr/sbin
export PATH

BASE=/usercfg/local-ssh
PTYDIR=/var/run/local-ssh-pts

[ -f "$BASE/lan.conf" ] || exit 1
LAN_IP=$(sed -n 's/^LAN_IP=//p' "$BASE/lan.conf")
LAN_CIDR=$(sed -n 's/^LAN_CIDR=//p' "$BASE/lan.conf")
WAN_STATIC_IP=$(sed -n 's/^WAN_STATIC_IP=//p' "$BASE/lan.conf")
UPSTREAM_LAN_CIDR=$(sed -n 's/^UPSTREAM_LAN_CIDR=//p' "$BASE/lan.conf")

case "$LAN_IP" in ''|*[!0-9.]*) exit 1;; esac
case "$LAN_CIDR" in ''|*[!0-9./]*) exit 1;; esac

COUNT=0
while ! ip -4 addr show dev br0 2>/dev/null | grep -Fq "inet $LAN_IP/"; do
    COUNT=$((COUNT + 1))
    [ "$COUNT" -ge 15 ] && exit 1
    sleep 1
done

[ -s "$BASE/authorized_keys" ] || exit 1
[ -s "$BASE/host_ed25519" ] || exit 1

mkdir -p "$PTYDIR" || exit 1
if ! grep -q " $PTYDIR devpts " /proc/mounts; then
    mount -t devpts -o newinstance,ptmxmode=0600,mode=0600 devpts "$PTYDIR" || exit 1
fi
chmod 700 "$PTYDIR" || exit 1

# 构建局域网白名单防火墙链，阻断一切公网 / IPv6 对 22 端口的访问
iptables -N E3630_SSH_LAN 2>/dev/null
iptables -F E3630_SSH_LAN
iptables -A E3630_SSH_LAN -i br0 -s "$LAN_CIDR" -j RETURN
if [ -n "$UPSTREAM_LAN_CIDR" ]; then
    iptables -A E3630_SSH_LAN -i nbif0 -s "$UPSTREAM_LAN_CIDR" -j RETURN
fi
iptables -A E3630_SSH_LAN -j DROP

iptables -C INPUT -p tcp --dport 22 -j E3630_SSH_LAN 2>/dev/null || \
    iptables -I INPUT 1 -p tcp --dport 22 -j E3630_SSH_LAN
ip6tables -C INPUT -p tcp --dport 22 -j DROP 2>/dev/null || \
    iptables -I INPUT 1 -p tcp --dport 22 -j DROP

if [ -s /var/run/local-ssh.pid ]; then
    PID=$(cat /var/run/local-ssh.pid)
    case "$PID" in ''|*[!0-9]*) ;; *)
        if kill -0 "$PID" 2>/dev/null && grep -aq "$BASE/dropbear" "/proc/$PID/cmdline"; then
            exit 0
        fi
    ;; esac
fi

BIND_ARGS="-p $LAN_IP:22"
if [ -n "$WAN_STATIC_IP" ] && ip -4 addr show dev nbif0 2>/dev/null | grep -Fq "inet $WAN_STATIC_IP/"; then
    BIND_ARGS="$BIND_ARGS -p $WAN_STATIC_IP:22"
fi

"$BASE/dropbear" $BIND_ARGS -D "$BASE" -r "$BASE/host_ed25519" \
    -P /var/run/local-ssh.pid -j -k -K 30 -I 900 >>/var/log/local-ssh.log 2>&1
