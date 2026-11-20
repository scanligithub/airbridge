if (-not ('AirBridge.G01.NativeMethods' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;

namespace AirBridge.G01
{
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct CryptUiWizImportSrcInfo
    {
        public UInt32 dwSize;
        public UInt32 dwSubjectChoice;
        public IntPtr pwszFileName; // Union member for CRYPTUI_WIZ_IMPORT_SUBJECT_FILE
        public UInt32 dwFlags;
        public IntPtr pwszPassword;
    }

    public static class NativeMethods
    {
        // CERT_STORE_PROV_SYSTEM_W is the SDK provider pseudo-handle (LPCSTR) 10.
        // CERT_SYSTEM_STORE_CURRENT_USER is the high-word store-location flag.
        [DllImport("crypt32.dll", EntryPoint = "CertOpenStore", SetLastError = true)]
        public static extern IntPtr CertOpenStore(
            IntPtr lpszStoreProvider,
            UInt32 dwEncodingType,
            IntPtr hCryptProv,
            UInt32 dwFlags,
            IntPtr pvPara);

        [DllImport("crypt32.dll", EntryPoint = "CertCloseStore", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool CertCloseStore(IntPtr hCertStore, UInt32 dwFlags);

        [DllImport("cryptui.dll", EntryPoint = "CryptUIWizImport",
            CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool CryptUIWizImport(
            UInt32 dwFlags,
            IntPtr hwndParent,
            string pwszWizardTitle,
            ref CryptUiWizImportSrcInfo pImportSrc,
            IntPtr hDestCertStore);
    }
}
'@
}

function Open-G01CurrentUserRootStore {
    [CmdletBinding()]
    param()

    $rootNamePointer = [Runtime.InteropServices.Marshal]::StringToHGlobalUni('Root')
    try {
        $handle = [AirBridge.G01.NativeMethods]::CertOpenStore(
            [IntPtr]10,             # CERT_STORE_PROV_SYSTEM_W
            [uint32]0,
            [IntPtr]::Zero,
            [uint32]0x00010000,     # CERT_SYSTEM_STORE_CURRENT_USER
            $rootNamePointer
        )
    }
    finally {
        [Runtime.InteropServices.Marshal]::FreeHGlobal($rootNamePointer)
    }

    if ($handle -eq [IntPtr]::Zero) {
        $code = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        throw [System.ComponentModel.Win32Exception]::new($code, 'CertOpenStore(CurrentUser\Root) failed.')
    }
    return $handle
}

function Close-G01CertificateStore {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [IntPtr]$Handle
    )

    if ($Handle -ne [IntPtr]::Zero) {
        $closed = [AirBridge.G01.NativeMethods]::CertCloseStore($Handle, [uint32]0)
        if (-not $closed) {
            $code = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
            throw [System.ComponentModel.Win32Exception]::new($code, 'CertCloseStore failed.')
        }
    }
}

function Invoke-G01CryptUIWizImport {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$CertificateFile,

        [Parameter(Mandatory)]
        [IntPtr]$DestinationStore,

        [string]$WizardTitle = 'AirBridge certificate trust',

        # CI-only: skips all UI. Never use this switch in the product deployment flow.
        [switch]$NoUI,

        # Optional CI diagnostic file. Records the exact native-call boundary.
        [string]$PhaseFile
    )

    if (-not (Test-Path -LiteralPath $CertificateFile -PathType Leaf)) {
        throw "Certificate file does not exist: $CertificateFile"
    }
    if ($DestinationStore -eq [IntPtr]::Zero) {
        throw 'Destination certificate store handle is null.'
    }

    $filePointer = [Runtime.InteropServices.Marshal]::StringToHGlobalUni(
        [IO.Path]::GetFullPath($CertificateFile)
    )
    try {
        $source = New-Object 'AirBridge.G01.CryptUiWizImportSrcInfo'
        $source.dwSize = [uint32][Runtime.InteropServices.Marshal]::SizeOf($source)
        $source.dwSubjectChoice = [uint32]1 # CRYPTUI_WIZ_IMPORT_SUBJECT_FILE
        $source.pwszFileName = $filePointer
        $source.dwFlags = [uint32]0
        $source.pwszPassword = [IntPtr]::Zero

        # These two import flags are documented by Microsoft but are not
        # defined in a published Windows SDK header:
        #   ALLOW_CERT = 0x00020000
        #   NO_CHANGE_DEST_STORE = 0x00010000
        [uint32]$flags = 0x00020000 -bor 0x00010000
        if ($NoUI) {
            # CRYPTUI_WIZ_NO_UI = 0x00000001; CI-only test mode.
            $flags = $flags -bor 0x00000001
        }

        if ($PhaseFile) {
            'before-cryptuiwizimport-native-call' | Set-Content -LiteralPath $PhaseFile -Encoding UTF8
        }
        $ok = [AirBridge.G01.NativeMethods]::CryptUIWizImport(
            $flags,
            [IntPtr]::Zero,
            $WizardTitle,
            [ref]$source,
            $DestinationStore
        )
        if ($PhaseFile) {
            'after-cryptuiwizimport-native-call' | Set-Content -LiteralPath $PhaseFile -Encoding UTF8
        }
        if (-not $ok) {
            $code = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
            if ($NoUI) {
                throw [System.ComponentModel.Win32Exception]::new($code, 'CryptUIWizImport (CI no-UI test) failed.')
            }
            throw [System.ComponentModel.Win32Exception]::new($code, 'CryptUIWizImport wizard was cancelled or failed.')
        }

        return [pscustomobject]@{
            Success = $true
            Flags = ('0x{0:X8}' -f $flags)
            UiSuppressed = [bool]$NoUI
            Destination = 'Cert:\CurrentUser\Root'
        }
    }
    finally {
        [Runtime.InteropServices.Marshal]::FreeHGlobal($filePointer)
    }
}
