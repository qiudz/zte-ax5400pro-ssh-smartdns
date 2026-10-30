#!/bin/sh
# 自动拉取最新 anti-AD (SmartDNS 格式) 去广告规则并重启 SmartDNS (部署路径: /usercfg/update_adblock.sh)
# 注意：
# 1. 固件无内置 CA 根证书包，curl 需带 -k；
# 2. 规则保存在内存盘 /var/tmp/smartdns/anti-ad.conf (~3MB)，零磨损闪存；
# 3. SmartDNS 未注册 SIGHUP 重载处理器，禁止发 killall -HUP smartdns，须 killall 后通过 setsid 重新拉起。

mkdir -p /var/tmp/smartdns
TMP=/var/tmp/smartdns/anti-ad.conf.tmp
DEST=/var/tmp/smartdns/anti-ad.conf

URL1="https://anti-ad.net/anti-ad-for-smartdns.conf"
URL2="https://raw.githubusercontent.com/privacy-protection-tools/anti-AD/master/anti-ad-for-smartdns.conf"

curl -k -s -L --connect-timeout 10 -m 60 "$URL1" -o "$TMP"
if [ ! -s "$TMP" ] || ! grep -q "^address " "$TMP"; then
    curl -k -s -L --connect-timeout 10 -m 60 "$URL2" -o "$TMP"
fi

if [ -s "$TMP" ] && grep -q "^address " "$TMP"; then
    mv -f "$TMP" "$DEST"
    if [ -x /tmp/smartdns ]; then
        killall smartdns 2>/dev/null
        sleep 1
        setsid /tmp/smartdns -c /usercfg/smartdns.conf >/dev/null 2>&1 &
    fi
else
    rm -f "$TMP"
fi
