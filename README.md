# BMW CarData — Home Assistant Custom Integration

Native Home Assistant integration that connects directly to the **BMW CarData
streaming MQTT interface** and exposes your vehicle telemetry as sensors — no
separate bridge container or broker required.

It is the integration form of the [dj0abr/bmw-mqtt-bridge](https://github.com/dj0abr/bmw-mqtt-bridge)
project: same BMW protocol (OAuth2 device flow + MQTT v5 over TLS), reimplemented
in Python as a HACS-installable `custom_component`.

## Features

- **UI config flow** with a guided one-time BMW login (device flow).
- Connects straight to `customer.streaming-cardata.bmwgroup.com` — works on **HA
  Container / Core** (no add-ons or Supervisor needed).
- **Automatic token refresh**; re-auth flow if the refresh token is revoked.
- **Dynamic entities**: one sensor per BMW signal, grouped into a device per VIN.

## Requirements

- Home Assistant **2024.10** or newer.
- A BMW **CarData** streaming client (**Client ID** + **GCID**) from your My BMW
  account → CarData. The GCID is shown there as the MQTT *username*.

## Installation (HACS)

1. HACS → **⋮ → Custom repositories**.
2. Add this repository URL, category **Integration**.
3. Install **BMW CarData**, then **restart Home Assistant**.
4. **Settings → Devices & Services → Add Integration → BMW CarData**.

### Manual installation

Copy `custom_components/bmw_cardata/` into your Home Assistant `config/custom_components/`
folder, then restart Home Assistant.

## Setup

1. Start the integration and enter your **Client ID** and **GCID**.
2. A login URL is shown — open it, sign in to your BMW account, and approve.
   The flow completes automatically once you finish (no code to copy).
3. Sensors appear as signals stream in from BMW, under a device named `BMW <VIN>`.

To switch BMW accounts or recover from a revoked token, remove and re-add the
integration (or use the automatic **re-authentication** prompt).

## How it works

| Concern | Implementation |
|---------|----------------|
| Login | OAuth2 **device authorization grant** with PKCE (`bmw_auth.py`) |
| Streaming | `paho-mqtt` v5 over TLS to BMW, subscribing `"<GCID>/+"` (`coordinator.py`) |
| Auth to broker | username = GCID, password = `id_token` (JWT) |
| Token refresh | refresh-token grant ~10 min before `id_token` expiry, then reconnect |
| Entities | one sensor per `data.<signal>.value` in each payload (`sensor.py`) |

## Notes & limitations

- This integration is **community-built and not endorsed by BMW**.
- BMW's signal set varies by vehicle; entities are created from whatever the
  stream sends. Units/device-classes are not inferred and are left generic.
- `paho-mqtt` ships with Home Assistant, so no extra Python requirements are
  installed.

## License

MIT — built on [dj0abr/bmw-mqtt-bridge](https://github.com/dj0abr/bmw-mqtt-bridge).
