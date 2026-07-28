#!/bin/sh
set -eu

CONFIG_DIR="/config/tesla_vehicle_command"

CERT_FILE="${CONFIG_DIR}/telemetry-cert.pem"
KEY_FILE="${CONFIG_DIR}/telemetry-key.pem"
CA_FILE="${CONFIG_DIR}/telemetry-ca.pem"

CONFIG_FILE="/tmp/fleet-telemetry-config.json"

OPTIONS="/data/options.json"

RECEIVER_PID=""

shutdown() {

    echo "Stopping Tesla Fleet Telemetry..."

    if [ -n "${RECEIVER_PID}" ]; then
        kill -TERM "${RECEIVER_PID}" 2>/dev/null || true
        wait "${RECEIVER_PID}" 2>/dev/null || true
    fi

    exit 0
}

trap shutdown INT TERM

echo "Waiting for certificates..."

while [ ! -r "${CERT_FILE}" ] || \
      [ ! -r "${KEY_FILE}" ] || \
      [ ! -r "${CA_FILE}" ]; do
    sleep 2
done

echo "Certificates found."

LOG_LEVEL="info"
JSON_LOG_ENABLE="true"
LOG_V_MESSAGES="false"

if [ -f "${OPTIONS}" ]; then
    LOG_LEVEL=$(jq -r '.log_level // "info"' "${OPTIONS}")
    JSON_LOG_ENABLE=$(jq -r '.json_log_enable // true' "${OPTIONS}")
    LOG_V_MESSAGES=$(jq -r '.log_v_messages // false' "${OPTIONS}")
fi

if [ "${LOG_V_MESSAGES}" = "true" ]; then
    echo "Vehicle logging enabled."
    V_RECORD='"V": ["zmq","logger"],'
else
    echo "Vehicle logging disabled."
    V_RECORD='"V": ["zmq"],'
fi

cat > "${CONFIG_FILE}" <<EOF
{
  "host":"0.0.0.0",
  "port":4443,
  "status_port":8080,

  "log_level":"${LOG_LEVEL}",
  "json_log_enable":${JSON_LOG_ENABLE},

  "namespace":"tesla_telemetry",

  "transmit_decoded_records":true,

  "rate_limit":{
    "enabled":true,
    "message_interval_time":30,
    "message_limit":1000
  },

  "records":{
    ${V_RECORD}
    "alerts":["logger"],
    "errors":["logger"],
    "connectivity":["zmq","logger"]
  },

  "zmq":{
    "addr":"tcp://*:5284"
  },

  "tls":{
    "server_cert":"${CERT_FILE}",
    "server_key":"${KEY_FILE}",
    "ca_file":"${CA_FILE}"
  }
}
EOF

echo
echo "======================================================"
echo "Tesla Fleet Telemetry Receiver"
echo "======================================================"
echo "Binary:"
ls -lh /fleet-telemetry
echo
echo "Configuration:"
echo "Log level        : ${LOG_LEVEL}"
echo "JSON logs        : ${JSON_LOG_ENABLE}"
echo "Vehicle logging  : ${LOG_V_MESSAGES}"
echo "======================================================"
echo

while true
do

    echo "Starting Fleet Telemetry..."

    /fleet-telemetry -config "${CONFIG_FILE}" &

    RECEIVER_PID=$!

    if wait "${RECEIVER_PID}"
    then
        echo "Fleet Telemetry exited normally."
        exit 0
    fi

    EXIT_CODE=$?

    RECEIVER_PID=""

    echo "Fleet Telemetry exited with code ${EXIT_CODE}"

    echo "Restarting in 2 seconds..."

    sleep 2

done