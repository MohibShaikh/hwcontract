#!/usr/bin/env python3
"""Render demo/agent-question.svg from real output: the pinned sigrok NeoPixel
capture, exported and judged with the same commands a user types. Nothing in
the picture is written by hand; rerun after any change to the output.

    python3 demo/make_svg.py        # needs the capture cached (python3 -m hwcontract.demo ws2812b)
"""
import html
import subprocess
import sys
import tempfile
from pathlib import Path

OUT = Path(__file__).resolve().parent / "agent-question.svg"
COMMANDS = ["python3 -m hwcontract.demo ws2812b --export capture.csv",
            "hwcontract --check ws2812b capture.csv --samplerate 24MHz",
            "hwcontract --check ws2812 capture.csv --samplerate 24MHz"]
COLS, CHAR, LINE, PAD, TOP = 118, 9, 22, 24, 70
TYPE, PAUSE, HOLD = 0.035, 1.2, 6.0           # seconds per typed char, after output, at the end
COLOR = {"PASS": "#50fa7b", "FAIL": "#ff5555", "MARGINAL": "#f1fa8c", "MISSING": "#ff5555"}
FONT = ("font-family=\"JetBrains Mono,Fira Code,ui-monospace,SF Mono,Menlo,Consolas,monospace\" "
        "font-size=\"15\" xml:space=\"preserve\"")


def run(command, cwd):
    """The real output, as the shell would print it."""
    argv = command.split()
    if argv[0] == "python3":
        argv[0] = sys.executable
    else:                                          # the hwcontract console script
        argv = [sys.executable, "-m", "hwcontract.server", *argv[1:]]
    if "--export" in argv:
        argv.insert(argv.index("--export"), "--offline")
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True).stdout.rstrip("\n")


def wrap(line):
    """Hard-wrap at COLS like a terminal does."""
    return [line[i:i + COLS] for i in range(0, len(line), COLS)] or [""]


def spans(line):
    """One line of output, its status word colored."""
    for word, color in COLOR.items():
        for lead in ("  ", "verdict: "):
            if lead + word in line:
                a, b = line.split(lead + word, 1)
                return (f'{html.escape(a + lead)}<tspan fill="{color}">{word}</tspan>'
                        f"{html.escape(b)}")
    return html.escape(line)


def appear(t):
    return (f'<set attributeName="opacity" to="0" begin="loop.begin"/>'
            f'<set attributeName="opacity" to="1" begin="loop.begin+{t:.2f}s"/>')


def main():
    rows, t = [], 0.9                             # (text svg, appear time)
    with tempfile.TemporaryDirectory() as cwd:
        for command in COMMANDS:
            typed = "".join(f'<tspan opacity="0">{appear(t + i * TYPE)}{html.escape(ch)}</tspan>'
                            for i, ch in enumerate(command))
            rows.append((f'<tspan fill="#bd93f9">$ </tspan>{typed}', t - 0.3))
            t += len(command) * TYPE + 0.3
            for line in run(command, cwd).splitlines():
                for part in wrap(line):
                    rows.append((spans(part), t))
                    t += 0.05
            t += PAUSE
    total = t + HOLD
    height = TOP + LINE * len(rows) + PAD
    width = PAD * 2 + COLS * CHAR
    body = []
    for i, (text, at) in enumerate(rows):
        y = TOP + LINE * i
        body.append(f'<text x="{PAD}" y="{y}" fill="#f8f8f2" opacity="0" {FONT}>'
                    f'{appear(at)}{text}</text>')
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" '
           f'aria-label="hwcontract judging a real WS2812B capture against two contracts">\n'
           f'<rect width="{width}" height="{height}" fill="#282a36"/>\n'
           f'<rect width="{width}" height="42" fill="#21222c"/>\n'
           '<circle cx="22" cy="21" r="7" fill="#ff5f56"/><circle cx="46" cy="21" r="7" '
           'fill="#ffbd2e"/><circle cx="70" cy="21" r="7" fill="#27c93f"/>\n'
           f'<text x="{width / 2}" y="27" text-anchor="middle" fill="#6272a4" {FONT}>'
           'hwcontract: a real 24-LED WS2812B capture (sigrok-dumps), two contracts</text>\n'
           f'<rect width="0" height="0"><animate id="loop" attributeName="width" from="0" to="0" '
           f'dur="{total:.2f}s" repeatCount="indefinite"/></rect>\n'
           + "\n".join(body) + "\n</svg>\n")
    OUT.write_text(svg)
    print(f"wrote {OUT} ({len(rows)} lines, {total:.1f}s loop)")


if __name__ == "__main__":
    main()
