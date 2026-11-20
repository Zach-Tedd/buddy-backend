"""Pictures and diagrams for Buddy's screen.

Pipeline: spot a picture request -> turn it into a safe English search term
(DeepSeek) -> take the lead image of the matching Wikipedia article (free
licences only) -> fit it to the device's picture box as PNG -> serve it from
a short-lived URL the device downloads.
"""
import io
import json
import re
import secrets
import time

import httpx
from PIL import Image

from config import WIKIMEDIA_CONTACT
from services.ai import client as llm_client
from services.tutor import AI_MODEL

# Picture box on the UNIHIKER K10 (240x320 screen; the firmware reserves
# 46% of the height for pictures). Images are sent at exactly this size so
# the device never has to scale them.
BOX_WIDTH = 240
BOX_HEIGHT = 147

IMAGE_TTL_SECONDS = 600
_images: dict[str, tuple[bytes, float]] = {}

_REQUEST_WORDS = re.compile(
    r"\b(picture|pictures|pic|image|images|photo|photos|diagram|diagrams|drawing|"
    r"illustration|show me|look like|looks like|"
    r"picha|mchoro|michoro|taswira|nionyeshe|onyesha|nionyeshe)\b",
    re.IGNORECASE,
)

_WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
# Wikimedia rejects API clients whose User-Agent has no contact details
# (https://meta.wikimedia.org/wiki/User-Agent_policy).
_HTTP_HEADERS = {"User-Agent": f"BuddyTutor/1.0 ({WIKIMEDIA_CONTACT})"}


def is_configured() -> bool:
    return bool(WIKIMEDIA_CONTACT)

_EXTRACT_PROMPT = """A Grade 7 student in Kenya (age about 13) said something to their learning device.
Decide whether they are asking to SEE a picture, photo or diagram, and of what.

Reply with JSON only: {"show": true|false, "query": "<search term>", "safe": true|false}

- "query": a short English Wikipedia article title for the thing to show
  (translate from Kiswahili if needed), e.g. "Human heart", "Magnet", "Bunsen burner".
- "safe": false if the picture could be unsuitable for a 13-year-old: nudity,
  genitals or reproductive organs, sexual content, injuries or gore, violence,
  weapons, drugs, or frightening content. Otherwise true.
"""


def looks_like_picture_request(text: str) -> bool:
    """Cheap keyword check so most questions skip the extra model call."""
    return bool(_REQUEST_WORDS.search(text))


def extract_picture_query(text: str) -> dict | None:
    """Returns {"query": ...} for a safe picture request, {"blocked": True}
    for an unsafe one, or None if no picture was asked for. Blocking."""
    response = llm_client.chat.completions.create(
        model=AI_MODEL,
        messages=[
            {"role": "system", "content": _EXTRACT_PROMPT},
            {"role": "user", "content": text},
        ],
        response_format={"type": "json_object"},
        max_tokens=60,
    )
    try:
        data = json.loads(response.choices[0].message.content or "{}")
    except ValueError:
        return None
    if not data.get("show") or not str(data.get("query", "")).strip():
        return None
    if not data.get("safe", False):
        return {"blocked": True}
    return {"query": str(data["query"]).strip()}


def _find_wikipedia_thumbnail(query: str) -> tuple[str, str] | None:
    """Returns (article title, thumbnail URL) for the best-matching article
    that has a freely licensed lead image."""
    params = {
        "action": "query",
        "format": "json",
        "formatversion": "2",
        "generator": "search",
        "gsrsearch": query,
        "gsrlimit": "5",
        "prop": "pageimages",
        "piprop": "thumbnail",
        "pithumbsize": "480",
        "pilicense": "free",
    }
    response = httpx.get(_WIKIPEDIA_API, params=params, headers=_HTTP_HEADERS, timeout=8)
    response.raise_for_status()
    pages = response.json().get("query", {}).get("pages", [])
    for page in sorted(pages, key=lambda p: p.get("index", 99)):
        thumb = page.get("thumbnail", {}).get("source")
        if thumb:
            return page["title"], thumb
    return None


def _fit_to_box(raw: bytes) -> bytes:
    """Scales the image to fit the picture box, on a white background (many
    diagrams are transparent), and encodes it as PNG — the device has a PNG
    decoder but no JPEG decoder."""
    with Image.open(io.BytesIO(raw)) as img:
        img = img.convert("RGBA")
        img.thumbnail((BOX_WIDTH, BOX_HEIGHT), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", img.size, (255, 255, 255))
        canvas.paste(img, mask=img.getchannel("A"))
        out = io.BytesIO()
        canvas.save(out, format="PNG", optimize=True)
        return out.getvalue()


def fetch_picture(query: str) -> tuple[str, str] | None:
    """Finds and prepares a picture. Returns (title, image id) or None.
    Blocking; run it in a thread."""
    if not is_configured():
        print("Buddy pictures: WIKIMEDIA_CONTACT is not set; Wikipedia refuses anonymous clients")
        return None
    found = _find_wikipedia_thumbnail(query)
    if not found:
        return None
    title, url = found
    response = httpx.get(url, headers=_HTTP_HEADERS, timeout=8, follow_redirects=True)
    response.raise_for_status()
    png = _fit_to_box(response.content)
    return title, store(png)


def store(png: bytes) -> str:
    now = time.time()
    for key in [k for k, (_, exp) in _images.items() if exp < now]:
        del _images[key]
    image_id = secrets.token_urlsafe(16)
    _images[image_id] = (png, now + IMAGE_TTL_SECONDS)
    return image_id


def get(image_id: str) -> bytes | None:
    entry = _images.get(image_id)
    if not entry or entry[1] < time.time():
        return None
    return entry[0]
