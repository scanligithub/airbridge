$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# API control test only: exercise CryptUIWizImport against CurrentUser\My.
# Root is deliberately not written by hosted Actions because the same call
# against CurrentUser\Root timed out inside the native API on windows-2025.

. (Join-Path $PSScriptRoot 'g01-cryptui-native.ps1')
Import-Module PKI -ErrorAction Stop

$runId = [Guid]::NewGuid().ToString('N')
$evidenceBase = if ($env:G01_EVIDENCE_ROOT) { $env:G01_EVIDENCE_ROOT } else { $env:RUNNER_TEMP }
$evidenceDir = Join-Path $evidenceBase ('g01-cryptui-control-' + $runId)
New-Item -ItemType Directory -Path $evidenceDir -Force | Out-Null

$results = [ordered]@{
    gate = 'G-01A2-CRYPTUI-API-CONTROL'
    run_id = $runId
    windows_identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    elevated_admin_token = $false
    native_api = 'CryptUIWizImport'
    store_provider = 'CERT_STORE_PROV_SYSTEM_W'
    store_location = 'CERT_SYSTEM_STORE_CURRENT_USER'
    destination_store = 'My'
    import_flags = 'ALLOW_CERT | NO_CHANGE_DEST_STORE | NO_UI (CI ONLY)'
    interactive_wizard_tested = $false
    current_user_my_import = 'NOT_RUN'
    current_user_my_readback = 'NOT_RUN'
    current_user_root_handle_open = 'NOT_RUN'
    current_user_root_store_readonly = 'NOT_RUN'
    current_user_root_localized_name = 'NOT_RUN'
    current_user_root_unchanged = 'NOT_RUN'
    local_machine_root_unchanged = 'NOT_RUN'
    cleanup = 'NOT_RUN'
}
$localMachineBefore = @()
$currentUserRootBefore = @()
$cert = $null
$thumb = $null
$publicCert = $null
$store = [IntPtr]::Zero
$rootStore = [IntPtr]::Zero
$cerPath = Join-Path $evidenceDir 'cryptui-control-public.cer'
$phaseFile = Join-Path $evidenceDir 'phase.txt'

function Set-G01Phase { param([string]$Name) $Name | Set-Content -LiteralPath $phaseFile -Encoding UTF8 }
function Get-G01Thumbprints {
    param([Parameter(Mandatory)][string]$Path)
    @(Get-ChildItem -LiteralPath $Path -ErrorAction Stop |
        ForEach-Object { $_.Thumbprint.ToUpperInvariant() } | Sort-Object -Unique)
}
function Test-G01SameSet {
    param([string[]]$Before, [string[]]$After)
    return $null -eq (Compare-Object -ReferenceObject $Before -DifferenceObject $After)
}

try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    $results.elevated_admin_token = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if ($results.elevated_admin_token) { throw 'API control test must run as standard user.' }
    if (-not [Environment]::UserInteractive -or [Diagnostics.Process]::GetCurrentProcess().SessionId -eq 0) { throw 'API control test must not run in Session 0.' }

    $localMachineBefore = Get-G01Thumbprints -Path 'Cert:\LocalMachine\Root'
    $currentUserRootBefore = Get-G01Thumbprints -Path 'Cert:\CurrentUser\Root'
    $results.local_machine_root_before_count = $localMachineBefore.Count
    $results.current_user_root_before_count = $currentUserRootBefore.Count

    Set-G01Phase 'before-create-control-certificate'
    $certParams = @{
        Type = 'Custom'
        Subject = ('CN=AirBridge G01 CryptUI Control ' + $runId)
        DnsName = @('g01-control.invalid')
        KeyAlgorithm = 'RSA'
        KeyLength = 2048
        HashAlgorithm = 'SHA256'
        KeyExportPolicy = 'NonExportable'
        KeyUsageProperty = 'All'
        KeyUsage = @('DigitalSignature', 'KeyEncipherment')
        TextExtension = @('2.5.29.19={critical}{text}ca=FALSE', '2.5.29.37={text}1.3.6.1.5.5.7.3.1')
        NotBefore = (Get-Date).AddMinutes(-5)
        NotAfter = (Get-Date).AddDays(1)
        CertStoreLocation = 'Cert:\CurrentUser\My'
    }
    $cert = New-SelfSignedCertificate @certParams
    $thumb = $cert.Thumbprint.ToUpperInvariant()
    $results.control_thumbprint = $thumb
    if (-not $cert.HasPrivateKey) { throw 'Control certificate private key is missing.' }
    Export-Certificate -Cert $cert -FilePath $cerPath -Type CERT -Force | Out-Null
    $publicCert = [Security.Cryptography.X509Certificates.X509Certificate2]::new($cerPath)
    if ($publicCert.HasPrivateKey) { throw 'Exported CER unexpectedly contains private-key material.' }

    Set-G01Phase 'before-remove-control-cert-from-my'
    Remove-Item -LiteralPath ('Cert:\CurrentUser\My\' + $thumb) -Force -ErrorAction Stop
    if (Test-Path -LiteralPath ('Cert:\CurrentUser\My\' + $thumb)) { throw 'Control certificate still present in My before import.' }

    Set-G01Phase 'before-open-currentuser-my-handle'
    $store = Open-G01CurrentUserStore -StoreName 'My'
    Set-G01Phase 'currentuser-my-handle-opened'
    $import = Invoke-G01CryptUIWizImport -CertificateFile $cerPath -DestinationStore $store -WizardTitle 'AirBridge G-01 CryptUI API control' -DestinationLabel 'Cert:\CurrentUser\My' -NoUI -PhaseFile $phaseFile
    $results.current_user_my_import = 'PASS'
    $results.actual_flags = $import.Flags
    Close-G01CertificateStore -Handle $store
    $store = [IntPtr]::Zero

    $myAfterImport = Get-G01Thumbprints -Path 'Cert:\CurrentUser\My'
    if ($myAfterImport -notcontains $thumb) { throw 'CryptUIWizImport returned TRUE but thumbprint is absent from CurrentUser\My.' }
    $results.current_user_my_readback = 'PASS'

    if (-not (Test-G01SameSet -Before $currentUserRootBefore -After (Get-G01Thumbprints -Path 'Cert:\CurrentUser\Root'))) { throw 'CurrentUser\Root changed during My control test.' }
    $results.current_user_root_unchanged = 'PASS'
    if (-not (Test-G01SameSet -Before $localMachineBefore -After (Get-G01Thumbprints -Path 'Cert:\LocalMachine\Root'))) { throw 'LocalMachine\Root changed during My control test.' }
    $results.local_machine_root_unchanged = 'PASS'
    Set-G01Phase 'cryptui-my-control-import-passed'

    # Read-only Root-target subtest. This intentionally does not call
    # CryptUIWizImport for Root on hosted runners: the prior NO_UI Root call
    # stalled inside the native API. It verifies the exact CurrentUser Root
    # handle and the localized-name property required by the interactive UI.
    Set-G01Phase 'before-open-currentuser-root-localized-handle'
    $rootStore = Open-G01CurrentUserStore -StoreName 'Root' -SetLocalizedName -ReadOnly
    if ($rootStore -eq [IntPtr]::Zero) {
        throw 'CertOpenStore(CurrentUser\\Root, SET_LOCALIZED_NAME) returned a null handle.'
    }
    $results.current_user_root_handle_open = 'PASS'
    $results.current_user_root_store_readonly = 'PASS_FLAG_0x00008000'
    Set-G01Phase 'currentuser-root-localized-handle-opened'

    $localizedName = Get-G01CertificateStoreLocalizedName -Handle $rootStore
    $results.current_user_root_localized_name = $localizedName
    if ([string]::IsNullOrWhiteSpace($localizedName)) {
        throw 'CertGetStoreProperty did not return the CurrentUser Root localized name.'
    }
    Set-G01Phase 'currentuser-root-localized-name-read'

    Close-G01CertificateStore -Handle $rootStore
    $rootStore = [IntPtr]::Zero
    if (-not (Test-G01SameSet -Before $currentUserRootBefore -After (Get-G01Thumbprints -Path 'Cert:\\CurrentUser\\Root'))) {
        throw 'CurrentUser\\Root changed during read-only destination-handle test.'
    }
    $results.current_user_root_unchanged = 'PASS'
    Set-G01Phase 'cryptui-root-store-readonly-subtest-passed'
}
catch {
    $results.failure = $_.Exception.ToString()
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
}
finally {
    try {
        if ($store -ne [IntPtr]::Zero) { Close-G01CertificateStore -Handle $store; $store = [IntPtr]::Zero }
        if ($rootStore -ne [IntPtr]::Zero) { Close-G01CertificateStore -Handle $rootStore; $rootStore = [IntPtr]::Zero }
        if ($thumb) {
            $myPath = 'Cert:\CurrentUser\My\' + $thumb
            if (Test-Path -LiteralPath $myPath) { Remove-Item -LiteralPath $myPath -Force -ErrorAction Stop }
        }
        if ($publicCert) { $publicCert.Dispose() }
        if ($cert) { $cert.Dispose() }
        if (Test-Path -LiteralPath $cerPath) { Remove-Item -LiteralPath $cerPath -Force -ErrorAction Stop }
        $results.temporary_current_user_my_cleanup = if ($thumb -and (Get-G01Thumbprints -Path 'Cert:\CurrentUser\My') -notcontains $thumb) { 'PASS' } else { 'FAIL' }
        $results.current_user_root_unchanged = if (Test-G01SameSet -Before $currentUserRootBefore -After (Get-G01Thumbprints -Path 'Cert:\CurrentUser\Root')) { 'PASS' } else { 'FAIL' }
        $results.local_machine_root_unchanged = if (Test-G01SameSet -Before $localMachineBefore -After (Get-G01Thumbprints -Path 'Cert:\LocalMachine\Root')) { 'PASS' } else { 'FAIL' }
        $results.cleanup = if ($results.temporary_current_user_my_cleanup -eq 'PASS' -and $results.current_user_root_unchanged -eq 'PASS' -and $results.local_machine_root_unchanged -eq 'PASS') { 'PASS' } else { 'FAIL' }
    }
    catch { $results.cleanup = 'FAIL'; $results.cleanup_failure = $_.Exception.ToString() }
    $results.completed_at_utc = [DateTime]::UtcNow.ToString('o')
    $resultPath = Join-Path $evidenceDir 'g01-cryptui-control-result.json'
    $results | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $resultPath -Encoding UTF8
    Write-Host ('CryptUI API control evidence: ' + $resultPath)
    Get-Content -LiteralPath $resultPath | Write-Host
}

if ($results.current_user_my_import -ne 'PASS' -or $results.current_user_my_readback -ne 'PASS' -or $results.current_user_root_handle_open -ne 'PASS' -or $results.current_user_root_store_readonly -ne 'PASS_FLAG_0x00008000' -or [string]::IsNullOrWhiteSpace([string]$results.current_user_root_localized_name) -or $results.current_user_root_unchanged -ne 'PASS' -or $results.local_machine_root_unchanged -ne 'PASS' -or $results.temporary_current_user_my_cleanup -ne 'PASS' -or $results.cleanup -ne 'PASS') {
    throw 'G-01A2 CryptUIWizImport control subgate failed; inspect g01-cryptui-control-result.json.'
}