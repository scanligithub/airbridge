$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# GitHub-hosted Windows Actions cannot complete the interactive Windows
# confirmation required when adding a certificate to CurrentUser\Root.
# This CI harness intentionally never writes either Root store. It validates
# the standard-user profile, read-only store scope, custom-root chain logic,
# certificate fixtures, and exact cleanup. Actual Root import belongs in the
# interactive deployment flow after explicit user consent.

$runId = [Guid]::NewGuid().ToString('N')
$evidenceBase = if ($env:G01_EVIDENCE_ROOT) { $env:G01_EVIDENCE_ROOT } else { $env:RUNNER_TEMP }
$evidenceDir = Join-Path $evidenceBase "airbridge-g01-$runId"
New-Item -ItemType Directory -Path $evidenceDir -Force | Out-Null

$root = $null
$leaf = $null
$mismatchLeaf = $null
$untrustedRoot = $null
$untrustedLeaf = $null
$rootPublic = $null
$rootThumb = $null
$localMachineBefore = @()
$currentUserRootBefore = @()
$certsToRemove = [System.Collections.Generic.List[string]]::new()
$phaseFile = Join-Path $evidenceDir 'phase.txt'
function Set-G01Phase { param([string]$Name) $Name | Set-Content -LiteralPath $phaseFile -Encoding UTF8 }
Set-G01Phase 'script-start'

$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
$results = [ordered]@{
    run_id = $runId
    runner = $env:RUNNER_NAME
    windows_identity = $identity.Name
    runner_is_elevated_admin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    powershell = $PSVersionTable.PSVersion.ToString()
    process_session_id = [Diagnostics.Process]::GetCurrentProcess().SessionId
    environment_user_interactive = [Environment]::UserInteractive
    current_user_root_import = 'NOT_RUN_INTERACTIVE_WINDOWS_CONSENT_REQUIRED'
    current_user_root_unchanged = 'NOT_RUN'
    local_machine_root_unchanged = 'NOT_RUN'
    trusted_custom_root_chain_build = 'NOT_RUN'
    untrusted_custom_root_chain_rejected = 'NOT_RUN'
    wrong_host_san_fixture = 'NOT_RUN'
    cleanup = 'NOT_RUN'
}

function Get-ThumbprintSnapshot {
    param([Parameter(Mandatory)][string]$Path)
    @(
        Get-ChildItem -Path $Path -ErrorAction Stop |
            ForEach-Object { $_.Thumbprint.ToUpperInvariant() } |
            Sort-Object -Unique
    )
}

function New-G01RootCertificate {
    param([Parameter(Mandatory)][string]$Subject)
    $parameters = @{
        Type = 'Custom'
        Subject = $Subject
        KeyAlgorithm = 'RSA'
        KeyLength = 2048
        HashAlgorithm = 'SHA256'
        KeyExportPolicy = 'NonExportable'
        KeyUsageProperty = 'All'
        KeyUsage = @('CertSign', 'CRLSign')
        TextExtension = @('2.5.29.19={critical}{text}ca=TRUE&pathlength=0')
        NotBefore = (Get-Date).AddMinutes(-5)
        NotAfter = (Get-Date).AddDays(3)
        CertStoreLocation = 'Cert:\CurrentUser\My'
    }
    New-SelfSignedCertificate @parameters
}

function New-G01ServerCertificate {
    param(
        [Parameter(Mandatory)][System.Security.Cryptography.X509Certificates.X509Certificate2]$Signer,
        [Parameter(Mandatory)][string]$Subject,
        [Parameter(Mandatory)][string[]]$DnsName
    )
    $parameters = @{
        Type = 'Custom'
        Subject = $Subject
        DnsName = $DnsName
        Signer = $Signer
        KeyAlgorithm = 'RSA'
        KeyLength = 2048
        HashAlgorithm = 'SHA256'
        KeyExportPolicy = 'NonExportable'
        KeyUsageProperty = 'All'
        KeyUsage = @('DigitalSignature', 'KeyEncipherment')
        TextExtension = @(
            '2.5.29.19={critical}{text}ca=FALSE',
            '2.5.29.37={text}1.3.6.1.5.5.7.3.1'
        )
        NotBefore = (Get-Date).AddMinutes(-5)
        NotAfter = (Get-Date).AddDays(2)
        CertStoreLocation = 'Cert:\CurrentUser\My'
    }
    New-SelfSignedCertificate @parameters
}

function Test-CustomRootChain {
    param(
        [Parameter(Mandatory)][System.Security.Cryptography.X509Certificates.X509Certificate2]$Certificate,
        [Parameter(Mandatory)][System.Security.Cryptography.X509Certificates.X509Certificate2]$TrustedRoot,
        [Parameter(Mandatory)][bool]$ExpectedSuccess
    )
    $chain = [System.Security.Cryptography.X509Certificates.X509Chain]::new()
    try {
        $chain.ChainPolicy.TrustMode = [System.Security.Cryptography.X509Certificates.X509ChainTrustMode]::CustomRootTrust
        $chain.ChainPolicy.CustomTrustStore.Add($TrustedRoot)
        $chain.ChainPolicy.RevocationMode = [System.Security.Cryptography.X509Certificates.X509RevocationMode]::NoCheck
        $chain.ChainPolicy.VerificationFlags = [System.Security.Cryptography.X509Certificates.X509VerificationFlags]::NoFlag
        $ok = $chain.Build($Certificate)
        $status = ($chain.ChainStatus | ForEach-Object { "$($_.Status): $($_.StatusInformation.Trim())" }) -join '; '
        if ($ExpectedSuccess -and -not $ok) {
            throw "Expected custom-root chain to build, but it failed: $status"
        }
        if (-not $ExpectedSuccess -and $ok) {
            throw 'A chain that does not terminate at the configured custom root unexpectedly built successfully.'
        }
        return $ok
    }
    finally {
        $chain.Dispose()
    }
}

try {
    Set-G01Phase 'before-import-pki-module'
    Import-Module PKI -ErrorAction Stop
    Set-G01Phase 'pki-module-loaded'
    Write-Host 'G-01 noninteractive Windows store-scope and custom-chain checks'
    if ($results.runner_is_elevated_admin) {
        throw 'G-01 store-scope test must run as a standard, non-elevated Windows user.'
    }
    Write-Host "Identity: $($results.windows_identity)"
    Write-Host "Elevated admin token: $($results.runner_is_elevated_admin)"
    Write-Host "PowerShell: $($results.powershell)"
    Write-Host "SessionId: $($results.process_session_id)"
    Write-Host "Environment.UserInteractive: $($results.environment_user_interactive)"

    Set-G01Phase 'before-localmachine-snapshot'
    $localMachineBefore = Get-ThumbprintSnapshot -Path 'Cert:\LocalMachine\Root'
    Set-G01Phase 'localmachine-snapshot-complete'
    $results.local_machine_root_before_count = $localMachineBefore.Count
    if ($localMachineBefore.Count -eq 0) {
        throw 'LocalMachine Root snapshot unexpectedly empty; refusing an inconclusive scope test.'
    }

    Set-G01Phase 'before-currentuser-root-snapshot'
    $currentUserRootBefore = Get-ThumbprintSnapshot -Path 'Cert:\CurrentUser\Root'
    Set-G01Phase 'currentuser-root-snapshot-complete'
    $results.current_user_root_before_count = $currentUserRootBefore.Count

    Set-G01Phase 'before-create-test-certificates'
    $root = New-G01RootCertificate -Subject "CN=AirBridge G01 CI Custom Root $runId"
    $leaf = New-G01ServerCertificate -Signer $root -Subject 'CN=localhost' -DnsName @('localhost')
    $mismatchLeaf = New-G01ServerCertificate -Signer $root -Subject 'CN=wrong-host.invalid' -DnsName @('wrong-host.invalid')
    $untrustedRoot = New-G01RootCertificate -Subject "CN=AirBridge G01 CI Untrusted Root $runId"
    $untrustedLeaf = New-G01ServerCertificate -Signer $untrustedRoot -Subject 'CN=localhost Untrusted' -DnsName @('localhost')

    Set-G01Phase 'test-certificates-created'
    foreach ($cert in @($root, $leaf, $mismatchLeaf, $untrustedRoot, $untrustedLeaf)) {
        if (-not $cert.HasPrivateKey) {
            throw "Expected CurrentUser\My private key for $($cert.Subject)"
        }
        $certsToRemove.Add($cert.Thumbprint.ToUpperInvariant())
    }

    $rootThumb = $root.Thumbprint.ToUpperInvariant()
    $results.root_thumbprint = $rootThumb
    $results.leaf_thumbprint = $leaf.Thumbprint.ToUpperInvariant()
    $results.root_private_key_present_in_current_user_my = $root.HasPrivateKey
    $results.leaf_private_key_present_in_current_user_my = $leaf.HasPrivateKey
    if ($currentUserRootBefore -contains $rootThumb) {
        throw 'Disposable test root unexpectedly already exists in CurrentUser Root.'
    }

    Set-G01Phase 'before-export-root-public-cer'
    $rootCer = Join-Path $evidenceDir 'g01-root-public.cer'
    Export-Certificate -Cert $root -FilePath $rootCer -Type CERT -Force | Out-Null
    Set-G01Phase 'root-public-cer-exported'
    $rootPublic = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new($rootCer)
    if ($rootPublic.HasPrivateKey) {
        throw 'Exported CA CER unexpectedly contains a private key.'
    }
    $results.root_public_cer_has_private_key = $rootPublic.HasPrivateKey

    Set-G01Phase 'before-trusted-custom-root-chain-build'
    [void](Test-CustomRootChain -Certificate $leaf -TrustedRoot $rootPublic -ExpectedSuccess $true)
    $results.trusted_custom_root_chain_build = 'PASS'
    Set-G01Phase 'trusted-custom-root-chain-pass'

    Set-G01Phase 'before-untrusted-custom-root-chain-build'
    [void](Test-CustomRootChain -Certificate $untrustedLeaf -TrustedRoot $rootPublic -ExpectedSuccess $false)
    $results.untrusted_custom_root_chain_rejected = 'PASS'
    Set-G01Phase 'untrusted-custom-root-chain-rejected'

    $leafDnsNames = @($leaf.DnsNameList | ForEach-Object { $_.Unicode })
    $mismatchDnsNames = @($mismatchLeaf.DnsNameList | ForEach-Object { $_.Unicode })
    if ($leafDnsNames -notcontains 'localhost' -or $mismatchDnsNames -contains 'localhost') {
        throw 'The positive / wrong-host DNS SAN fixtures are not configured as expected.'
    }
    $results.wrong_host_san_fixture = 'PASS'
    Set-G01Phase 'san-fixtures-verified'

    $currentUserRootAfterTest = Get-ThumbprintSnapshot -Path 'Cert:\CurrentUser\Root'
    if (Compare-Object -ReferenceObject $currentUserRootBefore -DifferenceObject $currentUserRootAfterTest) {
        throw 'CurrentUser Root changed during noninteractive CI test; this harness must remain read-only for Root.'
    }
    $results.current_user_root_unchanged = 'PASS'
    $results.current_user_root_after_count = $currentUserRootAfterTest.Count

    $localMachineAfterTest = Get-ThumbprintSnapshot -Path 'Cert:\LocalMachine\Root'
    if (Compare-Object -ReferenceObject $localMachineBefore -DifferenceObject $localMachineAfterTest) {
        throw 'LocalMachine Root changed during noninteractive CI test.'
    }
    $results.local_machine_root_unchanged = 'PASS'
    $results.local_machine_root_after_count = $localMachineAfterTest.Count

    Write-Host 'Noninteractive G-01 CI checks passed. CurrentUser Root import was intentionally NOT RUN; deployment import requires an interactive Windows consent session.'
}
catch {
    $results.failure = $_.Exception.ToString()
    throw
}
finally {
    Set-G01Phase 'cleanup-start'
    if ($rootPublic) { $rootPublic.Dispose() }

    foreach ($thumb in $certsToRemove) {
        Remove-Item -LiteralPath "Cert:\CurrentUser\My\$thumb" -ErrorAction SilentlyContinue
    }
    $rootCerPath = Join-Path $evidenceDir 'g01-root-public.cer'
    if (Test-Path -LiteralPath $rootCerPath) {
        Remove-Item -LiteralPath $rootCerPath -Force -ErrorAction SilentlyContinue
    }

    if ($currentUserRootBefore.Count -gt 0) {
        $currentUserRootAfterCleanup = Get-ThumbprintSnapshot -Path 'Cert:\CurrentUser\Root'
        $currentUserDelta = Compare-Object -ReferenceObject $currentUserRootBefore -DifferenceObject $currentUserRootAfterCleanup
        if ($currentUserDelta) {
            $results.current_user_root_unchanged = 'FAIL'
            $results.cleanup = 'FAIL'
            $results.current_user_root_cleanup_delta = ($currentUserDelta | Out-String)
        }
        elseif ($results.current_user_root_unchanged -ne 'FAIL') {
            $results.current_user_root_unchanged = 'PASS'
        }

        $localMachineAfterCleanup = Get-ThumbprintSnapshot -Path 'Cert:\LocalMachine\Root'
        $cleanupDelta = Compare-Object -ReferenceObject $localMachineBefore -DifferenceObject $localMachineAfterCleanup
        if ($cleanupDelta) {
            $results.local_machine_root_unchanged = 'FAIL'
            $results.cleanup = 'FAIL'
            $results.local_machine_root_cleanup_delta = ($cleanupDelta | Out-String)
        }
        elseif ($results.local_machine_root_unchanged -ne 'FAIL') {
            $results.local_machine_root_unchanged = 'PASS'
        }

        $remainingMy = @($certsToRemove | Where-Object { Test-Path -LiteralPath "Cert:\CurrentUser\My\$_" })
        $results.certificate_cleanup_verified = ($remainingMy.Count -eq 0)
        if (-not $results.certificate_cleanup_verified) {
            $results.cleanup = 'FAIL'
            $results.remaining_current_user_my_thumbprints = $remainingMy
        }
        elseif ($results.cleanup -ne 'FAIL') {
            $results.cleanup = 'PASS'
        }

        $results.current_user_root_after_cleanup_count = $currentUserRootAfterCleanup.Count
        $results.local_machine_root_after_cleanup_count = $localMachineAfterCleanup.Count
    }
    else {
        $results.cleanup = 'FAIL'
        if (-not $results.failure) { $results.failure = 'Store baseline was not captured; cleanup scope could not be verified.' }
    }

    Set-G01Phase ('cleanup-complete-' + $results.cleanup)
    $resultPath = Join-Path $evidenceDir 'g01-result.json'
    $results | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $resultPath -Encoding UTF8
    Get-Content -LiteralPath $resultPath | Write-Host

    if ($results.cleanup -ne 'PASS' -or $results.local_machine_root_unchanged -ne 'PASS' -or $results.current_user_root_unchanged -ne 'PASS') {
        throw 'G-01 cleanup / CurrentUser Root / LocalMachine Root integrity check failed.'
    }
}
