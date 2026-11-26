#!/usr/bin/env python3
"""Pi 4 component diagnostics. No GPIO/bus operations in preflight or on import.

Hardware tests require an explicit command, a verified configuration section,
and --wiring-confirmed. This software does not verify electrical safety.
"""
import argparse
from datetime import datetime, timezone
import glob
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import signal
import sys
import time

OUTPUTS = ("relay", "buzzer", "led1", "led2")
INPUTS = ("sw1", "sw2")
COMMANDS = ("preflight", "dht22", "adc", "oled") + OUTPUTS + INPUTS
PACKAGES = ("Adafruit-Blinka", "adafruit-circuitpython-dht",
            "adafruit-circuitpython-ssd1306", "gpiozero", "spidev", "lgpio")


class ConfigurationError(ValueError):
    pass


def integer(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise ConfigurationError(f"{name} must be an integer from {low} to {high}.")
    return value


def finite_number(value, low, high, name):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ConfigurationError(f"{name} must be a finite number from {low} to {high}.")
    return value


def load_config(path):
    try:
        cfg = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ConfigurationError(f"Cannot read {path}: {exc}. Copy hardware.example.json to hardware.json and fill only confirmed mappings.") from exc
    if not isinstance(cfg, dict):
        raise ConfigurationError("Configuration must be a JSON object.")
    return cfg


def validate_profile(cfg, command, acknowledged, output_ack=False):
    """Validate everything before importing a hardware library or claiming a pin."""
    if not acknowledged:
        raise ConfigurationError("Hardware tests need --wiring-confirmed after electrical wiring is independently established. The 3D diagram is not that verification.")
    section = cfg.get(command)
    if not isinstance(section, dict) or section.get("wiring_verified") is not True:
        raise ConfigurationError(f"{command}: wiring_verified is not true. Header positions alone do not establish wiring.")
    if command in OUTPUTS and not output_ack:
        raise ConfigurationError("Output tests need --confirm-output. Keep relay load terminals empty.")
    # Catch a GPIO accidentally assigned to more than one enabled function,
    # including the fixed SPI0/I2C1 pins used by these diagnostics.
    claimed = {}
    for name in COMMANDS[1:]:
        s = cfg.get(name, {})
        if not isinstance(s, dict) or s.get("wiring_verified") is not True:
            continue
        if name == "adc":
            bus = integer(s.get("bus"), 0, 0, "adc.bus (SPI0 only)")
            device = integer(s.get("device"), 0, 1, "adc.device")
            gpio_pins = [9, 10, 11, 8 if device == 0 else 7]
        elif name == "oled":
            gpio_pins = [2, 3]  # Fixed Pi I2C1, not a scan of arbitrary buses.
        else:
            gpio_pins = [integer(s.get("bcm"), 2, 27, f"{name}.bcm")]
        for bcm in gpio_pins:
            if bcm in claimed:
                raise ConfigurationError(f"BCM{bcm} conflict: {claimed[bcm]} and {name}.")
            claimed[bcm] = name
    if command in OUTPUTS:
        if type(section.get("active_high")) is not bool:
            raise ConfigurationError(f"{command}.active_high must be true or false from the actual driver circuit.")
    elif command in INPUTS:
        if type(section.get("pull_up")) is not bool:
            raise ConfigurationError(f"{command}.pull_up must be true or false from the actual switch circuit.")
    elif command == "adc":
        integer(section.get("speed_hz"), 10000, 100000, "adc.speed_hz")
        if section.get("vref") is not None:
            finite_number(section["vref"], 0.1, 5.5, "adc.vref")
        channels = section.get("channels")
        if not isinstance(channels, dict) or not channels:
            raise ConfigurationError("adc.channels must map sensor names to confirmed channel numbers.")
        seen = set()
        for label, channel in channels.items():
            if not isinstance(label, str) or not label.strip():
                raise ConfigurationError("ADC channel labels must be nonempty strings.")
            integer(channel, 0, 7, f"adc.channels.{label}")
            if channel in seen:
                raise ConfigurationError("Assign a different ADC channel to each sensor label.")
            seen.add(channel)
    elif command == "oled":
        addr = section.get("address")
        if not isinstance(addr, str) or addr.lower() not in ("0x3c", "0x3d"):
            raise ConfigurationError("oled.address must be the confirmed SSD1306 address, '0x3c' or '0x3d'. No address scan is performed.")
        integer(section.get("width"), 128, 128, "oled.width")
        if type(section.get("height")) is not int or section["height"] not in (32, 64):
            raise ConfigurationError("oled.height must be 32 or 64.")
    return section


def preflight():
    model_path = Path("/proc/device-tree/model")
    model = model_path.read_bytes().decode(errors="replace").strip("\x00\n") if model_path.exists() else "Not detected (this may not be a Pi)"
    packages = {}
    for name in PACKAGES:
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "not installed / possibly supplied by apt"
    return {"status": "SOFTWARE_CHECK_ONLY", "model": model,
            "python": platform.python_version(), "os": platform.platform(),
            "device_nodes": {name: sorted(glob.glob(name)) for name in ("/dev/i2c-*", "/dev/spidev*", "/dev/gpiochip*")},
            "packages": packages,
            "note": "No GPIO/bus was opened or scanned. Device-node presence does not prove a sensor or trainer works."}


def decode_mcp3008(response):
    if len(response) != 3 or any(type(x) is not int or not 0 <= x <= 255 for x in response):
        raise ValueError("Invalid SPI response length or bytes.")
    return ((response[1] & 3) << 8) | response[2]


def adc_summary(records):
    summary = {}
    for label in records[0]["channels"]:
        values = [row["channels"][label]["raw"] for row in records]
        low, high = min(values), max(values)
        note = "Raw samples collected; this does not establish calibration or sensor identity."
        if low == high and low in (0, 1023):
            note = "INCONCLUSIVE: constant rail value can be real saturation, wiring, power or a disconnected ADC."
        elif low == high:
            note = "INCONCLUSIVE: constant reading; vary only the intended sensor stimulus and compare."
        summary[label] = {"min": low, "max": high, "mean": sum(values) / len(values), "note": note}
    return summary


def run_adc(section, args):
    import spidev
    spi = spidev.SpiDev()
    records = []
    try:
        spi.open(section["bus"], section["device"])
        spi.max_speed_hz = section["speed_hz"]
        spi.mode = 0
        for index in range(args.samples):
            row = {"sample": index + 1, "channels": {}}
            for label, channel in section["channels"].items():
                raw = decode_mcp3008(spi.xfer2([1, (8 + channel) << 4, 0]))
                reading = {"channel": channel, "raw": raw}
                if section.get("vref") is not None:
                    reading["estimated_adc_volts"] = round(raw * section["vref"] / 1024, 4)
                row["channels"][label] = reading
            records.append(row)
            print(json.dumps(row), flush=True)
            if index + 1 < args.samples:
                time.sleep(args.interval)
    finally:
        spi.close()
    return {"status": "RAW_SAMPLES_ONLY", "samples": records, "summary": adc_summary(records),
            "note": "SPI performs transfers; it is not a passive voltage test. No ppm, AQI, lux or gas safety judgement is calculated. Do not release gas or use a flame for testing."}


def run_dht(section, args):
    import board
    import adafruit_dht
    device = adafruit_dht.DHT22(getattr(board, f"D{section['bcm']}"), use_pulseio=False)
    records, failures = [], []
    try:
        for index in range(args.samples):
            try:
                temp, humidity = device.temperature, device.humidity
                if temp is None or humidity is None or not math.isfinite(temp) or not math.isfinite(humidity) or not -40 <= temp <= 80 or not 0 <= humidity <= 100:
                    raise RuntimeError("Missing or out-of-range DHT22 reading.")
                row = {"sample": index + 1, "temperature_c": temp, "humidity_percent": humidity}
                records.append(row)
                print(json.dumps(row), flush=True)
            except RuntimeError as exc:
                failures.append(str(exc))
                print(f"DHT retry {index + 1}/{args.samples}: {exc}", flush=True)
            if index + 1 < args.samples:
                time.sleep(max(2.2, args.interval))
    finally:
        device.exit()
    return {"status": "READINGS_RECEIVED" if records else "NO_VALID_READINGS",
            "samples": records, "read_errors": failures,
            "note": "Valid data is evidence of communication, not a calibration or wiring-safety certificate. DHT communication drives its data pin during the request."}


def run_oled(section, args):
    import board
    import busio
    import adafruit_ssd1306
    bus = busio.I2C(board.SCL, board.SDA)
    display = None
    try:
        display = adafruit_ssd1306.SSD1306_I2C(section["width"], section["height"], bus, addr=int(section["address"], 16))
        display.fill(0)
        display.rect(0, 0, section["width"], section["height"], 1)
        for x in range(8, section["width"] - 8, 8):
            display.vline(x, 6, section["height"] - 12, 1)
        display.show()
        print("Observe OLED: border + vertical stripes. Pattern clears after the observation interval.", flush=True)
        time.sleep(args.seconds)
    finally:
        try:
            if display is not None:
                display.fill(0)
                display.show()
        finally:
            bus.deinit()
    return {"status": "OBSERVATION_REQUIRED", "note": "Display commands completed. Confirm that the physical OLED actually showed the pattern; an I2C transaction alone is not visual verification."}


def run_output(section, args):
    from gpiozero import DigitalOutputDevice
    device = DigitalOutputDevice(section["bcm"], active_high=section["active_high"], initial_value=False)
    try:
        print(f"{args.command}: activating driver for {args.pulse_seconds:.2f}s, then returning inactive.", flush=True)
        device.on()
        time.sleep(args.pulse_seconds)
    finally:
        try:
            device.off()
        finally:
            device.close()
    return {"status": "OBSERVATION_REQUIRED", "note": "Observe the physical response. Relay contacts must remain unloaded. Releasing a GPIO does not guarantee the driver remains inactive; its hardware bias must be known. A passive buzzer may only click with this DC pulse."}


def run_input(section, args):
    from gpiozero import Button
    records = []
    button = Button(section["bcm"], pull_up=section["pull_up"], bounce_time=0.03)
    try:
        print("Press and release the selected switch during this interval.", flush=True)
        until = time.monotonic() + args.seconds
        previous = None
        while time.monotonic() < until:
            state = bool(button.is_pressed)
            if state != previous:
                row = {"elapsed_s": round(args.seconds - (until - time.monotonic()), 3), "pressed": state}
                records.append(row)
                print(json.dumps(row), flush=True)
                previous = state
            time.sleep(0.02)
    finally:
        button.close()
    return {"status": "TRANSITIONS_RECEIVED" if len(records) > 1 else "INCONCLUSIVE",
            "transitions": records, "note": "Polarity/pull configuration must match the physical circuit."}


def parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", nargs="?", choices=COMMANDS, default="preflight")
    p.add_argument("--config", type=Path, default=Path(__file__).with_name("hardware.json"))
    p.add_argument("--wiring-confirmed", action="store_true")
    p.add_argument("--confirm-output", action="store_true")
    p.add_argument("--samples", type=int, default=5)
    p.add_argument("--interval", type=float, default=2.2)
    p.add_argument("--seconds", type=float, default=8, help="OLED/switch observation time, 1–30 seconds")
    p.add_argument("--pulse-seconds", type=float, default=0.5, help="Output pulse, 0.05–2 seconds")
    p.add_argument("--report", type=Path, default=Path("diagnostic-report.json"))
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    result = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "command": args.command,
              "electrical_safety_verified_by_script": False}
    code = 0
    try:
        integer(args.samples, 1, 60, "--samples")
        finite_number(args.interval, 0.1, 30, "--interval")
        finite_number(args.seconds, 1, 30, "--seconds")
        finite_number(args.pulse_seconds, 0.05, 2, "--pulse-seconds")
        if args.command == "preflight":
            result.update(preflight())
        else:
            cfg = load_config(args.config)
            section = validate_profile(cfg, args.command, args.wiring_confirmed, args.confirm_output)
            result["settings_used"] = section
            model = Path("/proc/device-tree/model")
            if not model.exists() or "Raspberry Pi" not in model.read_bytes().decode(errors="replace"):
                raise ConfigurationError("Hardware tests must run on the Raspberry Pi. Use preflight elsewhere.")
            if args.command == "adc":
                result.update(run_adc(section, args))
            elif args.command == "dht22":
                result.update(run_dht(section, args))
            elif args.command == "oled":
                result.update(run_oled(section, args))
            elif args.command in OUTPUTS:
                result.update(run_output(section, args))
            else:
                result.update(run_input(section, args))
            if result["status"] == "NO_VALID_READINGS":
                code = 1
    except ConfigurationError as exc:
        result.update(status="BLOCKED_CONFIGURATION", error=str(exc))
        code = 2
    except ImportError as exc:
        result.update(status="MISSING_DEPENDENCY", error=str(exc), hint="Activate the venv and install requirements.txt; see README.md.")
        code = 1
    except KeyboardInterrupt:
        result.update(status="INTERRUPTED", note="Cleanup attempted; confirm outputs are physically inactive.")
        code = 130
    except Exception as exc:
        result.update(status="TEST_ERROR", error=f"{type(exc).__name__}: {exc}",
                      hint="Check permissions, enabled interfaces and the verified wiring. Do not change pins or voltage by trial and error.")
        code = 1
    try:
        args.report.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        print(f"\nReport: {args.report.resolve()}")
    except OSError as exc:
        print(f"Cannot save report: {exc}", file=sys.stderr)
        code = code or 1
    print(json.dumps(result, indent=2, allow_nan=False))
    return code


def interrupt(_signum, _frame):
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, interrupt)
    raise SystemExit(main())
