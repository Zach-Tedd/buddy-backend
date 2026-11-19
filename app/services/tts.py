"""Text-to-speech via Microsoft Edge's free online voices (edge-tts).

No API key or account is needed. Kenyan voices exist for both English and
Kiswahili. This uses an unofficial endpoint, so it can change without notice.
"""
import re

import edge_tts

from config import TTS_VOICE_EN, TTS_VOICE_SW
from services.audio import mp3_to_pcm

_MARKDOWN = re.compile(r"[*_#`>]+")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def clean_for_speech(text: str) -> str:
    """Strips Markdown so it is not read aloud."""
    text = _MARKDOWN.sub("", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.MULTILINE)
    return re.sub(r"\s+", " ", text).strip()


def split_sentences(text: str, min_chars: int = 40) -> list[str]:
    """Splits text into sentences, merging short ones, so the first audio can
    start playing while later sentences are still being synthesised."""
    chunks: list[str] = []
    for part in _SENTENCE_END.split(text):
        part = part.strip()
        if not part:
            continue
        if chunks and len(chunks[-1]) < min_chars:
            chunks[-1] = f"{chunks[-1]} {part}"
        else:
            chunks.append(part)
    return chunks


def voice_for(language: str) -> str:
    return TTS_VOICE_SW if language == "sw" else TTS_VOICE_EN


async def synthesize(text: str, language: str = "en") -> bytes:
    """Returns 16 kHz mono 16-bit PCM for `text`."""
    mp3 = bytearray()
    async for chunk in edge_tts.Communicate(text, voice_for(language)).stream():
        if chunk["type"] == "audio":
            mp3 += chunk["data"]
    if not mp3:
        return b""
    return mp3_to_pcm(bytes(mp3))
