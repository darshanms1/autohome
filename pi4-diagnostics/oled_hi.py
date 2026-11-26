"""Write a greeting once; retain the OLED framebuffer after this process exits."""
import json
import board
import busio
import adafruit_ssd1306

GLYPHS = {
    'H': ['10001', '10001', '10001', '11111', '10001', '10001', '10001'],
    'i': ['00100', '00000', '01100', '00100', '00100', '00100', '01110'],
    '(': ['00010', '00100', '01000', '01000', '01000', '00100', '00010'],
    ')': ['01000', '00100', '00010', '00010', '00010', '00100', '01000'],
    '^': ['00100', '01010', '10001', '00000', '00000', '00000', '00000'],
    '_': ['00000', '00000', '00000', '00000', '00000', '00000', '11111'],
}


def main():
    bus = busio.I2C(board.SCL, board.SDA)
    try:
        display = adafruit_ssd1306.SSD1306_I2C(128, 64, bus, addr=0x3c)
        lines = [('Hi', 3, 5), ('(^_^)', 2, 40)]
        display.fill(0)
        for text, scale, top in lines:
            width = len(text) * 6 - 1
            left = (128 - width * scale) // 2
            for index, char in enumerate(text):
                for y, row in enumerate(GLYPHS[char]):
                    for x, bit in enumerate(row):
                        if bit == '1':
                            display.fill_rect(left + (index * 6 + x) * scale,
                                              top + y * scale, scale, scale, 1)
        display.show()
        print(json.dumps({'status': 'GREETING_SENT', 'address': '0x3c',
                          'size': [128, 64], 'lines': ['Hi', '(^_^)'],
                          'retained_until': 'Power removal, display reset, or another display write'}))
    finally:
        bus.deinit()


if __name__ == '__main__':
    main()
