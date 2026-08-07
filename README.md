# Tesla Pulse

Home Assistant custom integration that receives Tesla vehicle state through Fleet Telemetry and sends signed commands through the Tesla Fleet API.

Internal integration domain and service names remain `tesla_vehicle_command` for backward compatibility.

Tesla command authentication has four distinct parts:

1. Tesla OAuth lets Home Assistant access the account.
2. Tesla partner registration associates the Tesla developer app with a public domain and public command key.
3. A command key pair lets a vehicle authenticate commands end to end.
4. Tesla's `tesla-http-proxy` converts the Fleet API calls made by this integration into authenticated vehicle commands.

This guide configures all four parts using the Home Assistant Add-on Store, a minimal public-key server, and Cloudflare Tunnel. Replace every value in angle brackets with values from your own deployment.

## Navigation

- [Before You Begin](#before-you-begin)
- [Security Model](#security-model)
- [1. Create the Tesla Developer App](#1-create-the-tesla-developer-app)
- [2. Generate the Command Key Pair](#2-generate-the-command-key-pair)
- [3. Add the Packaged Add-ons](#3-add-the-packaged-add-ons)
- [4. Route the Key Server Through Cloudflare Tunnel](#4-route-the-key-server-through-cloudflare-tunnel)
- [5. Register the Tesla Partner Account](#5-register-the-tesla-partner-account)
- [6. Start the Tesla Vehicle Command Proxy](#6-start-the-tesla-vehicle-command-proxy)
- [7. Install and Configure the Integration](#7-install-and-configure-the-integration)
- [8. Pair the Existing Command Key With the Vehicle](#8-pair-the-existing-command-key-with-the-vehicle)
- [9. Enable Fleet Telemetry](#9-enable-fleet-telemetry)
- [Startup State Restoration](#startup-state-restoration)
- [Verify the Installation](#verify-the-installation)
- [Battery Health Sensors](#battery-health-sensors)
- [PEM and Add-on Diagnostics](#pem-and-add-on-diagnostics)
- [Optional Proxy API Diagnostics](#optional-proxy-api-diagnostics)
- [Metric Units](#metric-units)
- [Versioning](#versioning)
- [Troubleshooting](#troubleshooting)
- [References](#references)

## Before You Begin

You need:

- Home Assistant OS or Supervised, with access to a Terminal/SSH add-on.
- A Tesla account and an eligible Tesla vehicle.
- A Tesla Developer account at <https://developer.tesla.com>.
- A domain managed by Cloudflare.
- An HTTPS external URL for Home Assistant, such as `https://<ha-hostname>.<your-domain>`.
- A second hostname dedicated to Tesla's public key, such as `https://<tesla-key-hostname>.<your-domain>`.
- Cloudflare Tunnel connected to the Home Assistant network.

Do not reuse the Home Assistant hostname as the Tesla key hostname. The Tesla key endpoint must be public and unauthenticated, while Home Assistant should retain its normal authentication.

## Security Model

Only one file is public: the command **public** key at Tesla's required `.well-known` URL. It cannot be used to control a vehicle.

Never publish, upload, or paste a command **private** key, Tesla Client Secret, OAuth token, or Home Assistant configuration directory into a web server, Cloudflare Tunnel configuration, Git repository, issue report, or chat.

The local command proxy has no published host port. Do not add it to Cloudflare Tunnel or configure router port forwarding for it.

## 1. Create the Tesla Developer App

1. Open <https://developer.tesla.com> and create an **App**.
2. Add this redirect URI exactly:

   ```text
   https://<ha-hostname>.<your-domain>/auth/external/callback
   ```

3. Add this allowed origin exactly:

   ```text
   https://<tesla-key-hostname>.<your-domain>
   ```

4. Enable these scopes:

   ```text
   openid
   offline_access
   vehicle_device_data
   vehicle_cmds
   vehicle_charging_cmds
  vehicle_location
   ```

5. Save the **Client ID** and **Client Secret**. They are entered into Home Assistant later.

The redirect URI and allowed origin are separate values. The redirect URI points to Home Assistant; the allowed origin matches the public-key hostname exactly.

## 2. Generate the Command Key Pair

Run these commands in the Home Assistant Terminal/SSH add-on. They generate a NIST P-256 key pair compatible with Tesla vehicle command authentication.

```sh
mkdir -p /config/tesla_vehicle_command/keys /config/tesla_public
chmod 700 /config/tesla_vehicle_command/keys

openssl ecparam -name prime256v1 -genkey -noout \
  -out /config/tesla_vehicle_command/keys/tesla-command-private-key.pem
openssl ec -in /config/tesla_vehicle_command/keys/tesla-command-private-key.pem \
  -pubout \
  -out /config/tesla_public/com.tesla.3p.public-key.pem

chmod 600 /config/tesla_vehicle_command/keys/tesla-command-private-key.pem
chmod 755 /config/tesla_public
chmod 644 /config/tesla_public/com.tesla.3p.public-key.pem
```

The files have different purposes:

```text
/config/tesla_vehicle_command/keys/tesla-command-private-key.pem  Secret; import into the integration.
/config/tesla_public/com.tesla.3p.public-key.pem                  Public; serve this one file to Tesla.
```

Show the public PEM that Nginx will serve. Do not print, paste, or share the private PEM:

```sh
cat /config/tesla_public/com.tesla.3p.public-key.pem
```

Check that the hosted public PEM was derived from the private key without showing private-key material:

```sh
openssl ec -in /config/tesla_vehicle_command/keys/tesla-command-private-key.pem \
  -pubout \
  | diff - /config/tesla_public/com.tesla.3p.public-key.pem
```

`diff` returns no output when the public keys match.

Do not generate another key later. The public key hosted for Tesla, the private key imported into Home Assistant, and the key paired with the vehicle must all be the same key pair.

## 3. Add the Packaged Add-ons

This repository is both a HACS integration repository and a Home Assistant add-on repository. In Home Assistant, open **Settings > Add-ons > Add-on store**, select the overflow menu, then choose **Repositories**. Add:

```text
https://github.com/expansing/Tesla-Pulse
```

After the repository loads, **do not install the add-ons from the Add-on Store UI**. Instead, create the add-on configuration files manually on disk through the terminal (or SSH add-on). This ensures the add-ons use the correct local paths and certificates generated by the integration.

For each add-on below, create the directory and `config.yaml` file:

### 3.1 Tesla Public Key Server

```sh
mkdir -p /config/addons/tesla_public_key_server
cat > /config/addons/tesla_public_key_server/config.yaml << 'EOF'
name: "Tesla Public Key Server"
version: "1.0.0"
slug: "tesla_public_key_server"
description: "Serves Tesla public key for vehicle command enrollment"
url: "https://github.com/expansing/Tesla-Pulse/tree/main/tesla_key_server"
arch:
  - "aarch64"
  - "amd64"
startup: "services"
boot: "auto"
ports:
  8099/tcp: 8099
map:
  - "config:rw"
  - "ssl:rw"
options: {}
schema: {}
image: "ghcr.io/expansing/tesla-public-key-server:latest"
EOF
```

### 3.2 Tesla Vehicle Command Proxy

```sh
mkdir -p /config/addons/tesla_vehicle_command_proxy
cat > /config/addons/tesla_vehicle_command_proxy/config.yaml << 'EOF'
name: "Tesla Vehicle Command Proxy"
version: "1.0.0"
slug: "tesla_vehicle_command_proxy"
description: "Tesla vehicle command proxy for Fleet API"
url: "https://github.com/expansing/Tesla-Pulse/tree/main/tesla_vehicle_command_proxy"
arch:
  - "aarch64"
  - "amd64"
startup: "services"
boot: "auto"
ports:
  4443/tcp: 4443
map:
  - "config:rw"
  - "ssl:rw"
options: {}
schema: {}
image: "ghcr.io/expansing/tesla-vehicle-command-proxy:latest"
EOF
```

### 3.3 Tesla Fleet Telemetry Receiver (only when enabling telemetry in step 9)

```sh
mkdir -p /config/addons/tesla_fleet_telemetry_receiver
cat > /config/addons/tesla_fleet_telemetry_receiver/config.yaml << 'EOF'
name: "Tesla Fleet Telemetry Receiver"
version: "1.0.0"
slug: "tesla_fleet_telemetry_receiver"
description: "Receives Fleet Telemetry mTLS stream from Tesla vehicles"
url: "https://github.com/expansing/Tesla-Pulse/tree/main/tesla_vehicle_command_telemetry"
arch:
  - "aarch64"
  - "amd64"
startup: "services"
boot: "auto"
ports:
  4443/tcp: 4443
  5284/tcp: 5284
map:
  - "config:rw"
  - "ssl:rw"
options: {}
schema: {}
image: "ghcr.io/expansing/tesla-fleet-telemetry-receiver:latest"
EOF
```

After creating these files, reload the Add-on Store (**Settings > Add-ons > Add-on store > overflow menu > Reload**). The three add-ons will appear under **Local add-ons**. Install each one and enable **Start on boot**.

Start the public-key server after the command key from step 2 exists. It serves only `/config/tesla_public/com.tesla.3p.public-key.pem`; no add-on files need to be copied or created manually.

Confirm the server exposes only the intended public key:

```sh
curl -i http://<home-assistant-lan-ip>:8099/.well-known/appspecific/com.tesla.3p.public-key.pem
curl -i http://<home-assistant-lan-ip>:8099/
```

The first response must be `200` and start with `-----BEGIN PUBLIC KEY-----`. The second response must be `404`.

## 4. Route the Key Server Through Cloudflare Tunnel

In Cloudflare Zero Trust:

1. Go to **Networks > Tunnels** and select the tunnel that can reach the Home Assistant network.
2. Add a public hostname: `<tesla-key-hostname>.<your-domain>`.
3. Set service type to `HTTP`.
4. Set the service URL to:

   ```text
   http://<home-assistant-lan-ip>:8099
   ```

   Use the Home Assistant host's LAN address. `localhost` works only when `cloudflared` runs in the same network namespace as the Nginx server.

5. Do not configure Cloudflare Access, login, redirects, or any authentication policy for this hostname.

Verify from outside the home network, for example using mobile data:

```sh
curl -i --max-time 10 \
  https://<tesla-key-hostname>.<your-domain>/.well-known/appspecific/com.tesla.3p.public-key.pem
```

It must return the public PEM directly with HTTP `200`. A Cloudflare error page, `524` timeout, redirect, or login page will prevent Tesla registration and vehicle pairing.

## 5. Register the Tesla Partner Account

Tesla Fleet API partner registration is regional. Use the region associated with your Tesla account:

```text
Europe:        https://fleet-api.prd.eu.vn.cloud.tesla.com
North America: https://fleet-api.prd.na.vn.cloud.tesla.com
```

Obtain a partner token in a trusted terminal. Set `FLEET_API_BASE_URL` to the regional endpoint selected above.

```sh
CLIENT_ID='<Tesla Client ID>'
CLIENT_SECRET='<Tesla Client Secret>'
FLEET_API_BASE_URL='https://fleet-api.prd.eu.vn.cloud.tesla.com'

PARTNER_TOKEN="$(
  curl -sS --request POST \
    --header 'Content-Type: application/x-www-form-urlencoded' \
    --data-urlencode 'grant_type=client_credentials' \
    --data-urlencode "client_id=$CLIENT_ID" \
    --data-urlencode "client_secret=$CLIENT_SECRET" \
    --data-urlencode 'scope=openid vehicle_device_data vehicle_cmds vehicle_charging_cmds' \
    --data-urlencode "audience=$FLEET_API_BASE_URL" \
    'https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token' \
  | jq -r '.access_token'
)"
```

Register the public-key hostname. Do not include `https://` or a path.

```sh
curl -i --request POST \
  --header "Authorization: Bearer $PARTNER_TOKEN" \
  --header 'Content-Type: application/json' \
  --data '{"domain":"<tesla-key-hostname>.<your-domain>"}' \
  "$FLEET_API_BASE_URL/api/1/partner_accounts"
```

Tesla must be able to retrieve the public key from the hostname during registration. Keep the key service available permanently: Tesla may retrieve it again for key pairing or command validation.

## 6. Start the Tesla Vehicle Command Proxy

Start **Tesla Vehicle Command Proxy** from the Add-on Store after installing it in step 3. It runs Tesla's official `tesla/vehicle-command` image and needs no configuration.

The first log messages may say:

```text
Waiting for Tesla Vehicle Command integration files...
```

This is expected until the integration writes its local TLS material and command-key copy. The add-on is internal only. Never expose port `4443` through Cloudflare Tunnel, a reverse proxy, or router port forwarding.

## 7. Install and Configure the Integration

### Install

Install through HACS using this repository. HACS installs the integration only; the three add-ons are installed separately from the Add-on Store in step 3.

For unsupported manual installation, copy the integration files to:

```text
/config/custom_components/tesla_vehicle_command/
```

When installing manually, copy the contents of this repository's
`custom_components/tesla_vehicle_command/` directory into that destination.
The `brand/` and `translations/` directories are required and must be copied
with the Python modules.

Restart Home Assistant after installation or updates.

### Configure

1. Open **Settings > Devices & services > Add integration**.
2. Select **Tesla Pulse**.
3. Enter the Tesla Developer App Client ID and Client Secret.
4. Complete Tesla OAuth in the browser.
5. Select the vehicle or vehicles to add.
6. Choose **Import** and paste the complete content of:

   ```text
   /config/tesla_vehicle_command/keys/tesla-command-private-key.pem
   ```

   Include both PEM delimiters. A correct key begins with `-----BEGIN EC PRIVATE KEY-----` or `-----BEGIN PRIVATE KEY-----` and ends with the matching five-dash `END` line.

The integration creates the following private internal proxy files automatically:

```text
/config/tesla_vehicle_command/proxy-cert.pem
/config/tesla_vehicle_command/proxy-key.pem
/config/tesla_vehicle_command/proxy-ca.pem
/config/tesla_vehicle_command/proxy-command-key.pem
```

Do not edit these generated files. The integration and local proxy use the Supervisor DNS hostname `local-tesla-vehicle-command-proxy` internally.

### Startup State Restoration

Tesla Pulse saves the most recently received telemetry state locally. After a Home Assistant restart or an integration reload, it restores those cached entity states before waiting for new Fleet Telemetry records. This keeps the vehicle asleep while preserving the last known sensor values.

By default, Tesla Pulse does not wake vehicles or request fresh Fleet API data during startup. To opt in, open **Settings > Devices & services > Tesla Pulse > Configure** and enable **Wake vehicle and refresh from API on startup**.

When enabled, Tesla Pulse restores the cache first, then wakes every configured vehicle and requests fresh vehicle data. This can increase Fleet API traffic and wake the vehicle, so leave it disabled when preserving sleep is more important than immediately refreshed data.

Tesla Pulse also exposes a **Vehicle Awake Status** diagnostic sensor. A vehicle is `Awake` while Fleet Telemetry frames are arriving and becomes `Asleep` after the configured **Minutes without telemetry before vehicle is asleep** interval.

Tesla Pulse requires Fleet Telemetry to wake and confirm a sleeping vehicle
before sending a command. A command is sent directly when the vehicle has sent
recent telemetry and no later `DISCONNECTED` connectivity record reports a lost
Wi-Fi or LTE link. Otherwise, it follows this sequence:

1. Tesla Pulse records the current telemetry generation and sends a wake-up request.
2. It waits for a `CONNECTED`/`ONLINE` record or vehicle telemetry frame, confirming that the vehicle is awake.
3. The original command is sent only after that confirmation.

While this sequence runs, Tesla Pulse disables controls for that vehicle and
rejects additional commands for the same vehicle instead of queuing them. If no
telemetry frame arrives within 90 seconds after wake-up, the command fails with
an explicit timeout instead of being sent prematurely.

After Tesla accepts a command with a deterministic result, Tesla Pulse updates
the corresponding entity state immediately instead of making another Fleet API
request. This includes locks, Sentry Mode, charge port, climate controls,
temperature and charge-limit values, heaters, windows, and trunks. A later
matching telemetry signal confirms that state; after a bounded confirmation
window of 30 seconds, explicit telemetry can correct it. This allows a manual
change made inside the vehicle to update Home Assistant promptly. The rear trunk
remains telemetry-only because Tesla exposes it as a toggle rather than an
explicit open/close command. Transient or conditional actions such as horn,
lights, navigation, charging transitions, and media controls remain
telemetry-only.

Advanced rear-left and rear-right powertrain diagnostics are disabled by default because many vehicles do not expose those channels. Tesla Pulse also evaluates explicit optional telemetry groups across completed awake sessions. After at least two sessions containing three or more vehicle telemetry frames without a valid group signal, it disables the corresponding REL/RER or Powershare entities. A valid signal re-enables only entities that Tesla Pulse disabled itself; manually disabled entities are unchanged. Tonneau, sunroof, and rear-display HVAC capability observations are retained for future entity support, but have no current entity to enable or disable. Tesla `<INVALID>` values are not used as evidence of absent hardware.

## 8. Pair the Existing Command Key With the Vehicle

With the vehicle online and the Tesla mobile app signed in, open:

```text
https://tesla.com/_ak/<tesla-key-hostname>.<your-domain>?vin=<vehicle-vin>
```

Approve the enrollment prompt in the Tesla mobile app. The domain in this URL must exactly match the Tesla Developer App allowed origin and the hostname serving the public PEM.

If the vehicle reports that the public key is not paired, do not generate a replacement private key. Confirm the public PEM served at the `.well-known` endpoint was derived from the same private key imported in step 7, then repeat the enrollment link with the vehicle awake and online.

## 9. Enable Fleet Telemetry

Fleet Telemetry is required for vehicle state. The receiver accepts the vehicle's mTLS stream and publishes decoded records on a private ZMQ endpoint.

> [!IMPORTANT]
> By default, the integration restores entity state from Home Assistant's local telemetry cache and then updates it only with incoming Fleet Telemetry records. If **Wake vehicle and refresh from API on startup** is enabled, it also requests fresh vehicle data after waking each configured vehicle during startup. Until the receiver or optional API refresh has delivered a field, its corresponding entity has no state.

### Dummy Example

This example uses these placeholder values. Replace them with your own public DNS hostname, forwarded TCP port, and vehicle VIN:

```text
Fleet Telemetry hostname: telemetry.example.net
Fleet Telemetry port:     4443
Vehicle VIN:              5YJ3E1EA0PF000000
```

1. Install **Tesla Fleet Telemetry Receiver** from this repository in **Settings > Add-ons > Add-on store**. Enable **Start on boot**, but do not start it yet.
2. Open **Settings > Devices & services**, select **Tesla Pulse**, then select **Configure**. Enter `telemetry.example.net` as **Fleet Telemetry hostname** and `4443` as **Fleet Telemetry port**. Submit the options and reload the integration.
3. The integration generates a dedicated telemetry CA and server certificate under `/config/tesla_vehicle_command/`. These are separate from the command proxy certificates. Confirm the files exist from the Home Assistant Terminal/SSH add-on:

  ```sh
  ls -l /config/tesla_vehicle_command/telemetry-ca.pem \
    /config/tesla_vehicle_command/telemetry-cert.pem \
    /config/tesla_vehicle_command/telemetry-key.pem
  ```

4. Create a DNS record for `telemetry.example.net` in your Cloudflare zone that resolves to the public IP address for the Home Assistant network. Set the record to **DNS only** (grey cloud), then configure the router or firewall to forward public TCP port `4443` to the Home Assistant host's TCP port `4443`.

### Using an Existing Cloudflare Tunnel

Continue using the existing Cloudflare Tunnel for the public-key server in step 4. A standard Cloudflare Tunnel cannot carry the Fleet Telemetry connection: Tesla opens raw mTLS TCP and cannot run the `cloudflared` client or complete a Cloudflare Access TCP login.

For telemetry, use one of these routes:

1. **Cloudflare DNS only:** Use the DNS record and TCP port forwarding from step 4 above. This is the usual home-network setup. The telemetry hostname remains in Cloudflare DNS, but traffic does not traverse the tunnel or Cloudflare proxy.
2. **Cloudflare Spectrum:** If your Cloudflare plan includes Spectrum, create a TCP Spectrum application for `telemetry.example.net:4443` with the Tesla Fleet Telemetry Receiver as its origin. Do not add Cloudflare Access or HTTP/TLS termination in front of the receiver. Spectrum setup is account-specific and may still require the receiver origin to be reachable from Cloudflare.
3. **No inbound home-network port:** This integration cannot receive telemetry directly. Use a public receiver and an authenticated outbound bridge to Home Assistant; that bridge is not implemented in this integration.

Tesla must reach the receiver without a browser login, redirect, HTTP reverse proxy, Cloudflare Access policy, or TLS termination. The receiver validates the connection using the dedicated telemetry CA generated by this integration.

5. Start **Tesla Fleet Telemetry Receiver**. Its log should show that the receiver started successfully. The add-on publishes only TCP `4443`; its ZMQ endpoint on port `5284` is private and must not be forwarded or exposed.
6. In **Developer tools > Actions**, run `tesla_vehicle_command.configure_fleet_telemetry`. Enter the dummy VIN `5YJ3E1EA0PF000000` only as a format example; use your actual 17-character VIN when running the action.
7. Check the action result and receiver logs. A successful registration allows Tesla to connect to `telemetry.example.net:4443` with the CA certificate generated in step 3. Connection and record activity should appear in the receiver log once the vehicle sends telemetry.

To change the hostname, port, or telemetry CA, update the integration options and reload the integration. Re-run `tesla_vehicle_command.configure_fleet_telemetry` for every configured VIN after any such change.

### Estimated battery state of health

Tesla Pulse estimates current usable battery capacity from `Soc` and `EnergyRemaining` values received together in Fleet Telemetry. It does not read a battery-health value directly from Tesla.

#### How the calculation works

1. **Start a window.** Tesla Pulse saves a same-record pair of SOC and remaining energy, such as 68% and 44.88 kWh.
2. **Wait for a useful change.** It waits until a later same-record pair moves at least 20 SOC percentage points in the same direction. If the car starts charging, then starts discharging, or there is a gap longer than six hours, that window is discarded and a new one starts. This avoids comparing unrelated battery states.
3. **Calculate usable capacity.** It divides the energy change by the SOC change, then scales it to 100%:

  $$
  \\text{usable capacity (kWh)} = \\frac{|\\Delta \\text{EnergyRemaining}|}{|\\Delta \\text{SOC}|} \\times 100
  $$

  For example, if energy rises from 44.88 kWh at 68% to 57.78 kWh at 88%:

  $$
  \\frac{|57.78 - 44.88|}{|88 - 68|} \\times 100 = \\frac{12.90}{20} \\times 100 = 64.5\\ \\text{kWh}
  $$

  The accepted end of this window becomes the next start, so accepted windows do not overlap.

4. **Combine recent windows.** Tesla Pulse retains up to the newest 12 valid windows and uses their median as **Usable Capacity**. The median is the middle value after sorting the window estimates, so one unusually high or low window has less influence than it would on an average.

To calculate **Battery SOH**, Tesla Pulse compares **Usable Capacity** with the **Original usable capacity** entered in integration options:

$$
\\text{Battery SOH (\\%)} = \\frac{\\text{estimated usable capacity}}{\\text{original usable capacity when new}} \\times 100
$$

For example, a 64.5 kWh estimate and an original usable capacity of 73.5 kWh gives:

$$
\\frac{64.5}{73.5} \\times 100 = 87.8\\%
$$

**SOH Confidence** is the total SOC span across the retained valid windows, capped at 100%. A 20% confidence means one accepted 20-point window; it does not mean the result is 20% accurate or inaccurate. Confidence reflects the amount of recent accepted data, not elapsed time. As more valid charge or discharge windows are collected, the median becomes less sensitive to temperature, balancing, rounding, and BMS recalibration. Once more than 12 windows exist, the oldest window is removed when a new one is accepted, so the estimate follows sustained long-term change instead of being fixed forever.

Open the integration options and enter each vehicle's **Original usable capacity** in kWh to enable the **Battery SOH** sensor. Enter the usable battery capacity when new, not the pack's nominal nameplate capacity. Leaving the value blank disables only **Battery SOH**; **Usable Capacity** and **SOH Confidence** remain available after enough telemetry has been collected.

The result is an estimate, not a Tesla BMS or service-mode measurement. Temperature, battery balancing, hidden buffers, and BMS recalibration can affect it. Confidence reaches 100% after accepted estimation windows cover a cumulative 100 SOC percentage points.

When Recorder is enabled and no estimate has been saved yet, Tesla Pulse scans up to 30 days of existing Battery Level and Energy Remaining history. It imports only source states recorded within two seconds of each other, preserving the same-update requirement. If the retained history does not contain at least one valid 20-point SOC window, the sensors remain unavailable until enough new telemetry arrives.

The **Usable Capacity** sensor includes `accepted_window_count` and `accepted_windows` diagnostic attributes. Each accepted window records its start and end SOC, start and end energy, energy delta, calculated capacity, timestamps, and whether it came from live telemetry or Recorder history. Older estimates created before this diagnostic data was added remain usable but show blank endpoint fields.

Do not reuse the command-proxy certificate for telemetry, publish the ZMQ endpoint, or place the telemetry private key in a tunnel, reverse proxy, Git repository, or support request.

## Verify the Installation

1. The proxy add-on log includes `Listening on 0.0.0.0:4443`.
2. The Fleet Telemetry Receiver add-on log shows a vehicle connection and decoded `V` records.
3. In **Settings > Devices & services**, the vehicle's **Telemetry Status** diagnostic sensor changes to `receiving`.
4. Check its `received_fields`, `processed_fields`, and `unprocessed_fields` attributes. A state sensor is populated only after its source field first arrives.
5. Test a harmless command, such as flashing lights, only after the public key is paired.

## Battery Health Sensors

The integration provides battery health diagnostics derived from Fleet Telemetry:

| Sensor | Unit | Description |
|--------|------|-------------|
| **Brick Voltage Max** | mV | Highest individual brick voltage in the pack |
| **Brick Voltage Min** | mV | Lowest individual brick voltage in the pack |
| **Brick Voltage Imbalance** | mV | Difference between max and min brick voltage (ΔV) |
| **Battery Balance Score** | % | SOC-aware health score (0–100%) |
| **Usable Capacity** | kWh | Median usable-capacity estimate from same-record SOC and remaining-energy windows |
| **Battery SOH** | % | Estimated usable capacity divided by the configured usable capacity when new |
| **SOH Confidence** | % | Cumulative accepted SOC span, capped at 100% |

> **Note:** These sensors require **Fleet Telemetry** to be configured and running. The `BrickVoltageMax` and `BrickVoltageMin` fields are only available via telemetry signals, not from the Fleet API `vehicle_data` endpoint. They will show as unavailable until telemetry data is received.

Tesla Pulse updates brick diagnostics only when `BrickVoltageMax`,
`BrickVoltageMin`, `NumBrickVoltageMax`, and `NumBrickVoltageMin` arrive in the
same decoded telemetry record. It does not combine fields from separate
records; the last valid brick maximum, minimum, indices, and imbalance remain
visible until a new complete snapshot replaces them.

### Battery Balance Score

The **Battery Balance Score** adjusts its thresholds based on State of Charge (SOC), since cell imbalance naturally varies across the discharge curve:

| SOC Range | Excellent (100%) | Good (85%) | Watch (70%) | Warning (55%) |
|-----------|------------------|------------|-------------|---------------|
| **≥ 90%** | ≤ 10 mV | ≤ 20 mV | ≤ 30 mV | > 30 mV |
| **50–89%** | ≤ 20 mV | ≤ 30 mV | ≤ 50 mV | > 50 mV |
| **< 50%** | ≤ 40 mV | ≤ 80 mV | ≤ 120 mV | > 120 mV |

This avoids false alarms at low SOC where the voltage curve steepens and small capacity differences produce larger voltage spreads. The score uses the last valid same-record imbalance and the most recent valid SOC.

All battery health sensors are categorized as **Diagnostic** and appear under the vehicle device in **Settings > Devices & services**.

## PEM and Add-on Diagnostics

List the files used by the key server and proxy add-on:

```sh
ls -l /config/tesla_public/com.tesla.3p.public-key.pem
ls -l /config/tesla_vehicle_command
```

Confirm the local Nginx add-on returns the public PEM after it starts:

```sh
curl --fail --show-error \
  http://<home-assistant-host>:8099/.well-known/appspecific/com.tesla.3p.public-key.pem
```

Confirm the public Cloudflare hostname returns the same file. The response must be HTTP `200` and must not show a Cloudflare Access page or redirect:

```sh
curl --fail --show-error --location \
  https://<tesla-key-hostname>.<your-domain>/.well-known/appspecific/com.tesla.3p.public-key.pem
```

## Optional Proxy API Diagnostics

These examples call the internal Tesla proxy directly from a Home Assistant terminal that can reach the Supervisor network. They are useful for diagnostics; normal day-to-day control should use the Home Assistant entities and services.

Set these values in the terminal. Use a short-lived Tesla OAuth access token and do not save it in shell history, scripts, or screenshots.

```sh
PROXY_URL='https://local-tesla-vehicle-command-proxy:4443'
CA_FILE='/config/tesla_vehicle_command/proxy-ca.pem'
ACCESS_TOKEN='<Tesla OAuth access token>'
VIN='<17-character vehicle VIN>'

tesla_get() {
  curl --fail-with-body --cacert "$CA_FILE" \
    --header "Authorization: Bearer $ACCESS_TOKEN" \
    "$PROXY_URL$1"
}

tesla_post() {
  curl --fail-with-body --cacert "$CA_FILE" \
    --header "Authorization: Bearer $ACCESS_TOKEN" \
    --header 'Content-Type: application/json' \
    --data "$2" \
    "$PROXY_URL$1"
}
```

If the terminal does not resolve `local-tesla-vehicle-command-proxy`, run the commands from a terminal add-on on the Home Assistant host or use the proxy add-on container's Supervisor-network address. Do not expose port `4443` to make terminal access easier.

Wake an asleep vehicle. It may take up to a minute before the vehicle accepts another command:

```sh
tesla_post "/api/1/vehicles/$VIN/wake_up" '{}'
```

Lock or unlock the vehicle:

```sh
tesla_post "/api/1/vehicles/$VIN/command/door_lock" '{}'
tesla_post "/api/1/vehicles/$VIN/command/door_unlock" '{}'
```

Flash lights or honk the horn:

```sh
tesla_post "/api/1/vehicles/$VIN/command/flash_lights" '{}'
tesla_post "/api/1/vehicles/$VIN/command/honk_horn" '{}'
```

Start or stop climate conditioning:

```sh
tesla_post "/api/1/vehicles/$VIN/command/auto_conditioning_start" '{}'
tesla_post "/api/1/vehicles/$VIN/command/auto_conditioning_stop" '{}'
```

Set both cabin temperature targets in Celsius:

```sh
tesla_post "/api/1/vehicles/$VIN/command/set_temps" \
  '{"driver_temp":21,"passenger_temp":21}'
```

Start or stop charging, or set the charge limit:

```sh
tesla_post "/api/1/vehicles/$VIN/command/charge_start" '{}'
tesla_post "/api/1/vehicles/$VIN/command/charge_stop" '{}'
tesla_post "/api/1/vehicles/$VIN/command/set_charge_limit" '{"percent":80}'
```

Open the charge port, rear trunk, or front trunk:

```sh
tesla_post "/api/1/vehicles/$VIN/command/charge_port_door_open" '{}'
tesla_post "/api/1/vehicles/$VIN/command/actuate_trunk" '{"which_trunk":"rear"}'
tesla_post "/api/1/vehicles/$VIN/command/actuate_trunk" '{"which_trunk":"front"}'
```

An HTTP `408` means the vehicle is asleep or offline. An error saying that the public key is not paired means the matching public key has not yet been enrolled with the vehicle.

## Metric Units

The integration reports values in metric units:

- Distance and range: `km`
- Speed: `km/h`
- Temperature: `°C`
- Tire pressure: `bar`
- Charge power: `kW`
- Added energy: `kWh`

## Versioning

The integration version shown by Home Assistant comes from this field:

```text
custom_components/tesla_vehicle_command/manifest.json  -> version
```

Set this value to the GitHub release number without the `v` prefix, then publish the matching Git tag and release, such as `v0.6.0`.

The HACS display name comes from:

```text
hacs.json  -> name
```

The proxy and telemetry add-ons are independent and use these fields:

```text
tesla_vehicle_command_proxy/config.yaml  -> version
tesla_vehicle_command_telemetry/config.yaml  -> version
```

Increase an add-on version whenever its Docker image, startup script, or configuration changes. Add-on versions do not need to match the integration release version.

## Troubleshooting

| Symptom | Resolution |
| --- | --- |
| `412 partner account must be registered` | Register the partner account using the correct Fleet API region and key hostname. |
| Cloudflare `524` or login page at the key URL | Fix the Tunnel origin; remove Cloudflare Access and redirects from the key hostname. |
| Proxy waits for integration files | Start or reload the integration and ensure the proxy add-on has `config:ro` mapping. |
| `invalid private key: expected PEM encoding` | Import the complete original P-256 private PEM, including both five-dash delimiter lines. |
| `certificate signed by unknown authority` from Fleet API | Rebuild the proxy add-on from this repository; its image installs `ca-certificates`. |
| `public key has not been paired with the vehicle` | Serve the public key matching the imported private key and repeat the Tesla enrollment link. |
| `408 vehicle unavailable` | The vehicle is offline or asleep. Wake it in the Tesla app and retry. |
| Proxy TLS `EOF` messages | Reload the integration and restart the proxy add-on after the integration has generated its TLS files. |

## References

- [Tesla Fleet API documentation](https://developer.tesla.com/docs/fleet-api)
- [Tesla partner endpoints](https://developer.tesla.com/docs/fleet-api/endpoints/partner-endpoints)
- [Tesla partner tokens](https://developer.tesla.com/docs/fleet-api/authentication/partner-tokens)
- [Tesla virtual-key developer guide](https://developer.tesla.com/docs/fleet-api/virtual-keys/developer-guide)
- [Tesla vehicle-command SDK](https://github.com/teslamotors/vehicle-command)
