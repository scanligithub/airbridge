$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$runId = [Guid]::NewGuid().ToString('N')
$evidenceDir = Join-Path $env:RUNNER_TEMP "airbridge-g01-$runId"
New-Item -ItemType Directory -Path $evidenceDir -Force | Out-Null

$root = $null
$leaf = $null
$mismatchLeaf = $null
$untrustedRoot = $null
$untrustedLeaf = $null
$rootCer = Join-Path $evidenceDir 'g01-root-public.cer'
$rootThumb = $null
$localMachineBefore = @()
$certsToRemove = [System.Collections.Generic.List[string]]::new()
$results = [ordered]@{
    run_id = $runId
    runner = $env:RUNNER_NAME
    windows_identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    powershell = $PSVersionTable.PSVersion.ToString()
    current_user_root_import = 'NOT_RUN'
    local_machine_root_unchanged = 'NOT_RUN'
    trusted_chain_build = 'NOT_RUN'
    localhost_https = 'NOT_RUN'
    hostname_mismatch_rejected = 'NOT_RUN'
    untrusted_ca_rejected = 'NOT_RUN'
    cleanup = 'NOT_RUN'
}

Add-Type -TypeDefinition @'
using System;
using System.Net;
using System.Net.Sockets;
using System.Net.Security;
using System.Security.Authentication;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Threading;

public sealed class G01LoopbackTlsServer : IDisposable
{
    private readonly TcpListener _ipv4;
    private readonly TcpListener _ipv6;
    private readonly X509Certificate2 _certificate;
    private readonly Thread _thread4;
    private readonly Thread _thread6;
    private volatile bool _stopping;

    public int Port { get; private set; }
    public string LastError { get; private set; }

    public G01LoopbackTlsServer(X509Certificate2 certificate)
    {
        _certificate = certificate;
        _ipv4 = new TcpListener(IPAddress.Loopback, 0);
        _ipv4.Start();
        Port = ((IPEndPoint)_ipv4.LocalEndpoint).Port;

        try
        {
            _ipv6 = new TcpListener(IPAddress.IPv6Loopback, Port);
            _ipv6.Server.DualMode = false;
            _ipv6.Start();
        }
        catch
        {
            _ipv4.Stop();
            throw;
        }

        _thread4 = new Thread(() => ServeOne(_ipv4));
        _thread4.IsBackground = true;
        _thread4.Start();

        _thread6 = new Thread(() => ServeOne(_ipv6));
        _thread6.IsBackground = true;
        _thread6.Start();
    }

    private void ServeOne(TcpListener listener)
    {
        try
        {
            using (TcpClient tcp = listener.AcceptTcpClient())
            using (SslStream ssl = new SslStream(tcp.GetStream(), false))
            {
                tcp.ReceiveTimeout = 8000;
                ssl.ReadTimeout = 8000;
                ssl.WriteTimeout = 8000;
                ssl.AuthenticateAsServer(_certificate, false, SslProtocols.Tls12, false);
                byte[] request = new byte[4096];
                int received = ssl.Read(request, 0, request.Length);
                if (received > 0)
                {
                    byte[] response = Encoding.ASCII.GetBytes(
                        "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK");
                    ssl.Write(response, 0, response.Length);
                    ssl.Flush();
                }
            }
        }
        catch (Exception ex)
        {
            if (!_stopping)
                LastError = ex.GetType().FullName + ": " + ex.Message;
        }
    }

    public void Dispose()
    {
        _stopping = true;
        try { _ipv4.Stop(); } catch { }
        try { _ipv6.Stop(); } catch { }
        try { _thread4.Join(1500); } catch { }
        try { _thread6.Join(1500); } catch { }
    }
}
'@

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

function Test-TrustedChain {
    param([Parameter(Mandatory)][System.Security.Cryptography.X509Certificates.X509Certificate2]$Certificate)

    $chain = [System.Security.Cryptography.X509Certificates.X509Chain]::new()
    try {
        $chain.ChainPolicy.RevocationMode = [System.Security.Cryptography.X509Certificates.X509RevocationMode]::NoCheck
        $chain.ChainPolicy.VerificationFlags = [System.Security.Cryptography.X509Certificates.X509VerificationFlags]::NoFlag
        $ok = $chain.Build($Certificate)
        if (-not $ok) {
            $status = ($chain.ChainStatus | ForEach-Object { "$($_.Status): $($_.StatusInformation.Trim())" }) -join '; '
            throw "Certificate chain did not build: $status"
        }
    }
    finally {
        $chain.Dispose()
    }
}

function Invoke-G01HttpsProbe {
    param([Parameter(Mandatory)][System.Security.Cryptography.X509Certificates.X509Certificate2]$Certificate)

    $server = [G01LoopbackTlsServer]::new($Certificate)
    $handler = [System.Net.Http.HttpClientHandler]::new()
    $handler.UseProxy = $false
    $handler.CheckCertificateRevocationList = $false
    $client = [System.Net.Http.HttpClient]::new($handler)
    $client.Timeout = [TimeSpan]::FromSeconds(10)
    try {
        $uri = "https://localhost:$($server.Port)/health"
        $response = $client.GetAsync($uri).GetAwaiter().GetResult()
        try {
            $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
            if (-not $response.IsSuccessStatusCode) {
                throw "HTTPS endpoint returned HTTP $([int]$response.StatusCode): $body"
            }
            return [PSCustomObject]@{
                StatusCode = [int]$response.StatusCode
                Body = $body
                Port = $server.Port
            }
        }
        finally {
            $response.Dispose()
        }
    }
    finally {
        $client.Dispose()
        $handler.Dispose()
        $server.Dispose()
    }
}

function Assert-G01HttpsRejected {
    param(
        [Parameter(Mandatory)][System.Security.Cryptography.X509Certificates.X509Certificate2]$Certificate,
        [Parameter(Mandatory)][string]$Uri,
        [Parameter(Mandatory)][string]$Scenario
    )

    $server = [G01LoopbackTlsServer]::new($Certificate)
    $handler = [System.Net.Http.HttpClientHandler]::new()
    $handler.UseProxy = $false
    $handler.CheckCertificateRevocationList = $false
    $client = [System.Net.Http.HttpClient]::new($handler)
    $client.Timeout = [TimeSpan]::FromSeconds(10)
    $rejectedForTls = $false
    try {
        try {
            $response = $client.GetAsync($Uri.Replace('{PORT}', [string]$server.Port)).GetAwaiter().GetResult()
            $response.Dispose()
        }
        catch {
            $detail = $_.Exception.ToString()
            if ($detail -notmatch 'SSL|TLS|certificate|authentication|trust|secure channel') {
                throw "The $Scenario probe failed for a non-TLS reason: $detail"
            }
            $rejectedForTls = $true
            Write-Host "Expected $Scenario rejection: $($_.Exception.GetType().FullName)"
        }
    }
    finally {
        $client.Dispose()
        $handler.Dispose()
        $server.Dispose()
    }

    if (-not $rejectedForTls) {
        throw "TLS client did not reject the $Scenario certificate."
    }
}

try {
    Import-Module PKI -ErrorAction Stop

    Write-Host 'G-01 Windows CurrentUser trust test'
    Write-Host "Identity: $($results.windows_identity)"
    Write-Host "PowerShell: $($results.powershell)"

    $localMachineBefore = Get-ThumbprintSnapshot -Path 'Cert:\LocalMachine\Root'
    $results.local_machine_root_before_count = $localMachineBefore.Count

    $root = New-G01RootCertificate -Subject "CN=AirBridge G01 CI Root $runId"
    $leaf = New-G01ServerCertificate -Signer $root -Subject 'CN=localhost' -DnsName @('localhost')
    $mismatchLeaf = New-G01ServerCertificate -Signer $root -Subject 'CN=wrong-host.invalid' -DnsName @('wrong-host.invalid')
    $untrustedRoot = New-G01RootCertificate -Subject "CN=AirBridge G01 CI Untrusted Root $runId"
    $untrustedLeaf = New-G01ServerCertificate -Signer $untrustedRoot -Subject 'CN=localhost Untrusted' -DnsName @('localhost')

    foreach ($cert in @($root, $leaf, $mismatchLeaf, $untrustedRoot, $untrustedLeaf)) {
        if (-not $cert.HasPrivateKey) {
            throw "Expected a CurrentUser\My private key for $($cert.Subject)"
        }
        $certsToRemove.Add($cert.Thumbprint.ToUpperInvariant())
    }

    $rootThumb = $root.Thumbprint.ToUpperInvariant()
    $results.root_thumbprint = $rootThumb
    $results.leaf_thumbprint = $leaf.Thumbprint.ToUpperInvariant()
    $results.root_private_key_present_in_current_user_my = $root.HasPrivateKey
    $results.leaf_private_key_present_in_current_user_my = $leaf.HasPrivateKey

    Export-Certificate -Cert $root -FilePath $rootCer -Type CERT -Force | Out-Null
    if (-not (Test-Path -LiteralPath $rootCer -PathType Leaf)) {
        throw 'Public CA CER export failed.'
    }

    # Import only the public CA certificate into the runner identity's CurrentUser Root.
    # The explicit consent UI is owned by the installer; CI validates the non-interactive
    # Windows certificate-store operation that follows an already-approved consent.
    $rootPublic = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new($rootCer)
    if ($rootPublic.HasPrivateKey) {
        throw 'The exported CA CER unexpectedly contains a private key.'
    }
    $rootStore = [System.Security.Cryptography.X509Certificates.X509Store]::new(
        [System.Security.Cryptography.X509Certificates.StoreName]::Root,
        [System.Security.Cryptography.X509Certificates.StoreLocation]::CurrentUser
    )
    try {
        $rootStore.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
        $rootStore.Add($rootPublic)
    }
    finally {
        $rootStore.Close()
    }

    $trustedRoot = Get-Item -LiteralPath "Cert:\CurrentUser\Root\$rootThumb" -ErrorAction Stop
    if ($trustedRoot.HasPrivateKey) {
        throw 'The CurrentUser Root entry unexpectedly contains a private key. Only the public CA certificate may be imported.'
    }
    $results.current_user_root_import = 'PASS'
    $results.current_user_root_import_method = '.NET X509Store CurrentUser Root Add'

    $certutilOutput = & certutil.exe -user -store Root $rootThumb 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "certutil -user -store Root failed: $($certutilOutput -join [Environment]::NewLine)"
    }
    $certutilOutput | Set-Content -LiteralPath (Join-Path $evidenceDir 'certutil-currentuser-root.txt') -Encoding UTF8

    $localMachineAfterImport = Get-ThumbprintSnapshot -Path 'Cert:\LocalMachine\Root'
    $importDelta = Compare-Object -ReferenceObject $localMachineBefore -DifferenceObject $localMachineAfterImport
    if ($importDelta) {
        $importDelta | Format-Table | Out-String | Write-Host
        throw 'LocalMachine Root changed during CurrentUser import.'
    }
    $results.local_machine_root_unchanged = 'PASS'
    $results.local_machine_root_after_import_count = $localMachineAfterImport.Count

    Write-Host 'Phase: X509Chain.Build for a CA trusted in CurrentUser Root'
    Test-TrustedChain -Certificate $leaf
    $results.trusted_chain_build = 'PASS'

    # Real loopback TLS handshake using the platform default certificate validation.
    Write-Host 'Phase: positive https://localhost loopback TLS probe'
    $httpsResult = Invoke-G01HttpsProbe -Certificate $leaf
    $results.localhost_https = 'PASS'
    $results.https_status_code = $httpsResult.StatusCode
    $results.https_body = $httpsResult.Body
    $results.https_port = $httpsResult.Port

    # Same trusted CA, but a wrong DNS SAN must be rejected by the system TLS client.
    Write-Host 'Phase: negative hostname mismatch TLS probe'
    Assert-G01HttpsRejected -Certificate $mismatchLeaf -Uri 'https://localhost:{PORT}/health' -Scenario 'hostname-mismatch'
    $results.hostname_mismatch_rejected = 'PASS'

    # A second CA which is not in CurrentUser Root must not be trusted.
    Write-Host 'Phase: negative untrusted CA TLS probe'
    Assert-G01HttpsRejected -Certificate $untrustedLeaf -Uri 'https://localhost:{PORT}/health' -Scenario 'untrusted-CA'
    $results.untrusted_ca_rejected = 'PASS'

    Write-Host 'G-01 positive and negative CurrentUser TLS checks passed.'
}
catch {
    $results.failure = $_.Exception.ToString()
    throw
}
finally {
    # Remove only certificates created by this run, using their exact thumbprints.
    if ($rootThumb) {
        try {
            $cleanupRootStore = [System.Security.Cryptography.X509Certificates.X509Store]::new(
                [System.Security.Cryptography.X509Certificates.StoreName]::Root,
                [System.Security.Cryptography.X509Certificates.StoreLocation]::CurrentUser
            )
            $cleanupRootStore.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
            foreach ($trustedCert in @($cleanupRootStore.Certificates | Where-Object { $_.Thumbprint.ToUpperInvariant() -eq $rootThumb })) {
                $cleanupRootStore.Remove($trustedCert)
            }
            $cleanupRootStore.Close()
        }
        catch {
            if ($cleanupRootStore) { $cleanupRootStore.Close() }
            throw
        }
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

    $results | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $evidenceDir 'g01-result.json') -Encoding UTF8
    Get-Content -LiteralPath (Join-Path $evidenceDir 'g01-result.json') | Write-Host

    if ($results.cleanup -ne 'PASS' -or $results.local_machine_root_unchanged -ne 'PASS') {
        throw 'G-01 cleanup / LocalMachine integrity check failed.'
    }
}
