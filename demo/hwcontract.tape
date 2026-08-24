# hwcontract demo tape. Rendered with: vhs demo/hwcontract.tape
# Output: demo/hwcontract.gif

Output demo/hwcontract.gif
Set Shell "bash"
Set FontSize 15
Set Width 1100
Set Height 520
Set Padding 24
Set Framerate 30
Set Theme "Dracula"
Set PlaybackSpeed 1.0

Type "cd /home/noman/personal-projects/hwcontract"
Enter
Sleep 300ms

Type "ls hwcontract/examples/ | head -14"
Enter
Sleep 700ms

Type "ls hwcontract/examples/ | tail -13"
Enter
Sleep 700ms

# the real demo: a genuine 24-LED NeoPixel capture, judged against two contracts
Type "uv run python3 demo/ws2812b_neopixel.py"
Enter
Sleep 4s
Sleep 2s

# the temporal engine: one DMA-broken SPI frame in a hundred
Type "uv run python3 demo/spi_dma_temporal.py"
Enter
Sleep 4s

# judge a serial boot log the same way
Type "cat hwcontract/examples/esp32.contract.yaml"
Enter
Sleep 800ms

Type "uv run python3 -c \"import json; from hwcontract.judge import load_contract, run_serial, render;\""
Enter
Sleep 500ms
Type "uv run python3 -c \"import json; from hwcontract.judge import load_contract, run_serial, render; c=load_contract('hwcontract/examples/esp32.contract.yaml'); log='ets Jun  8 2016 00:22:57\\nrst:0x1 (POWERON_RESET)\\nboot:0x13 (SPI_FAST_FLASH_BOOT)\\nentry 0x4008047c\\nI (30) boot: ESP-IDF v5.2.1\\n'; print(render(run_serial(c, log)[0]))\""
Enter
Sleep 1.5s

Type "clear"
Enter
Sleep 500ms

Type "# same contract, a log that panics instead of booting:"
Enter
Sleep 600ms

Type "uv run python3 -c \"import json; from hwcontract.judge import load_contract, run_serial, render; c=load_contract('hwcontract/examples/esp32.contract.yaml'); log='rst:0x3 (SW_RESET)\\nGuru Meditation Error: Core  0 panic\\n'; print(render(run_serial(c, log)[0]))\""
Enter
Sleep 1.5s