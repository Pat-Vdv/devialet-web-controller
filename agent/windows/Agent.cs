// Devialet spectrum agent: WASAPI loopback of one output -> 2 x 10 band levels
// -> local WebSocket to the FastAPI backend. Audio samples never leave this
// process; nothing is written to disk; the output signal is not modified.
//
//   DevialetSpectrumAgent.exe [--device "My Phantom System"] [--server ws://localhost:8000/api/spectrum/agent]
//                             [--fps 25] [--selftest]
// Default --device: environment variable DEVIALET_AGENT_DEVICE, else "My Phantom System"
// (part of the Windows output name, e.g. the Bluetooth name of the stereo pair).
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace DevialetSpectrum {
  public sealed class Options {
    public string Device = Environment.GetEnvironmentVariable("DEVIALET_AGENT_DEVICE") ?? "My Phantom System";
    public string Server = "ws://localhost:8000/api/spectrum/agent";
    public int Fps = 25;
    public bool SelfTest;

    public static Options Parse(string[] args) {
      Options o = new Options();
      for (int i = 0; i < args.Length; i++) {
        string a = args[i];
        if (a == "--selftest") o.SelfTest = true;
        else if (a == "--device" && i + 1 < args.Length) o.Device = args[++i];
        else if (a == "--server" && i + 1 < args.Length) o.Server = args[++i];
        else if (a == "--fps" && i + 1 < args.Length) o.Fps = Math.Max(5, Math.Min(30, int.Parse(args[++i])));
        else throw new ArgumentException("unknown argument: " + a);
      }
      return o;
    }
  }

  // Latest-value outbox: status messages are queued (bounded), level frames
  // overwrite each other so a slow link never builds a backlog.
  public sealed class Outbox {
    readonly object gate = new object();
    readonly Queue<string> statuses = new Queue<string>();
    readonly AutoResetEvent signal = new AutoResetEvent(false);
    string levels;

    public void PutStatus(string json) {
      lock (gate) {
        if (statuses.Count >= 16) statuses.Dequeue();
        statuses.Enqueue(json);
      }
      signal.Set();
    }

    public void PutLevels(string json) {
      lock (gate) levels = json;
      signal.Set();
    }

    public void Clear() {
      lock (gate) { statuses.Clear(); levels = null; }
    }

    public string Take(int timeoutMs) {
      for (int attempt = 0; attempt < 2; attempt++) {
        lock (gate) {
          if (statuses.Count > 0) return statuses.Dequeue();
          if (levels != null) { string l = levels; levels = null; return l; }
        }
        if (attempt == 0 && !signal.WaitOne(timeoutMs)) return null;
      }
      return null;
    }

    public void Wake() { signal.Set(); }
  }

  public sealed class Agent {
    const int FftSize = 4096;            // ~93 ms at 44.1 kHz: enough resolution for the 31 Hz band
    const int SilentIntervalMs = 500;    // 2 messages/s while silent
    const int HeartbeatMs = 2000;

    readonly Options options;
    readonly Outbox outbox = new Outbox();
    readonly ManualResetEvent stopEvent = new ManualResetEvent(false);
    volatile bool stopping, connected, wantCapture;
    readonly object statusGate = new object();
    string state = "idle", device, message = "", format = "";
    bool isDefault;
    long discontinuities;

    public Agent(Options options) { this.options = options; }

    public static void Log(string text) {
      Console.WriteLine(DateTime.Now.ToString("HH:mm:ss") + " " + text);
    }

    public void Stop() {
      stopping = true;
      stopEvent.Set();
      outbox.Wake();
    }

    string StatusJson() {
      lock (statusGate) return Json.Status(state, device, isDefault, message, discontinuities, format);
    }

    void SetStatus(string newState, string newMessage) {
      bool changed;
      lock (statusGate) {
        changed = newState != state || newMessage != message;
        state = newState;
        message = newMessage ?? "";
      }
      if (changed) Log("status: " + newState + (string.IsNullOrEmpty(newMessage) ? "" : " - " + newMessage));
      outbox.PutStatus(StatusJson());
    }

    public void Run() {
      Thread captureThread = new Thread(CaptureLoop);
      captureThread.IsBackground = true;
      captureThread.SetApartmentState(ApartmentState.MTA);
      captureThread.Start();
      int backoffMs = 1000;
      while (!stopping) {
        try {
          ConnectAndServe().Wait();
          backoffMs = 1000;
        } catch (AggregateException e) {
          Log("backend: " + e.InnerException.Message);
        } catch (Exception e) {
          Log("backend: " + e.Message);
        }
        connected = false;
        wantCapture = false;
        if (stopping) break;
        Log("reconnecting in " + backoffMs / 1000.0 + " s");
        stopEvent.WaitOne(backoffMs);
        backoffMs = Math.Min(backoffMs * 2, 5000);
      }
      captureThread.Join(3000);
      Log("stopped");
    }

    async Task ConnectAndServe() {
      using (ClientWebSocket ws = new ClientWebSocket()) {
        ws.Options.KeepAliveInterval = TimeSpan.FromSeconds(10);
        using (CancellationTokenSource timeout = new CancellationTokenSource(5000)) {
          await ws.ConnectAsync(new Uri(options.Server), timeout.Token);
        }
        Log("connected to " + options.Server);
        outbox.Clear();
        await Send(ws, Json.Hello(Program.Version));
        await Send(ws, StatusJson());
        connected = true;
        Task receive = ReceiveLoop(ws);
        Task send = SendLoop(ws);
        await Task.WhenAny(receive, send);
        connected = false;
        wantCapture = false;
        outbox.Wake();
        if (ws.State == WebSocketState.Open) {
          try {
            using (CancellationTokenSource t = new CancellationTokenSource(2000))
              await ws.CloseAsync(WebSocketCloseStatus.NormalClosure, "agent stopping", t.Token);
          } catch (Exception) { }
        }
        await Task.WhenAll(IgnoreErrors(receive), IgnoreErrors(send));
        if (!stopping) throw new Exception("connection closed");
      }
    }

    static async Task IgnoreErrors(Task t) {
      try { await t; } catch (Exception) { }
    }

    static Task Send(ClientWebSocket ws, string json) {
      byte[] bytes = Encoding.UTF8.GetBytes(json);
      return ws.SendAsync(new ArraySegment<byte>(bytes), WebSocketMessageType.Text, true, CancellationToken.None);
    }

    async Task SendLoop(ClientWebSocket ws) {
      Stopwatch sinceLast = Stopwatch.StartNew();
      while (!stopping && connected && ws.State == WebSocketState.Open) {
        string json = await Task.Run(() => outbox.Take(250));
        if (json == null) {
          if (sinceLast.ElapsedMilliseconds < HeartbeatMs) continue;
          json = StatusJson();  // heartbeat: lets the backend detect a frozen agent
        }
        await Send(ws, json);
        sinceLast.Restart();
      }
    }

    async Task ReceiveLoop(ClientWebSocket ws) {
      byte[] buffer = new byte[4096];
      StringBuilder text = new StringBuilder();
      while (!stopping && ws.State == WebSocketState.Open) {
        WebSocketReceiveResult r = await ws.ReceiveAsync(new ArraySegment<byte>(buffer), CancellationToken.None);
        if (r.MessageType == WebSocketMessageType.Close) break;
        text.Append(Encoding.UTF8.GetString(buffer, 0, r.Count));
        if (!r.EndOfMessage) continue;
        bool? capture = Json.ParseCapture(text.ToString());
        text.Clear();
        if (capture.HasValue && capture.Value != wantCapture) {
          wantCapture = capture.Value;
          Log("backend requests capture " + (wantCapture ? "ON" : "OFF"));
        }
      }
    }

    void CaptureLoop() {
      while (!stopping) {
        if (!(connected && wantCapture)) {
          if (state != "idle" && state != "no_device" && state != "error") SetStatus("idle", "");
          stopEvent.WaitOne(100);
          continue;
        }
        LoopbackCapture capture = null;
        try {
          capture = new LoopbackCapture(options.Device);
          RunCapture(capture);
        } catch (Exception e) {
          SetStatus(LoopbackCapture.IsDeviceGone(e) ? "no_device" : "error", e.Message);
          stopEvent.WaitOne(2000);
        } finally {
          if (capture != null) capture.Dispose();
        }
      }
    }

    void RunCapture(LoopbackCapture capture) {
      int rate = capture.SampleRate;
      LevelStream stream = new LevelStream(rate, FftSize, options.Fps);
      float[] left = new float[rate], right = new float[rate];
      lock (statusGate) {
        device = capture.DeviceName;
        format = capture.FormatDescription + (capture.Channels < 2 ? " (mono: L = R)" : "");
        isDefault = capture.DeviceName == LoopbackCapture.DefaultOutputName();
      }
      SetStatus("capturing", "");
      Stopwatch clock = Stopwatch.StartNew();
      long lastAudio = 0, padCursor = 0, lastSilentSent = -SilentIntervalMs, lastDefaultCheck = 0, seq = 0;
      bool first = true;
      while (!stopping && connected && wantCapture) {
        Thread.Sleep(10);
        Frame frame = null;
        int got = capture.Drain(left, right, delegate(int frames, uint flags) {
          // The first packet after Start() is routinely flagged; later flags mean lost samples.
          if ((flags & LoopbackCapture.FLAG_DISCONTINUITY) != 0 && !first) {
            stream.Discontinuity();
            lock (statusGate) discontinuities = stream.Discontinuities;
          }
          first = false;
          Frame f = stream.Push(left, right, frames);
          if (f != null) frame = f;
        });
        long now = clock.ElapsedMilliseconds;
        if (got > 0) {
          lastAudio = padCursor = now;
        } else if (now - lastAudio > 100) {
          // Loopback delivers no packet while nothing plays: feed zeros so levels fall.
          Frame f = stream.PushSilence((int)((now - padCursor) * rate / 1000));
          padCursor = now;
          if (f != null) frame = f;
        }
        if (frame != null && (!frame.Silent || now - lastSilentSent >= SilentIntervalMs)) {
          if (frame.Silent) lastSilentSent = now;
          outbox.PutLevels(Json.Levels(++seq, frame, rate));
        }
        if (now - lastDefaultCheck > 5000) {
          lastDefaultCheck = now;
          bool nowDefault = capture.DeviceName == LoopbackCapture.DefaultOutputName();
          bool changed;
          lock (statusGate) { changed = nowDefault != isDefault; isDefault = nowDefault; }
          if (changed) SetStatus("capturing", nowDefault ? "" : "not the default Windows output");
        }
      }
    }
  }

  public static class Program {
    public const string Version = "2.3.0";

    public static int Main(string[] args) {
      Options options;
      try {
        options = Options.Parse(args);
      } catch (Exception e) {
        Console.Error.WriteLine(e.Message);
        return 64;
      }
      if (options.SelfTest) return SelfTest.Run();
      bool created;
      using (Mutex single = new Mutex(true, @"Local\DevialetSpectrumAgent", out created)) {
        if (!created) {
          Agent.Log("another agent is already running");
          return 2;
        }
        Agent agent = new Agent(options);
        Console.CancelKeyPress += delegate(object sender, ConsoleCancelEventArgs e) {
          e.Cancel = true;
          Agent.Log("stopping...");
          agent.Stop();
        };
        Agent.Log("Devialet spectrum agent " + Version + " - device '" + options.Device + "', " +
                  options.Fps + " frames/s, server " + options.Server);
        agent.Run();
      }
      return 0;
    }
  }
}
