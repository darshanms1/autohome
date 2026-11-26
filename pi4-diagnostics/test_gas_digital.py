#!/usr/bin/env python3
"""Bounded D0 input observation. Default: dry run; never controls heater power."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import signal
import time

RESERVED = {2, 3, 4, 7, 8, 9, 10, 11, 16, 20}


def validate(config):
    if not isinstance(config, dict):
        raise ValueError('Configuration must be an object.')
    for name in ('power_path_verified', 'signal_route_verified', 'divider_installed'):
        if config.get(name) is not True:
            raise ValueError(f'{name} is not confirmed. No GPIO will be opened.')
    bcm = config.get('bcm')
    if type(bcm) is not int or not 2 <= bcm <= 27 or bcm in RESERVED:
        raise ValueError('Select a traced unused BCM GPIO, excluding the existing I2C, DHT, SPI and reserved output pins.')
    if config.get('divider_resistors_ohms') != [4700, 4700]:
        raise ValueError('This test plan requires two 4.7 kOhm resistors in the established divider.')
    order = config.get('physical_pin_order')
    if not isinstance(order, list) or len(order) != 4 or set(order) != {'A0', 'D0', 'GND', 'VCC'}:
        raise ValueError('The actual four-post function order has not been recorded.')
    return bcm


def observe(bcm, seconds):
    from gpiozero import DigitalInputDevice
    device = DigitalInputDevice(bcm, pull_up=None, active_state=True)
    start = time.monotonic()
    changes = []
    high = low = 0
    previous = None
    try:
        while time.monotonic() - start < seconds:
            level = int(device.value)
            high += level
            low += 1 - level
            if level != previous:
                row = {'elapsed_s': round(time.monotonic() - start, 3), 'raw_level': level}
                changes.append(row)
                print(json.dumps(row), flush=True)
                previous = level
            time.sleep(.05)
    finally:
        device.close()
    return {'status': 'INPUT_OBSERVED', 'transitions': changes,
            'high_samples': high, 'low_samples': low,
            'interpretation': 'Raw D0 logic only. A static level does not demonstrate gas response; polarity, threshold, heater operation, calibration and sensitivity are not established by this recording.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path(__file__).with_name('gas-digital.example.json'))
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--wiring-confirmed', action='store_true')
    parser.add_argument('--seconds', type=int, choices=range(5, 61), default=15, metavar='5..60')
    parser.add_argument('--report', type=Path, default=Path('gas-digital-report.json'))
    args = parser.parse_args(argv)
    result = {'timestamp_utc': datetime.now(timezone.utc).isoformat(),
              'electrical_safety_verified_by_script': False,
              'heater_power_controlled': False}
    code = 0
    try:
        config = json.loads(args.config.read_text(encoding='utf-8-sig'))
        if not args.run:
            result.update(status='DRY_RUN_NO_GPIO_ACCESS',
                          pending=[k for k in ('power_path_verified', 'signal_route_verified', 'divider_installed') if config.get(k) is not True],
                          note='Preparation only. No gas power connection or physical post order is assumed. Heater power and GPIO signal wiring must be established before --run.')
        else:
            if not args.wiring_confirmed:
                raise ValueError('--wiring-confirmed is required after the circuit is established.')
            bcm = validate(config)
            result['bcm'] = bcm
            result.update(observe(bcm, args.seconds))
    except KeyboardInterrupt:
        result.update(status='INTERRUPTED');code = 130
    except Exception as exc:
        result.update(status='BLOCKED_OR_ERROR', error=f'{type(exc).__name__}: {exc}');code = 1
    args.report.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))
    return code


def interrupt(_signum, _frame):
    raise KeyboardInterrupt


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, interrupt)
    raise SystemExit(main())
