# VHS tape for the main demo GIF: one shift on a line that breaks down.
#
# Regenerate with `make demo` (or ./scripts/record-demo.sh), which puts the
# project virtualenv on PATH first. Requires https://github.com/charmbracelet/vhs
#   brew install vhs

Output demo/demo.gif

Set Shell "bash"
Set FontSize 16
Set Width 1300
Set Height 780
Set Padding 24
Set Theme "Catppuccin Mocha"
Set TypingSpeed 30ms

# Simulate an eight-hour shift, discarding the first 30 minutes of fill.
Sleep 1s
Type "smtsim run --minutes 480 --warmup 30 --config configs/baseline.toml --out runs/demo.jsonl"
Sleep 500ms
Enter
Sleep 7s

Hide
Type "clear"
Enter
Show
Sleep 300ms

# Rebuild the same tables from the saved log, without simulating again.
Type "smtsim stats runs/demo.jsonl"
Sleep 500ms
Enter
Sleep 7s
