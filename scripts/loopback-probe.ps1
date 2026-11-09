# Feasibility probe: WASAPI loopback capture of ONE Windows render endpoint.
#
# It only: finds the active output whose name contains -Match (default: env DEVIALET_AGENT_DEVICE),
# captures -Seconds of audio in loopback mode, prints the mix format and the
# RMS / peak level of each channel.
# It never writes audio to disk, never modifies the signal (loopback is a
# read-only tap of the shared-mode mix) and makes no network connection.
#
# Usage (PowerShell): .\loopback-probe.ps1 -Match "My Phantom System" -Seconds 3
# Messages are ASCII on purpose: Windows PowerShell 5.1 reads BOM-less files as ANSI.
param(
  [string]$Match = $(if ($env:DEVIALET_AGENT_DEVICE) { $env:DEVIALET_AGENT_DEVICE } else { "My Phantom System" }),
  [ValidateRange(1, 10)][int]$Seconds = 3
)

$code = @"
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

namespace DevialetProbe {
  [StructLayout(LayoutKind.Sequential)]
  public struct PropertyKey { public Guid fmtid; public int pid; }

  // Native PROPVARIANT: VARTYPE + three reserved WORDs (8 bytes), then a union
  // whose largest members are two pointer-sized words: 16 bytes on x86,
  // 24 bytes on x64. Must be released with PropVariantClear.
  [StructLayout(LayoutKind.Sequential)]
  public struct PropVariant {
    public ushort vt;
    public ushort reserved1, reserved2, reserved3;
    public IntPtr data1, data2;
  }

  // All methods use [PreserveSig]: without it the CLR assumes a hidden
  // [out, retval] parameter and the vtable call no longer matches the native
  // signature. Interfaces declare only their leading methods, in vtable order.
  [ComImport, Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IMMDeviceEnumerator {
    [PreserveSig] int EnumAudioEndpoints(int dataFlow, int stateMask, out IMMDeviceCollection devices);
    [PreserveSig] int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice device);
  }

  [ComImport, Guid("0BD7A1BE-7A1A-44DB-8397-CC5392387B5E"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IMMDeviceCollection {
    [PreserveSig] int GetCount(out int count);
    [PreserveSig] int Item(int index, out IMMDevice device);
  }

  [ComImport, Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IMMDevice {
    [PreserveSig] int Activate(ref Guid iid, int clsCtx, IntPtr activationParams,
                               [MarshalAs(UnmanagedType.IUnknown)] out object iface);
    [PreserveSig] int OpenPropertyStore(int access, out IPropertyStore store);
  }

  [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IPropertyStore {
    [PreserveSig] int GetCount(out int count);
    [PreserveSig] int GetAt(int index, out PropertyKey key);
    [PreserveSig] int GetValue(ref PropertyKey key, out PropVariant value);
  }

  [ComImport, Guid("1CB9AD4C-DBFA-4C32-B178-C2F568A703B2"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IAudioClient {
    [PreserveSig] int Initialize(int shareMode, int streamFlags, long bufferDuration, long periodicity,
                                 IntPtr format, IntPtr sessionGuid);
    [PreserveSig] int GetBufferSize(out uint frames);
    [PreserveSig] int GetStreamLatency(out long latency);
    [PreserveSig] int GetCurrentPadding(out uint padding);
    [PreserveSig] int IsFormatSupported(int shareMode, IntPtr format, out IntPtr closest);
    [PreserveSig] int GetMixFormat(out IntPtr format);
    [PreserveSig] int GetDevicePeriod(out long defaultPeriod, out long minimumPeriod);
    [PreserveSig] int Start();
    [PreserveSig] int Stop();
    [PreserveSig] int Reset();
    [PreserveSig] int SetEventHandle(IntPtr handle);
    [PreserveSig] int GetService(ref Guid iid, [MarshalAs(UnmanagedType.IUnknown)] out object service);
  }

  [ComImport, Guid("C8ADBD64-E71E-48A0-A4DE-185C395CD317"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IAudioCaptureClient {
    [PreserveSig] int GetBuffer(out IntPtr data, out uint frames, out uint flags, out ulong devicePosition,
                                out ulong qpcPosition);
    [PreserveSig] int ReleaseBuffer(uint frames);
    [PreserveSig] int GetNextPacketSize(out uint frames);
  }

  [ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
  class MMDeviceEnumerator { }

  static class Native {
    [DllImport("ole32.dll")]
    public static extern int PropVariantClear(ref PropVariant value);
  }

  enum SampleKind { Unsupported, Float32, Float64, Int16, Int24, Int32 }

  public static class Loopback {
    const int eRender = 0, eConsole = 0, DEVICE_STATE_ACTIVE = 1, STGM_READ = 0, CLSCTX_ALL = 23;
    const int AUDCLNT_SHAREMODE_SHARED = 0, AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000;
    const uint AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY = 0x1, AUDCLNT_BUFFERFLAGS_SILENT = 0x2;
    const int AUDCLNT_S_BUFFER_EMPTY = 0x08890001;
    const ushort WAVE_FORMAT_PCM = 1, WAVE_FORMAT_IEEE_FLOAT = 3, WAVE_FORMAT_EXTENSIBLE = 0xFFFE;
    const long REFTIMES_PER_SEC = 10000000;
    static readonly Guid SUBTYPE_PCM = new Guid("00000001-0000-0010-8000-00AA00389B71");
    static readonly Guid SUBTYPE_IEEE_FLOAT = new Guid("00000003-0000-0010-8000-00AA00389B71");
    static readonly Guid PKEY_Device_FriendlyName = new Guid("A45C254E-DF1C-4EFD-8020-67D146A850E0");

    static void Check(int hr, string what) {
      if (hr < 0) throw new COMException(what + " failed: HRESULT 0x" + hr.ToString("X8") + Describe(hr), hr);
    }

    static string Describe(int hr) {
      switch ((uint)hr) {
        case 0x88890004: return " (AUDCLNT_E_DEVICE_INVALIDATED: output removed or changed)";
        case 0x88890008: return " (AUDCLNT_E_UNSUPPORTED_FORMAT)";
        case 0x8889000A: return " (AUDCLNT_E_DEVICE_IN_USE)";
        case 0x80070005: return " (E_ACCESSDENIED)";
        default: return "";
      }
    }

    static void Release(object o) {
      if (o != null && Marshal.IsComObject(o)) Marshal.ReleaseComObject(o);
    }

    static string FriendlyName(IMMDevice device) {
      IPropertyStore store = null;
      try {
        Check(device.OpenPropertyStore(STGM_READ, out store), "OpenPropertyStore");
        PropertyKey key = new PropertyKey();
        key.fmtid = PKEY_Device_FriendlyName;
        key.pid = 14;
        PropVariant value;
        Check(store.GetValue(ref key, out value), "IPropertyStore.GetValue");
        try {
          return value.vt == 31 /* VT_LPWSTR */ ? Marshal.PtrToStringUni(value.data1) : "(no name)";
        } finally {
          Native.PropVariantClear(ref value);
        }
      } finally {
        Release(store);
      }
    }

    // Decode one sample (bytes at offset) to the -1..1 range.
    static double Sample(SampleKind kind, byte[] b, int o) {
      switch (kind) {
        case SampleKind.Float32: return BitConverter.ToSingle(b, o);
        case SampleKind.Float64: return BitConverter.ToDouble(b, o);
        case SampleKind.Int16: return BitConverter.ToInt16(b, o) / 32768.0;
        case SampleKind.Int24: return (b[o] | (b[o + 1] << 8) | ((sbyte)b[o + 2] << 16)) / 8388608.0;
        // 32-bit container: full 32-bit PCM, or 24/20-bit valid bits left-justified
        // (WAVEFORMATEXTENSIBLE rule), so the same scale is correct for both.
        case SampleKind.Int32: return BitConverter.ToInt32(b, o) / 2147483648.0;
        default: return 0;
      }
    }

    static string Db(double linear) {
      return linear > 0 ? (20 * Math.Log10(linear)).ToString("F1") + " dBFS" : "-inf dBFS";
    }

    public static string Run(string match, int seconds) {
      StringBuilder log = new StringBuilder();
      IMMDeviceEnumerator enumerator = null;
      IMMDeviceCollection collection = null;
      IMMDevice defaultDevice = null, target = null;
      List<IMMDevice> devices = new List<IMMDevice>();
      IAudioClient client = null;
      IAudioCaptureClient capture = null;
      IntPtr format = IntPtr.Zero;
      bool started = false;
      try {
        enumerator = (IMMDeviceEnumerator)new MMDeviceEnumerator();
        Check(enumerator.EnumAudioEndpoints(eRender, DEVICE_STATE_ACTIVE, out collection), "EnumAudioEndpoints");
        int count;
        Check(collection.GetCount(out count), "IMMDeviceCollection.GetCount");
        List<string> matches = new List<string>();
        for (int i = 0; i < count; i++) {
          IMMDevice device;
          Check(collection.Item(i, out device), "IMMDeviceCollection.Item");
          devices.Add(device);
          string name = FriendlyName(device);
          log.AppendLine("output  : " + name);
          if (name.IndexOf(match, StringComparison.OrdinalIgnoreCase) >= 0) {
            matches.Add(name);
            target = device;
          }
        }
        if (enumerator.GetDefaultAudioEndpoint(eRender, eConsole, out defaultDevice) >= 0 && defaultDevice != null)
          log.AppendLine("default : " + FriendlyName(defaultDevice));
        if (matches.Count == 0) {
          log.AppendLine("RESULT: no active output contains '" + match + "'");
          return log.ToString();
        }
        if (matches.Count > 1) {
          log.AppendLine("RESULT: several outputs contain '" + match + "' (" + string.Join(" | ", matches.ToArray())
                         + "): use a more specific -Match");
          return log.ToString();
        }
        log.AppendLine("target  : " + matches[0]);

        Guid iidAudioClient = typeof(IAudioClient).GUID;
        object obj;
        Check(target.Activate(ref iidAudioClient, CLSCTX_ALL, IntPtr.Zero, out obj), "IMMDevice.Activate");
        client = (IAudioClient)obj;
        Check(client.GetMixFormat(out format), "IAudioClient.GetMixFormat");

        // WAVEFORMATEX: tag@0 channels@2 rate@4 avgBytes@8 blockAlign@12 bits@14 cbSize@16
        // WAVEFORMATEXTENSIBLE adds: validBits@18 channelMask@20 subFormat@24
        ushort tag = (ushort)Marshal.ReadInt16(format, 0);
        int channels = Marshal.ReadInt16(format, 2);
        int rate = Marshal.ReadInt32(format, 4);
        int blockAlign = Marshal.ReadInt16(format, 12);
        int bits = Marshal.ReadInt16(format, 14);
        int cbSize = Marshal.ReadInt16(format, 16);
        int validBits = bits;
        uint channelMask = 0;
        Guid subFormat = Guid.Empty;
        if (tag == WAVE_FORMAT_EXTENSIBLE && cbSize >= 22) {
          validBits = Marshal.ReadInt16(format, 18);
          channelMask = (uint)Marshal.ReadInt32(format, 20);
          subFormat = (Guid)Marshal.PtrToStructure(IntPtr.Add(format, 24), typeof(Guid));
        }
        bool isFloat = tag == WAVE_FORMAT_IEEE_FLOAT || subFormat == SUBTYPE_IEEE_FLOAT;
        bool isPcm = tag == WAVE_FORMAT_PCM || subFormat == SUBTYPE_PCM;
        int bytesPerSample = channels > 0 ? blockAlign / channels : 0;
        SampleKind kind = SampleKind.Unsupported;
        if (isFloat && bytesPerSample == 4) kind = SampleKind.Float32;
        else if (isFloat && bytesPerSample == 8) kind = SampleKind.Float64;
        else if (isPcm && bytesPerSample == 2) kind = SampleKind.Int16;
        else if (isPcm && bytesPerSample == 3) kind = SampleKind.Int24;
        else if (isPcm && bytesPerSample == 4) kind = SampleKind.Int32;
        log.AppendLine(string.Format(
          "format  : {0} Hz, {1} ch, {2}-bit container, {3} valid bits, {4}, tag 0x{5:X4}, channel mask 0x{6:X}",
          rate, channels, bytesPerSample * 8, validBits, isFloat ? "float" : isPcm ? "PCM" : "unknown",
          (int)tag, channelMask));
        if (kind == SampleKind.Unsupported)
          log.AppendLine("warning : sample format not decoded, levels cannot be measured");

        Check(client.Initialize(AUDCLNT_SHAREMODE_SHARED, AUDCLNT_STREAMFLAGS_LOOPBACK, REFTIMES_PER_SEC, 0,
                                format, IntPtr.Zero), "IAudioClient.Initialize(loopback)");
        Guid iidCapture = typeof(IAudioCaptureClient).GUID;
        Check(client.GetService(ref iidCapture, out obj), "IAudioClient.GetService");
        capture = (IAudioCaptureClient)obj;

        double[] sumSquares = new double[channels];
        double[] peak = new double[channels];
        long frames = 0, silentFrames = 0, packets = 0, discontinuities = 0;
        byte[] buffer = new byte[0];
        Check(client.Start(), "IAudioClient.Start");
        started = true;
        Stopwatch clock = Stopwatch.StartNew();
        while (clock.Elapsed.TotalSeconds < seconds) {
          Thread.Sleep(10);
          uint packetFrames;
          Check(capture.GetNextPacketSize(out packetFrames), "GetNextPacketSize");
          while (packetFrames > 0) {
            IntPtr data;
            uint n, flags;
            ulong devicePosition, qpcPosition;
            int hr = capture.GetBuffer(out data, out n, out flags, out devicePosition, out qpcPosition);
            if (hr == AUDCLNT_S_BUFFER_EMPTY) break;
            Check(hr, "IAudioCaptureClient.GetBuffer");
            packets++;
            if ((flags & AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY) != 0) discontinuities++;
            if ((flags & AUDCLNT_BUFFERFLAGS_SILENT) != 0) {
              silentFrames += n;  // the buffer content must be treated as zeros
            } else if (kind != SampleKind.Unsupported && n > 0) {
              int bytes = (int)n * blockAlign;
              if (buffer.Length < bytes) buffer = new byte[bytes];
              Marshal.Copy(data, buffer, 0, bytes);
              for (int f = 0; f < n; f++) {
                for (int c = 0; c < channels; c++) {
                  double s = Sample(kind, buffer, f * blockAlign + c * bytesPerSample);
                  sumSquares[c] += s * s;
                  double a = Math.Abs(s);
                  if (a > peak[c]) peak[c] = a;
                }
              }
            }
            frames += n;
            Check(capture.ReleaseBuffer(n), "IAudioCaptureClient.ReleaseBuffer");
            Check(capture.GetNextPacketSize(out packetFrames), "GetNextPacketSize");
          }
        }

        log.AppendLine(string.Format("captured: {0} frames ({1:F2} s of {2} s), {3} packets, {4} silent frames, {5} discontinuities",
                                     frames, rate > 0 ? frames / (double)rate : 0, seconds, packets, silentFrames, discontinuities));
        if (frames == 0) {
          log.AppendLine("RESULT: capture opened but no audio packet received: nothing is playing on this output");
          return log.ToString();
        }
        bool stereo = channels == 2 && (channelMask == 0 || channelMask == 0x3);
        for (int c = 0; c < channels; c++) {
          string label = stereo ? (c == 0 ? "L" : "R") : "ch" + c;
          log.AppendLine(string.Format("channel {0}: RMS {1}, peak {2}", label,
                                       Db(Math.Sqrt(sumSquares[c] / frames)), Db(peak[c])));
        }
        log.AppendLine(silentFrames == frames ? "RESULT: loopback OK but all frames were flagged silent"
                                              : "RESULT: loopback OK");
        return log.ToString();
      } finally {
        if (started && client != null) client.Stop();
        if (format != IntPtr.Zero) Marshal.FreeCoTaskMem(format);
        Release(capture);
        Release(client);
        Release(defaultDevice);
        foreach (IMMDevice device in devices) Release(device);
        Release(collection);
        Release(enumerator);
      }
    }
  }
}
"@

Add-Type -TypeDefinition $code -Language CSharp
try {
  [DevialetProbe.Loopback]::Run($Match, $Seconds)
} catch {
  $e = $_.Exception
  if ($e.InnerException) { $e = $e.InnerException }
  "RESULT: ERROR " + $e.Message
}
