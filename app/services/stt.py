"""Speech-to-text via Groq's hosted Whisper (free tier).

Whisper handles both English and Kiswahili and detects the language itself.
"""
from openai import OpenAI

from config import GROQ_API_KEY, STT_MODEL
from services.audio import pcm_to_wav

_client = (
    OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")
    if GROQ_API_KEY
    else None
)


def is_configured() -> bool:
    return _client is not None


def transcribe(pcm: bytes) -> str:
    """Transcribes 16 kHz mono 16-bit PCM. Blocking; run it in a thread."""
    if _client is None:
        raise RuntimeError("GROQ_API_KEY is not set")
    result = _client.audio.transcriptions.create(
        model=STT_MODEL,
        file=("speech.wav", pcm_to_wav(pcm), "audio/wav"),
        # Nudges Whisper towards the spellings Buddy's students use.
        prompt="A Kenyan Grade 7 student asks Buddy about Science, Kiswahili or AI.",
    )
    return (result.text or "").strip()
