# VHS tape for the API demo GIF: compose up, POST a run, watch it stream.
#
# Regenerate with `make demo-api` (or ./scripts/record-demo.sh api).
# Requires vhs and a working Docker daemon.

Output demo/api.gif

Set Shell "bash"
Set FontSize 16
Set Width 1300
Set Height 500
Set Padding 24
Set Theme "Catppuccin Mocha"
Set TypingSpeed 25ms

Hide
Type "docker compose down -v >/dev/null 2>&1"
Enter
Type "clear"
Enter
Show
Sleep 500ms

# Postgres, migrations, then the API -- migrations are their own one-shot
# service, so exactly one process ever touches the schema.
Type "docker compose up -d --wait"
Sleep 400ms
Enter
Sleep 15s

Type "curl -s localhost:8000/healthz"
Sleep 300ms
Enter
Sleep 2s

# Submit a 5,000-minute run and watch its events arrive over the WebSocket
# while the simulation is still going.
Type "python scripts/stream_run.py --minutes 5000"
Sleep 400ms
Enter
Sleep 7s

Hide
Type "clear"
Enter
Show
Sleep 300ms

Type "curl -s 'localhost:8000/runs?limit=1' | python -m json.tool | head -18"
Sleep 300ms
Enter
Sleep 5s
