Write-Host "Starting LogSentinel Demo Fleet..." -ForegroundColor Cyan
Write-Host "Stopping any existing containers..."
docker compose -f docker-compose.demo.yml down -v

Write-Host "Building and starting the demo stack..."
docker compose -f docker-compose.demo.yml up -d --build

Write-Host "Waiting for services to become healthy..."
Start-Sleep -Seconds 15

Write-Host "Demo fleet is running!" -ForegroundColor Green
Write-Host "Dashboard: http://localhost:8080"
Write-Host "To trigger an incident, run: python scripts/trigger_demo_incident.py" -ForegroundColor Yellow
