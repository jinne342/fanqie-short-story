param(
    [string]$Destination = (Join-Path $HOME ".dsh\skills")
)

$source = $PSScriptRoot
$target = Join-Path $Destination "fanqie-short-story"

New-Item -ItemType Directory -Force $target | Out-Null

$items = @("SKILL.md", "prompt.md", "README.md", "LICENSE", "assets", "references", "scripts", "examples")
foreach ($item in $items) {
    Copy-Item -Path (Join-Path $source $item) -Destination $target -Recurse -Force
}

Write-Host "Installed to: $target"
Write-Host "Start a new session and invoke the fanqie-short-story skill."
