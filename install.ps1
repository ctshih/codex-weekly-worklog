param([string]$Destination='')
$ErrorActionPreference='Stop'
if(-not $Destination){
 $skillHome=if($env:CODEX_HOME){$env:CODEX_HOME}else{Join-Path $env:USERPROFILE '.codex'}
 $Destination=Join-Path $skillHome 'skills\codex-weekly-worklog'
}
$source=Join-Path $PSScriptRoot 'codex-weekly-worklog'
if(Test-Path -LiteralPath $Destination){throw 'An existing skill will not be overwritten. Inspect it before updating.'}
$null=New-Item -ItemType Directory -Path $Destination
foreach($relative in @('SKILL.md','scripts','assets','agents')){
 $item=Join-Path $source $relative
 if(Test-Path -LiteralPath $item -PathType Leaf){Copy-Item -LiteralPath $item -Destination $Destination}else{
  $target=Join-Path $Destination $relative
  $null=New-Item -ItemType Directory -Path $target
  Get-ChildItem -LiteralPath $item -File | ForEach-Object {Copy-Item -LiteralPath $_.FullName -Destination $target}
 }
}
Write-Output $Destination
