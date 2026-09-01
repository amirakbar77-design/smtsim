# VHS tape for the what-if comparison GIF.
#
# Regenerate with `make demo` (or ./scripts/record-demo.sh), which puts the
# project virtualenv on PATH first. Requires https://github.com/charmbracelet/vhs
#   brew install vhs

Output demo/compare.gif

Set Shell "bash"
Set FontSize 16
Set Width 1300
Set Height 420
Set Padding 24
Set Theme "Catppuccin Mocha"
Set TypingSpeed 30ms

# Are longer conveyors worth it? Sixty shifts, paired by seed. The effect is
# small enough that no single shift could resolve it.
Sleep 1s
Type "smtsim compare configs/tight_buffers.toml configs/baseline.toml --seeds 30 --minutes 480 --warmup 30"
Sleep 500ms
Enter
Sleep 10s
