$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# This script runs only inside the disposable standard-user profile created
# by run-g01-as-standard-user.ps1. It exercises CryptUIWizImport's NO_UI mode
# so hosted CI can validate the native import API without pretending to test
# the interactive Windows wizard. The temporary profile is deleted afterward.

. (Join-Path $PSScriptRoot 'g01-cryptui-native.ps1')
Import-Module PKI -ErrorAction Stop

$runId = [Guid]::NewGuid().ToString('N')
$evidenceBase = if ($env:G01_EVIDENCE_ROOT) { $env:G01_EVIDENCE_ROOT } else { $env:RUNNER_TEMP }
$evidenceDir = Join-Path $evidenceBase "g01-cryptui-import-$runId"
New-Item -ItemType Directory -Path $evidenceDir -Force | Out-Null

$results = [ordered]@{
    gate = 'G-01A2-CRYPTUI-API'
    run_id = $runId
    windows_identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    elevated_admin_token = $false
    native_api = 'CryptUIWizImport'
    store_provider = 'CERT_STORE_PROV_SYSTEM_W'
    store_location = 'CERT_SYSTEM_STORE_CURRENT_USER'
    destination_store = 'Root'
    import_flags = 'ALLOW_CERT | NO_CHANGE_DEST_STORE | NO_UI (CI ONLY)'
    ui_suppressed_in_ci = $true
    interactive_wizard_tested = $false
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
$root = $null
$leaf = $null
$rootPublic = $null
$rootThumb = $null
$leafThumb = $null
$rootCer = Join-Path $evidenceDir 'cryptui-ci-root-public.cer'
$destStore = [IntPtr]::Zero
$phaseFile = Join-Path $evidenceDir 'phase.txt'
function Set-G01CryptPhase { param([string]$Name) $Name | Set-Content -LiteralPath $phaseFile -Encoding UTF8 }

function Get-G01Thumbprints {
    param([Parameter(Mandatory)][string]$Path)
    @(
        Get-ChildItem -LiteralPath $Path -ErrorAction Stop |
            ForEach-Object { $_.Thumbprint.ToUpperInvariant() } |
            Sort-Object -Unique
    )
}

function Test-G01WindowsChain {
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

try {
    Set-G01CryptPhase 'before-identity-check'
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    $results.elevated_admin_token = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if ($results.elevated_admin_token) {
        throw 'CryptUI API test must run as a standard, non-elevated user.'
    }
    if (-not [Environment]::UserInteractive -or [Diagnostics.Process]::GetCurrentProcess().SessionId -eq 0) {
        throw 'CryptUI API test must not run in Session 0.'
    }

    Set-G01CryptPhase 'before-store-snapshots'
    $localMachineBefore = Get-G01Thumbprints -Path 'Cert:\LocalMachine\Root'
    $currentUserRootBefore = Get-G01Thumbprints -Path 'Cert:\CurrentUser\Root'
    $results.local_machine_root_before_count = $localMachineBefore.Count
    $results.current_user_root_before_count = $currentUserRootBefore.Count

    Set-G01CryptPhase 'before-create-temporary-ca'
    $root = New-SelfSignedCertificate @{
        Type = 'Custom'
        Subject = "CN=AirBridge G01 CryptUI CI Root $runId"
        KeyAlgorithm = 'RSA'
        KeyLength = 2048
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
    if ($currentUserRootBefore -contains $rootThumb) {
        throw 'Temporary Root already exists in CurrentUser\Root before import.'
    }
    if (-not $root.HasPrivateKey -or -not $leaf.HasPrivateKey) {
        throw 'Expected both temporary signing keys in CurrentUser\My.'
    }

    Export-Certificate -Cert $root -FilePath $rootCer -Type CERT -Force | Out-Null
    $rootPublic = [Security.Cryptography.X509Certificates.X509Certificate2]::new($rootCer)
    if ($rootPublic.HasPrivateKey) {
        throw 'The exported CER contains private-key material.'
    }
    $results.root_cer_has_private_key = $false

    Set-G01CryptPhase 'before-default-chain-negative'
    $beforeChain = Test-G01WindowsChain -Certificate $leaf
    if ($beforeChain.Success) {
        throw 'Windows unexpectedly trusted the test Leaf before importing its CA.'
    }
    $results.default_windows_chain_before_import = 'REJECTED'
    $results.default_windows_chain_before_import_status = $beforeChain.Status

    Set-G01CryptPhase 'before-open-currentuser-root-handle'
    $destStore = Open-G01CurrentUserRootStore
    if ($destStore -eq [IntPtr]::Zero) {
        throw 'CertOpenStore did not return a CurrentUser Root store handle.'
    }
    $results.destination_store_handle_opened = 'PASS'
    Set-G01CryptPhase 'currentuser-root-handle-opened'

    # NO_UI is intentionally used only in this disposable hosted-CI test.
    # Production G-01B calls the same native API without NO_UI, so Windows
    # displays its import wizard to the target user.
    $importResult = Invoke-G01CryptUIWizImport -CertificateFile $rootCer -DestinationStore $destStore -WizardTitle 'AirBridge G-01 CI API test' -NoUI
    $results.api_return = 'TRUE'
    $results.actual_flags = $importResult.Flags
    $results.current_user_root_import = 'PASS'
    Close-G01CertificateStore -Handle $destStore
    $destStore = [IntPtr]::Zero

    Set-G01CryptPhase 'before-currentuser-root-readback'
    $currentUserRootAfterImport = Get-G01Thumbprints -Path 'Cert:\CurrentUser\Root'
    if ($currentUserRootAfterImport -notcontains $rootThumb) {
        throw 'CryptUIWizImport returned TRUE, but the exact thumbprint is absent from CurrentUser\Root.'
    }
    $results.current_user_root_readback = 'PASS'

    Set-G01CryptPhase 'before-default-chain-positive'
    $afterChain = Test-G01WindowsChain -Certificate $leaf
    if (-not $afterChain.Success) {
        throw "Windows did not trust the Leaf after CryptUIWizImport: $($afterChain.Status)"
    }
    $results.default_windows_chain_after_import = 'PASS'
    $results.default_windows_chain_after_import_status = $afterChain.Status

    $localMachineAfterImport = Get-G01Thumbprints -Path 'Cert:\LocalMachine\Root'
    if (Compare-Object -ReferenceObject $localMachineBefore -DifferenceObject $localMachineAfterImport) {
        throw 'LocalMachine\Root changed during the CurrentUser CryptUI API test.'
    }
    $results.local_machine_root_unchanged_after_import = 'PASS'
    Set-G01CryptPhase 'cryptui-import-and-chain-tests-passed'
}
catch {
    $results.failure = $_.Exception.ToString()
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
}
finally {
    Set-G01CryptPhase 'cleanup-start'
    try {
        if ($destStore -ne [IntPtr]::Zero) {
            Close-G01CertificateStore -Handle $destStore
            $destStore = [IntPtr]::Zero
        }

        # Remove this exact test thumbprint only if this run inserted it.
        if ($rootThumb -and $currentUserRootBefore -notcontains $rootThumb) {
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
        if (Test-Path -LiteralPath $rootCer) {
            Remove-Item -LiteralPath $rootCer -Force -ErrorAction Stop
        }

        $currentUserRootAfterCleanup = Get-G01Thumbprints -Path 'Cert:\CurrentUser\Root'
        $localMachineAfterCleanup = Get-G01Thumbprints -Path 'Cert:\LocalMachine\Root'
        $currentUserMyAfterCleanup = @(
            Get-ChildItem -LiteralPath 'Cert:\CurrentUser\My' |
                ForEach-Object { $_.Thumbprint.ToUpperInvariant() }
        )

        $results.current_user_root_restored_after_cleanup =
            if (Compare-Object -ReferenceObject $currentUserRootBefore -DifferenceObject $currentUserRootAfterCleanup) { 'FAIL' } else { 'PASS' }
        $results.local_machine_root_unchanged =
            if (Compare-Object -ReferenceObject $localMachineBefore -DifferenceObject $localMachineAfterCleanup) { 'FAIL' } else { 'PASS' }
        $leftovers = @(@($rootThumb, $leafThumb) | Where-Object { $_ -and $currentUserMyAfterCleanup -contains $_ })
        $results.current_user_my_cleanup = if ($leftovers.Count -eq 0) { 'PASS' } else { 'FAIL' }
        $results.remaining_current_user_my_thumbprints = $leftovers
        $results.cleanup = if (
            $results.current_user_root_restored_after_cleanup -eq 'PASS' -and
            $results.local_machine_root_unchanged -eq 'PASS' -and
            $results.current_user_my_cleanup -eq 'PASS'
        ) { 'PASS' } else { 'FAIL' }
        $results.current_user_root_after_cleanup_count = $currentUserRootAfterCleanup.Count
        $results.local_machine_root_after_cleanup_count = $localMachineAfterCleanup.Count
    }
    catch {
        $results.cleanup = 'FAIL'
        $results.cleanup_failure = $_.Exception.ToString()
    }

    $results.completed_at_utc = [DateTime]::UtcNow.ToString('o')
    $resultPath = Join-Path $evidenceDir 'g01-cryptui-result.json'
    $results | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $resultPath -Encoding UTF8
    Write-Host "CryptUI API evidence: $resultPath"
    Get-Content -LiteralPath $resultPath | Write-Host
}

if (
    $results.current_user_root_import -ne 'PASS' -or
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
