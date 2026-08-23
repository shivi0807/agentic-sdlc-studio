[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "High")]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[a-z][a-z0-9-]{4,28}[a-z0-9]$")]
    [string]$ProjectId,

    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[a-z]+-[a-z]+[0-9]+$")]
    [string]$Region,

    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[a-z0-9.-]+/.+:[A-Za-z0-9._-]+$")]
    [string]$Image,

    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[a-z0-9-]+@[a-z][a-z0-9-]+\.iam\.gserviceaccount\.com$")]
    [string]$RuntimeServiceAccount,

    [Parameter(Mandatory = $true)]
    [switch]$ProductionReadinessApproved,

    [string]$ServiceName = "agentic-sdlc-studio"
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
    throw "Google Cloud CLI (gcloud) is not installed or is not on PATH."
}

if (-not $ProductionReadinessApproved) {
    throw "Deployment is blocked until production authentication and durable storage are approved."
}

$arguments = @(
    "run", "deploy", $ServiceName,
    "--project", $ProjectId,
    "--region", $Region,
    "--platform", "managed",
    "--image", $Image,
    "--service-account", $RuntimeServiceAccount,
    "--port", "8080",
    "--cpu", "1",
    "--memory", "512Mi",
    "--min", "0",
    "--max", "1",
    "--concurrency", "20",
    "--timeout", "300",
    "--cpu-throttling",
    "--execution-environment", "gen2",
    "--allow-unauthenticated",
    "--set-env-vars", "APP_ENV=production,PORT=8080,LOG_LEVEL=INFO,DEMO_AUTH_ENABLED=false,AGENT_PROVIDER=deterministic"
)

$displayCommand = "gcloud " + ($arguments -join " ")
Write-Host "Review this potentially billable deployment command:"
Write-Host $displayCommand

if ($PSCmdlet.ShouldProcess(
        "Google Cloud project $ProjectId",
        "Deploy Cloud Run service $ServiceName"
    )) {
    & gcloud @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "gcloud deployment failed with exit code $LASTEXITCODE."
    }
}
