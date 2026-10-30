#!/bin/sh
# 幂等初始化脚本 (部署路径: /usercfg/init_custom.sh)
# 负责拉起 Dropbear SSH、双路由跨网段互通 (可选)、解压并启动 SmartDNS、透明接管 IPv4/IPv6 DNS
PATH=/sbin:/bin:/usr/bin:/usr/sbin
export PATH

CONF_FILE=/usercfg/local-ssh/lan.conf
LAN_CIDR="192.168.5.0/24"
UPSTREAM_LAN_CIDR=""
if [ -f "$CONF_FILE" ]; then
    _c=$(sed -n 's/^LAN_CIDR=//p' "$CONF_FILE")
    [ -n "$_c" ] && LAN_CIDR="$_c"
    UPSTREAM_LAN_CIDR=$(sed -n 's/^UPSTREAM_LAN_CIDR=//p' "$CONF_FILE")
fi

# 1. 拉起 Dropbear SSH
if [ -x /usercfg/local-ssh/start.sh ]; then
    /usercfg/local-ssh/start.sh >/dev/null 2>&1
fi

# 2. 二级路由跨网段免绕行互通 (仅当在 lan.conf 中配置了 UPSTREAM_LAN_CIDR 时生效，例如 192.168.1.0/24)
if [ -n "$UPSTREAM_LAN_CIDR" ]; then
    echo 1 > /proc/sys/net/ipv4/ip_forward
    echo 0 > /proc/sys/net/ipv4/conf/br0/rp_filter 2>/dev/null
    echo 0 > /proc/sys/net/ipv4/conf/all/rp_filter 2>/dev/null

    iptables -C INPUT -s "$UPSTREAM_LAN_CIDR" -j ACCEPT 2>/dev/null || \
        iptables -I INPUT 1 -s "$UPSTREAM_LAN_CIDR" -j ACCEPT
    iptables -C FORWARD -s "$UPSTREAM_LAN_CIDR" -d "$LAN_CIDR" -j ACCEPT 2>/dev/null || \
        iptables -I FORWARD 1 -s "$UPSTREAM_LAN_CIDR" -d "$LAN_CIDR" -j ACCEPT
    iptables -t nat -C POSTROUTING -s "$UPSTREAM_LAN_CIDR" -o br0 -j MASQUERADE 2>/dev/null || \
        iptables -t nat -I POSTROUTING 1 -s "$UPSTREAM_LAN_CIDR" -o br0 -j MASQUERADE
fi

# 3. 解压 SmartDNS 至内存盘 /tmp (双闪存分区冗余兜底, 日常零磨损 NAND Flash)
if [ ! -x /tmp/smartdns ]; then
    if [ -s /UUPlugin/smartdns.gz ]; then
        gunzip -c /UUPlugin/smartdns.gz > /tmp/smartdns
    elif [ -s /status/smartdns.gz ]; then
        gunzip -c /status/smartdns.gz > /tmp/smartdns
    fi
    chmod +x /tmp/smartdns 2>/dev/null
fi

mkdir -p /var/tmp/smartdns
if [ ! -f /var/tmp/smartdns/anti-ad.conf ]; then
    touch /var/tmp/smartdns/anti-ad.conf
    setsid /usercfg/update_adblock.sh >/dev/null 2>&1 &
fi

# 4. 确保 SmartDNS 存活
if [ -x /tmp/smartdns ] && [ -f /usercfg/smartdns.conf ]; then
    if ! ps | grep '[s]martdns' >/dev/null 2>&1; then
        setsid /tmp/smartdns -c /usercfg/smartdns.conf >/dev/null 2>&1 &
        sleep 1
    fi
fi

# 5. IPv4 53 端口透明劫持至 SmartDNS (:6053) + IPv6 53 端口快速 REJECT 防绕过
if ps | grep '[s]martdns' >/dev/null 2>&1; then
    DNS_ALLOW_SRC="$LAN_CIDR"
    # 若启用了上游一级内网共享 DNS，放宽到 192.168.0.0/16 以便一级有线网设备同样享受 SmartDNS
    [ -n "$UPSTREAM_LAN_CIDR" ] && DNS_ALLOW_SRC="192.168.0.0/16"

    iptables -C INPUT -s "$DNS_ALLOW_SRC" -p udp --dport 6053 -j ACCEPT 2>/dev/null || \
        iptables -I INPUT 1 -s "$DNS_ALLOW_SRC" -p udp --dport 6053 -j ACCEPT
    iptables -C INPUT -s "$DNS_ALLOW_SRC" -p tcp --dport 6053 -j ACCEPT 2>/dev/null || \
        iptables -I INPUT 1 -s "$DNS_ALLOW_SRC" -p tcp --dport 6053 -j ACCEPT
    iptables -t nat -C PREROUTING -s "$DNS_ALLOW_SRC" -p udp --dport 53 -j REDIRECT --to-ports 6053 2>/dev/null || \
        iptables -t nat -I PREROUTING 1 -s "$DNS_ALLOW_SRC" -p udp --dport 53 -j REDIRECT --to-ports 6053
    iptables -t nat -C PREROUTING -s "$DNS_ALLOW_SRC" -p tcp --dport 53 -j REDIRECT --to-ports 6053 2>/dev/null || \
        iptables -t nat -I PREROUTING 1 -s "$DNS_ALLOW_SRC" -p tcp --dport 53 -j REDIRECT --to-ports 6053

    # 固件 ip6tables v1.4.13 无 nat REDIRECT 模块，对 br0 的 IPv6 53 端口快速返回 REJECT，
    # 促使手机/平板在 <0.1ms 内回退至 IPv4 SmartDNS (SmartDNS 仍正常解析并返回 AAAA 记录，不影响 IPv6 访问)
    ip6tables -C INPUT -i br0 -p udp --dport 53 -j REJECT --reject-with icmp6-port-unreachable 2>/dev/null || \
        ip6tables -I INPUT 1 -i br0 -p udp --dport 53 -j REJECT --reject-with icmp6-port-unreachable
    ip6tables -C INPUT -i br0 -p tcp --dport 53 -j REJECT --reject-with tcp-reset 2>/dev/null || \
        ip6tables -I INPUT 1 -i br0 -p tcp --dport 53 -j REJECT --reject-with tcp-reset
    ip6tables -C FORWARD -i br0 -p udp --dport 53 -j REJECT --reject-with icmp6-port-unreachable 2>/dev/null || \
        ip6tables -I FORWARD 1 -i br0 -p udp --dport 53 -j REJECT --reject-with icmp6-port-unreachable
    ip6tables -C FORWARD -i br0 -p tcp --dport 53 -j REJECT --reject-with tcp-reset 2>/dev/null || \
        ip6tables -I FORWARD 1 -i br0 -p tcp --dport 53 -j REJECT --reject-with tcp-reset
fi
