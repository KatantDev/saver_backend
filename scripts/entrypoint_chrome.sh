#!/bin/bash

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

log "Installing redsocks..."
apt-get update && apt-get install -y redsocks iptables

PROXY_STRING=$(echo $SAVER_BACKEND_PROXIES_RU | sed "s/\[//g" | sed "s/\]//g" | sed "s/'//g" | cut -d',' -f1 | xargs)

if [ -n "$PROXY_STRING" ]; then

    # Извлекаем компоненты прокси
    PROXY_TYPE=$(echo $PROXY_STRING | cut -d':' -f1)
    PROXY_AUTH=$(echo $PROXY_STRING | cut -d'/' -f3 | cut -d'@' -f1)
    PROXY_HOST=$(echo $PROXY_STRING | cut -d'@' -f2 | cut -d':' -f1)
    PROXY_PORT=$(echo $PROXY_STRING | cut -d':' -f4 | cut -d'/' -f1)
    PROXY_USER=$(echo $PROXY_AUTH | cut -d':' -f1)
    PROXY_PASS=$(echo $PROXY_AUTH | cut -d':' -f2)

    log "type: $PROXY_TYPE Proxy host: $PROXY_HOST, port: $PROXY_PORT, user: $PROXY_USER"

cat > /tmp/redsocks.conf << EOF
base {
    log_debug = off;
    log_info = on;
    daemon = off;
    redirector = iptables;
}

redsocks {
    local_ip = 127.0.0.1;
    local_port = 12345;
    ip = ${PROXY_HOST};
    port = ${PROXY_PORT};
    type = socks5;
    login = "${PROXY_USER}";
    password = "${PROXY_PASS}";
}
EOF

iptables -t nat -N PROXY
iptables -t nat -A PROXY -d 0.0.0.0/8 -j RETURN
iptables -t nat -A PROXY -d 10.0.0.0/8 -j RETURN
iptables -t nat -A PROXY -d 127.0.0.0/8 -j RETURN
iptables -t nat -A PROXY -d 169.254.0.0/16 -j RETURN
iptables -t nat -A PROXY -d 172.16.0.0/12 -j RETURN
iptables -t nat -A PROXY -d 192.168.0.0/16 -j RETURN
iptables -t nat -A PROXY -d 224.0.0.0/4 -j RETURN
iptables -t nat -A PROXY -d 240.0.0.0/4 -j RETURN


iptables -t nat -A PROXY -d ${PROXY_HOST} -j RETURN
iptables -t nat -A PROXY -p tcp --dport 12345 -j RETURN
iptables -t nat -A PROXY -p tcp --sport 12345 -j RETURN

iptables -t nat -A PROXY -p tcp -j REDIRECT --to-ports 12345

iptables -t nat -A OUTPUT -p tcp -j PROXY
log "IPTables configured"

log "Starting redsocks..."
redsocks -c /tmp/redsocks.conf &
REDSOCKS_PID=$!

fi



socat -v TCP-LISTEN:9222,fork,reuseaddr TCP:localhost:9223 > /dev/null 2>&1 &
echo '=== Starting Chrome ==='
/headless-shell/headless-shell --no-sandbox --disable-gpu --remote-debugging-address=0.0.0.0 --remote-debugging-port=9223 --remote-allow-origins='*'
