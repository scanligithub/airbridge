$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$runId = [Guid]::NewGuid().ToString('N')
$evidenceBase = if ($env:G01_EVIDENCE_ROOT) { $env:G01_EVIDENCE_ROOT } else { $env:RUNNER_TEMP }
$evidenceDir = Join-Path $evidenceBase "airbridge-g01-$runId"
New-Item -ItemType Directory -Path $evidenceDir -Force | Out-Null

$root = $null
$leaf = $null
$mismatchLeaf = $null
$untrustedRoot = $null
$untrustedLeaf = $null
$rootThumb = $null
$rootCer = Join-Path $evidenceDir 'g01-root-public.cer'
$localMachineBefore = @()
$certsToRemove = [System.Collections.Generic.List[string]]::new()
$phaseFile = Join-Path $evidenceDir 'phase.txt'
function Set-G01Phase { param([string]$Name) $Name | Set-Content -LiteralPath $phaseFile -Encoding UTF8 }
Set-G01Phase 'script-start'

$results = [ordered]@{
    run_id = $runId
    runner = $env:RUNNER_NAME
    windows_identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    runner_is_elevated_admin = ([Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    powershell = $PSVersionTable.PSVersion.ToString()
    current_user_root_import = 'NOT_RUN'
    local_machine_root_unchanged = 'NOT_RUN'
    trusted_chain_build = 'NOT_RUN'
    untrusted_ca_rejected_by_chain = 'NOT_RUN'
    wrong_host_san_fixture = 'NOT_RUN'
    localhost_https_client_handshake = 'NOT_RUN'
    antigravity_opencode_client_tls = 'NOT_RUN'
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

function Test-ChainBuild {
    param(
        [Parameter(Mandatory)][System.Security.Cryptography.X509Certificates.X509Certificate2]$Certificate,
        [Parameter(Mandatory)][bool]$ExpectedSuccess
    )
    $chain = [System.Security.Cryptography.X509Certificates.X509Chain]::new()
    try {
        $chain.ChainPolicy.RevocationMode = [System.Security.Cryptography.X509Certificates.X509RevocationMode]::NoCheck
        $chain.ChainPolicy.VerificationFlags = [System.Security.Cryptography.X509Certificates.X509VerificationFlags]::NoFlag
        $ok = $chain.Build($Certificate)
        if ($ExpectedSuccess -and -not $ok) {
            $status = ($chain.ChainStatus | ForEach-Object { "$($_.Status): $($_.StatusInformation.Trim())" }) -join '; '
            throw "Expected trusted chain to build, but it failed: $status"
        }
        if (-not $ExpectedSuccess -and $ok) {
            throw 'An untrusted CA chain unexpectedly built successfully.'
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
    Write-Host 'G-01 CurrentUser Root import and X509Chain validation'
    if ($results.runner_is_elevated_admin) { throw 'G-01 trust-store test must run as a standard, non-elevated Windows user.' }
    Write-Host "Identity: $($results.windows_identity)"
    Write-Host "Elevated admin token: $($results.runner_is_elevated_admin)"
    Write-Host "PowerShell: $($results.powershell)"

    Set-G01Phase 'before-localmachine-snapshot'
    $localMachineBefore = Get-ThumbprintSnapshot -Path 'Cert:\LocalMachine\Root'
    Set-G01Phase 'localmachine-snapshot-complete'
    $results.local_machine_root_before_count = $localMachineBefore.Count
    if ($localMachineBefore.Count -eq 0) {
        throw 'LocalMachine Root snapshot unexpectedly empty; refusing an inconclusive scope test.'
    }

    Set-G01Phase 'before-create-test-certificates'
    $root = New-G01RootCertificate -Subject "CN=AirBridge G01 CI Root $runId"
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

    Set-G01Phase 'before-export-root-public-cer'
    Export-Certificate -Cert $root -FilePath $rootCer -Type CERT -Force | Out-Null
    Set-G01Phase 'root-public-cer-exported'
    $rootPublic = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new($rootCer)
    if ($rootPublic.HasPrivateKey) {
        throw 'Exported CA CER unexpectedly contains a private key.'
    }

    # The installer must obtain explicit user consent before this write operation.
    # This CI-only probe uses the native CryptoAPI store API after simulating consent.
    # It tests CurrentUser scope without relying on certutil or a certificate UI prompt.
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;

namespace AirBridge.G01
{
    public static class CurrentUserRootStore
    {
        private const uint X509_ASN_ENCODING = 0x00000001;
        private const uint PKCS_7_ASN_ENCODING = 0x00010000;
        private const uint CERT_SYSTEM_STORE_CURRENT_USER = 0x00010000;
        private const uint CERT_STORE_ADD_NEW = 1;
        private const uint Encoding = X509_ASN_ENCODING | PKCS_7_ASN_ENCODING;

        [DllImport("crypt32.dll", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
        private static extern IntPtr CertOpenStore(
            IntPtr storeProvider,
            uint messageAndCertificateEncodingType,
            IntPtr cryptographicProvider,
            uint flags,
            string storeName);

        [DllImport("crypt32.dll", ExactSpelling = true, SetLastError = true)]
        private static extern bool CertAddEncodedCertificateToStore(
            IntPtr certificateStore,
            uint certificateEncodingType,
            byte[] encodedCertificate,
            uint encodedCertificateLength,
            uint addDisposition,
            out IntPtr certificateContext);

        [DllImport("crypt32.dll", ExactSpelling = true, SetLastError = true)]
        private static extern bool CertFreeCertificateContext(IntPtr certificateContext);

        [DllImport("crypt32.dll", ExactSpelling = true, SetLastError = true)]
        private static extern bool CertCloseStore(IntPtr certificateStore, uint flags);

        public static IntPtr OpenCurrentUserRoot()
        {
            // CERT_STORE_PROV_SYSTEM_W is MAKEINTRESOURCEW(10); ROOT is the system store name.
            IntPtr store = CertOpenStore(
                new IntPtr(10), Encoding, IntPtr.Zero, CERT_SYSTEM_STORE_CURRENT_USER, "ROOT");
            if (store == IntPtr.Zero)
            {
                int error = Marshal.GetLastWin32Error();
                throw new Win32Exception(error, "CertOpenStore(CurrentUser\\ROOT) failed.");
            }
            return store;
        }

        public static void AddRoot(IntPtr store, byte[] encodedCertificate)
        {
            if (store == IntPtr.Zero)
                throw new ArgumentException("The CurrentUser ROOT store handle is invalid.", "store");
            if (encodedCertificate == null || encodedCertificate.Length == 0)
                throw new ArgumentException("The public CA certificate is empty.", "encodedCertificate");

            IntPtr context = IntPtr.Zero;
            try
            {
                if (!CertAddEncodedCertificateToStore(
                    store, Encoding, encodedCertificate, (uint)encodedCertificate.Length,
                    CERT_STORE_ADD_NEW, out context))
                {
                    int error = Marshal.GetLastWin32Error();
                    throw new Win32Exception(error, "CertAddEncodedCertificateToStore failed.");
                }
            }
            finally
            {
                if (context != IntPtr.Zero)
                    CertFreeCertificateContext(context);
            }
        }

        public static void CloseStore(IntPtr store)
        {
            if (store != IntPtr.Zero && !CertCloseStore(store, 0))
            {
                int error = Marshal.GetLastWin32Error();
                throw new Win32Exception(error, "CertCloseStore failed.");
            }
        }
    }
}
'@ -Language CSharp

    $nativeStore = [IntPtr]::Zero
    try {
        Set-G01Phase 'before-cryptapi-currentuser-root-open'
        Write-Host 'Phase: crypt32 CertOpenStore for CurrentUser Root'
        $nativeStore = [AirBridge.G01.CurrentUserRootStore]::OpenCurrentUserRoot()
        Set-G01Phase 'cryptapi-currentuser-root-opened'
        $results.current_user_root_store_open = 'PASS'

        Set-G01Phase 'before-cryptapi-currentuser-root-add'
        Write-Host 'Phase: crypt32 CertAddEncodedCertificateToStore into CurrentUser Root'
        [AirBridge.G01.CurrentUserRootStore]::AddRoot($nativeStore, $rootPublic.RawData)
        Set-G01Phase 'cryptapi-currentuser-root-add-returned'
        $rootPublic.Dispose()
    }
    finally {
        if ($nativeStore.ToInt64() -ne 0) {
            [AirBridge.G01.CurrentUserRootStore]::CloseStore($nativeStore)
        }
    }

    $trustedRoot = Get-Item -LiteralPath "Cert:\CurrentUser\Root\$rootThumb" -ErrorAction Stop
    if ($trustedRoot.HasPrivateKey) {
        throw 'CurrentUser Root unexpectedly contains a private key; only public CA material may be imported.'
    }
    $results.current_user_root_import = 'PASS'
    $results.current_user_root_import_method = 'crypt32.dll CertAddEncodedCertificateToStore (CurrentUser ROOT)'

    $results.current_user_root_readback = 'PASS (exact CurrentUser provider path)'

    Set-G01Phase 'before-localmachine-after-import-snapshot'
    $localMachineAfterImport = Get-ThumbprintSnapshot -Path 'Cert:\LocalMachine\Root'
    $importDelta = Compare-Object -ReferenceObject $localMachineBefore -DifferenceObject $localMachineAfterImport
    if ($importDelta) {
        $importDelta | Format-Table | Out-String | Write-Host
        throw 'LocalMachine Root changed during CurrentUser Root import.'
    }
    $results.local_machine_root_unchanged = 'PASS'
    $results.local_machine_root_after_import_count = $localMachineAfterImport.Count

    Set-G01Phase 'before-trusted-chain-build'
    Write-Host 'Phase: verify a certificate chain trusted by CurrentUser Root'
    [void](Test-ChainBuild -Certificate $leaf -ExpectedSuccess $true)
    $results.trusted_chain_build = 'PASS'
    Set-G01Phase 'trusted-chain-build-pass'

    Set-G01Phase 'before-untrusted-chain-build'
    Write-Host 'Phase: verify an unrelated CA is not trusted'
    [void](Test-ChainBuild -Certificate $untrustedLeaf -ExpectedSuccess $false)
    $results.untrusted_ca_rejected_by_chain = 'PASS'
    Set-G01Phase 'untrusted-chain-rejected'

    $leafDnsNames = @($leaf.DnsNameList | ForEach-Object { $_.Unicode })
    $mismatchDnsNames = @($mismatchLeaf.DnsNameList | ForEach-Object { $_.Unicode })
    if ($leafDnsNames -notcontains 'localhost' -or $mismatchDnsNames -contains 'localhost') {
        throw 'The positive / wrong-host DNS SAN fixtures are not configured as expected.'
    }
    $results.wrong_host_san_fixture = 'PASS'
    Set-G01Phase 'san-fixtures-verified'

    Write-Host 'G-01 CurrentUser store and chain tests passed. Live HTTP client handshake and vendor MCP clients remain separate gates.'
}
catch {
    $results.failure = $_.Exception.ToString()
    throw
}
finally {
    Set-G01Phase 'cleanup-start'
    # Remove only certificates created by this run, by their exact thumbprints.
    if ($rootThumb) {
        $rootTrustPath = "Cert:\CurrentUser\Root\$rootThumb"
        if (Test-Path -LiteralPath $rootTrustPath) {
            Remove-Item -LiteralPath $rootTrustPath -Force -ErrorAction Stop
        }
        $results.current_user_root_cleanup = 'PASS'
    }

    foreach ($thumb in $certsToRemove) {
        Remove-Item -LiteralPath "Cert:\CurrentUser\My\$thumb" -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $rootCer) {
        Remove-Item -LiteralPath $rootCer -Force -ErrorAction SilentlyContinue
    }

    if ($localMachineBefore.Count -gt 0) {
        $localMachineAfterCleanup = Get-ThumbprintSnapshot -Path 'Cert:\LocalMachine\Root'
        $cleanupDelta = Compare-Object -ReferenceObject $localMachineBefore -DifferenceObject $localMachineAfterCleanup
        if ($cleanupDelta) {
            $results.local_machine_root_unchanged = 'FAIL'
            $results.cleanup = 'FAIL'
            $results.cleanup_delta = ($cleanupDelta | Out-String)
        }
        else {
            if ($results.local_machine_root_unchanged -ne 'FAIL') {
                $results.local_machine_root_unchanged = 'PASS'
            }
            $results.cleanup = 'PASS'
        }

        if ($rootThumb) {
            $stillTrusted = Test-Path -LiteralPath "Cert:\CurrentUser\Root\$rootThumb"
            $stillInMy = Test-Path -LiteralPath "Cert:\CurrentUser\My\$rootThumb"
            $results.certificate_cleanup_verified = (-not $stillTrusted -and -not $stillInMy)
            if (-not $results.certificate_cleanup_verified) {
                $results.cleanup = 'FAIL'
            }
        }
        $results.local_machine_root_after_cleanup_count = $localMachineAfterCleanup.Count
    }

    Set-G01Phase ('cleanup-complete-' + $results.cleanup)
    $results | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $evidenceDir 'g01-result.json') -Encoding UTF8
    Get-Content -LiteralPath (Join-Path $evidenceDir 'g01-result.json') | Write-Host

    if ($results.cleanup -ne 'PASS' -or $results.local_machine_root_unchanged -ne 'PASS') {
        throw 'G-01 cleanup / LocalMachine integrity check failed.'
    }
}
