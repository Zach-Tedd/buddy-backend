import os
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Missing SUPABASE_URL or SUPABASE_KEY. Check your .env file.")

if not DEEPSEEK_API_KEY:
    raise ValueError("Missing DEEPSEEK_API_KEY. Check your .env file.")

if not ADMIN_API_KEY:
    raise ValueError("Missing ADMIN_API_KEY. Check your .env file.")

# --- Buddy firmware (voice devices) ---------------------------------------
# These are optional so the text API keeps working without them. The voice
# endpoints refuse to serve devices until they are set.

# Signs the session tokens handed to Buddy firmware devices. Any long random
# string, e.g. `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
DEVICE_TOKEN_SECRET = os.getenv("DEVICE_TOKEN_SECRET")

# Speech-to-text runs on Groq's hosted Whisper (free tier, OpenAI-compatible).
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
STT_MODEL = os.getenv("STT_MODEL", "whisper-large-v3-turbo")

# Text-to-speech uses Microsoft Edge's free online voices (no key needed).
TTS_VOICE_EN = os.getenv("TTS_VOICE_EN", "en-KE-AsiliaNeural")
TTS_VOICE_SW = os.getenv("TTS_VOICE_SW", "sw-KE-ZuriNeural")

# Subject a voice session starts in. Students can switch by voice.
DEFAULT_DEVICE_SUBJECT = os.getenv("DEFAULT_DEVICE_SUBJECT", "science")

# Contact details (an email address or URL) sent in the User-Agent when
# fetching pictures from Wikipedia, which blocks anonymous API clients.
WIKIMEDIA_CONTACT = os.getenv("WIKIMEDIA_CONTACT")

# Optional public base URL (e.g. https://buddy.up.railway.app). When unset,
# the WebSocket URL handed to devices is derived from the incoming request.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL")
