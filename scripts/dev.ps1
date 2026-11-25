# Raccourcis de développement local (Windows / PowerShell).
# Utilisation depuis la racine du projet : .\dev <commande>   (voir .\dev help)

param(
    [Parameter(Position = 0)] [string] $Command = "up",
    [Parameter(Position = 1, ValueFromRemainingArguments = $true)] [string[]] $Rest
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

# Push direct sur dev/main bloqué par .githooks/pre-push (voir docs/developpement.md § Branches et versions).
git config core.hooksPath .githooks

# Les commandes git (feature, pr, merge) marchent aussi dans un worktree, qui n'a pas de .env.
$GitCommands = "feature", "pr", "merge"
function Ok { if ($LASTEXITCODE) { exit $LASTEXITCODE } }

if (-not (Test-Path ".env") -and $Command -notin $GitCommands) {
    Write-Host "Fichier .env absent : copie de .env.example puis remplace les valeurs." -ForegroundColor Red
    exit 1
}

function Compose { docker compose -f docker-compose.yml -f docker-compose.dev.yml @args; if ($LASTEXITCODE) { exit $LASTEXITCODE } }
# Sortie capturée (outils, CI) : pas de terminal, « exec » doit tourner sans TTY (-T).
$ExecFlags = @(); if ([Console]::IsOutputRedirected) { $ExecFlags = @("-T") }
function Manage  { Compose exec @ExecFlags web python manage.py @args }

# Agent du poste (scripts/ia-agent.ps1) : exécute « Lancer / Arrêter les services » demandés depuis le site.
$AgentPid = ".ia-agent.pid"
function Start-Agent {
    if (Test-Path $AgentPid) {
        $running = Get-Process -Id (Get-Content $AgentPid) -ErrorAction SilentlyContinue
        if ($running) { Write-Host "Agent du poste déjà actif (PID $($running.Id))."; return }
    }
    $agent = Start-Process powershell -WindowStyle Hidden -PassThru -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PWD\scripts\ia-agent.ps1`"")
    $agent.Id | Set-Content $AgentPid
    Write-Host "Agent du poste démarré (PID $($agent.Id)) : boutons « Lancer / Arrêter les services » actifs."
}
function Stop-Agent {
    if (Test-Path $AgentPid) {
        Stop-Process -Id (Get-Content $AgentPid) -ErrorAction SilentlyContinue
        Remove-Item $AgentPid
        Write-Host "Agent du poste arrêté."
    }
}

switch ($Command) {
    "up"             { Compose up --build -d; Start-Agent; Write-Host "`nApplication : http://localhost:8000  (journaux : .\dev logs)" -ForegroundColor Green }
    "down"           { Stop-Agent; Compose down }
    "agent"          { Start-Agent }
    "agent-stop"     { Stop-Agent }
    "restart"        { Compose restart web }
    "logs"           { Compose logs -f web }
    "ps"             { Compose ps }
    "journal"        { $service, $lines = "web", 40
                       foreach ($a in $Rest) { if ($a -match '^\d+$') { $lines = $a } else { $service = $a } }
                       Compose logs --no-color --tail $lines $service }
    "test"           { Manage test @Rest }
    "tq"             {
        # Les journaux (audit, django.request) passent sur stderr pendant les tests : on ne garde que le
        # rapport final d'unittest (échecs puis « Ran N tests »), ou la fin de la sortie s'il n'y en a pas.
        $ErrorActionPreference = "Continue"
        $lines = @(docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T web `
            python manage.py test --noinput --parallel -v 0 @Rest 2>&1 | ForEach-Object {
                if ($_ -is [System.Management.Automation.ErrorRecord]) { "$($_.TargetObject)" } else { "$_" } })
        $code = $LASTEXITCODE
        $start = 0..($lines.Count - 1) | Where-Object { $lines[$_] -match '^={40,}$' } | Select-Object -First 1
        if ($null -eq $start) { $start = 0..($lines.Count - 1) | Where-Object { $lines[$_] -match '^-{40,}$' } | Select-Object -Last 1 }
        if ($null -eq $start) { $start = [Math]::Max(0, $lines.Count - 40) }
        $lines[$start..($lines.Count - 1)]
        exit $code
    }
    "py"             { Manage shell -c ($Rest -join " ") }
    "migrate"        { Manage migrate }
    "makemigrations" { Manage makemigrations @Rest }
    "superuser"      { Manage createsuperuser }
    "shell"          { Manage shell }
    "manage"         { Manage @Rest }
    "import"         { if (Test-Path app/anssi) { Manage import_guides @Rest; Manage import_referentials; Manage import_certifications }; Manage import_quizzes; Manage index_texts }
    "news"           { Manage fetch_news }
    "audio"          { Manage narrate @Rest }
    "ia"             { Compose --profile ia up -d ollama quizgen coursegen podcastgen glossarygen tts narrator; foreach ($m in "gemma3:12b", "qwen3:14b") { Compose exec ollama ollama pull $m } }
    "qcm"            { Manage generate_quizzes @Rest }
    "cours"          { Manage generate_courses @Rest }
    "podcast"        { Manage generate_podcasts @Rest }
    "lexique"        { Manage generate_glossaries @Rest }
    "jeu"            { Manage mode_jeu on }
    "fin-jeu"        { Manage mode_jeu off }
    "feature"        {
        # .\dev feature 15 (issue existante) ou .\dev feature "titre" (crée l'issue) → branche feat/15-titre depuis dev.
        $ErrorActionPreference = "Continue"   # git et gh écrivent leur progression sur stderr
        $arg = $Rest -join " "
        if (-not $arg) { Write-Host 'Usage : .\dev feature 15   ou   .\dev feature "titre"' -ForegroundColor Red; exit 1 }
        if ($arg -match '^\d+$') { $num = $arg; $title = gh issue view $num --json title -q .title; Ok }
        else { $title = $arg; $url = gh issue create --title $title --body "Créée par .\dev feature." --label enhancement; Ok; $num = $url.Split("/")[-1] }
        $slug = ($title.Normalize("FormD") -replace '\p{Mn}', '').ToLower() -replace '[^a-z0-9]+', '-'
        $slug = $slug.Substring(0, [Math]::Min(40, $slug.Length)).Trim("-")
        git fetch origin dev; Ok
        git switch -c "feat/$num-$slug" origin/dev; Ok
        Write-Host "Issue #$num : $title`nBranche feat/$num-$slug prête. Une fois fini : .\dev pr" -ForegroundColor Green
    }
    "pr"             {
        # Tests (si l'environnement existe), push de la branche et PR vers dev qui fermera l'issue à la fusion.
        $ErrorActionPreference = "Continue"
        $branch = git branch --show-current
        if ($branch -in "dev", "main") { Write-Host "Vous êtes sur $branch : .\dev feature d'abord." -ForegroundColor Red; exit 1 }
        if (Test-Path ".env") { & $PSCommandPath tq; Ok }
        git push -u origin HEAD; Ok
        gh pr view --json url -q .url 2>$null
        if (-not $LASTEXITCODE) { exit 0 }   # PR déjà ouverte : le push suffit
        $body = Get-Content .github/pull_request_template.md -Raw
        if ($branch -match '/(\d+)-') { $num = $Matches[1]; $title = gh issue view $num --json title -q .title; $body += "`nCloses #$num`n" }
        else { $title = git log -1 --format=%s }
        gh pr create --base dev --title $title --body $body; Ok
    }
    "merge"          {
        # .\dev merge [n° PR] : attend la CI, fusionne dans dev (commit de fusion : les commits d'étape restent), nettoie.
        $ErrorActionPreference = "Continue"
        gh pr checks @Rest --watch --fail-fast; Ok
        gh pr merge @Rest --merge --delete-branch; Ok
        git switch dev; Ok
        git pull --ff-only; Ok
    }
    "reset"          { Compose down -v; Write-Host "Conteneurs et volumes (base de données comprise) supprimés." -ForegroundColor Yellow }
    default {
        @"
Commandes :
  .\dev up              Construit et démarre l'environnement (http://localhost:8000) et l'agent du poste
  .\dev down            Arrête les conteneurs et l'agent du poste
  .\dev agent           Démarre seulement l'agent du poste (boutons « Lancer / Arrêter les services »)
  .\dev restart         Redémarre le serveur Django
  .\dev logs            Suit les journaux de l'application
  .\dev journal [svc] [n] Affiche les n dernières lignes du journal (web, 40 par défaut) sans le suivre
  .\dev ps              État des services
  .\dev test [app]      Lance les tests (ex. .\dev test catalogue.tests_queues)
  .\dev tq [app]        Tests silencieux : seulement les échecs et le résumé, en parallèle
  .\dev py "<code>"     Exécute du Python dans le shell Django (ex. .\dev py "from catalogue.models import Guide; print(Guide.objects.count())")
  .\dev migrate         Applique les migrations
  .\dev makemigrations  Crée les migrations après modification d'un modèle
  .\dev superuser       Crée un compte administrateur
  .\dev shell           Shell Django
  .\dev manage <cmd>    N'importe quelle commande manage.py
  .\dev import          Charge les QCM de catalogue/data/quiz/ et indexe le texte (+ import ANSSI si app/anssi)
  .\dev news            Récupère tout de suite les actualités cyber (sinon : toutes les heures)
  .\dev audio --tout    Met en file la version audio de tous les guides (générée en arrière-plan)
  .\dev ia              Démarre l'IA locale (Ollama, GPU) et les files de génération (QCM, cours, podcasts, lexiques, audio)
  .\dev qcm --tout      Met en file un QCM pour chaque guide qui n'en a pas (à relire ensuite)
  .\dev cours --themes  Met en file un cours par thème (--guides : un cours par guide), à relire ensuite
  .\dev podcast --themes Met en file une série de podcasts par thème, à relire avant l'enregistrement
  .\dev lexique --tout  Met en file le lexique (sigles et termes) de chaque guide, à relire ensuite
  .\dev jeu             Mode jeu : coupe l'IA et les générations, libère la carte graphique
  .\dev fin-jeu         Quitte le mode jeu : tout reprend
  .\dev reset           Supprime conteneurs ET données locales (base de dev)

Git (une issue → une branche → une PR vers dev) :
  .\dev feature "titre" Crée l'issue et sa branche feat/N-titre depuis dev (.\dev feature 15 : issue existante)
  .\dev pr              Tests, push de la branche, PR vers dev (« Closes #N »)
  .\dev merge [n°]      Attend la CI verte, fusionne la PR dans dev, revient sur dev à jour
"@
    }
}
