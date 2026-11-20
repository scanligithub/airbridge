$ErrorActionPreference = 'Stop'

Import-Module Microsoft.PowerShell.LocalAccounts -ErrorAction Stop

$runId = [Guid]::NewGuid().ToString('N')
$userName = "g01ci$($runId.Substring(0, 8))"
$password = ConvertTo-SecureString ("Aa1!" + [Guid]::NewGuid().ToString('N')) -AsPlainText -Force
$evidenceRoot = Join-Path $env:RUNNER_TEMP "g01-standard-user-$runId"
New-Item -ItemType Directory -Path $evidenceRoot -Force | Out-Null

$userCreated = $false
try {
    New-LocalUser -Name $userName -Password $password -PasswordNeverExpires -AccountNeverExpires -Description 'Temporary AirBridge G-01 CI standard user' | Out-Null
    $userCreated = $true

    $account = Get-LocalUser -Name $userName -ErrorAction Stop
    $adminGroup = Get-LocalGroup -SID 'S-1-5-32-544' -ErrorAction Stop
    $adminMembers = @(Get-LocalGroupMember -Group $adminGroup | ForEach-Object { $_.SID.Value })
    if ($adminMembers -contains $account.SID.Value) {
        throw 'Temporary G-01 test identity must not be a member of the local Administrators group.'
    }

    $aclIdentity = "$env:COMPUTERNAME\$userName"
    $grantSpec = $aclIdentity + ':(OI)(CI)F'
    & icacls.exe $evidenceRoot /grant $grantSpec | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not grant the temporary standard user access to the evidence folder.'
    }

    $testScript = Join-Path $PSScriptRoot 'g01-currentuser-trust.ps1'
    $pwshPath = (Get-Command pwsh.exe -ErrorAction Stop).Source

    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $pwshPath
    $psi.Arguments = '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $testScript + '"'
    $psi.WorkingDirectory = (Get-Location).Path
    $psi.UserName = $userName
    $psi.Domain = $env:COMPUTERNAME
    $psi.Password = $password
    $psi.LoadUserProfile = $true
    $psi.Environment['G01_EVIDENCE_ROOT'] = $evidenceRoot
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true

    Write-Host "Launching G-01 test under standard user: $aclIdentity"
    Write-Host "Evidence root: $evidenceRoot"
    $process = [System.Diagnostics.Process]::Start($psi)
    Write-Host "Standard-user child PID: $($process.Id)"
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()

    if (-not $process.WaitForExit(120000)) {
        Write-Host 'Standard-user process timeout. Last phase markers:'
        Get-ChildItem -Path $evidenceRoot -Filter 'phase.txt' -Recurse -File -ErrorAction SilentlyContinue | ForEach-Object { Write-Host (Get-Content -LiteralPath $_.FullName -Raw) }
        try { $process.Kill($true) } catch { }
        throw 'Standard-user G-01 test exceeded the 120-second process timeout.'
    }
    $process.WaitForExit()

    $stdout = $stdoutTask.GetAwaiter().GetResult()
    $stderr = $stderrTask.GetAwaiter().GetResult()
    if ($stdout) { Write-Host $stdout }
    if ($stderr) { Write-Host $stderr }

    if ($process.ExitCode -ne 0) {
        throw "Standard-user G-01 test process exited with code $($process.ExitCode)."
    }

    $resultFiles = @(Get-ChildItem -Path $evidenceRoot -Filter 'g01-result.json' -Recurse -File)
    if ($resultFiles.Count -lt 1) {
        throw 'Standard-user G-01 test did not produce a result JSON.'
    }
    foreach ($file in $resultFiles) {
        Get-Content -LiteralPath $file.FullName | Write-Host
    }
    Write-Host 'Standard-user CurrentUser trust test completed.'
}
finally {
    if ($userCreated) {
        $userSid = $account.SID.Value
        Remove-LocalUser -Name $userName -ErrorAction SilentlyContinue

        # A forcibly terminated child may not run the test script's finally block.
        # Remove the temporary Windows profile by its exact SID so any residual
        # CurrentUser certificate material is removed with that disposable profile.
        $profileDeadline = (Get-Date).AddSeconds(20)
        do {
            $profile = Get-CimInstance -ClassName Win32_UserProfile -Filter ("SID='" + $userSid + "'") -ErrorAction SilentlyContinue
            if ($null -eq $profile -or -not $profile.Loaded) { break }
            Start-Sleep -Seconds 1
        } while ((Get-Date) -lt $profileDeadline)

        if ($profile) {
            if ($profile.Loaded) {
                throw "Temporary standard-user profile is still loaded; refusing to leave its certificate store behind (SID $userSid)."
            }
            Remove-CimInstance -InputObject $profile -ErrorAction Stop
        }
    }
}
