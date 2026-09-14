[CmdletBinding()]
param(
    [Parameter()]
    [string]$CompilerPath,

    [Parameter()]
    [string]$PythonPath,

    [Parameter()]
    [string]$OutputPath,

    [Parameter()]
    [switch]$NoDecompile
)

$ErrorActionPreference = "Stop"
$HelpRoot = $PSScriptRoot
$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $HelpRoot "..\..")).Path
$ProjectFile = Join-Path $HelpRoot "SemiAutoProbeHelp.hhp"
$CompiledFile = Join-Path $HelpRoot "build\SemiAutoProbeHelp.chm"
$Verifier = Join-Path $HelpRoot "tools\verify_help.py"

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $RepoRoot "src\semi_auto_probe\assets\SemiAutoProbeHelp.chm"
} elseif (-not [System.IO.Path]::IsPathRooted($OutputPath)) {
    $OutputPath = Join-Path (Get-Location).Path $OutputPath
}

function Find-Python([string]$ExplicitPath) {
    if (-not [string]::IsNullOrWhiteSpace($ExplicitPath)) {
        if (Test-Path -LiteralPath $ExplicitPath -PathType Leaf) {
            return @{ Exe = (Resolve-Path -LiteralPath $ExplicitPath).Path; Args = @() }
        }
        throw "指定的 Python 不存在：$ExplicitPath"
    }
    if (-not [string]::IsNullOrWhiteSpace($env:PYTHON) -and (Test-Path -LiteralPath $env:PYTHON -PathType Leaf)) {
        return @{ Exe = (Resolve-Path -LiteralPath $env:PYTHON).Path; Args = @() }
    }
    if (-not [string]::IsNullOrWhiteSpace($env:VIRTUAL_ENV)) {
        $virtualPython = Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"
        if (Test-Path -LiteralPath $virtualPython -PathType Leaf) {
            return @{ Exe = (Resolve-Path -LiteralPath $virtualPython).Path; Args = @() }
        }
    }
    $repoPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $repoPython -PathType Leaf) {
        return @{ Exe = (Resolve-Path -LiteralPath $repoPython).Path; Args = @() }
    }
    $pyLauncher = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($null -ne $pyLauncher) {
        return @{ Exe = $pyLauncher.Source; Args = @("-3") }
    }
    $python = Get-Command "python.exe" -ErrorAction SilentlyContinue
    if ($null -ne $python) {
        return @{ Exe = $python.Source; Args = @() }
    }
    throw "找不到 Python 3；无法运行 CHM 源文件校验。"
}

function Find-HelpCompiler([string]$ExplicitPath) {
    $candidates = [System.Collections.Generic.List[string]]::new()
    if (-not [string]::IsNullOrWhiteSpace($ExplicitPath)) {
        $candidates.Add($ExplicitPath)
    }
    if (-not [string]::IsNullOrWhiteSpace($env:HTML_HELP_COMPILER)) {
        $candidates.Add($env:HTML_HELP_COMPILER)
    }
    $command = Get-Command "hhc.exe" -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        $candidates.Add($command.Source)
    }
    $candidates.Add((Join-Path $RepoRoot ".runtime\htmlhelp\hhc.exe"))
    $candidates.Add((Join-Path $RepoRoot ".runtime\htmlhelp\HTML Help Workshop\hhc.exe"))
    if (-not [string]::IsNullOrWhiteSpace(${env:ProgramFiles(x86)})) {
        $candidates.Add((Join-Path ${env:ProgramFiles(x86)} "HTML Help Workshop\hhc.exe"))
    }
    if (-not [string]::IsNullOrWhiteSpace($env:ProgramFiles)) {
        $candidates.Add((Join-Path $env:ProgramFiles "HTML Help Workshop\hhc.exe"))
    }
    foreach ($candidate in $candidates) {
        if (-not [string]::IsNullOrWhiteSpace($candidate) -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    throw "找不到 hhc.exe。请安装 Microsoft HTML Help Workshop，或用 -CompilerPath 指定编译器。脚本不会下载或安装工具。"
}

function Write-CompilerMetadata([string]$Path, [string]$Text) {
    # HTML Help Compiler 4.74 can crash on UTF-8 project metadata. Keep the
    # checked-in files UTF-8 and create short-lived GBK/CP936 copies for hhc.
    $encoderFallback = [System.Text.EncoderExceptionFallback]::new()
    $decoderFallback = [System.Text.DecoderExceptionFallback]::new()
    $cp936 = [System.Text.Encoding]::GetEncoding(936, $encoderFallback, $decoderFallback)
    $crlfText = [regex]::Replace($Text, "\r\n|\r|\n", "`r`n")
    [System.IO.File]::WriteAllText($Path, $crlfText, $cp936)
}

function Read-Utf8Source([string]$Path) {
    $utf8Strict = [System.Text.UTF8Encoding]::new($false, $true)
    return [System.IO.File]::ReadAllText($Path, $utf8Strict)
}
$python = Find-Python $PythonPath
$sourceVerifyArguments = @($python.Args) + @($Verifier, "--root", $HelpRoot)
& $python.Exe @sourceVerifyArguments
if ($LASTEXITCODE -ne 0) {
    throw "CHM 源文件校验失败；未开始编译。"
}

$compiler = Find-HelpCompiler $CompilerPath
$buildDirectory = Split-Path -Parent $CompiledFile
New-Item -ItemType Directory -Path $buildDirectory -Force | Out-Null
if (Test-Path -LiteralPath $CompiledFile -PathType Leaf) {
    Remove-Item -LiteralPath $CompiledFile -Force
}

$tempToken = [Guid]::NewGuid().ToString("N")
$tempStem = "SemiAutoProbeHelp.compile-$tempToken"
$tempProject = Join-Path $HelpRoot "$tempStem.hhp"
$tempContents = Join-Path $HelpRoot "$tempStem.hhc"
$tempIndex = Join-Path $HelpRoot "$tempStem.hhk"
$tempMap = Join-Path $HelpRoot "$tempStem.h"
$tempFiles = @($tempProject, $tempContents, $tempIndex, $tempMap)
$compilerExitCode = $null

try {
    $projectText = Read-Utf8Source $ProjectFile
    $projectText = $projectText.Replace("SemiAutoProbeHelp.hhc", "$tempStem.hhc")
    $projectText = $projectText.Replace("SemiAutoProbeHelp.hhk", "$tempStem.hhk")
    $projectText = $projectText.Replace("#include context_ids.h", "#include $tempStem.h")
    Write-CompilerMetadata $tempProject $projectText
    Write-CompilerMetadata $tempContents (Read-Utf8Source (Join-Path $HelpRoot "SemiAutoProbeHelp.hhc"))
    Write-CompilerMetadata $tempIndex (Read-Utf8Source (Join-Path $HelpRoot "SemiAutoProbeHelp.hhk"))
    Write-CompilerMetadata $tempMap (Read-Utf8Source (Join-Path $HelpRoot "context_ids.h"))

    Write-Host "Compiling $ProjectFile through temporary CP936/CRLF metadata"
    Write-Host "Compiler: $compiler"
    $started = Get-Date
    Push-Location $HelpRoot
    try {
        & $compiler $tempProject
        $compilerExitCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }
} finally {
    foreach ($tempFile in $tempFiles) {
        if (Test-Path -LiteralPath $tempFile -PathType Leaf) {
            Remove-Item -LiteralPath $tempFile -Force
        }
    }
}

# hhc.exe versions are inconsistent about success codes (0 and 1 are both seen).
# The generated artifact and the verifier are authoritative.
if (-not (Test-Path -LiteralPath $CompiledFile -PathType Leaf)) {
    throw "hhc.exe 未生成 $CompiledFile（返回码 $compilerExitCode）。"
}
$compiledItem = Get-Item -LiteralPath $CompiledFile
if ($compiledItem.LastWriteTime -lt $started.AddSeconds(-2)) {
    throw "生成的 CHM 不是本次构建产物：$CompiledFile"
}

$verifyArguments = @($Verifier, "--root", $HelpRoot, "--chm", $CompiledFile)
if ($NoDecompile) {
    $verifyArguments += "--no-decompile"
}
$compiledVerifyArguments = @($python.Args) + $verifyArguments
& $python.Exe @compiledVerifyArguments
if ($LASTEXITCODE -ne 0) {
    throw "已生成 CHM，但成品校验失败（hhc.exe 返回码 $compilerExitCode）。"
}

$outputDirectory = Split-Path -Parent $OutputPath
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
Copy-Item -LiteralPath $CompiledFile -Destination $OutputPath -Force
$outputItem = Get-Item -LiteralPath $OutputPath
Write-Host "CHM ready: $($outputItem.FullName) ($($outputItem.Length) bytes; hhc.exe code $compilerExitCode)"
