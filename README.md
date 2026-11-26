# AutoHome Smart Home

AutoHome is a Raspberry Pi 4 sensor demo with an OLED display and a local Windows dashboard.

## Components

- `dashboard.py` reads the Pi's live status over SSH, serves the dashboard on this computer, and records samples in `sensor_readings.csv`.
- The dashboard logs temperature alerts above 27 °C, repeats reminders every five minutes until the reading is 27 °C or lower, and records trigger, reminder, and clear events in `sensor_alerts.csv`.
- Gas status comes from the MQ-2 module's digital threshold output. It is not a calibrated concentration measurement or a certified safety alarm.
- `pi4-diagnostics/` contains the Pi OLED demo, its systemd service, and component diagnostic utilities.

## Run the local dashboard

1. Copy `dashboard.local.example.json` to `dashboard.local.json` and set the Pi's SSH host, user, and project directory. This local file is ignored by Git.
2. In PowerShell, run `.\run_dashboard.ps1` from this folder.
3. Enter the Pi's SSH password when prompted. The password is used in memory only.
4. Open `http://127.0.0.1:8765/` if the browser does not open automatically. Select **Enable desktop alerts** and allow notifications.

Keep the PowerShell process running while you want live updates and logging. The dashboard polls the Pi every two seconds. CSV files are local data outputs and are excluded from Git.

## Raspberry Pi files

`pi4-diagnostics/oled_demo.py` is the live OLED, DHT22, and gas-input program. `autohome-oled@.service` is a systemd template for boot startup. Copy the Pi files to `/home/<user>/autohome`, create the virtual environment there, then enable it with `sudo systemctl enable --now autohome-oled@<user>.service`.

The diagnostic CLI defaults to safe preflight behavior. Use its hardware tests only with independently established wiring and safe voltage levels. The example hardware configuration deliberately leaves mappings unverified.

## Repository contents and exclusions

This repository contains source code, the service unit, the OLED font asset, dependency lists, and an example hardware configuration. It excludes live sensor readings, alert history, local connection settings, virtual environments, and the project handover note.
