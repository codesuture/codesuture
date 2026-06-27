# ============================================================
#  CodeSuture Comment Manager
#  Usage:
#    .\comment_manager.ps1 audit          — count & list all comments
#    .\comment_manager.ps1 review         — interactive keep/remove per comment
#    .\comment_manager.ps1 strip-auto     — auto-remove obvious AI comments
#    .\comment_manager.ps1 strip-all      — remove ALL comments (nuclear)
#    .\comment_manager.ps1 restore        — restore from backup
# ============================================================

param(
    [Parameter(Position=0)]
    [ValidateSet("audit","review","strip-auto","strip-all","restore")]
    [string]$Mode = "audit",

    [string]$ProjectPath = ".",
    [string]$BackupDir   = ".comment_backup"
)

$ErrorActionPreference = "Stop"

# ── AI-generated comment patterns (case-insensitive) ─────────────────────────
$AI_PATTERNS = @(
    '^\s*#\s*This (function|method|class|variable|loop|block|line|code)',
    '^\s*#\s*We (can|will|need to|are|use|check|handle|create|define)',
    '^\s*#\s*Now we ',
    '^\s*#\s*The following',
    '^\s*#\s*Here we ',
    '^\s*#\s*This is (a|an|the) ',
    '^\s*#\s*Initialize ',
    '^\s*#\s*Loop (through|over|for)',
    '^\s*#\s*Iterate (through|over)',
    '^\s*#\s*Return the (result|value|output|response)',
    '^\s*#\s*Print (the|a|an) ',
    '^\s*#\s*Set (the|a|an) ',
    '^\s*#\s*Get (the|a|an) ',
    '^\s*#\s*Check if ',
    '^\s*#\s*Add (the|a|an) ',
    '^\s*#\s*Create (a|an|the) ',
    '^\s*#\s*Define (a|an|the) ',
    '^\s*#\s*Call (the|a|an) ',
    '^\s*#\s*Import (the|a|an) ',
    '^\s*#\s*Convert (the|a|an) ',
    '^\s*#\s*Calculate ',
    '^\s*#\s*Process (the|a|an) ',
    '^\s*#\s*Handle (the|a|an) ',
    '^\s*#\s*Store (the|a|an) ',
    '^\s*#\s*Update (the|a|an) ',
    '^\s*#\s*Delete (the|a|an) ',
    '^\s*#\s*Open (the|a|an) ',
    '^\s*#\s*Close (the|a|an) ',
    '^\s*#\s*Read (the|a|an) ',
    '^\s*#\s*Write (the|a|an) ',
    '^\s*#\s*Save (the|a|an) '
)

# ── helpers ───────────────────────────────────────────────────────────────────
function Get-PyFiles {
    Get-ChildItem -Path $ProjectPath -Recurse -Filter "*.py" |
        Where-Object { $_.FullName -notmatch '\\\.git\\|/__pycache__/|\.egg-info' }
}

function Is-InString {
    param([string]$line)
    # heuristic: count quote chars before the # using regex matches
    $before = ($line -split '#')[0]
    $sq = ([regex]::Matches($before, "'" )).Count
    $dq = ([regex]::Matches($before, '"' )).Count
    return ($sq % 2 -ne 0) -or ($dq % 2 -ne 0)
}

function Is-AIComment {
    param([string]$line)
    foreach ($pat in $AI_PATTERNS) {
        if ($line -match $pat) { return $true }
    }
    return $false
}

function Get-AllComments {
    $results = @()
    foreach ($file in Get-PyFiles) {
        $lines = Get-Content $file.FullName -Encoding UTF8
        for ($i = 0; $i -lt $lines.Count; $i++) {
            $line = $lines[$i]
            # skip shebangs and encoding declarations
            if ($line -match '^\s*#!|^\s*#.*coding') { continue }
            if ($line -match '^\s*#' -and -not (Is-InString $line)) {
                $results += [PSCustomObject]@{
                    File    = $file.FullName
                    LineNum = $i + 1
                    Text    = $line.Trim()
                    IsAI    = Is-AIComment $line
                }
            }
        }
    }
    return $results
}

function Backup-Files {
    Write-Host "`n📦 Creating backup in $BackupDir ..." -ForegroundColor Cyan
    if (Test-Path $BackupDir) { Remove-Item $BackupDir -Recurse -Force }
    New-Item -ItemType Directory -Path $BackupDir | Out-Null
    foreach ($file in Get-PyFiles) {
        $rel  = $file.FullName.Substring((Resolve-Path $ProjectPath).Path.Length + 1)
        $dest = Join-Path $BackupDir $rel
        $destDir = Split-Path $dest -Parent
        if (-not (Test-Path $destDir)) { New-Item -ItemType Directory -Path $destDir | Out-Null }
        Copy-Item $file.FullName $dest
    }
    Write-Host "✓ Backup complete.`n" -ForegroundColor Green
}

# ── AUDIT ─────────────────────────────────────────────────────────────────────
function Run-Audit {
    Write-Host "`n╔══════════════════════════════════════════════════════╗" -ForegroundColor Cyan
    Write-Host "║         CodeSuture Comment Audit                    ║" -ForegroundColor Cyan
    Write-Host "╚══════════════════════════════════════════════════════╝`n" -ForegroundColor Cyan

    $all = Get-AllComments
    $ai  = $all | Where-Object { $_.IsAI }
    $keep= $all | Where-Object { -not $_.IsAI }

    Write-Host "📊 SUMMARY" -ForegroundColor White
    Write-Host "─────────────────────────────────────────────" -ForegroundColor DarkGray
    Write-Host ("  Total comments   : {0}" -f $all.Count)  -ForegroundColor White
    Write-Host ("  ✅ Keep (technical): {0}" -f $keep.Count) -ForegroundColor Green
    Write-Host ("  🤖 AI-ish (remove) : {0}" -f $ai.Count)  -ForegroundColor Yellow
    Write-Host ""

    # group by file
    $byFile = $all | Group-Object File
    Write-Host "📁 PER FILE" -ForegroundColor White
    Write-Host "─────────────────────────────────────────────" -ForegroundColor DarkGray
    foreach ($g in $byFile | Sort-Object Count -Descending) {
        $relPath = $g.Name.Replace((Resolve-Path $ProjectPath).Path, ".")
        $aiCount = ($g.Group | Where-Object IsAI).Count
        $color   = if ($aiCount -gt 0) { "Yellow" } else { "Green" }
        Write-Host ("  {0,-45} total:{1,3}  ai:{2,3}" -f $relPath, $g.Count, $aiCount) -ForegroundColor $color
    }

    Write-Host "`n🤖 AI-PATTERN COMMENTS (candidates for removal)" -ForegroundColor Yellow
    Write-Host "─────────────────────────────────────────────" -ForegroundColor DarkGray
    foreach ($c in $ai) {
        $relPath = $c.File.Replace((Resolve-Path $ProjectPath).Path, ".")
        Write-Host ("  [{0,4}] {1}" -f $c.LineNum, $relPath) -ForegroundColor DarkGray
        Write-Host ("         {0}" -f $c.Text) -ForegroundColor Yellow
    }

    Write-Host "`n✅ TECHNICAL COMMENTS (recommended to keep)" -ForegroundColor Green
    Write-Host "─────────────────────────────────────────────" -ForegroundColor DarkGray
    foreach ($c in $keep | Select-Object -First 30) {
        $relPath = $c.File.Replace((Resolve-Path $ProjectPath).Path, ".")
        Write-Host ("  [{0,4}] {1}" -f $c.LineNum, $relPath) -ForegroundColor DarkGray
        Write-Host ("         {0}" -f $c.Text) -ForegroundColor Green
    }
    if ($keep.Count -gt 30) {
        Write-Host ("  ... and {0} more technical comments (not shown)" -f ($keep.Count - 30)) -ForegroundColor DarkGray
    }

    Write-Host "`n💡 Next steps:" -ForegroundColor Cyan
    Write-Host "   .\comment_manager.ps1 review      — decide each comment interactively"
    Write-Host "   .\comment_manager.ps1 strip-auto  — auto-remove only AI-pattern comments"
    Write-Host "   .\comment_manager.ps1 strip-all   — remove every comment"
    Write-Host "   .\comment_manager.ps1 restore     — undo any changes`n"
}

# ── INTERACTIVE REVIEW ────────────────────────────────────────────────────────
function Run-Review {
    $all = Get-AllComments
    Write-Host "`n🔍 Interactive Review — $($all.Count) comments found" -ForegroundColor Cyan
    Write-Host "   For each comment: [K] Keep  [R] Remove  [S] Skip file  [Q] Quit & save`n" -ForegroundColor DarkGray

    Backup-Files

    # track removals: file → list of 1-based line numbers to remove
    $toRemove = @{}

    $skipFile = $null
    foreach ($c in $all) {
        if ($skipFile -eq $c.File) { continue }

        $relPath = $c.File.Replace((Resolve-Path $ProjectPath).Path, ".")
        $aiLabel = if ($c.IsAI) { " [AI?]" } else { "" }

        Write-Host ("─── Line {0} · {1}{2}" -f $c.LineNum, $relPath, $aiLabel) -ForegroundColor DarkGray
        $color = if ($c.IsAI) { "Yellow" } else { "Green" }
        Write-Host ("    {0}" -f $c.Text) -ForegroundColor $color
        $ans = Read-Host "    Action [K/r/s/q]"

        switch ($ans.ToLower()) {
            'r' {
                if (-not $toRemove.ContainsKey($c.File)) { $toRemove[$c.File] = @() }
                $toRemove[$c.File] += $c.LineNum
                Write-Host "    → Marked for removal" -ForegroundColor Red
            }
            's' {
                $skipFile = $c.File
                Write-Host "    → Skipping rest of file" -ForegroundColor DarkGray
            }
            'q' {
                Write-Host "`n Saving and quitting..." -ForegroundColor Cyan
                Apply-Removals $toRemove
                return
            }
            default {
                Write-Host "    → Kept" -ForegroundColor Green
            }
        }
    }
    Apply-Removals $toRemove
}

function Apply-Removals {
    param($toRemove)
    if ($toRemove.Count -eq 0) {
        Write-Host "`n✓ No changes made." -ForegroundColor Green
        return
    }
    $totalRemoved = 0
    foreach ($file in $toRemove.Keys) {
        $lines      = Get-Content $file -Encoding UTF8
        $removeSet  = [System.Collections.Generic.HashSet[int]]($toRemove[$file])
        $newLines   = @()
        for ($i = 0; $i -lt $lines.Count; $i++) {
            if (-not $removeSet.Contains($i + 1)) { $newLines += $lines[$i] }
        }
        [System.IO.File]::WriteAllLines($file, $newLines, [System.Text.UTF8Encoding]::new($false))
        $removed = $lines.Count - $newLines.Count
        $totalRemoved += $removed
        $relPath = $file.Replace((Resolve-Path $ProjectPath).Path, ".")
        Write-Host ("  ✓ {0} — removed {1} comment line(s)" -f $relPath, $removed) -ForegroundColor Green
    }
    Write-Host ("`n✅ Done. Removed {0} comment line(s) total." -f $totalRemoved) -ForegroundColor Green
    Write-Host "   Run '.\comment_manager.ps1 restore' to undo.`n" -ForegroundColor DarkGray
}

# ── STRIP AUTO (AI patterns only) ────────────────────────────────────────────
function Run-StripAuto {
    $all = Get-AllComments
    $ai  = $all | Where-Object { $_.IsAI }

    Write-Host "`n🤖 Auto-strip: $($ai.Count) AI-pattern comments found" -ForegroundColor Yellow
    if ($ai.Count -eq 0) { Write-Host "Nothing to remove. All clean!`n" -ForegroundColor Green; return }

    Write-Host "`nPreview of what will be removed:" -ForegroundColor White
    foreach ($c in $ai) {
        $relPath = $c.File.Replace((Resolve-Path $ProjectPath).Path, ".")
        Write-Host ("  [{0,4}] {1}" -f $c.LineNum, $relPath) -ForegroundColor DarkGray
        Write-Host ("         {0}" -f $c.Text) -ForegroundColor Yellow
    }

    $confirm = Read-Host "`nRemove these $($ai.Count) comments? [y/N]"
    if ($confirm.ToLower() -ne 'y') { Write-Host "Aborted.`n"; return }

    Backup-Files
    $toRemove = @{}
    foreach ($c in $ai) {
        if (-not $toRemove.ContainsKey($c.File)) { $toRemove[$c.File] = @() }
        $toRemove[$c.File] += $c.LineNum
    }
    Apply-Removals $toRemove
}

# ── STRIP ALL ─────────────────────────────────────────────────────────────────
function Run-StripAll {
    $all = Get-AllComments
    Write-Host "`n☢️  STRIP ALL: will remove ALL $($all.Count) comments" -ForegroundColor Red
    Write-Host "   (shebangs and encoding declarations are preserved)`n" -ForegroundColor DarkGray

    $confirm = Read-Host "This is irreversible without backup. Proceed? [y/N]"
    if ($confirm.ToLower() -ne 'y') { Write-Host "Aborted.`n"; return }

    Backup-Files
    $toRemove = @{}
    foreach ($c in $all) {
        if (-not $toRemove.ContainsKey($c.File)) { $toRemove[$c.File] = @() }
        $toRemove[$c.File] += $c.LineNum
    }
    Apply-Removals $toRemove
}

# ── RESTORE ───────────────────────────────────────────────────────────────────
function Run-Restore {
    if (-not (Test-Path $BackupDir)) {
        Write-Host "`n❌ No backup found at $BackupDir" -ForegroundColor Red
        Write-Host "   Run audit or strip first to create one.`n"
        return
    }
    Write-Host "`n♻️  Restoring from $BackupDir ..." -ForegroundColor Cyan
    $backups = Get-ChildItem -Path $BackupDir -Recurse -Filter "*.py"
    foreach ($b in $backups) {
        $rel  = $b.FullName.Substring((Resolve-Path $BackupDir).Path.Length + 1)
        $dest = Join-Path (Resolve-Path $ProjectPath).Path $rel
        $destDir = Split-Path $dest -Parent
        if (-not (Test-Path $destDir)) { New-Item -ItemType Directory -Path $destDir | Out-Null }
        Copy-Item $b.FullName $dest -Force
        Write-Host ("  ✓ Restored {0}" -f $rel) -ForegroundColor Green
    }
    Write-Host "`n✅ All files restored.`n" -ForegroundColor Green
}

# ── DISPATCH ──────────────────────────────────────────────────────────────────
switch ($Mode) {
    "audit"      { Run-Audit }
    "review"     { Run-Review }
    "strip-auto" { Run-StripAuto }
    "strip-all"  { Run-StripAll }
    "restore"    { Run-Restore }
}