# Ship24 Package Tracker for Home Assistant

Track your packages directly in Home Assistant using the [Ship24](https://ship24.com) API.
Import your Ship24 trackers into Home Assistant and poll them safely by Ship24 tracker ID.

[![GitHub Release](https://img.shields.io/github/v/release/szajbergyerek/ha-ship24?style=flat-square)](https://github.com/szajbergyerek/ha-ship24/releases)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg?style=flat-square)](https://github.com/hacs/integration)
[![CI](https://img.shields.io/github/actions/workflow/status/szajbergyerek/ha-ship24/ci.yaml?label=CI&style=flat-square)](https://github.com/szajbergyerek/ha-ship24/actions/workflows/ci.yaml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg?style=flat-square)](LICENSE)

---

## Features

- Track unlimited packages via Ship24's universal tracking API (supports 1,400+ carriers)
- Each package becomes its own **sensor entity** with human-readable status
- **`sensor.ship24_package_summary`**: a single sensor whose state is a complete spoken summary of all packages, ready for voice assistants
- Sensor attributes: tracking number, courier, last event, last location, ETA, full event history (last 10 events)
- **Voice assistant ready**: ask Assist, Google Home, or Alexa about your packages without saying a tracking number
- **Dashboard cards**: works with Entities, Glance, Mushroom, and any custom card
- **Services** to add and remove packages dynamically from automations
- **Friendly names**: name your packages (e.g. "Amazon Order") for display and voice
- Polling interval: 1 hour. Refreshes are read-only and use Ship24 `trackerId`, so reloading Home Assistant does not create duplicate Ship24 trackers.

---

## Free Tier

The Ship24 free tier API is more than enough for personal use. It covers 1,400+ carriers worldwide, including all major postal services, couriers, and e-commerce platforms. You do not need a paid plan to track your everyday packages at home.

Get your API key at [dashboard.ship24.com/integrations/api-keys](https://dashboard.ship24.com/integrations/api-keys).

---

## Installation via HACS

1. Open HACS in Home Assistant
2. Go to **Integrations**, click the three-dot menu in the top right corner
3. Select **Custom repositories**
4. Add URL: `https://github.com/szajbergyerek/ha-ship24`, set Category to **Integration**
5. Search for **Ship24** and click **Download**
6. Restart Home Assistant

---

## Setup

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Ship24**
3. Enter your **Ship24 API key**; the setup wizard validates it automatically

A free tier is available at [ship24.com/pricing](https://ship24.com/pricing).

---

## Adding Packages

### Via the Ship24 website

The easiest way to manage your packages is through the [Ship24 website](https://ship24.com). You can add tracking numbers, set custom names, and monitor statuses there directly. The integration will automatically pick up any trackers associated with your API key on the next poll.

### Via the UI

**Settings → Devices & Services → Ship24 → Configure**

Enter one package per line. Optionally add a friendly name after a colon:

```
1Z999AA10123456784:Amazon Order
RR123456789CN:AliExpress Shoes
JD014600000000
```

Click **Submit**. Home Assistant reloads and creates sensor entities for each package.
The friendly name appears in the UI and is used by voice assistants instead of the long tracking number.
Configured tracking numbers are matched to existing Ship24 trackers during read-only polling. Use the service below when you want Home Assistant to create a new Ship24 tracker.

### Via Service Call

Add a package from an automation or Developer Tools:

```yaml
service: ship24.add_package
data:
  tracking_number: "1Z999AA10123456784"
  friendly_name: "Amazon Order"   # optional
```

`ship24.add_package` first checks the Ship24 account for an existing tracker with that tracking number. If one exists, Home Assistant imports it and keeps the existing Ship24 metadata such as courier, destination postcode, countries, and dashboard title. If none exists, the service creates one with `POST /trackers` and then polls future updates by the returned `trackerId`.

Remove a package:

```yaml
service: ship24.remove_package
data:
  tracking_number: "1Z999AA10123456784"
```

---

## Voice Assistant

### HA Assist (built-in)

Add this to your `configuration.yaml` and restart HA:

```yaml
intent_script:
  WhereAreMyPackages:
    speech:
      text: "{{ states('sensor.ship24_package_summary') }}"

conversation:
  intents:
    WhereAreMyPackages:
      - "where are my packages"
      - "where is my package"
      - "what is the status of my packages"
      - "track my packages"
```

Example response:
> *"You have 3 packages. Amazon Order is In Transit, last seen in Frankfurt, estimated delivery 2024-03-16. AliExpress Shoes is Delivered. Package JD014... is Pending."*

### TTS Morning Briefing

```yaml
automation:
  - alias: "Morning package briefing"
    trigger:
      - platform: time
        at: "08:00:00"
    condition:
      - condition: template
        value_template: >
          {{ states('sensor.ship24_package_summary') != 'You have no tracked packages.' }}
    action:
      - service: tts.speak
        data:
          message: "{{ states('sensor.ship24_package_summary') }}"
```

### Delivery Notification

```yaml
automation:
  - alias: "Notify on delivery"
    trigger:
      - platform: state
        entity_id: sensor.amazon_order
        to: "Delivered"
    action:
      - service: notify.mobile_app
        data:
          message: >
            {{ state_attr('sensor.amazon_order', 'friendly_name') or 'Your package' }}
            has been delivered!
```

---

## Dashboard

### Glance Card

```yaml
type: glance
title: Packages
entities:
  - entity: sensor.amazon_order
    name: Amazon
  - entity: sensor.aliexpress_shoes
    name: AliExpress
```

### Entities Card with Summary

```yaml
type: entities
title: Package Tracker
entities:
  - sensor.ship24_package_summary
  - sensor.amazon_order
  - sensor.aliexpress_shoes
```

### Mushroom Template Card (HACS)

```yaml
type: custom:mushroom-template-card
primary: "{{ state_attr('sensor.amazon_order', 'friendly_name') }}"
secondary: "{{ states('sensor.amazon_order') }}"
icon: >
  {% set s = state_attr('sensor.amazon_order', 'status_code') %}
  {% if s == 'delivered' %} mdi:package-variant-closed-check
  {% elif s == 'in_transit' %} mdi:truck-fast
  {% elif s == 'out_for_delivery' %} mdi:truck-delivery
  {% else %} mdi:package-variant-closed {% endif %}
icon_color: >
  {% if is_state('sensor.amazon_order', 'Delivered') %} green
  {% elif is_state('sensor.amazon_order', 'In Transit') %} blue
  {% elif is_state('sensor.amazon_order', 'Exception') %} red
  {% else %} grey {% endif %}
```

---

## Sensor Reference

### `sensor.ship24_package_summary`

| Attribute | Example |
|-----------|---------|
| **State** | `You have 2 packages. Amazon Order is In Transit...` |
| `spoken_summary` | Full summary text (no 255-char limit) |
| `package_count` | `2` |

### Per-package sensor

Each tracked package creates a sensor whose device name equals the friendly name (or tracking number).

| Attribute | Example |
|-----------|---------|
| **State** | `In Transit` |
| `tracker_id` | `26148317-7502-d3ac-44a9-546d240ac0dd` |
| `tracking_number` | `1Z999AA10123456784` |
| `friendly_name` | `Amazon Order` |
| `title` | `Nike shoes for Marc` |
| `client_tracker_id` | `order-4242` |
| `shipment_reference` | `DF14R2022` |
| `status_code` | `in_transit` |
| `courier` | `ups` |
| `destination_post_code` | `08008` |
| `last_event` | `Departed facility` |
| `last_event_time` | `2024-03-15T14:30:00.000Z` |
| `last_location` | `Frankfurt, DE` |
| `estimated_delivery` | `2024-03-16T00:00:00.000Z` |
| `origin_country` | `US` |
| `destination_country` | `DE` |
| `events` | List of last 10 events |

### Status Values

| Status Code | Display |
|-------------|---------|
| `pending` | Pending |
| `info_received` | Info Received |
| `in_transit` | In Transit |
| `out_for_delivery` | Out for Delivery |
| `failed_attempt` | Failed Delivery Attempt |
| `available_for_pickup` | Available for Pickup |
| `delivered` | Delivered |
| `exception` | Exception |
| `expired` | Expired |

---

## Requirements

- Home Assistant 2023.1.0+
- Ship24 API key ([free tier available](https://ship24.com/pricing))

---

## Contributing

Pull requests are welcome!
Please open an issue first to discuss significant changes.
