"""Live OLED demo using DHT22 GPIO4 and divided gas D0 GPIO17."""
import json
import fcntl
import math
from pathlib import Path
import signal
import time

import adafruit_dht
import adafruit_ssd1306
import board
import busio
from gpiozero import DigitalInputDevice

BASE = Path(__file__).resolve().parent
FONT = (BASE / 'font5x8.bin').read_bytes()
STOP = False


def stop(_signum, _frame):
    global STOP
    STOP = True


def text(display, value, x, y, scale=1):
    for index, char in enumerate(value):
        offset = 2 + ord(char) * FONT[0]
        for column in range(FONT[0]):
            bits = FONT[offset + column]
            for row in range(FONT[1]):
                if bits & (1 << row):
                    display.fill_rect(x + (index * 6 + column) * scale,
                                      y + row * scale, scale, scale, 1)


def draw(display, temperature, humidity, stale, active, tick):
    display.fill(0)
    text(display, 'AUTOHOME', 3, 1)
    text(display, 'DEMO', 96, 1)
    display.hline(0, 11, 128, 1)
    text(display, 'TEMP C', 3, 15)
    text(display, 'RH %', 87, 15)
    value = '--.-' if temperature is None or stale else f'{temperature:.1f}'
    text(display, value, 3, 26, 2)
    text(display, '--' if humidity is None or stale else f'{humidity:.0f}', 87, 26, 2)
    display.hline(0, 44, 128, 1)
    text(display, 'GAS INPUT', 3, 48)
    text(display, 'ACTIVE' if active else 'IDLE', 86 if not active else 80, 48)
    text(display, 'DHT WAIT' if temperature is None else 'DHT STALE' if stale else 'LIVE', 3, 57)
    text(display, '(o_o)' if active else '(^_^)', 92, 57)
    if tick % 2 == 0:
        display.fill_rect(119, 16, 3, 3, 1)
    display.show()


def main():
    instance_lock = (BASE / 'oled-demo.lock').open('a')
    fcntl.flock(instance_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    bus = busio.I2C(board.SCL, board.SDA)
    sensor = gas = None
    temperature = humidity = last_valid = None
    last_error = None
    next_read = 0
    tick = 0
    try:
        display = adafruit_ssd1306.SSD1306_I2C(128, 64, bus, addr=0x3c)
        sensor = adafruit_dht.DHT22(board.D4, use_pulseio=False)
        gas = DigitalInputDevice(17, pull_up=None, active_state=True)
        while not STOP:
            now = time.monotonic()
            if now >= next_read:
                next_read = now + 3
                try:
                    current_temp = sensor.temperature
                    current_rh = sensor.humidity
                    if current_temp is None or current_rh is None:
                        raise RuntimeError('Incomplete DHT reading')
                    if not (math.isfinite(current_temp) and math.isfinite(current_rh)
                            and -40 <= current_temp <= 80 and 0 <= current_rh <= 100):
                        raise RuntimeError('DHT reading outside supported range')
                    temperature, humidity = current_temp, current_rh
                    last_valid = time.monotonic()
                    last_error = None
                except RuntimeError as exc:
                    last_error = str(exc)
            raw = int(gas.value)
            age = None if last_valid is None else time.monotonic() - last_valid
            stale = age is not None and age > 30
            draw(display, temperature, humidity, stale, raw == 0, tick)
            status = {'status': 'RUNNING', 'temperature_c': temperature,
                      'humidity_percent': humidity, 'dht_age_seconds': age,
                      'dht_error': last_error, 'gas_raw': raw,
                      'gas_input': 'ACTIVE' if raw == 0 else 'IDLE',
                      'gas_concentration_calibrated': False,
                      'relay_buzzer_controlled': False, 'frame': tick,
                      'updated_epoch': time.time()}
            temporary = BASE / 'oled-demo-status.tmp'
            temporary.write_text(json.dumps(status, indent=2) + '\n')
            temporary.replace(BASE / 'oled-demo-status.json')
            tick += 1
            time.sleep(1)
    finally:
        if gas is not None:
            gas.close()
        if sensor is not None:
            sensor.exit()
        bus.deinit()
        instance_lock.close()


if __name__ == '__main__':
    main()
