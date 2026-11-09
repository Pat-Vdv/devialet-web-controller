// WASAPI loopback capture of one render endpoint (read-only tap of the mix).
// COM declarations validated by scripts/loopback-probe.ps1 on Windows 11.
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

namespace DevialetSpectrum {
  [StructLayout(LayoutKind.Sequential)]
  struct PropertyKey { public Guid fmtid; public int pid; }

  // Native PROPVARIANT: 16 bytes on x86, 24 bytes on x64. Released with PropVariantClear.
  [StructLayout(LayoutKind.Sequential)]
  struct PropVariant {
    public ushort vt;
    public ushort reserved1, reserved2, reserved3;
    public IntPtr data1, data2;
  }

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
    [PreserveSig] int GetId([MarshalAs(UnmanagedType.LPWStr)] out string id);
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

  static class NativeMethods {
    [DllImport("ole32.dll")]
    public static extern int PropVariantClear(ref PropVariant value);
  }

  public sealed class DeviceNotFoundException : Exception {
    public DeviceNotFoundException(string message) : base(message) { }
  }

  public enum SampleKind { Unsupported, Float32, Float64, Int16, Int24, Int32 }

  // One open loopback stream. Dispose() stops the stream and releases every COM object.
  public sealed class LoopbackCapture : IDisposable {
    const int eRender = 0, eConsole = 0, DEVICE_STATE_ACTIVE = 1, STGM_READ = 0, CLSCTX_ALL = 23;
    const int AUDCLNT_SHAREMODE_SHARED = 0, AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000;
    public const uint FLAG_DISCONTINUITY = 0x1, FLAG_SILENT = 0x2;
    const int AUDCLNT_S_BUFFER_EMPTY = 0x08890001;
    const ushort WAVE_FORMAT_PCM = 1, WAVE_FORMAT_IEEE_FLOAT = 3, WAVE_FORMAT_EXTENSIBLE = 0xFFFE;
    static readonly Guid SUBTYPE_PCM = new Guid("00000001-0000-0010-8000-00AA00389B71");
    static readonly Guid SUBTYPE_IEEE_FLOAT = new Guid("00000003-0000-0010-8000-00AA00389B71");
    static readonly Guid PKEY_Device_FriendlyName = new Guid("A45C254E-DF1C-4EFD-8020-67D146A850E0");

    IMMDeviceEnumerator enumerator;
    IMMDevice device;
    IAudioClient client;
    IAudioCaptureClient capture;
    IntPtr format = IntPtr.Zero;
    bool started;
    byte[] bytes = new byte[0];
    float[] floats = new float[0];

    public string DeviceName { get; private set; }
    public int SampleRate { get; private set; }
    public int Channels { get; private set; }
    public SampleKind Kind { get; private set; }
    public string FormatDescription { get; private set; }
    int blockAlign, bytesPerSample;

    static void Check(int hr, string what) {
      if (hr < 0) throw new COMException(what + " failed: HRESULT 0x" + hr.ToString("X8") + Describe(hr), hr);
    }

    public static string Describe(int hr) {
      switch ((uint)hr) {
        case 0x88890004: return " (device invalidated: output removed or changed)";
        case 0x88890008: return " (unsupported format)";
        case 0x8889000A: return " (device in use)";
        case 0x80070005: return " (access denied)";
        default: return "";
      }
    }

    public static bool IsDeviceGone(Exception e) {
      COMException c = e as COMException;
      return e is DeviceNotFoundException || (c != null && (uint)c.ErrorCode == 0x88890004);
    }

    static void Release(object o) {
      if (o != null && Marshal.IsComObject(o)) Marshal.ReleaseComObject(o);
    }

    static string FriendlyName(IMMDevice d) {
      IPropertyStore store = null;
      try {
        Check(d.OpenPropertyStore(STGM_READ, out store), "OpenPropertyStore");
        PropertyKey key = new PropertyKey();
        key.fmtid = PKEY_Device_FriendlyName;
        key.pid = 14;
        PropVariant value;
        Check(store.GetValue(ref key, out value), "IPropertyStore.GetValue");
        try {
          return value.vt == 31 ? Marshal.PtrToStringUni(value.data1) : "";
        } finally {
          NativeMethods.PropVariantClear(ref value);
        }
      } finally {
        Release(store);
      }
    }

    // Name of the current default output (for the "is default" indicator).
    public static string DefaultOutputName() {
      IMMDeviceEnumerator en = null;
      IMMDevice d = null;
      try {
        en = (IMMDeviceEnumerator)new MMDeviceEnumerator();
        if (en.GetDefaultAudioEndpoint(eRender, eConsole, out d) < 0 || d == null) return null;
        return FriendlyName(d);
      } finally {
        Release(d);
        Release(en);
      }
    }

    public LoopbackCapture(string match) {
      try {
        enumerator = (IMMDeviceEnumerator)new MMDeviceEnumerator();
        IMMDeviceCollection collection = null;
        List<string> matches = new List<string>();
        try {
          Check(enumerator.EnumAudioEndpoints(eRender, DEVICE_STATE_ACTIVE, out collection), "EnumAudioEndpoints");
          int count;
          Check(collection.GetCount(out count), "GetCount");
          for (int i = 0; i < count; i++) {
            IMMDevice d;
            Check(collection.Item(i, out d), "Item");
            string name = FriendlyName(d);
            if (name.IndexOf(match, StringComparison.OrdinalIgnoreCase) >= 0) {
              matches.Add(name);
              Release(device);
              device = d;
              DeviceName = name;
            } else {
              Release(d);
            }
          }
        } finally {
          Release(collection);
        }
        if (matches.Count == 0) throw new DeviceNotFoundException("no active output contains '" + match + "'");
        if (matches.Count > 1)
          throw new DeviceNotFoundException("several outputs contain '" + match + "': " + string.Join(" | ", matches.ToArray()));

        Guid iidClient = typeof(IAudioClient).GUID;
        object obj;
        Check(device.Activate(ref iidClient, CLSCTX_ALL, IntPtr.Zero, out obj), "IMMDevice.Activate");
        client = (IAudioClient)obj;
        Check(client.GetMixFormat(out format), "GetMixFormat");
        ReadFormat();
        Check(client.Initialize(AUDCLNT_SHAREMODE_SHARED, AUDCLNT_STREAMFLAGS_LOOPBACK, 10000000, 0, format, IntPtr.Zero),
              "IAudioClient.Initialize(loopback)");
        Guid iidCapture = typeof(IAudioCaptureClient).GUID;
        Check(client.GetService(ref iidCapture, out obj), "GetService");
        capture = (IAudioCaptureClient)obj;
        Check(client.Start(), "IAudioClient.Start");
        started = true;
      } catch {
        Dispose();
        throw;
      }
    }

    void ReadFormat() {
      ushort tag = (ushort)Marshal.ReadInt16(format, 0);
      Channels = Marshal.ReadInt16(format, 2);
      SampleRate = Marshal.ReadInt32(format, 4);
      blockAlign = Marshal.ReadInt16(format, 12);
      int bits = Marshal.ReadInt16(format, 14), cbSize = Marshal.ReadInt16(format, 16);
      Guid sub = Guid.Empty;
      if (tag == WAVE_FORMAT_EXTENSIBLE && cbSize >= 22)
        sub = (Guid)Marshal.PtrToStructure(IntPtr.Add(format, 24), typeof(Guid));
      bool isFloat = tag == WAVE_FORMAT_IEEE_FLOAT || sub == SUBTYPE_IEEE_FLOAT;
      bool isPcm = tag == WAVE_FORMAT_PCM || sub == SUBTYPE_PCM;
      bytesPerSample = Channels > 0 ? blockAlign / Channels : 0;
      Kind = SampleKind.Unsupported;
      if (isFloat && bytesPerSample == 4) Kind = SampleKind.Float32;
      else if (isFloat && bytesPerSample == 8) Kind = SampleKind.Float64;
      else if (isPcm && bytesPerSample == 2) Kind = SampleKind.Int16;
      else if (isPcm && bytesPerSample == 3) Kind = SampleKind.Int24;
      else if (isPcm && bytesPerSample == 4) Kind = SampleKind.Int32;
      FormatDescription = SampleRate + " Hz, " + Channels + " ch, " + (bytesPerSample * 8) + "-bit " +
                          (isFloat ? "float" : isPcm ? "PCM" : "unknown") +
                          (bits != bytesPerSample * 8 ? " (" + bits + " bits)" : "");
      if (Kind == SampleKind.Unsupported) throw new COMException("unsupported mix format: " + FormatDescription);
    }

    static double Decode(SampleKind kind, byte[] b, int o) {
      switch (kind) {
        case SampleKind.Float32: return BitConverter.ToSingle(b, o);
        case SampleKind.Float64: return BitConverter.ToDouble(b, o);
        case SampleKind.Int16: return BitConverter.ToInt16(b, o) / 32768.0;
        case SampleKind.Int24: return (b[o] | (b[o + 1] << 8) | ((sbyte)b[o + 2] << 16)) / 8388608.0;
        case SampleKind.Int32: return BitConverter.ToInt32(b, o) / 2147483648.0;
        default: return 0;
      }
    }

    // Drains every available packet. For each packet calls onData(left, right, frames, flags);
    // silent packets are delivered as zeros. Returns the number of frames read.
    public int Drain(float[] left, float[] right, Action<int, uint> onData) {
      int total = 0;
      uint packet;
      Check(capture.GetNextPacketSize(out packet), "GetNextPacketSize");
      while (packet > 0) {
        IntPtr data;
        uint n, flags;
        ulong p1, p2;
        int hr = capture.GetBuffer(out data, out n, out flags, out p1, out p2);
        if (hr == AUDCLNT_S_BUFFER_EMPTY) break;
        Check(hr, "GetBuffer");
        try {
          int frames = (int)Math.Min(n, (uint)left.Length);
          if ((flags & FLAG_SILENT) != 0 || frames == 0) {
            Array.Clear(left, 0, frames);
            Array.Clear(right, 0, frames);
          } else if (Kind == SampleKind.Float32) {
            int count = frames * Channels;
            if (floats.Length < count) floats = new float[count];
            Marshal.Copy(data, floats, 0, count);
            for (int f = 0; f < frames; f++) {
              left[f] = floats[f * Channels];
              right[f] = Channels > 1 ? floats[f * Channels + 1] : left[f];
            }
          } else {
            int size = frames * blockAlign;
            if (bytes.Length < size) bytes = new byte[size];
            Marshal.Copy(data, bytes, 0, size);
            for (int f = 0; f < frames; f++) {
              left[f] = (float)Decode(Kind, bytes, f * blockAlign);
              right[f] = Channels > 1 ? (float)Decode(Kind, bytes, f * blockAlign + bytesPerSample) : left[f];
            }
          }
          onData(frames, flags);
          total += frames;
        } finally {
          Check(capture.ReleaseBuffer(n), "ReleaseBuffer");
        }
        Check(capture.GetNextPacketSize(out packet), "GetNextPacketSize");
      }
      return total;
    }

    public void Dispose() {
      if (started && client != null) {
        try { client.Stop(); } catch (COMException) { }
        started = false;
      }
      if (format != IntPtr.Zero) {
        Marshal.FreeCoTaskMem(format);
        format = IntPtr.Zero;
      }
      Release(capture); capture = null;
      Release(client); client = null;
      Release(device); device = null;
      Release(enumerator); enumerator = null;
    }
  }
}
