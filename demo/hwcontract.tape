# hwcontract demo tape. Rendered with: vhs demo/hwcontract.tape
# Output: demo/hwcontract.gif

Output demo/hwcontract.gif
Set Shell "bash"
Set FontSize 15
Set Width 1100
Set Height 620
Set Padding 24
Set Framerate 30
Set Theme "Dracula"
Set PlaybackSpeed 1.0

Type "cd /home/noman/personal-projects/hwcontract"
Enter
Sleep 300ms

Type "ls hwcontract/examples/ | head -14"
Enter
Sleep 900ms

Type "clear"
Enter
Sleep 400ms

# the real demo: a genuine 24-LED NeoPixel capture, judged against two contracts
Type "uv run python3 demo/ws2812b_neopixel.py"
Enter
Sleep 6s

Type "clear"
Enter
Sleep 400ms

# the temporal engine: one DMA-broken SPI frame in a hundred
Type "uv run python3 demo/spi_dma_temporal.py"
Enter
Sleep 6s

Type "clear"
Enter
Sleep 400ms

# serial boot logs, judged the same way
Type "uv run python3 demo/serial_boot.py"
Enter
Sleep 4s
