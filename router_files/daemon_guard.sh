#!/bin/sh
# 常驻自愈守护进程 (部署路径: /usercfg/daemon_guard.sh)
# 每 30 秒执行一次幂等检查 (SSH / 防火墙规则 / SmartDNS)，每 24 小时自动更新一次 anti-AD 规则
PIDFILE=/var/run/custom_guard.pid

if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
        exit 0
    fi
fi
echo $$ > "$PIDFILE"

COUNTER=0
while true; do
    /usercfg/init_custom.sh >/dev/null 2>&1
    COUNTER=$((COUNTER + 30))
    # 每 24 小时 (86400 秒) 异步拉取最新 anti-AD 去广告规则
    if [ "$COUNTER" -ge 86400 ]; then
        COUNTER=0
        /usercfg/update_adblock.sh >/dev/null 2>&1 &
    fi
    sleep 30
done
