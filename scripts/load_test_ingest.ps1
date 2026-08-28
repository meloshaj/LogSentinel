#!/usr/bin/env pwsh
# =============================================================================
# LogSentinel — Synthetic Telemetry Load Test
# Validates: Nginx TLS → FastAPI ingest → Valkey stream → Drain3 → TimescaleDB
# =============================================================================

$ErrorActionPreference = "Continue"

$BASE_URL      = "https://localhost:8443/api/v1/logs/ingest"
$API_KEY       = "dev-local-key"
$TOTAL_BATCHES = 20
$DELAY_SEC     = 1

# Microservice names matching topology expectations
$SERVICES = @("api-gateway", "auth-service", "order-service", "payment-service", "notification-service")

$LEVELS = @("info", "info", "info", "warning", "error", "critical")

# Realistic log message templates (Drain3 will cluster these)
$INFO_MESSAGES = @(
    "Received incoming request POST /api/v1/orders from client 192.168.1.{0}",
    "Successfully authenticated user user-{0} via JWT token",
    "Order ORD-{0} created successfully, total: `${1}.99",
    "Payment processed for order ORD-{0}, transaction ID: TXN-{1}",
    "Notification email dispatched to user user-{0} for order ORD-{1}",
    "Health check passed: latency={0}ms, connections={1}",
    "Cache hit for key session:{0}, ttl=300s",
    "Database query executed in {0}ms, rows returned: {1}",
    "Rate limiter allowed request from IP 10.0.{0}.{1}",
    "WebSocket connection established for client session-{0}"
)

$WARNING_MESSAGES = @(
    "Slow query detected: SELECT * FROM orders took {0}ms (threshold: 200ms)",
    "Connection pool nearing capacity: {0}/50 active connections",
    "Retry attempt {0}/3 for downstream service call to payment-service",
    "JWT token expiring in {0} seconds for user user-{1}",
    "Memory usage at {0}% - approaching warning threshold"
)

$ERROR_MESSAGES = @(
    "Failed to connect to payment-service: connection timeout after {0}ms",
    "Database connection pool exhausted: {0} requests queued",
    "Unhandled exception in order processing: NullReferenceError at line {0}",
    "Circuit breaker OPEN for auth-service after {0} consecutive failures",
    "TLS handshake failed for upstream notification-service: certificate expired"
)

$CRITICAL_MESSAGES = @(
    "FATAL: Database primary node unreachable - failover initiated",
    "CRITICAL: Payment gateway returned HTTP 503 for {0} consecutive requests",
    "CRITICAL: Disk usage at 95% on /var/lib/postgresql/data",
    "FATAL: Out of memory - killing process order-worker PID {0}",
    "CRITICAL: Cascading failure detected across auth-service and order-service"
)

function Get-RandomMessage {
    param([string]$Level)
    $r1 = Get-Random -Minimum 1 -Maximum 999
    $r2 = Get-Random -Minimum 1 -Maximum 500

    switch ($Level) {
        "info"     { $msg = $INFO_MESSAGES | Get-Random; return ($msg -f $r1, $r2) }
        "warning"  { $msg = $WARNING_MESSAGES | Get-Random; return ($msg -f $r1, $r2) }
        "error"    { $msg = $ERROR_MESSAGES | Get-Random; return ($msg -f $r1, $r2) }
        "critical" { $msg = $CRITICAL_MESSAGES | Get-Random; return ($msg -f $r1, $r2) }
        default    { return "Generic log event id=$r1" }
    }
}

function New-LogEntry {
    param(
        [string]$ServiceName,
        [string]$Level,
        [string]$CorrelationId
    )
    $message = Get-RandomMessage -Level $Level
    return @{
        timestamp    = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
        service_name = $ServiceName
        level        = $Level
        message      = $message
        metadata     = @{
            hostname       = "pod-$ServiceName-$(Get-Random -Minimum 1 -Maximum 10)"
            container_id   = [guid]::NewGuid().ToString().Substring(0, 12)
            kubernetes_pod = "$ServiceName-deployment-$(Get-Random -Minimum 1000 -Maximum 9999)"
        }
        raw          = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff') [$($Level.ToUpper())] [$ServiceName] $message"
    }
}

Write-Host ""
Write-Host "===============================================================" -ForegroundColor Cyan
Write-Host " LogSentinel  Synthetic Telemetry Load Test" -ForegroundColor Cyan
Write-Host " Target: $BASE_URL" -ForegroundColor DarkCyan
Write-Host " Batches: $TOTAL_BATCHES x 5 logs = $(($TOTAL_BATCHES * 5)) total events" -ForegroundColor DarkCyan
Write-Host "===============================================================" -ForegroundColor Cyan
Write-Host ""

$successCount = 0
$failCount = 0
$totalLogs = 0
$rateLimited = 0

for ($batch = 1; $batch -le $TOTAL_BATCHES; $batch++) {
    $correlationId = "req-$(([guid]::NewGuid().ToString().Substring(0, 8)))"
    $logs = @()

    # Each batch simulates a request chain across services
    foreach ($svc in $SERVICES) {
        # Batch 7-8 intentionally spike errors for anomaly detection
        if ($batch -ge 7 -and $batch -le 8 -and ($svc -eq "payment-service" -or $svc -eq "order-service")) {
            $level = @("error", "critical") | Get-Random
        } else {
            $level = $LEVELS | Get-Random
        }
        $entry = New-LogEntry -ServiceName $svc -Level $level -CorrelationId $correlationId
        $logs += $entry
    }

    $payload = @{
        source         = "load-test-script"
        environment    = "development"
        correlation_id = $correlationId
        logs           = $logs
    } | ConvertTo-Json -Depth 5

    try {
        $params = @{
            Uri         = $BASE_URL
            Method      = "POST"
            ContentType = "application/json"
            Headers     = @{ "X-API-Key" = $API_KEY }
            Body        = $payload
            ErrorAction = "Stop"
        }

        # Handle self-signed cert
        try {
            $response = Invoke-RestMethod @params -SkipCertificateCheck
        } catch [System.Management.Automation.ParameterBindingException] {
            # PS 5.1 fallback: disable cert validation
            if (-not ([System.Net.ServicePointManager]::ServerCertificateValidationCallback)) {
                [System.Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
            }
            $response = Invoke-RestMethod @params
        }

        $statusIcon = if ($response.accepted) { "[OK]" } else { "[REJECTED]" }
        $color = if ($response.accepted) { "Green" } else { "Yellow" }

        Write-Host ("  Batch {0:D2}/{1} {2} | corr={3} | queue={4} | {5}" -f `
            $batch, $TOTAL_BATCHES, $statusIcon, $correlationId, $response.queue_size, $response.message) `
            -ForegroundColor $color

        if ($response.accepted) { $successCount++ } else { $failCount++ }
        $totalLogs += $logs.Count

    } catch {
        $statusCode = $_.Exception.Response.StatusCode.value__
        if ($statusCode -eq 429) {
            $rateLimited++
            Write-Host ("  Batch {0:D2}/{1} [429 RATE LIMITED] | corr={2}" -f $batch, $TOTAL_BATCHES, $correlationId) `
                -ForegroundColor Yellow
        } else {
            $failCount++
            Write-Host ("  Batch {0:D2}/{1} [FAIL] | corr={2} | error={3}" -f `
                $batch, $TOTAL_BATCHES, $correlationId, $_.Exception.Message) `
                -ForegroundColor Red
        }
    }

    if ($batch -lt $TOTAL_BATCHES) {
        Start-Sleep -Seconds $DELAY_SEC
    }
}

Write-Host ""
Write-Host "===============================================================" -ForegroundColor Cyan
Write-Host " Load Test Complete" -ForegroundColor Cyan
Write-Host "---------------------------------------------------------------" -ForegroundColor DarkGray
Write-Host "  Total batches sent:     $TOTAL_BATCHES"
Write-Host "  Total log events:       $totalLogs"
Write-Host "  Accepted:               $successCount" -ForegroundColor Green
Write-Host "  Failed:                 $failCount" -ForegroundColor $(if ($failCount -gt 0) { "Red" } else { "Green" })
Write-Host "  Rate-limited (429):     $rateLimited" -ForegroundColor $(if ($rateLimited -gt 0) { "Yellow" } else { "Green" })
Write-Host "===============================================================" -ForegroundColor Cyan
Write-Host ""
