# hwcontract demo tape. Rendered from the repo root with: vhs demo/hwcontract.tape
# Output: demo/hwcontract.gif
# Needs hwcontract on PATH. The first command downloads the real capture
# (cached afterwards); everything shown is what a user types.

Output demo/hwcontract.gif
Set Shell "bash"
Set FontSize 15
Set Width 1300
Set Height 700
Set Padding 24
Set Framerate 30
Set Theme "Dracula"
Set PlaybackSpeed 1.0

Hide
Type "export PS1='$ ' && cd $(mktemp -d) && clear"
Enter
Sleep 500ms
Show

# a real 24-LED WS2812B ring recorded at 24MHz (sigrok-dumps), as a 0/1 CSV
Type "python3 -m hwcontract.demo ws2812b --export capture.csv"
Enter
Sleep 2s

Type "hwcontract --check ws2812b capture.csv --samplerate 24MHz"
Enter
Sleep 3s

# same wire, the generic WS2812 contract: a different chip's windows
Type "hwcontract --check ws2812 capture.csv --samplerate 24MHz"
Enter
Sleep 5s

Type "clear"
Enter
Sleep 400ms

# the temporal engine: one DMA-broken SPI frame in a hundred (synthetic)
Type "python3 -m hwcontract.demo spi"
Enter
Sleep 10s

Type "clear"
Enter
Sleep 400ms

# serial boot logs, judged the same way (synthetic)
Type "python3 -m hwcontract.demo serial"
Enter
Sleep 6s
