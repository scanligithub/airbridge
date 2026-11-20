[CmdletBinding()]
param(
    [string]$EvidenceDirectory
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
. (Join-Path $PSScriptRoot 'g01-cryptui-native.ps1')

# G-01B is a disposable, interactive Windows PoC. Run it as the target
# Windows user from a visible, non-elevated desktop PowerShell session.
# Production mode deliberately omits CRYPTUI_WIZ_NO_UI.

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
$sessionId = [Diagnostics.Process]::GetCurrentProcess().SessionId

$results = [ordered]@{
    gate = 'G-01B'
    run_id = $runId
    started_at_utc = [DateTime]::UtcNow.ToString('o')
    windows_version = [Environment]::OSVersion.VersionString
    powershell_version = $PSVersionTable.PSVersion.ToString()
    windows_identity = $identity.Name
    process_session_id = $sessionId
    environment_user_interactive = [Environment]::UserInteractive
    elevated_admin_token = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    native_api = 'CryptUIWizImport'
    destination_store = 'Cert:\CurrentUser\Root'
    import_flags = 'CRYPTUI_WIZ_IMPORT_ALLOW_CERT | CRYPTUI_WIZ_IMPORT_NO_CHANGE_DEST_STORE'
    cryptui_wiz_no_ui = $false
    current_user_context = 'NOT_RUN'
    application_consent = 'NOT_RUN'
    windows_import_wizard = 'NOT_RUN'
    current_user_root_import = 'NOT_RUN'
    current_user_root_readback = 'NOT_RUN'
    default_windows_chain_before_import = 'NOT_RUN'
    default_windows_chain_after_import = 'NOT_RUN'
    local_machine_root_unchanged = 'NOT_RUN'
    current_user_root_restored_after_cleanup = 'NOT_RUN'
    current_user_my_cleanup = 'NOT_RUN'
    cleanup = 'NOT_RUN'
}

$localMachineBefore = @()
$currentUserRootBefore = @()
$baselineCaptured = $false
$root = $null
$leaf = $null
$rootPublic = $null
$rootThumb = $null
$leafThumb = $null
$destStore = [IntPtr]::Zero
$rootCerPath = Join-Path $EvidenceDirectory 'g01b-root-public.cer'
$evidencePath = Join-Path $EvidenceDirectory 'g01b-result.json'

function Get-G01BThumbprints {
    param([Parameter(Mandatory)][string]$StorePath)
    @(
        Get-ChildItem -LiteralPath $StorePath -ErrorAction Stop |
            ForEach-Object { $_.Thumbprint.ToUpperInvariant() } |
            Sort-Object -Unique
    )
}

function Test-G01BWindowsChain {
    param([Parameter(Mandatory)][Security.Cryptography.X509Certificates.X509Certificate2]$Certificate)
    $chain = [Security.Cryptography.X509Certificates.X509Chain]::new()
    try {
        $chain.ChainPolicy.RevocationMode = [Security.Cryptography.X509Certificates.X509RevocationMode]::NoCheck
        $chain.ChainPolicy.VerificationFlags = [Security.Cryptography.X509Certificates.X509VerificationFlags]::NoFlag
        [void]$chain.ChainPolicy.ApplicationPolicy.Add([Security.Cryptography.Oid]::new('1.3.6.1.5.5.7.3.1'))
        $ok = $chain.Build($Certificate)
        $status = ($chain.ChainStatus | ForEach-Object { $_.Status.ToString() }) -join ','
        return [pscustomobject]@{ Success = $ok; Status = $status }
    }
    finally {
        $chain.Dispose()
    }
}

function Test-G01BSameSet {
    param([string[]]$Before, [string[]]$After)
    return $null -eq (Compare-Object -ReferenceObject $Before -DifferenceObject $After)
}

try {
    if (-not [Environment]::UserInteractive -or $sessionId -eq 0) {
        throw 'G-01B must run in a visible interactive Windows desktop session, not a service or hosted runner session.'
    }
    if ($results.elevated_admin_token) {
        throw 'G-01B must run without an elevated Administrator token. Close this shell and reopen PowerShell normally.'
    }
    if (-not (Get-Module -ListAvailable -Name PKI)) {
        throw 'The Windows PKI PowerShell module is unavailable.'
    }
    $results.current_user_context = 'PASS_NON_ELEVATED_INTERACTIVE_SESSION'

    $localMachineBefore = Get-G01BThumbprints -StorePath 'Cert:\LocalMachine\Root'
    $currentUserRootBefore = Get-G01BThumbprints -StorePath 'Cert:\CurrentUser\Root'
    $baselineCaptured = $true
    $results.local_machine_root_before_count = $localMachineBefore.Count
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
    $results.root_private_key_present = $root.HasPrivateKey
    $results.leaf_private_key_present = $leaf.HasPrivateKey
    $results.key_export_policy_requested = 'NonExportable'

    if (-not $root.HasPrivateKey -or -not $leaf.HasPrivateKey) {
        throw 'The disposable Root and Leaf private keys were not created in CurrentUser\My.'
    }
    if ($currentUserRootBefore -contains $rootThumb) {
        throw 'The new disposable Root thumbprint unexpectedly exists in CurrentUser\Root.'
    }
    if (@($leaf.DnsNameList | ForEach-Object { $_.Unicode }) -notcontains 'localhost') {
        throw 'The Leaf certificate does not contain the required localhost DNS SAN.'
    }

    Export-Certificate -Cert $root -FilePath $rootCerPath -Type CERT -Force | Out-Null
    $rootPublic = [Security.Cryptography.X509Certificates.X509Certificate2]::new($rootCerPath)
    if ($rootPublic.HasPrivateKey) {
        throw 'The exported Root CER unexpectedly contains private-key material.'
    }
    $results.root_public_cer_has_private_key = $false
    $results.localhost_dns_san = 'PASS'

    $beforeChain = Test-G01BWindowsChain -Certificate $leaf
    if ($beforeChain.Success) {
        throw 'Windows unexpectedly trusted the Leaf before importing the test Root.'
    }
    $results.default_windows_chain_before_import = 'REJECTED'
    $results.default_windows_chain_before_import_status = $beforeChain.Status

    Write-Host ''
    Write-Host 'G-01B: Windows native interactive certificate import test'
    Write-Host "Identity: $($identity.Name)"
    Write-Host "Target Store: Cert:\CurrentUser\Root"
    Write-Host "Root Subject: $($root.Subject)"
    Write-Host "Root Thumbprint: $rootThumb"
    Write-Host "Root Valid Until: $($root.NotAfter.ToString('o'))"
    Write-Host 'Private key: non-exportable; remains in CurrentUser\My'
    Write-Host 'LocalMachine Root: read-only baseline and post-check'
    Write-Host ''
    Write-Host 'This is a disposable test CA, not a production AirBridge CA.'
    Write-Host 'Only the public Root CER is passed to the Windows import wizard.'
    Write-Host ''

    $typedThumbprint = (Read-Host 'To give AirBridge-level consent for this exact test Root, type the full Root Thumbprint').Trim().Replace(' ', '').ToUpperInvariant()
    if ($typedThumbprint -ne $rootThumb) {
        $results.application_consent = 'DECLINED_OR_MISMATCHED'
        throw 'Application-level consent was not confirmed. No certificate import was attempted.'
    }
    $results.application_consent = 'APPROVED_FOR_EXACT_THUMBPRINT'

    $destStore = Open-G01CurrentUserRootStore
    if ($destStore -eq [IntPtr]::Zero) {
        throw 'Could not open the CurrentUser Root destination store.'
    }

    # Production interactive mode: NO_UI is NOT set. The destination is
    # pinned to the already-open CurrentUser Root handle and cannot be changed.
    $importResult = Invoke-G01CryptUIWizImport -CertificateFile $rootCerPath -DestinationStore $destStore -WizardTitle 'AirBridge - Trust local HTTPS certificate'
    $results.actual_import_flags = $importResult.Flags
    $results.windows_import_wizard = 'COMPLETED_API_RETURN_TRUE'
    Close-G01CertificateStore -Handle $destStore
    $destStore = [IntPtr]::Zero

    $currentUserRootAfterImport = Get-G01BThumbprints -StorePath 'Cert:\CurrentUser\Root'
    if ($currentUserRootAfterImport -notcontains $rootThumb) {
        throw 'CryptUIWizImport returned TRUE, but the exact Root thumbprint is absent from CurrentUser\Root.'
    }
    $results.current_user_root_readback = 'PASS'

    $afterChain = Test-G01BWindowsChain -Certificate $leaf
    if (-not $afterChain.Success) {
        throw "Windows default chain validation failed after import: $($afterChain.Status)"
    }
    $results.default_windows_chain_after_import = 'PASS'
    $results.default_windows_chain_after_import_status = $afterChain.Status
    $results.current_user_root_import = 'PASS'

    $localMachineAfterImport = Get-G01BThumbprints -StorePath 'Cert:\LocalMachine\Root'
    if (-not (Test-G01BSameSet -Before $localMachineBefore -After $localMachineAfterImport)) {
        throw 'LocalMachine\Root changed during the interactive CurrentUser test.'
    }
    $results.local_machine_root_unchanged_after_import = 'PASS'
}
catch {
    $results.failure = $_.Exception.ToString()
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
}
finally {
    try {
        if ($destStore -ne [IntPtr]::Zero) {
            Close-G01CertificateStore -Handle $destStore
            $destStore = [IntPtr]::Zero
        }

        # Remove only this run's exact thumbprints, and only if absent at baseline.
        if ($baselineCaptured -and $rootThumb -and $currentUserRootBefore -notcontains $rootThumb) {
            $rootPath = "Cert:\CurrentUser\Root\$rootThumb"
            if (Test-Path -LiteralPath $rootPath) {
                Remove-Item -LiteralPath $rootPath -Force -ErrorAction Stop
            }
        }
        foreach ($thumb in @($rootThumb, $leafThumb) | Where-Object { $_ }) {
            $myPath = "Cert:\CurrentUser\My\$thumb"
            if (Test-Path -LiteralPath $myPath) {
                Remove-Item -LiteralPath $myPath -Force -ErrorAction Stop
            }
        }

        if ($rootPublic) { $rootPublic.Dispose() }
        if ($leaf) { $leaf.Dispose() }
        if ($root) { $root.Dispose() }
        if (Test-Path -LiteralPath $rootCerPath) {
            Remove-Item -LiteralPath $rootCerPath -Force -ErrorAction Stop
        }

        if ($baselineCaptured) {
            $currentUserRootAfterCleanup = Get-G01BThumbprints -StorePath 'Cert:\CurrentUser\Root'
            $localMachineRootAfterCleanup = Get-G01BThumbprints -StorePath 'Cert:\LocalMachine\Root'
            $currentUserMyAfterCleanup = @(
                Get-ChildItem -LiteralPath 'Cert:\CurrentUser\My' |
                    ForEach-Object { $_.Thumbprint.ToUpperInvariant() }
            )

            $results.current_user_root_restored_after_cleanup =
                if (Test-G01BSameSet -Before $currentUserRootBefore -After $currentUserRootAfterCleanup) { 'PASS' } else { 'FAIL' }
            $results.local_machine_root_unchanged =
                if (Test-G01BSameSet -Before $localMachineBefore -After $localMachineRootAfterCleanup) { 'PASS' } else { 'FAIL' }
            $leftovers = @(@($rootThumb, $leafThumb) | Where-Object { $_ -and $currentUserMyAfterCleanup -contains $_ })
            $results.current_user_my_cleanup = if ($leftovers.Count -eq 0) { 'PASS' } else { 'FAIL' }
            $results.remaining_current_user_my_thumbprints = $leftovers
            $results.current_user_root_after_cleanup_count = $currentUserRootAfterCleanup.Count
            $results.local_machine_root_after_cleanup_count = $localMachineRootAfterCleanup.Count
            $results.cleanup = if (
                $results.current_user_root_restored_after_cleanup -eq 'PASS' -and
                $results.local_machine_root_unchanged -eq 'PASS' -and
                $results.current_user_my_cleanup -eq 'PASS'
            ) { 'PASS' } else { 'FAIL' }
        }
        else {
            $results.cleanup = 'NOT_VERIFIED_BASELINE_MISSING'
        }
    }
    catch {
        $results.cleanup = 'FAIL'
        $results.cleanup_failure = $_.Exception.ToString()
    }

    $results.completed_at_utc = [DateTime]::UtcNow.ToString('o')
    $results | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $evidencePath -Encoding UTF8
    Write-Host ''
    Write-Host "G-01B evidence: $evidencePath"
    Get-Content -LiteralPath $evidencePath | Write-Host
}

if (
    $results.current_user_root_import -ne 'PASS' -or
    $results.windows_import_wizard -ne 'COMPLETED_API_RETURN_TRUE' -or
    $results.current_user_root_readback -ne 'PASS' -or
    $results.default_windows_chain_after_import -ne 'PASS' -or
    $results.local_machine_root_unchanged -ne 'PASS' -or
    $results.current_user_root_restored_after_cleanup -ne 'PASS' -or
    $results.current_user_my_cleanup -ne 'PASS' -or
    $results.cleanup -ne 'PASS'
) {
    exit 1
}
exit 0
