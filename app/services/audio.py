"""Audio helpers for Buddy firmware devices.

Devices stream Opus (16 kHz, mono, 60 ms frames) and play Opus back. PyAV
bundles FFmpeg with libopus and an MP3 decoder, so no system packages are
needed on the server.
"""
import io
import math
import struct
import wave
from array import array

import av

SAMPLE_RATE = 16000
FRAME_MS = 60
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 960
FRAME_BYTES = FRAME_SAMPLES * 2                 # 16-bit mono


def _pcm_bytes(frame) -> bytes:
    # Planes can carry alignment padding past the real samples.
    return bytes(frame.planes[0])[: frame.samples * 2]


class OpusDecoder:
    """Decodes a device's uplink Opus packets to 16 kHz mono 16-bit PCM."""

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self._dec = av.CodecContext.create("opus", "r")
        self._dec.sample_rate = sample_rate
        self._dec.layout = "mono"
        self._rs = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)

    def decode(self, packet: bytes) -> bytes:
        out = bytearray()
        try:
            for frame in self._dec.decode(av.Packet(packet)):
                for r in self._rs.resample(frame):
                    out += _pcm_bytes(r)
        except av.error.InvalidDataError:
            pass  # a corrupt packet is dropped, not fatal
        return bytes(out)


def encode_opus(pcm: bytes) -> list[bytes]:
    """Encodes 16 kHz mono 16-bit PCM to a list of 60 ms Opus packets."""
    enc = av.CodecContext.create("libopus", "w")
    enc.sample_rate = SAMPLE_RATE
    enc.layout = "mono"
    enc.format = "s16"
    enc.bit_rate = 24000
    enc.options = {"frame_duration": str(FRAME_MS), "application": "voip"}
    enc.open()

    # Pad to a whole number of frames so the tail is not cut off.
    if len(pcm) % FRAME_BYTES:
        pcm += b"\x00" * (FRAME_BYTES - len(pcm) % FRAME_BYTES)

    packets: list[bytes] = []
    for i in range(0, len(pcm), FRAME_BYTES):
        frame = av.AudioFrame(format="s16", layout="mono", samples=FRAME_SAMPLES)
        frame.planes[0].update(pcm[i : i + FRAME_BYTES])
        frame.sample_rate = SAMPLE_RATE
        packets += [bytes(p) for p in enc.encode(frame)]
    packets += [bytes(p) for p in enc.encode(None)]
    return packets


def mp3_to_pcm(mp3: bytes) -> bytes:
    """Decodes MP3 (what the TTS returns) to 16 kHz mono 16-bit PCM."""
    out = bytearray()
    rs = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    with av.open(io.BytesIO(mp3)) as container:
        for frame in container.decode(audio=0):
            for r in rs.resample(frame):
                out += _pcm_bytes(r)
    for r in rs.resample(None):
        out += _pcm_bytes(r)
    return bytes(out)


def pcm_to_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return buf.getvalue()


def rms(pcm: bytes) -> float:
    """Root-mean-square loudness of 16-bit PCM, 0..32768."""
    if len(pcm) < 2:
        return 0.0
    samples = array("h", pcm[: len(pcm) - len(pcm) % 2])
    return math.sqrt(sum(s * s for s in samples) / len(samples))


class EndOfSpeechDetector:
    """Energy-based voice activity detection for "auto" listening mode.

    In auto mode the device keeps streaming audio and relies on the server
    to decide when the student has finished speaking.
    """

    def __init__(
        self,
        threshold: float = 500.0,     # RMS above this counts as voice
        min_speech_ms: int = 300,     # ignore clicks and short bumps
        end_silence_ms: int = 900,    # this much quiet after speech ends a turn
        max_utterance_ms: int = 20000,
    ):
        self.threshold = threshold
        self.min_speech_ms = min_speech_ms
        self.end_silence_ms = end_silence_ms
        self.max_utterance_ms = max_utterance_ms
        self.reset()

    def reset(self):
        self.speech_ms = 0
        self.silence_ms = 0
        self.total_ms = 0

    @property
    def heard_speech(self) -> bool:
        return self.speech_ms >= self.min_speech_ms

    def feed(self, pcm: bytes) -> bool:
        """Feeds one decoded frame; returns True when the turn has ended."""
        ms = len(pcm) // 2 * 1000 // SAMPLE_RATE
        self.total_ms += ms
        if rms(pcm) >= self.threshold:
            self.speech_ms += ms
            self.silence_ms = 0
        else:
            self.silence_ms += ms
        if self.heard_speech and self.silence_ms >= self.end_silence_ms:
            return True
        return self.heard_speech and self.total_ms >= self.max_utterance_ms
