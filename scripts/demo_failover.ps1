# Demo: Hexgate failover keeps your agent alive when a provider dies.
#
# Runs against a local Hexgate instance (no real API keys needed).
# Opens the dashboard so you can watch the Activity tab while you kill
# a provider mid-turn and see Hexgate fail over to the next route.
#
# Prerequisites: python 3.11+, hexgate installed (pip install -e .)

$ErrorActionPreference = "Stop"

$PORT = 8799
$BASE = "http://127.0.0.1:${PORT}"

Write-Host "==> Starting Hexgate on port ${PORT}..."
$proc = Start-Process python -ArgumentList "-m", "hexgate", "serve", "--no-browser", "--port", $PORT -RedirectStandardOutput "/tmp/hexgate_demo.log" -RedirectStandardError "/tmp/hexgate_demo.log" -PassThru

# Wait for it to be up
for ($i = 0; $i -lt 40; $i++) {
  try { $r = Invoke-RestMethod -Uri "${BASE}/healthz" -ErrorAction Stop; break } catch { Start-Sleep -Milliseconds 250 }
}
try { Invoke-RestMethod -Uri "${BASE}/healthz" -ErrorAction Stop } catch { Write-Host "Hexgate failed to start"; Get-Content /tmp/hexgate_demo.log; exit 1 }

Write-Host "==> Health OK."
$models = Invoke-RestMethod -Uri "${BASE}/v1/models"
Write-Host "   $($models.data.Count) models available"

Write-Host ""
Write-Host "==> Demo complete. Open http://127.0.0.1:${PORT} and watch the Activity tab."
Write-Host "    Kill a provider mid-turn and watch Hexgate fail over to the next route."
Write-Host ""
Write-Host "    Stop Hexgate: Stop-Process -Id $($proc.Id)"
