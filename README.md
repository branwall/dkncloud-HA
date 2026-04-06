# DKN Cloud NA for Home Assistant

A Home Assistant custom integration for HVAC systems using the [DKN Cloud NA](https://dkncloudna.com) platform (e.g., Daikin units with DKN Cloud WiFi adapters like AZAI6WSCDKA).

## Features

- **Climate control** — power on/off, HVAC modes (heat, cool, auto, fan, dry), target temperature, fan speed, and swing/louver control
- **Exterior temperature sensor** — reads the outdoor temperature reported by the unit
- **Real-time updates** via socket.io push from the DKN Cloud NA service
- **Config flow** — set up entirely through the Home Assistant UI

## Requirements

- A DKN Cloud WiFi Adapter (e.g., AZAI6WSCDKA) connected to your HVAC unit
- A DKN Cloud NA account (the same credentials you use in the DKN Cloud NA mobile app)

## Installation

### HACS (Recommended)

1. Add this repository as a custom repository in HACS
2. Search for "DKN Cloud NA" and install
3. Restart Home Assistant

### Manual

1. Copy the `custom_components/dkncloudna` folder into your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant

## Configuration

1. Go to **Settings** → **Devices & Services** → **Add Integration**
2. Search for **DKN Cloud NA**
3. Enter your DKN Cloud NA email and password
4. Your HVAC devices will be automatically discovered

## Supported Controls

| Feature | Details |
|---|---|
| HVAC Modes | Off, Heat, Cool, Heat/Cool (Auto), Fan Only, Dry |
| Fan Modes | Auto, Low, Medium Low, Medium, Medium High, High |
| Swing | On / Off (vertical louver) |
| Temperature | Setpoint control per mode |
| Exterior Temp | Outdoor temperature sensor |

## Credits

Based on the API reverse-engineering from [homebridge-dkncloudna](https://github.com/plecong/homebridge-dkncloudna) by Phuong LeCong.
