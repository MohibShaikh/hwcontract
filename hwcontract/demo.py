"""Installed demos: python -m hwcontract.demo {ws2812b,spi,serial}."""
import argparse
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Replay a real capture or run offline synthetic demos.")
    sub = parser.add_subparsers(dest="demo", required=True)
    led = sub.add_parser("ws2812b", help="real 24-LED capture; downloads and caches on first run")
    led.add_argument("--cache-dir", type=Path, help="download cache (default: XDG_CACHE_HOME/hwcontract)")
    led.add_argument("--capture", type=Path, help="use an existing copy of the pinned sigrok capture")
    led.add_argument("--offline", action="store_true", help="require a cached capture; never download")
    led.add_argument("--export", type=Path, metavar="CSV",
                     help="write the capture as a 0/1 CSV to judge yourself with hwcontract --check")
    sub.add_parser("spi", help="offline synthetic CS/SCK/MOSI frames and two deliberate faults")
    sub.add_parser("serial", help="offline synthetic clean and panicking boot logs")
    args = parser.parse_args(argv)
    if args.demo == "ws2812b":
        from hwcontract.demo_ws2812b import main as run
        try:
            return run(cache_dir=args.cache_dir, capture_path=args.capture, offline=args.offline,
                       export=args.export)
        except (OSError, ValueError) as exc:
            parser.exit(1, f"hwcontract demo: {exc}\n")
    if args.demo == "spi":
        from hwcontract.demo_spi import main as run
    else:
        from hwcontract.demo_serial import main as run
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
