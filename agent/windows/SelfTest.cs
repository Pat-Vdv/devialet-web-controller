// Self tests of the analysis core on synthetic signals (no audio device needed).
// Run with: DevialetSpectrumAgent.exe --selftest   (exit code 0 = all passed)
using System;
using System.Globalization;
using System.Threading;

namespace DevialetSpectrum {
  public static class SelfTest {
    const int Rate = 44100, N = 4096;
    static int failures, passed;

    static void Check(bool ok, string name, string detail) {
      if (ok) passed++; else failures++;
      Console.WriteLine((ok ? "PASS " : "FAIL ") + name + (detail != null ? "  [" + detail + "]" : ""));
    }

    static string F(double v) { return v.ToString("0.0", CultureInfo.InvariantCulture); }

    static float[] Sine(double freq, double amplitude, int count) {
      float[] s = new float[count];
      for (int i = 0; i < count; i++) s[i] = (float)(amplitude * Math.Sin(2 * Math.PI * freq * i / Rate));
      return s;
    }

    static Frame Analyze(float[] l, float[] r) {
      LevelStream stream = new LevelStream(Rate, N, 25);
      Frame last = null;
      int offset = 0;
      while (offset < l.Length) {
        int n = Math.Min(1000, l.Length - offset);
        float[] a = new float[n], b = new float[n];
        Array.Copy(l, offset, a, 0, n);
        Array.Copy(r, offset, b, 0, n);
        Frame f = stream.Push(a, b, n);
        if (f != null) last = f;
        offset += n;
      }
      return last;
    }

    static void FftMatchesNaiveDft() {
      int n = 64;
      Random rnd = new Random(1);
      double[] re = new double[n], im = new double[n], x = new double[n];
      for (int i = 0; i < n; i++) { x[i] = rnd.NextDouble() * 2 - 1; re[i] = x[i]; }
      new Fft(n).Transform(re, im);
      double maxErr = 0;
      for (int k = 0; k < n; k++) {
        double r = 0, m = 0;
        for (int t = 0; t < n; t++) {
          r += x[t] * Math.Cos(-2 * Math.PI * k * t / n);
          m += x[t] * Math.Sin(-2 * Math.PI * k * t / n);
        }
        maxErr = Math.Max(maxErr, Math.Max(Math.Abs(r - re[k]), Math.Abs(m - im[k])));
      }
      Check(maxErr < 1e-9, "fft matches naive DFT", "max error " + maxErr.ToString("E1"));
    }

    static void FftRejectsNonPowerOfTwo() {
      bool threw = false;
      try { new Fft(1000); } catch (ArgumentException) { threw = true; }
      Check(threw, "fft rejects non power of two size", null);
    }

    static void EachBandPeaksAtItsCentre() {
      for (int b = 0; b < Bands.Centers.Length; b++) {
        double freq = Bands.Centers[b];
        Frame f = Analyze(Sine(freq, 1.0, N * 3), Sine(freq, 1.0, N * 3));
        int best = 0;
        for (int i = 1; i < f.Left.Length; i++) if (f.Left[i] > f.Left[best]) best = i;
        double own = f.Left[b];
        double worstFar = Bands.FloorDb;
        for (int i = 0; i < f.Left.Length; i++)
          if (Math.Abs(i - b) >= 2) worstFar = Math.Max(worstFar, f.Left[i]);
        bool ok = best == b && Math.Abs(own) <= 1.5 && worstFar < -40;
        Check(ok, "full-scale " + freq + " Hz sine lands in its band at ~0 dB",
              "own " + F(own) + " dB, loudest non-adjacent " + F(worstFar) + " dB");
      }
    }

    static void LevelIsCalibrated() {
      Frame f = Analyze(Sine(1000, 0.1, N * 3), Sine(1000, 0.1, N * 3));  // -20 dBFS
      Check(Math.Abs(f.Left[5] + 20) <= 1.0, "-20 dBFS sine reads -20 dB", F(f.Left[5]) + " dB");
    }

    static void LeftRightAreIndependent() {
      Frame f = Analyze(Sine(1000, 1.0, N * 3), new float[N * 3]);
      Check(f.Left[5] > -1.5 && f.Right[5] <= Bands.FloorDb, "left-only signal stays on the left",
            "L " + F(f.Left[5]) + " dB, R " + F(f.Right[5]) + " dB");
      f = Analyze(Sine(250, 0.5, N * 3), Sine(4000, 0.5, N * 3));
      Check(f.Left[3] > -8 && f.Left[7] < -40 && f.Right[7] > -8 && f.Right[3] < -40,
            "250 Hz left / 4 kHz right are separated",
            "L250 " + F(f.Left[3]) + " L4k " + F(f.Left[7]) + " R4k " + F(f.Right[7]) + " R250 " + F(f.Right[3]));
    }

    static void SilenceIsFloorAndFlagged() {
      LevelStream stream = new LevelStream(Rate, N, 25);
      Frame f = stream.PushSilence(Rate);
      bool floor = true;
      for (int i = 0; i < f.Left.Length; i++) floor &= f.Left[i] <= Bands.FloorDb && f.Right[i] <= Bands.FloorDb;
      Check(f.Silent && floor, "silence gives floor levels and the silent flag", null);
    }

    static void SilenceAfterMusicFallsToFloor() {
      LevelStream stream = new LevelStream(Rate, N, 25);
      float[] s = Sine(1000, 1.0, N * 2);
      stream.Push(s, s, s.Length);
      Frame f = stream.PushSilence(N + stream.Hop);
      Check(f.Silent, "levels fall to the floor once the window holds only silence", null);
    }

    static void DiscontinuityClearsTheWindow() {
      LevelStream stream = new LevelStream(Rate, N, 25);
      float[] s = Sine(1000, 1.0, N * 2);
      stream.Push(s, s, s.Length);
      stream.Discontinuity();
      Frame f = stream.PushSilence(stream.Hop);  // only one hop of new (silent) data
      Check(f.Silent && stream.Discontinuities == 1,
            "discontinuity clears stale samples instead of mixing them", "count " + stream.Discontinuities);
    }

    static void FrameRateFollowsHop() {
      LevelStream stream = new LevelStream(Rate, N, 25);
      stream.PushSilence(Rate);
      Check(stream.Hop == 1764 && stream.Frames == 25, "25 frames per second of audio",
            "hop " + stream.Hop + ", frames " + stream.Frames);
    }

    static void MonoDuplicatesLeft() {
      LevelStream stream = new LevelStream(Rate, N, 25);
      float[] s = Sine(500, 1.0, N * 2);
      Frame f = stream.Push(s, null, s.Length);
      Check(Math.Abs(f.Left[4] - f.Right[4]) < 1e-9, "mono source feeds both channels", null);
    }

    static void JsonIsCultureInvariant() {
      CultureInfo previous = Thread.CurrentThread.CurrentCulture;
      Thread.CurrentThread.CurrentCulture = new CultureInfo("fr-FR");
      try {
        Frame f = new Frame();
        for (int i = 0; i < 10; i++) { f.Left[i] = -12.34; f.Right[i] = -0.05; }
        string json = Json.Levels(7, f, 44100);
        Check(json.Contains("-12.3") && !json.Contains("-12,3") && json.Contains("\"seq\":7"),
              "levels JSON uses '.' even with a French locale", json.Substring(0, 60));
        Check(Json.Escape("a\"b\\c") == "\"a\\\"b\\\\c\"", "JSON string escaping", null);
      } finally {
        Thread.CurrentThread.CurrentCulture = previous;
      }
    }

    static void ControlMessageParsing() {
      Check(Json.ParseCapture("{\"type\":\"control\",\"capture\":true}") == true &&
            Json.ParseCapture("{\"type\": \"control\", \"capture\": false}") == false &&
            Json.ParseCapture("{\"type\":\"other\",\"capture\":true}") == null &&
            Json.ParseCapture("garbage") == null,
            "control message parsing", null);
    }

    public static int Run() {
      FftMatchesNaiveDft();
      FftRejectsNonPowerOfTwo();
      EachBandPeaksAtItsCentre();
      LevelIsCalibrated();
      LeftRightAreIndependent();
      SilenceIsFloorAndFlagged();
      SilenceAfterMusicFallsToFloor();
      DiscontinuityClearsTheWindow();
      FrameRateFollowsHop();
      MonoDuplicatesLeft();
      JsonIsCultureInvariant();
      ControlMessageParsing();
      Console.WriteLine(passed + " passed, " + failures + " failed");
      return failures == 0 ? 0 : 1;
    }
  }
}
