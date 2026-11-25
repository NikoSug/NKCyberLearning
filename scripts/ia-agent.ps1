# Agent du poste : exécute les demandes « Lancer / Arrêter les services » du tableau de bord des générations.
#
# Le site n'a volontairement aucun accès à Docker (ce serait lui donner la main sur le PC) : il enregistre
# une demande, cet agent la récupère toutes les 8 secondes et lance une commande fixée d'avance. Rien
# d'autre ne peut être demandé.
#
# Démarré en arrière-plan par « .\dev up » (ou « .\dev agent »), arrêté par « .\dev down » (ou « .\dev agent-stop »).
# Journal : .ia-agent.log à la racine du projet.

$ErrorActionPreference = "Continue"
Set-Location (Split-Path $PSScriptRoot -Parent)

$Files = @("-f", "docker-compose.yml", "-f", "docker-compose.dev.yml")
$Services = @("ollama", "quizgen", "coursegen", "podcastgen", "glossarygen", "tts", "narrator")
$Models = @("gemma3:12b", "qwen3:14b")  # générateur, pré-relecteur
$Log = Join-Path (Get-Location) ".ia-agent.log"

function Write-Log([string] $Message) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Message" | Add-Content -Path $Log -Encoding utf8
}

function Invoke-Compose {
    $output = & docker compose @Files @args 2>&1 | ForEach-Object { "$_" }
    return @{ Ok = ($LASTEXITCODE -eq 0); Output = $output }
}

# L'agent tourne caché : toute erreur est consignée dans le journal plutôt que perdue.
trap { Write-Log "erreur : $_"; continue }

Write-Log "agent démarré (PID $PID)"
while ($true) {
    $line = & docker compose @Files exec -T web python manage.py service_requests --poll 2>$null
    if ($LASTEXITCODE -eq 0 -and "$line" -match '^(\d+) (lancer|arreter)$') {
        $id, $action = $Matches[1], $Matches[2]
        Write-Log "demande $id : $action"
        if ($action -eq "lancer") {
            $result = Invoke-Compose --profile ia up -d @Services
            if ($result.Ok) {
                foreach ($model in $Models) {
                    $pull = Invoke-Compose exec -T ollama ollama pull $model  # rapide si le modèle est déjà là
                    $result.Ok = $result.Ok -and $pull.Ok
                    $result.Output += $pull.Output
                }
            }
        } else {
            $result = Invoke-Compose stop @Services
        }
        # Compte rendu court, sans codes de couleur du terminal ni guillemets (passé en argument à Django).
        $lines = $result.Output | ForEach-Object { $_ -replace '\x1b\[[0-9;?]*[A-Za-z]', '' } | Where-Object { $_ -match '\S' }
        $summary = (($lines | Select-Object -Last 3) -join " ; ") -replace '["`]', "'"
        if ($summary.Length -gt 400) { $summary = $summary.Substring(0, 400) }
        $flag = if ($result.Ok) { "--ok" } else { "--error" }
        & docker compose @Files exec -T web python manage.py service_requests --done $id $flag "$summary" 2>$null | Out-Null
        $state = if ($result.Ok) { "faite" } else { "échec" }
        Write-Log "demande $id : $state - $summary"
    }
    Start-Sleep -Seconds 8
}
