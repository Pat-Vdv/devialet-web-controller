// Spectrum analysis core: no Windows/COM dependency, unit-tested by SelfTest.cs.
// C# 5 (compiled by the .NET Framework csc shipped with Windows).
using System;
using System.Globalization;
using System.Text;

namespace DevialetSpectrum {
  public static class Bands {
    public static readonly double[] Centers = { 31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000 };
    public const double FloorDb = -90.0;
  }

  // In-place iterative radix-2 complex FFT with precomputed twiddles.
  public sealed class Fft {
    readonly int n;
    readonly int[] bitReverse;
    readonly double[] cos, sin;

    public Fft(int size) {
      if (size < 2 || (size & (size - 1)) != 0) throw new ArgumentException("size must be a power of two");
      n = size;
      bitReverse = new int[n];
      int bits = 0;
      while ((1 << bits) < n) bits++;
      for (int i = 0; i < n; i++) {
        int r = 0;
        for (int b = 0; b < bits; b++) if ((i & (1 << b)) != 0) r |= 1 << (bits - 1 - b);
        bitReverse[i] = r;
      }
      cos = new double[n / 2];
      sin = new double[n / 2];
      for (int i = 0; i < n / 2; i++) {
        cos[i] = Math.Cos(-2 * Math.PI * i / n);
        sin[i] = Math.Sin(-2 * Math.PI * i / n);
      }
    }

    public int Size { get { return n; } }

    public void Transform(double[] re, double[] im) {
      for (int i = 0; i < n; i++) {
        int j = bitReverse[i];
        if (j > i) {
          double t = re[i]; re[i] = re[j]; re[j] = t;
          t = im[i]; im[i] = im[j]; im[j] = t;
        }
      }
      for (int len = 2; len <= n; len <<= 1) {
        int half = len >> 1, step = n / len;
        for (int start = 0; start < n; start += len) {
          for (int k = 0; k < half; k++) {
            double wr = cos[k * step], wi = sin[k * step];
            int a = start + k, b = a + half;
            double xr = re[b] * wr - im[b] * wi;
            double xi = re[b] * wi + im[b] * wr;
            re[b] = re[a] - xr; im[b] = im[a] - xi;
            re[a] += xr; im[a] += xi;
          }
        }
      }
    }
  }

  // Hann-windowed FFT -> energy per octave band, in dB relative to a
  // full-scale sine (a 0 dBFS sine centred in a band reads ~0 dB).
  public sealed class BandAnalyzer {
    readonly Fft fft;
    readonly double[] window, re, im;
    readonly int[] firstBin, lastBin;
    readonly double norm;

    public BandAnalyzer(int sampleRate, int fftSize) {
      fft = new Fft(fftSize);
      window = new double[fftSize];
      double sumSquares = 0;
      for (int i = 0; i < fftSize; i++) {
        window[i] = 0.5 - 0.5 * Math.Cos(2 * Math.PI * i / fftSize);
        sumSquares += window[i] * window[i];
      }
      // Parseval: mean square = 2 * sum(|X_k|^2, one-sided) / (N * sum(w^2)).
      // A sine of amplitude A has mean square A^2/2, hence the extra factor 2.
      norm = 4.0 / (fftSize * sumSquares);
      re = new double[fftSize];
      im = new double[fftSize];
      int count = Bands.Centers.Length;
      firstBin = new int[count];
      lastBin = new int[count];
      double binHz = (double)sampleRate / fftSize;
      int nyquistBin = fftSize / 2 - 1;
      for (int b = 0; b < count; b++) {
        double lo = Bands.Centers[b] / Math.Sqrt(2), hi = Bands.Centers[b] * Math.Sqrt(2);
        int first = b == 0 ? 1 : (int)Math.Ceiling(lo / binHz);  // lowest band starts after DC
        int last = b == count - 1 ? nyquistBin : (int)Math.Ceiling(hi / binHz) - 1;
        firstBin[b] = Math.Max(1, first);
        lastBin[b] = Math.Min(nyquistBin, last);
      }
    }

    public int FftSize { get { return fft.Size; } }

    // samples: the last FftSize samples, oldest first, starting at `offset` in a ring of FftSize.
    public void Analyze(float[] ring, int offset, double[] levelsDb) {
      int n = fft.Size;
      for (int i = 0; i < n; i++) {
        re[i] = ring[(offset + i) % n] * window[i];
        im[i] = 0;
      }
      fft.Transform(re, im);
      for (int b = 0; b < levelsDb.Length; b++) {
        double energy = 0;
        for (int k = firstBin[b]; k <= lastBin[b]; k++) energy += re[k] * re[k] + im[k] * im[k];
        double meanSquareRatio = energy * norm;  // 1.0 for a full-scale sine
        levelsDb[b] = meanSquareRatio > 1e-9 ? Math.Max(Bands.FloorDb, 10 * Math.Log10(meanSquareRatio))
                                             : Bands.FloorDb;
      }
    }
  }

  public sealed class Frame {
    public double[] Left = new double[Bands.Centers.Length];
    public double[] Right = new double[Bands.Centers.Length];
    public bool Silent;
  }

  // Accumulates interleaved stereo samples and emits one frame every `hop`
  // samples. Handles silence (no packets) by zero padding and WASAPI
  // discontinuities by clearing the analysis window.
  public sealed class LevelStream {
    readonly BandAnalyzer analyzer;
    readonly float[] left, right;
    readonly int size, hop;
    int write, sinceFrame;
    public long Discontinuities;
    public long Frames;

    public LevelStream(int sampleRate, int fftSize, int framesPerSecond) {
      analyzer = new BandAnalyzer(sampleRate, fftSize);
      size = fftSize;
      left = new float[size];
      right = new float[size];
      hop = Math.Max(1, sampleRate / Math.Max(1, framesPerSecond));
    }

    public int Hop { get { return hop; } }

    public void Discontinuity() {
      Array.Clear(left, 0, size);
      Array.Clear(right, 0, size);
      Discontinuities++;
    }

    // Push stereo frames; mono sources duplicate L into R. Returns the frame
    // produced by the last completed hop, or null.
    public Frame Push(float[] l, float[] r, int count) {
      Frame frame = null;
      for (int i = 0; i < count; i++) {
        left[write] = l[i];
        right[write] = r != null ? r[i] : l[i];
        write = (write + 1) % size;
        if (++sinceFrame >= hop) {
          sinceFrame = 0;
          frame = Compute();
        }
      }
      return frame;
    }

    public Frame PushSilence(int count) {
      Frame frame = null;
      int remaining = count;
      float[] zeros = new float[Math.Min(remaining, 4096)];
      while (remaining > 0) {
        int n = Math.Min(remaining, zeros.Length);
        Frame f = Push(zeros, zeros, n);
        if (f != null) frame = f;
        remaining -= n;
      }
      return frame;
    }

    Frame Compute() {
      Frame f = new Frame();
      analyzer.Analyze(left, write, f.Left);
      analyzer.Analyze(right, write, f.Right);
      bool silent = true;
      for (int b = 0; b < f.Left.Length; b++)
        if (f.Left[b] > Bands.FloorDb || f.Right[b] > Bands.FloorDb) { silent = false; break; }
      f.Silent = silent;
      Frames++;
      return f;
    }
  }

  public static class Json {
    static readonly CultureInfo Inv = CultureInfo.InvariantCulture;

    public static string Escape(string s) {
      if (s == null) return "null";
      StringBuilder sb = new StringBuilder("\"");
      foreach (char c in s) {
        if (c == '"' || c == '\\') sb.Append('\\').Append(c);
        else if (c < 0x20) sb.Append("\\u").Append(((int)c).ToString("x4"));
        else sb.Append(c);
      }
      return sb.Append('"').ToString();
    }

    static string Array(double[] values) {
      StringBuilder sb = new StringBuilder("[");
      for (int i = 0; i < values.Length; i++) {
        if (i > 0) sb.Append(',');
        sb.Append(Math.Round(values[i], 1).ToString("0.0", Inv));
      }
      return sb.Append(']').ToString();
    }

    public static string Levels(long seq, Frame f, int sampleRate) {
      return "{\"type\":\"levels\",\"seq\":" + seq.ToString(Inv) +
             ",\"l\":" + Array(f.Left) + ",\"r\":" + Array(f.Right) +
             ",\"silent\":" + (f.Silent ? "true" : "false") +
             ",\"rate\":" + sampleRate.ToString(Inv) + "}";
    }

    public static string Status(string state, string device, bool isDefault, string message,
                                long discontinuities, string format) {
      return "{\"type\":\"status\",\"state\":" + Escape(state) + ",\"device\":" + Escape(device) +
             ",\"isDefault\":" + (isDefault ? "true" : "false") + ",\"message\":" + Escape(message) +
             ",\"discontinuities\":" + discontinuities.ToString(Inv) + ",\"format\":" + Escape(format) + "}";
    }

    public static string Hello(string version) {
      return "{\"type\":\"hello\",\"agent\":\"devialet-spectrum\",\"version\":" + Escape(version) +
             ",\"bands\":[31,62,125,250,500,1000,2000,4000,8000,16000]}";
    }

    // Control messages come from our backend only: {"type":"control","capture":true|false}
    public static bool? ParseCapture(string message) {
      if (message == null || message.IndexOf("\"control\"", StringComparison.Ordinal) < 0) return null;
      string compact = message.Replace(" ", "");
      if (compact.Contains("\"capture\":true")) return true;
      if (compact.Contains("\"capture\":false")) return false;
      return null;
    }
  }
}
