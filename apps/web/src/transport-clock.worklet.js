// The audio thread wakes PCM delivery. Window timers may be throttled when a
// studio window is in the background; they must not be the playback clock.
class TransportClock extends AudioWorkletProcessor {
  frames = 0;
  process(_inputs, outputs) {
    this.frames += outputs[0]?.[0]?.length ?? 128;
    if (this.frames >= 1024) {
      this.frames = 0;
      this.port.postMessage(null);
    }
    return true;
  }
}
registerProcessor('transport-clock', TransportClock);
