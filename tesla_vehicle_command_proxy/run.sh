#!/bin/sh
set -eu

CONFIG_DIR=/config/tesla_vehicle_command
CERT_FILE="$CONFIG_DIR/proxy-cert.pem"
TLS_KEY_FILE="$CONFIG_DIR/proxy-key.pem"
COMMAND_KEY_FILE="$CONFIG_DIR/proxy-command-key.pem"
proxy_pid=""

shutdown() {
    if [ -n "$proxy_pid" ]; then
        kill -TERM "$proxy_pid" 2>/dev/null || true
        wait "$proxy_pid" 2>/dev/null || true
    fi
    exit 0
}

trap shutdown INT TERM

while [ ! -r "$CERT_FILE" ] || [ ! -r "$TLS_KEY_FILE" ] || [ ! -r "$COMMAND_KEY_FILE" ]; do
    echo "Waiting for Tesla Vehicle Command integration files..."
    sleep 2
done

export TESLA_VERBOSE=true

while true; do
    /usr/local/bin/tesla-http-proxy \
        -host 0.0.0.0 \
        -port 4443 \
        -cert "$CERT_FILE" \
        -tls-key "$TLS_KEY_FILE" \
        -key-file "$COMMAND_KEY_FILE" &
    proxy_pid=$!
    if wait "$proxy_pid"; then
        proxy_pid=""
        exit 0
    fi
    proxy_pid=""
    echo "Tesla Vehicle Command proxy exited; retrying in 2 seconds..."
    sleep 2
done
