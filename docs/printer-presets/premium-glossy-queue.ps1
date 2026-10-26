# Give the PC's "Premium glossy paper" queue its defaults: the "Glossy photo paper proxies"
# queue's (A4, rear feeder, High, portrait) with Epson Premium Glossy as the paper (media 325).
# Run on PomeloMadness as admin; it doesn't create the queue (Add-Printer -Name 'Premium glossy
# paper' -DriverName 'EPSON ET-8550 Series' -PortName 'EPA71B16:ET-8550 SERIES'). Used once on
# 2026-10-26. Over SSH it is longer than cmd's 8191-character limit: pipe it into a short
# -EncodedCommand bootstrap that reads stdin. The driver keeps media and quality in private
# DEVMODE bytes too (README.md here), so the change goes through DocumentProperties.
try {
$ErrorActionPreference = 'Stop'
if (-not ('WS' -as [type])) {
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class WS {
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
  public struct PRINTER_DEFAULTS { public IntPtr pDatatype; public IntPtr pDevMode; public int DesiredAccess; }
  [DllImport("winspool.drv", CharSet=CharSet.Unicode, SetLastError=true, EntryPoint="OpenPrinterW")]
  public static extern bool OpenPrinter(string name, out IntPtr h, ref PRINTER_DEFAULTS pd);
  [DllImport("winspool.drv", SetLastError=true)] public static extern bool ClosePrinter(IntPtr h);
  [DllImport("winspool.drv", CharSet=CharSet.Unicode, SetLastError=true, EntryPoint="GetPrinterW")]
  public static extern bool GetPrinter(IntPtr h, int level, IntPtr buf, int cb, out int needed);
  [DllImport("winspool.drv", CharSet=CharSet.Unicode, SetLastError=true, EntryPoint="SetPrinterW")]
  public static extern bool SetPrinter(IntPtr h, int level, IntPtr buf, int cmd);
  [DllImport("winspool.drv", CharSet=CharSet.Unicode, SetLastError=true, EntryPoint="DocumentPropertiesW")]
  public static extern int DocumentProperties(IntPtr hwnd, IntPtr h, string device, IntPtr outDm, IntPtr inDm, int mode);
  [DllImport("winspool.drv", CharSet=CharSet.Unicode, SetLastError=true, EntryPoint="DeviceCapabilitiesW")]
  public static extern int DeviceCapabilities(string device, string port, short cap, IntPtr output, IntPtr dm);

  public static IntPtr Open(string name, int access) {
    PRINTER_DEFAULTS pd = new PRINTER_DEFAULTS(); pd.DesiredAccess = access;
    IntPtr h; if (!OpenPrinter(name, out h, ref pd)) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "OpenPrinter " + name);
    return h;
  }
  // level 8 = global default devmode, level 9 = per-user devmode; returns bytes (dmSize+dmDriverExtra) or null
  public static byte[] GetDevModeLevel(string name, int level) {
    IntPtr h = Open(name, 8);
    try {
      int need; GetPrinter(h, level, IntPtr.Zero, 0, out need);
      if (need == 0) return null;
      IntPtr buf = Marshal.AllocHGlobal(need);
      try {
        if (!GetPrinter(h, level, buf, need, out need)) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "GetPrinter");
        IntPtr dm = Marshal.ReadIntPtr(buf, 0);
        if (dm == IntPtr.Zero) return null;
        int sz = Marshal.ReadInt16(dm, 68) + Marshal.ReadInt16(dm, 70);
        byte[] b = new byte[sz]; Marshal.Copy(dm, b, 0, sz); return b;
      } finally { Marshal.FreeHGlobal(buf); }
    } finally { ClosePrinter(h); }
  }
  // DocumentProperties: in=null -> driver default; else DM_IN_BUFFER|DM_OUT_BUFFER merge
  public static byte[] DocProps(string name, byte[] inDm) {
    IntPtr h = Open(name, 8);
    try {
      int sz = DocumentProperties(IntPtr.Zero, h, name, IntPtr.Zero, IntPtr.Zero, 0);
      if (sz <= 0) throw new Exception("DocumentProperties size failed " + Marshal.GetLastWin32Error());
      IntPtr o = Marshal.AllocHGlobal(sz); IntPtr i = IntPtr.Zero;
      try {
        int mode = 2; // DM_OUT_BUFFER
        if (inDm != null) { i = Marshal.AllocHGlobal(Math.Max(inDm.Length, sz)); Marshal.Copy(inDm, 0, i, inDm.Length); mode |= 8; }
        int r = DocumentProperties(IntPtr.Zero, h, name, o, i, mode);
        if (r != 1) throw new Exception("DocumentProperties returned " + r + " err " + Marshal.GetLastWin32Error());
        int real = Marshal.ReadInt16(o, 68) + Marshal.ReadInt16(o, 70);
        byte[] b = new byte[real]; Marshal.Copy(o, b, 0, real); return b;
      } finally { Marshal.FreeHGlobal(o); if (i != IntPtr.Zero) Marshal.FreeHGlobal(i); }
    } finally { ClosePrinter(h); }
  }
  public static void SetDevModeLevel8(string name, byte[] dm) {
    IntPtr h = Open(name, 0x000F000C); // PRINTER_ALL_ACCESS
    try {
      IntPtr d = Marshal.AllocHGlobal(dm.Length); Marshal.Copy(dm, 0, d, dm.Length);
      IntPtr info = Marshal.AllocHGlobal(IntPtr.Size); Marshal.WriteIntPtr(info, d);
      try { if (!SetPrinter(h, 8, info, 0)) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "SetPrinter 8"); }
      finally { Marshal.FreeHGlobal(info); Marshal.FreeHGlobal(d); }
    } finally { ClosePrinter(h); }
  }
  public static int Cap(string dev, string port, short cap, IntPtr outp) { return DeviceCapabilities(dev, port, cap, outp, IntPtr.Zero); }
}
'@
}
function U16($b,$o){ [BitConverter]::ToUInt16($b,$o) }
function I16($b,$o){ [BitConverter]::ToInt16($b,$o) }
function U32($b,$o){ [BitConverter]::ToUInt32($b,$o) }
function Str($b,$o,$n){ ([Text.Encoding]::Unicode.GetString($b,$o,$n*2)).Split([char]0)[0] }
function Decode($b) {
  [ordered]@{
    len = $b.Length
    dmDeviceName = Str $b 0 32
    dmSpecVersion = '0x{0:X4}' -f (U16 $b 64)
    dmDriverVersion = '0x{0:X4}' -f (U16 $b 66)
    dmSize = U16 $b 68
    dmDriverExtra = U16 $b 70
    dmFields = '0x{0:X8}' -f (U32 $b 72)
    dmOrientation = I16 $b 76
    dmPaperSize = I16 $b 78
    dmPaperLength = I16 $b 80
    dmPaperWidth = I16 $b 82
    dmScale = I16 $b 84
    dmCopies = I16 $b 86
    dmDefaultSource = I16 $b 88
    dmPrintQuality = I16 $b 90
    dmColor = I16 $b 92
    dmDuplex = I16 $b 94
    dmYResolution = I16 $b 96
    dmTTOption = I16 $b 98
    dmCollate = I16 $b 100
    dmFormName = Str $b 102 32
    dmNup = U32 $b 180
    dmICMMethod = U32 $b 188
    dmICMIntent = U32 $b 192
    dmMediaType = U32 $b 196
    dmDitherType = U32 $b 200
  }
}
function RegDM($q) {
  $raw = (Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\Print\Printers\$q").'Default DevMode'
  if (-not $raw) { return $null }
  $n = (U16 $raw 68) + (U16 $raw 70)
  [byte[]]$raw[0..($n-1)]
}
$GLOSSY = 'Glossy photo paper proxies'; $PLAIN = 'ET-8550 Series(Network)'; $NEW = 'Premium glossy paper'
$PORT = 'EPA71B16:ET-8550 SERIES'
Add-Type -AssemblyName ReachFramework, System.Printing
function Hex($b) { ($b | ForEach-Object { $_.ToString('x2') }) -join '' }
function SetU32([byte[]]$b, $off, $v) { $c = [byte[]]$b.Clone(); $x=[BitConverter]::GetBytes([uint32]$v); [Array]::Copy($x,0,$c,$off,4); $c }
function SetName([byte[]]$b, $name) { $c = [byte[]]$b.Clone(); for ($i=0;$i -lt 64;$i++){$c[$i]=0}; $nb=[Text.Encoding]::Unicode.GetBytes($name); [Array]::Copy($nb,0,$c,0,[Math]::Min(62,$nb.Length)); $c }
$PSF='http://schemas.microsoft.com/windows/2003/08/printing/printschemaframework'
$conv = New-Object System.Printing.Interop.PrintTicketConverter($NEW, 1)
function Summ($dm) { $t = $conv.ConvertDevModeToPrintTicket($dm, [System.Printing.PrintTicketScope]::JobScope); $x = New-Object Xml.XmlDocument; $ms = $t.GetXmlStream(); $ms.Position=0; $x.Load($ms)
  $ns = New-Object Xml.XmlNamespaceManager $x.NameTable; $ns.AddNamespace('psf',$PSF)
  ('PageInputBin','PageMediaType','PageOutputQuality','PageResolution','PageMediaSize','PageOrientation','PageBorderless','PageOutputColor' | ForEach-Object { $f=$x.SelectSingleNode("//psf:Feature[@name='psk:$_']/psf:Option",$ns); "$_=" + $f.name }) -join '  ' }
$g = SetName ([WS]::GetDevModeLevel($GLOSSY, 8)) $NEW
# 1) driver merges the media change (public dmMediaType -> 325)
$m = [WS]::DocProps($NEW, (SetU32 $g 196 325))
# 2) the driver resets quality to Standard on media change; restore the glossy queue's High-quality private bytes
$c = [byte[]]$m.Clone()
foreach ($off in 344,345,370,371,372,373,374,375,8008,8009) { $c[$off] = $g[$off] }
# 3) let the driver validate the result
$v = [WS]::DocProps($NEW, $c)
"candidate==validated: " + ((Hex $c) -eq (Hex $v))
"validated: " + (Summ $v)
"HEX|final_candidate|" + (Hex $v)
# 4) write printer-wide default
[WS]::SetDevModeLevel8($NEW, $v)
"SetPrinter level 8 ok"
$rb8 = [WS]::GetDevModeLevel($NEW, 8); $rbr = RegDM $NEW
"readback l8==written: " + ((Hex $rb8) -eq (Hex $v)) + "  reg==l8: " + ((Hex $rbr) -eq (Hex $rb8))
"HEX|readback_l8|" + (Hex $rb8)
$dp = [WS]::DocProps($NEW, $rb8); "DocProps(readback)==readback: " + ((Hex $dp) -eq (Hex $rb8))
"readback PT: " + (Summ $rb8)
foreach ($q in $PLAIN, $GLOSSY, $NEW) { $l9 = [WS]::GetDevModeLevel($q, 9); "level9 per-user devmode for ${q}: " + $(if ($l9) { "present len $($l9.Length)" } else { 'none' }) }

} catch { "ERROR: " + $_.Exception.ToString() + "`n" + $_.InvocationInfo.PositionMessage }
