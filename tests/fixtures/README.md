# Transcribed byte-for-byte from sigrok-cli decode.c jsontrace_annotation()
# (B/E pairs with ts/pid/tid/name, microseconds): the shape real
# `sigrok-cli --protocol-decoder-jsontrace` emits. Regenerate from a real
# capture with:
#   sigrok-cli -i capture.sr -P uart:tx=D0 -A uart=tx \
#       --protocol-decoder-jsontrace > uart-sigrok-trace.json
