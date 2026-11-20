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

        [DllImport("crypt32.dll", EntryPoint = "CertGetStoreProperty", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool CertGetStoreProperty(
            IntPtr hCertStore,
            UInt32 dwPropId,
            IntPtr pvData,
            ref UInt32 pcbData);

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

function Open-G01CurrentUserStore {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$StoreName,
        [switch]$SetLocalizedName,
        [switch]$ReadOnly
    )

    $storeNamePointer = [Runtime.InteropServices.Marshal]::StringToHGlobalUni($StoreName)
    try {
        [uint32]$storeFlags = 0x00010000 # CERT_SYSTEM_STORE_CURRENT_USER
        if ($SetLocalizedName) {
            # CERT_STORE_SET_LOCALIZED_NAME_FLAG: ask the provider to expose
            # CERT_STORE_LOCALIZED_NAME_PROP_ID for CryptUI wizard display.
            $storeFlags = $storeFlags -bor [uint32]0x00000002
        }
        if ($ReadOnly) {
            # CERT_STORE_READONLY_FLAG: reject attempts to change the store.
            $storeFlags = $storeFlags -bor [uint32]0x00008000
        }
        $handle = [AirBridge.G01.NativeMethods]::CertOpenStore(
            [IntPtr]10,             # CERT_STORE_PROV_SYSTEM_W
            [uint32]0,
            [IntPtr]::Zero,
            $storeFlags,
            $storeNamePointer
        )
    }
    finally {
        [Runtime.InteropServices.Marshal]::FreeHGlobal($storeNamePointer)
    }

    if ($handle -eq [IntPtr]::Zero) {
        $code = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        throw [System.ComponentModel.Win32Exception]::new($code, "CertOpenStore(CurrentUser\\$StoreName) failed.")
    }
    return $handle
}

function Open-G01CurrentUserRootStore {
    [CmdletBinding()]
    param()
    return Open-G01CurrentUserStore -StoreName 'Root' -SetLocalizedName
}

function Get-G01CertificateStoreLocalizedName {
    [CmdletBinding()]
    param([Parameter(Mandatory)][IntPtr]$Handle)

    if ($Handle -eq [IntPtr]::Zero) { throw 'Certificate store handle is null.' }

    [uint32]$byteCount = 0
    $sized = [AirBridge.G01.NativeMethods]::CertGetStoreProperty(
        $Handle, [uint32]0x00001000, [IntPtr]::Zero, [ref]$byteCount
    )
    if (-not $sized) {
        $code = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        throw [System.ComponentModel.Win32Exception]::new($code, 'CertGetStoreProperty(size) failed.')
    }
    if ($byteCount -lt 2 -or ($byteCount % 2) -ne 0) {
        throw "Unexpected localized store-name byte count: $byteCount"
    }

    $buffer = [Runtime.InteropServices.Marshal]::AllocHGlobal([int]$byteCount)
    try {
        [uint32]$actualByteCount = $byteCount
        $read = [AirBridge.G01.NativeMethods]::CertGetStoreProperty(
            $Handle, [uint32]0x00001000, $buffer, [ref]$actualByteCount
        )
        if (-not $read) {
            $code = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
            throw [System.ComponentModel.Win32Exception]::new($code, 'CertGetStoreProperty(read) failed.')
        }
        $name = [Runtime.InteropServices.Marshal]::PtrToStringUni($buffer)
        if ([string]::IsNullOrWhiteSpace($name)) {
            throw 'CertGetStoreProperty returned an empty localized store name.'
        }
        return $name
    }
    finally {
        [Runtime.InteropServices.Marshal]::FreeHGlobal($buffer)
    }
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

        [string]$DestinationLabel = 'Cert:\CurrentUser\Root',

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
            Destination = $DestinationLabel
        }
    }
    finally {
        [Runtime.InteropServices.Marshal]::FreeHGlobal($filePointer)
    }
}
