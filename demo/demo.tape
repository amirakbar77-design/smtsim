# VHS tape for the smtsim demo GIF.
#
# Regenerate with `make demo` (or ./scripts/record-demo.sh), which puts the
# project virtualenv on PATH first. Requires https://github.com/charmbracelet/vhs
#   brew install vhs

Output demo/demo.gif

Set Shell "bash"
Set FontSize 16
Set Width 1240
Set Height 620
Set Padding 24
Set Theme "Catppuccin Mocha"
Set TypingSpeed 35ms

# Simulate an eight-hour shift and write the event log.
Sleep 1s
Type "smtsim run --minutes 480 --seed 42 --out runs/demo.jsonl"
Sleep 600ms
Enter
Sleep 6s

Hide
Type "clear"
Enter
Show
Sleep 300ms

# Rebuild the same table from the saved log, without simulating again.
Type "smtsim stats runs/demo.jsonl"
Sleep 600ms
Enter
Sleep 8s
