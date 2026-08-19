---
name: hwcontract
description: Use when firmware has timing or level requirements you can measure against a spec: pulse widths on a WS2812/NeoPixel strip, DShot ESC, servo, I2C, NEC IR, DS18B20/DHT sensor, A4988/DRV8825 stepper, PWM fan, HC-SR04, or when a board prints a boot log over serial (ESP32, ESP8266, Zephyr, MicroPython, u-boot, Raspberry Pi, STM32). Invoke before declaring firmware done. Not for plain UART framing, power/wiring, or torque/current issues.
---

# hwcontract

Judge what the signal actually did against a spec. "Correct on paper" is not "correct on the wire."

## The one workflow

1. Find the closest contract in `hwcontract/examples/`. Match by chip, not guesswork.
2. Judge your signal against it. One MCP call or one CLI line.
3. `marginal` is a fail: in spec but too close to a rail. It works on the bench, dies in the field.
4. Iterate until all edges `pass`.

No contract for your part? Add a YAML. No code change.

## Tools

- `judge_contract` / `judge_serial`: no hardware, replay a capture or a log
- `check_ws2812` / `check_dshot`: capture a live line and judge it
- `check_serial`: read a serial port for N seconds and judge the log

## Contract format

```yaml
contract: ws2812b
headroom_pct: 20          # within 20% of a rail -> "marginal"
edges:
  - {name: T0H, min: 250, typ: 400, max: 550}
```

```yaml
contract: boot
kind: serial
expect: ["IMU init OK", "boot v\\d+"]
forbid: ["panic", "Guru Meditation"]
```

## Writing a new contract

1. Use the datasheet's real min/typ/max numbers. Don't invent margins.
2. Copy the closest example. Keep its `headroom_pct` style.
3. Name edges by what the adapter observes.
4. Judge a known-good capture against it before calling it done.

## Pitfalls

- **Chip-specific.** Two vendors, two contracts. A WS2812B isn't a WS2812.
- **Datasheet vs reality.** Clones drift. If a contract fails identically on real hardware, the datasheet may be wrong; measure, then widen the window.
- **Escaping.** Contract regexes are YAML strings; doubled backslashes become singles. Test against a real log.
- **Marginal is a fail.** Do not ship it.
- **Capture window.** A boot banner prints once at power-on; reset the board during capture.