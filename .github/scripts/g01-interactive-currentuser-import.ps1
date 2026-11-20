[CmdletBinding()]
param(
    [string]$EvidenceDirectory
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# G-01B is an interactive, disposable Windows PoC. Run this script directly
# from the target user's visible, non-elevated desktop PowerShell session.
# It never writes LocalMachine stores and never exports a private key.

$runId = [Guid]::NewGuid().ToString('N')
if ([string]::IsNullOrWhiteSpace($EvidenceDirectory)) {
    $desktop = [Environment]::GetFolderPath('Desktop')
    if ([string]::IsNullOrWhiteSpace($desktop) -or -not (Test-Path -LiteralPath $desktop)) {
        $desktop = [Environment]::GetFolderPath('MyDocuments')
    }
    $EvidenceDirectory = Join-Path $desktop "AirBridge-G01B-$runId"
}
New-Item -ItemType Directory -Path $EvidenceDirectory -Force | Out-Null

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
$processSessionId = [Diagnostics.Process]::GetCurrentProcess().SessionId

$results = [ordered]@{
    gate = 'G-01B'
    run_id = $runId
    started_at_utc = [DateTime]::UtcNow.ToString('o')
    windows_version = [Environment]::OSVersion.VersionString
    powershell_version = $PSVersionTable.PSVersion.ToString()
    current_user_context = 'NOT_RUN'
    process_session_id = $processSessionId
    environment_user_interactive = [Environment]::UserInteractive
    elevated_admin_token = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    current_user_root_import = 'NOT_RUN'
    application_consent = 'NOT_RUN'
    windows_root_confirmation = 'NOT_RUN'
    default_chain_before_import = 'NOT_RUN'
    default_chain_after_import = 'NOT_RUN'
    current_user_root_readback = 'NOT_RUN'
    local_machine_root_unchanged = 'NOT_RUN'
    current_user_root_restored_after_cleanup = 'NOT_RUN'
    temporary_current_user_my_cleanup = 'NOT_RUN'
    cleanup = 'NOT_RUN'
}

$root = $null
$leaf = $null
$rootPublic = $null
$rootThumb = $null
$leafThumb = $null
$rootWasAddedByThisRun = $false
$currentUserRootBefore = @()
$localMachineRootBefore = @()
$evidencePath = Join-Path $EvidenceDirectory 'g01b-result.json'
$rootCerPath = Join-Path $EvidenceDirectory 'g01b-root-public.cer'

function Get-ThumbprintSnapshot {
    param([Parameter(Mandatory)][string]$StorePath)
    @(
        Get-ChildItem -Path $StorePath -ErrorAction Stop |
            ForEach-Object { $_.Thumbprint.ToUpperInvariant() } |
            Sort-Object -Unique
    )
}

function Test-DefaultWindowsChain {
    param([Parameter(Mandatory)][Security.Cryptography.X509Certificates.X509Certificate2]$Certificate)

    $chain = [Security.Cryptography.X509Certificates.X509Chain]::new()
    try {
        $chain.ChainPolicy.RevocationMode = [Security.Cryptography.X509Certificates.X509RevocationMode]::NoCheck
        $chain.ChainPolicy.VerificationFlags = [Security.Cryptography.X509Certificates.X509VerificationFlags]::NoFlag
        [void]$chain.ChainPolicy.ApplicationPolicy.Add(
            [Security.Cryptography.Oid]::new('1.3.6.1.5.5.7.3.1')
        )
        $built = $chain.Build($Certificate)
        $statuses = @(
            $chain.ChainStatus |
                ForEach-Object { $_.Status.ToString() }
        )
        return [pscustomobject]@{
            Success = $built
            Status = ($statuses -join ',')
        }
    }
    finally {
        $chain.Dispose()
    }
}

function Test-SameSet {
    param([string[]]$Before, [string[]]$After)
    return $null -eq (Compare-Object -ReferenceObject $Before -DifferenceObject $After)
}

try {
    if (-not [Environment]::UserInteractive -or $processSessionId -eq 0) {
        throw 'G-01B must run in a visible interactive Windows desktop session, not a service or hosted runner session.'
    }
    if ($results.elevated_admin_token) {
        throw 'G-01B must run without an elevated Administrator token. Close this shell and reopen PowerShell normally.'
    }
    if (-not (Get-Module -ListAvailable -Name PKI)) {
        throw 'The Windows PKI PowerShell module is unavailable.'
    }

    $results.current_user_context = 'PASS_NON_ELEVATED_INTERACTIVE_SESSION'

    $localMachineRootBefore = Get-ThumbprintSnapshot -StorePath 'Cert:\LocalMachine\Root'
    $currentUserRootBefore = Get-ThumbprintSnapshot -StorePath 'Cert:\CurrentUser\Root'
    $results.local_machine_root_before_count = $localMachineRootBefore.Count
    $results.current_user_root_before_count = $currentUserRootBefore.Count

    Import-Module PKI -ErrorAction Stop

    $root = New-SelfSignedCertificate @{
        Type = 'Custom'
        Subject = "CN=AirBridge G-01B Disposable Root $runId"
        KeyAlgorithm = 'RSA'
        KeyLength = 3072
        HashAlgorithm = 'SHA256'
        KeyExportPolicy = 'NonExportable'
        KeyUsageProperty = 'All'
        KeyUsage = @('CertSign', 'CRLSign')
        TextExtension = @('2.5.29.19={critical}{text}ca=TRUE&pathlength=0')
        NotBefore = (Get-Date).AddMinutes(-5)
        NotAfter = (Get-Date).AddDays(2)
        CertStoreLocation = 'Cert:\CurrentUser\My'
    }

    $leaf = New-SelfSignedCertificate @{
        Type = 'Custom'
        Subject = 'CN=localhost'
        DnsName = @('localhost')
        Signer = $root
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
        NotAfter = (Get-Date).AddDays(1)
        CertStoreLocation = 'Cert:\CurrentUser\My'
    }

    $rootThumb = $root.Thumbprint.ToUpperInvariant()
    $leafThumb = $leaf.Thumbprint.ToUpperInvariant()
    $results.root_thumbprint = $rootThumb
    $results.leaf_thumbprint = $leafThumb
    $results.root_private_key_non_exportable = $root.HasPrivateKey
    $results.leaf_private_key_non_exportable = $leaf.HasPrivateKey

    if (-not $root.HasPrivateKey -or -not $leaf.HasPrivateKey) {
        throw 'The disposable Root and Leaf private keys were not created in CurrentUser\My.'
    }
    if ($currentUserRootBefore -contains $rootThumb) {
        throw 'The new disposable Root thumbprint unexpectedly exists in CurrentUser\Root before import.'
    }

    $leafDnsNames = @($leaf.DnsNameList | ForEach-Object { $_.Unicode })
    if ($leafDnsNames -notcontains 'localhost') {
        throw 'The Leaf certificate does not contain the required localhost DNS SAN.'
    }

    Export-Certificate -Cert $root -FilePath $rootCerPath -Type CERT -Force | Out-Null
    $rootPublic = [Security.Cryptography.X509Certificates.X509Certificate2]::new($rootCerPath)
    if ($rootPublic.HasPrivateKey) {
        throw 'The exported Root CER unexpectedly contains private-key material.'
    }
    $results.root_public_cer_has_private_key = $false
    $results.localhost_dns_san = 'PASS'

    $beforeChain = Test-DefaultWindowsChain -Certificate $leaf
    if ($beforeChain.Success) {
        throw 'The default Windows chain unexpectedly trusted the Leaf before the test Root was imported.'
    }
    $results.default_chain_before_import = 'REJECTED'
    $results.default_chain_before_import_status = $beforeChain.Status

    Write-Host ''
    Write-Host 'G-01B: disposable CurrentUser Root trust test'
    Write-Host "Target store: Cert:\CurrentUser\Root"
    Write-Host "Root Subject: $($root.Subject)"
    Write-Host "Root Thumbprint: $rootThumb"
    Write-Host "Root Valid Until: $($root.NotAfter.ToString('o'))"
    Write-Host 'Private key: non-exportable; remains in CurrentUser\My'
    Write-Host 'LocalMachine stores: read-only; they will not be modified.'
    Write-Host ''
    Write-Host 'This is a disposable test CA, not a production AirBridge CA.'
    Write-Host 'The script will remove only this run''s exact Root / Leaf thumbprints after verification.'
    Write-Host ''

    $typedThumbprint = (Read-Host 'To give AirBridge-level consent for this exact test Root, type the full Root Thumbprint').Trim().Replace(' ', '').ToUpperInvariant()
    if ($typedThumbprint -ne $rootThumb) {
        $results.application_consent = 'DECLINED_OR_MISMATCHED'
        throw 'Application-level consent was not confirmed. No Root import was attempted.'
    }
    $results.application_consent = 'APPROVED_FOR_EXACT_THUMBPRINT'

    # This is the only Root-store write in the script. No registry or native
    # API bypass is used. The Windows trust confirmation, if presented, must
    # be visibly approved by the operator.
    Import-Certificate -FilePath $rootCerPath -CertStoreLocation 'Cert:\CurrentUser\Root' -ErrorAction Stop | Out-Null
    $rootWasAddedByThisRun = $true

    $rootReadback = Get-ThumbprintSnapshot -StorePath 'Cert:\CurrentUser\Root'
    if ($rootReadback -notcontains $rootThumb) {
        throw 'Import-Certificate returned, but the exact Root thumbprint was not found in CurrentUser\Root.'
    }
    $results.current_user_root_readback = 'PASS'

    $windowsConfirmation = (Read-Host 'Did Windows display its Root trust confirmation and did you approve it? Type WINDOWS-ROOT-CONFIRMED').Trim()
    if ($windowsConfirmation -cne 'WINDOWS-ROOT-CONFIRMED') {
        $results.windows_root_confirmation = 'NOT_CONFIRMED_BY_OPERATOR'
        throw 'The Windows Root trust confirmation was not confirmed by the operator; G-01B cannot be marked PASS.'
    }
    $results.windows_root_confirmation = 'CONFIRMED_BY_OPERATOR'

    $afterChain = Test-DefaultWindowsChain -Certificate $leaf
    if (-not $afterChain.Success) {
        throw "The default Windows chain engine did not trust the Leaf after CurrentUser Root import: $($afterChain.Status)"
    }
    $results.default_chain_after_import = 'PASS'
    $results.current_user_root_import = 'PASS'

    $localMachineAfterImport = Get-ThumbprintSnapshot -StorePath 'Cert:\LocalMachine\Root'
    if (-not (Test-SameSet -Before $localMachineRootBefore -After $localMachineAfterImport)) {
        throw 'LocalMachine\Root changed during the CurrentUser trust test.'
    }
    $results.local_machine_root_unchanged_after_import = 'PASS'

    Write-Host ''
    Write-Host 'G-01B import and Windows chain validation passed. The disposable Root will now be removed.'
}
catch {
    if (-not $results.failure) {
        $results.failure = $_.Exception.Message
    }
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
}
finally {
    try {
        if ($rootThumb -and $rootWasAddedByThisRun) {
            $rootPath = "Cert:\CurrentUser\Root\$rootThumb"
            if (Test-Path -LiteralPath $rootPath) {
                Remove-Item -LiteralPath $rootPath -Force -ErrorAction Stop
            }
        }

        if ($leafThumb) {
            $leafPath = "Cert:\CurrentUser\My\$leafThumb"
            if (Test-Path -LiteralPath $leafPath) {
                Remove-Item -LiteralPath $leafPath -Force -ErrorAction Stop
            }
        }
        if ($rootThumb) {
            $rootMyPath = "Cert:\CurrentUser\My\$rootThumb"
            if (Test-Path -LiteralPath $rootMyPath) {
                Remove-Item -LiteralPath $rootMyPath -Force -ErrorAction Stop
            }
        }

        if ($rootPublic) { $rootPublic.Dispose() }
        if ($leaf) { $leaf.Dispose() }
        if ($root) { $root.Dispose() }
        if (Test-Path -LiteralPath $rootCerPath) {
            Remove-Item -LiteralPath $rootCerPath -Force -ErrorAction Stop
        }

        if ($currentUserRootBefore.Count -gt 0 -or $localMachineRootBefore.Count -gt 0) {
            $currentUserRootAfter = Get-ThumbprintSnapshot -StorePath 'Cert:\CurrentUser\Root'
            $localMachineRootAfter = Get-ThumbprintSnapshot -StorePath 'Cert:\LocalMachine\Root'
            $results.current_user_root_after_cleanup_count = $currentUserRootAfter.Count
            $results.local_machine_root_after_cleanup_count = $localMachineRootAfter.Count

            if (Test-SameSet -Before $currentUserRootBefore -After $currentUserRootAfter) {
                $results.current_user_root_restored_after_cleanup = 'PASS'
            }
            else {
                $results.current_user_root_restored_after_cleanup = 'FAIL'
            }

            if (Test-SameSet -Before $localMachineRootBefore -After $localMachineRootAfter) {
                $results.local_machine_root_unchanged = 'PASS'
            }
            else {
                $results.local_machine_root_unchanged = 'FAIL'
            }

            $remainingMy = @()
            foreach ($thumb in @($rootThumb, $leafThumb) | Where-Object { $_ }) {
                if (Test-Path -LiteralPath "Cert:\CurrentUser\My\$thumb") {
                    $remainingMy += $thumb
                }
            }
            $results.temporary_current_user_my_cleanup = if ($remainingMy.Count -eq 0) { 'PASS' } else { 'FAIL' }
            $results.remaining_current_user_my_thumbprints = $remainingMy
            $results.cleanup = if (
                $results.current_user_root_restored_after_cleanup -eq 'PASS' -and
                $results.local_machine_root_unchanged -eq 'PASS' -and
                $results.temporary_current_user_my_cleanup -eq 'PASS'
            ) { 'PASS' } else { 'FAIL' }
        }
        else {
            $results.cleanup = 'NOT_VERIFIED_BASELINE_MISSING'
        }
    }
    catch {
        $results.cleanup = 'FAIL'
        $results.cleanup_failure = $_.Exception.Message
    }

    $results.completed_at_utc = [DateTime]::UtcNow.ToString('o')
    $results | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $evidencePath -Encoding UTF8
    Write-Host ''
    Write-Host "G-01B evidence: $evidencePath"
    Get-Content -LiteralPath $evidencePath | Write-Host
}

if (
    $results.current_user_root_import -ne 'PASS' -or
    $results.default_chain_after_import -ne 'PASS' -or
    $results.local_machine_root_unchanged -ne 'PASS' -or
    $results.current_user_root_restored_after_cleanup -ne 'PASS' -or
    $results.temporary_current_user_my_cleanup -ne 'PASS' -or
    $results.cleanup -ne 'PASS'
) {
    exit 1
}

exit 0
