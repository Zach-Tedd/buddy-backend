"""Endpoints for Buddy firmware running on UNIHIKER K10 devices.

Flow:
1. On boot the device POSTs to /buddy/ota/ with its MAC in `Device-Id`.
   If the MAC is registered in `devices.device_id`, we reply with the
   WebSocket URL and a signed session token.
2. The device opens /buddy/ws with `Authorization: Bearer <token>`, sends
   a "hello", then streams Opus audio while the student speaks.
3. Per turn: speech-to-text -> Buddy tutor (DeepSeek) -> text-to-speech,
   streamed back as Opus frames between "tts start" and "tts stop".

The wire protocol matches the upstream open-source firmware this is built
on, with audio framing version 1 (raw Opus frames in binary messages).
"""
import asyncio
import json
import re
import time
import uuid

from fastapi import APIRouter, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from auth import (
    get_device_by_id,
    get_device_by_mac,
    issue_device_token,
    normalize_mac,
    verify_device_token,
)
from config import DEFAULT_DEVICE_SUBJECT, DEVICE_TOKEN_SECRET, PUBLIC_BASE_URL
from database import supabase
from services import images, stt, tts
from services.ai import SUBJECT_PROMPTS, ask_ai
from services.audio import (
    FRAME_MS,
    SAMPLE_RATE,
    EndOfSpeechDetector,
    OpusDecoder,
    encode_opus,
)
from services.content_loader import detect_language
from services.tutor import extract_strand, log_interaction

router = APIRouter(tags=["buddy-firmware"])

EAT_UTC_OFFSET_MINUTES = 180     # Kenya, UTC+3
FRAME_S = FRAME_MS / 1000
PLAYBACK_LEAD_S = 0.3            # how far ahead of the device's playback to send
NO_SPEECH_CLOSE_MS = 30_000      # end the session after this long without speech
IDLE_RECEIVE_TIMEOUT_S = 120     # nothing at all received for this long -> close
PRE_ROLL_BYTES = SAMPLE_RATE * 2 // 2  # keep 0.5 s of audio before speech starts
MAX_UTTERANCE_BYTES = SAMPLE_RATE * 2 * 30
MIN_MANUAL_UTTERANCE_BYTES = SAMPLE_RATE * 2 * 3 // 10  # 0.3 s

SORRY = {
    "en": "Sorry, I had a problem answering that. Please try again.",
    "sw": "Samahani, nimepata tatizo. Tafadhali jaribu tena.",
}


# --- OTA / configuration ---------------------------------------------------

def _http_base_url(conn: Request | WebSocket) -> str:
    """Public http(s) base URL of this server, as the device sees it."""
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL.rstrip("/")
    proto = conn.headers.get("x-forwarded-proto") or conn.url.scheme
    proto = {"ws": "http", "wss": "https"}.get(proto, proto)
    host = conn.headers.get("x-forwarded-host") or conn.headers.get("host")
    return f"{proto}://{host}"


def _websocket_url(request: Request) -> str:
    base = re.sub(r"^http", "ws", _http_base_url(request))  # http->ws, https->wss
    return f"{base}/buddy/ws"


@router.get("/buddy/img/{image_id}.png", include_in_schema=False)
def buddy_image(image_id: str):
    """Pictures for the device screen. IDs are random and expire after 10 min."""
    png = images.get(image_id)
    if png is None:
        raise HTTPException(status_code=404)
    # The firmware needs Content-Length (no chunked responses); Response sets it.
    return Response(content=png, media_type="image/png")


@router.get("/buddy/ota/")
@router.get("/buddy/ota", include_in_schema=False)
def buddy_ota_status():
    return {"status": "ok", "service": "Buddy firmware OTA"}


@router.post("/buddy/ota/")
@router.post("/buddy/ota", include_in_schema=False)
async def buddy_ota(request: Request):
    """Called by Buddy firmware on every boot to get its server settings."""
    mac = request.headers.get("device-id")
    if not mac:
        return JSONResponse({"error": "Missing Device-Id header"}, status_code=400)
    if not DEVICE_TOKEN_SECRET:
        return JSONResponse({"error": "Voice devices are not configured"}, status_code=503)

    try:
        body = await request.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}

    device = await asyncio.to_thread(get_device_by_mac, mac)
    if not device:
        # Printed so an admin can find the MAC in the logs and register it.
        print(f"Buddy firmware: unregistered device {normalize_mac(mac)} requested OTA")
        return JSONResponse({"error": "Device not registered"}, status_code=403)
    if not device["is_active"]:
        return JSONResponse({"error": "Device disabled"}, status_code=403)

    application = body.get("application") or {}
    current_version = application.get("version") or "0.0.0"

    await asyncio.to_thread(
        lambda: supabase.table("devices")
        .update({"last_seen_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        .eq("id", device["id"])
        .execute()
    )

    # No "mqtt" key: its presence would switch the firmware to MQTT.
    return {
        "server_time": {
            "timestamp": int(time.time() * 1000),
            "timezone_offset": EAT_UTC_OFFSET_MINUTES,
        },
        # Echo the running version with an empty URL: "no update available".
        "firmware": {"version": current_version, "url": ""},
        "websocket": {
            "url": _websocket_url(request),
            "token": issue_device_token(device),
        },
    }


# --- Voice session ---------------------------------------------------------

_SWITCH_WORDS = (
    "switch", "change", "let's", "lets", "learn", "study", "do", "start",
    "badilisha", "tusome", "tujifunze", "nataka", "somo",
)
_SUBJECT_WORDS = {
    "ai_literacy": ("ai literacy", "artificial intelligence", "akili bandia", "akili mnemba", "about ai"),
    "kiswahili": ("kiswahili", "swahili"),
    "science": ("science", "sayansi"),
}
_SWITCH_CONFIRM = {
    "science": ("Okay, let's do Science! What would you like to know?", "en"),
    "kiswahili": ("Sawa, tusome Kiswahili! Ungependa kujua nini?", "sw"),
    "ai_literacy": ("Great, let's learn about AI! What would you like to know?", "en"),
}


def detect_subject_switch(text: str) -> str | None:
    """Recognises short requests like "let's do Kiswahili" or "tusome sayansi"."""
    lowered = text.lower()
    words = re.findall(r"[\w']+", lowered)
    if len(words) > 10 or not any(w in _SWITCH_WORDS for w in words):
        return None
    for subject, phrases in _SUBJECT_WORDS.items():
        if any(p in lowered for p in phrases):
            return subject
    return None


class VoiceSession:
    def __init__(self, ws: WebSocket, device: dict):
        self.ws = ws
        self.device = device
        self.session_id = uuid.uuid4().hex
        self.subject = DEFAULT_DEVICE_SUBJECT if DEFAULT_DEVICE_SUBJECT in SUBJECT_PROMPTS else "science"
        self.decoder = OpusDecoder()
        self.vad = EndOfSpeechDetector()
        self.pcm = bytearray()
        self.listening = False
        self.mode = "auto"
        self.turn: asyncio.Task | None = None
        self.background: set[asyncio.Task] = set()
        self.send_lock = asyncio.Lock()
        self.play_end: float | None = None
        self.closing = False
        self.http_base = _http_base_url(ws)
        self.mcp_id = 0

    # -- sending --

    async def send_json(self, msg: dict):
        async with self.send_lock:
            await self.ws.send_text(json.dumps(msg, ensure_ascii=False))

    async def send_audio(self, packet: bytes):
        """Sends one Opus frame, pacing so we stay just ahead of playback.
        Pacing keeps the device's buffer small, so "abort" stops speech fast."""
        now = time.monotonic()
        if self.play_end is None or self.play_end < now:
            self.play_end = now  # device's buffer has run dry
        ahead = self.play_end - now
        if ahead > PLAYBACK_LEAD_S:
            await asyncio.sleep(ahead - PLAYBACK_LEAD_S)
        async with self.send_lock:
            await self.ws.send_bytes(packet)
        self.play_end += FRAME_S

    async def speak(self, text: str, language: str):
        sentences = tts.split_sentences(tts.clean_for_speech(text))
        if not sentences:
            return
        # Synthesise every sentence at once; play them in order as they finish.
        jobs = [asyncio.create_task(tts.synthesize(s, language)) for s in sentences]
        self.play_end = None
        try:
            await self.send_json({"type": "tts", "state": "start", "session_id": self.session_id})
            for i, job in enumerate(jobs):
                pcm = await job
                packets = await asyncio.to_thread(encode_opus, pcm) if pcm else []
                # The device replaces its on-screen text with each sentence_start,
                # so send everything said so far: the screen shows the answer as
                # a paragraph that grows while Buddy speaks.
                await self.send_json({
                    "type": "tts", "state": "sentence_start",
                    "text": " ".join(sentences[: i + 1]), "session_id": self.session_id,
                })
                for packet in packets:
                    await self.send_audio(packet)
            # Let the last frames play before telling the device we're done.
            if self.play_end:
                await asyncio.sleep(max(0.0, self.play_end - time.monotonic()))
        finally:
            for job in jobs:
                job.cancel()
            try:
                await self.send_json({"type": "tts", "state": "stop", "session_id": self.session_id})
            except Exception:
                pass

    # -- receiving --

    async def run(self):
        try:
            while not self.closing:
                try:
                    msg = await asyncio.wait_for(self.ws.receive(), IDLE_RECEIVE_TIMEOUT_S)
                except asyncio.TimeoutError:
                    if self.turn and not self.turn.done():
                        continue
                    break
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("text") is not None:
                    await self.on_text(msg["text"])
                elif msg.get("bytes") is not None:
                    self.on_audio(msg["bytes"])
        except WebSocketDisconnect:
            pass
        finally:
            if self.turn:
                self.turn.cancel()
            try:
                await self.ws.close()
            except Exception:
                pass

    async def on_text(self, raw: str):
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        kind = msg.get("type")

        if kind == "hello":
            params = msg.get("audio_params") or {}
            rate = params.get("sample_rate", SAMPLE_RATE)
            if rate != SAMPLE_RATE:
                self.decoder = OpusDecoder(sample_rate=rate)
            await self.send_json({
                "type": "hello",
                "transport": "websocket",
                "session_id": self.session_id,
                "audio_params": {
                    "format": "opus",
                    "sample_rate": SAMPLE_RATE,
                    "channels": 1,
                    "frame_duration": FRAME_MS,
                },
            })

        elif kind == "listen":
            state = msg.get("state")
            if state == "start":
                self.mode = msg.get("mode", "auto")
                if self.turn and not self.turn.done():
                    self.turn.cancel()
                self.pcm.clear()
                self.vad.reset()
                self.listening = True
            elif state == "stop":
                # Push-to-talk: the student released the button.
                was_listening, self.listening = self.listening, False
                if was_listening and len(self.pcm) >= MIN_MANUAL_UTTERANCE_BYTES:
                    self.begin_turn()
            # "detect" (wake word heard) needs no reply; a "start" follows.

        elif kind == "abort":
            if self.turn and not self.turn.done():
                self.turn.cancel()

        elif kind == "mcp":
            # Replies to our tool calls (e.g. showing a picture). Only errors matter.
            payload = msg.get("payload") or {}
            if isinstance(payload, dict) and payload.get("error"):
                print(f"Buddy firmware: device tool call failed: {payload['error']}")

        # Other message types are not used.

    async def show_picture(self, image_id: str):
        """Asks the device to download and display a picture."""
        self.mcp_id += 1
        await self.send_json({
            "type": "mcp",
            "session_id": self.session_id,
            "payload": {
                "jsonrpc": "2.0",
                "id": self.mcp_id,
                "method": "tools/call",
                "params": {
                    "name": "self.screen.preview_image",
                    "arguments": {"url": f"{self.http_base}/buddy/img/{image_id}.png"},
                },
            },
        })

    async def prepare_picture(self, question: str) -> str | None:
        """Runs the picture pipeline for a picture request. Shows the picture
        if one is found and returns a note telling the tutor what happened;
        returns None if the student did not ask for a picture."""
        if not images.looks_like_picture_request(question):
            return None
        try:
            request = await asyncio.to_thread(images.extract_picture_query, question)
            if request is None:
                return None
            if request.get("blocked"):
                return (
                    "The student asked to see a picture that Buddy will not show because "
                    "it is not suitable for school. Do not describe one. Politely say so "
                    "and offer to explain the topic in words suitable for Grade 7."
                )
            found = await asyncio.to_thread(images.fetch_picture, request["query"])
        except Exception as e:
            print(f"Buddy firmware: picture lookup failed: {e!r}")
            found = None
        if not found:
            return (
                "The student asked to see a picture, but none could be found. Say so "
                "briefly, then explain in words. Do not say you can never show pictures."
            )
        title, image_id = found
        await self.show_picture(image_id)
        return (
            f"The student asked to see a picture. A picture from the Wikipedia article "
            f"'{title}' is now on the student's screen. Tell them to look at the screen "
            f"and explain what it shows. Do not say you cannot show pictures."
        )

    def on_audio(self, packet: bytes):
        if not self.listening:
            return
        pcm = self.decoder.decode(packet)
        self.pcm += pcm

        if self.mode == "manual":
            if len(self.pcm) > MAX_UTTERANCE_BYTES:
                del self.pcm[: len(self.pcm) - MAX_UTTERANCE_BYTES]
            return

        if self.vad.feed(pcm):
            self.listening = False
            self.begin_turn()
        elif not self.vad.heard_speech:
            # Keep only a little audio from before the student starts talking.
            if len(self.pcm) > PRE_ROLL_BYTES:
                del self.pcm[: len(self.pcm) - PRE_ROLL_BYTES]
            if self.vad.total_ms >= NO_SPEECH_CLOSE_MS:
                self.closing = True  # device returns to wake-word standby

    def begin_turn(self):
        pcm = bytes(self.pcm)
        self.pcm.clear()
        self.vad.reset()
        self.turn = asyncio.create_task(self.handle_turn(pcm))

    async def handle_turn(self, pcm: bytes):
        started = time.monotonic()
        language = "en"
        spoke = False
        try:
            question = await asyncio.to_thread(stt.transcribe, pcm)
            if len(question) < 2:
                return  # noise, not speech

            await self.send_json({"type": "stt", "text": question, "session_id": self.session_id})

            switch_to = detect_subject_switch(question)
            if switch_to:
                self.subject = switch_to
                reply, language = _SWITCH_CONFIRM[switch_to]
                spoke = True
                await self.speak(reply, language)
                return

            await self.send_json({"type": "llm", "emotion": "thinking", "session_id": self.session_id})
            picture_note = await self.prepare_picture(question)
            answer = await asyncio.to_thread(ask_ai, question, self.subject, picture_note)
            latency_ms = int((time.monotonic() - started) * 1000)

            if self.subject == "kiswahili" or detect_language(answer) == "sw":
                language = "sw"
            await self.send_json({"type": "llm", "emotion": "happy", "session_id": self.session_id})

            job = asyncio.create_task(asyncio.to_thread(
                log_interaction,
                device=self.device,
                student_id=None,
                question=question,
                language=detect_language(question),
                subject=self.subject,
                grade=7,
                strand_id=extract_strand(answer),
                answer=answer,
                latency_ms=latency_ms,
            ))
            self.background.add(job)
            job.add_done_callback(self.background.discard)

            spoke = True
            await self.speak(answer, language)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"Buddy firmware: turn failed for device {self.device['device_id']}: {e!r}")
            if not spoke:
                try:
                    spoke = True
                    await self.speak(SORRY[language], language)
                except Exception:
                    pass
        finally:
            # Nothing was said, so the device is still in listening state and
            # never gets a "tts stop" to restart from: keep listening ourselves.
            if not spoke and self.mode != "manual":
                self.listening = True


@router.websocket("/buddy/ws")
async def buddy_ws(ws: WebSocket):
    claims = verify_device_token(
        ws.headers.get("authorization", ""), ws.headers.get("device-id")
    )
    if claims is None:
        await ws.close(code=4401)
        return

    device = await asyncio.to_thread(get_device_by_id, claims["d"])
    if not device or not device["is_active"]:
        await ws.close(code=4403)
        return

    if not stt.is_configured():
        print("Buddy firmware: GROQ_API_KEY is not set; refusing voice session")
        await ws.close(code=1011)
        return

    await ws.accept()
    await VoiceSession(ws, device).run()
