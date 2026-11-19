# buddy-backend

Backend for **Buddy**, an AI learning companion for Kenyan CBC Junior
Secondary classrooms. Students ask questions — by voice on a Buddy device or
as text — and Buddy answers like a patient tutor: short, simple, grounded in
the curriculum, in English or Kiswahili.

It serves two kinds of clients:

- **Buddy devices** — DFRobot UNIHIKER K10 boards running
  [Buddy firmware](../buddy-firmware), used by voice.
- **Text clients** — anything that can call `POST /api/v1/ask`.

Built with FastAPI, Supabase, DeepSeek, Groq Whisper, Microsoft Edge voices
and Wikipedia. Every external service is free or free-tier except hosting.

---

## Architecture

```
                 ┌──────────────────────── buddy-backend ────────────────────────┐
 Buddy device    │                                                               │
 (UNIHIKER K10)  │  /buddy/ota/  ── device registered? ── issue session token    │
   │  voice      │                                                               │
   └──WebSocket──┼─▶ /buddy/ws                                                   │
     Opus audio  │     │ decode Opus ─▶ detect end of speech                     │
                 │     │ speech-to-text (Groq Whisper)                           │
                 │     │ picture requested? ─▶ picture pipeline ─▶ device screen │
                 │     ▼                                                         │
 Text client ────┼─▶ /api/v1/ask ──▶  tutor pipeline (DeepSeek)                  │
                 │     │                     ├─▶ detect strand ─▶ diagrams       │
                 │     │                     └─▶ log interaction (Supabase)      │
                 │     ▼                                                         │
                 │   text-to-speech (Edge, Kenyan voices) ─▶ Opus ─▶ device      │
                 └───────────────────────────────────────────────────────────────┘
```

---

## Pipelines

### 1. Tutor pipeline

Shared by voice and text ([services/ai.py](app/services/ai.py)).

1. **Prompt building (at startup).** The curriculum JSON files in
   [app/content/](app/content/) are loaded per subject (`science`,
   `kiswahili`, `ai_literacy`). Each subject gets one system prompt made of:
   the strand/sub-strand map with key concepts, a few example Q&A pairs for
   tone, and rules — identify the strand, answer in the student's language,
   simple words, one Kenyan example, under 100 words (it is read aloud), end
   with one follow-up question, stay within scope.
2. **Answer.** The question and the subject's prompt go to DeepSeek
   (`deepseek-chat`). Optionally a context note is added, e.g. that a
   picture is on the student's screen.
3. **Strand detection.** The answer is scanned for the strand it names
   (`2.1`, `Mada 1.4.1`, `AI.2`, …) to tag the interaction and, for `/ask`,
   attach confirmed diagrams from the content files.
4. **Logging.** Question, answer, language, subject, strand and latency are
   written to `interactions`.

Adding content needs no code: drop a JSON file in the subject folder and
restart.

### 2. Device connection pipeline

[routers/firmware.py](app/routers/firmware.py) implements the server side of
the protocol used by Buddy firmware.

1. **Boot.** The device POSTs to `/buddy/ota/` with its Wi-Fi MAC address.
   If the MAC is registered and active in `devices`, the backend replies with
   the WebSocket URL and a signed session token (HMAC, valid 7 days).
   Unknown MACs are logged so they can be registered.
2. **Connect.** The device opens `/buddy/ws` with the token and sends
   `hello`; the server answers with its audio format (Opus, 16 kHz, mono,
   60 ms frames).
3. **Session.** One WebSocket carries the whole conversation. It closes after
   30 s without speech and the device returns to waiting for its wake word.

### 3. Voice pipeline

One spoken question, from microphone to speaker:

1. **Listen.** After the wake word or a button press, the device streams Opus
   frames. The server decodes them to PCM. In hands-free mode it detects the
   end of the question from loudness (≈0.9 s of quiet after speech); in
   push-to-talk mode the device signals when the button is released.
2. **Transcribe.** The audio goes to Whisper on Groq
   (`whisper-large-v3-turbo`), which handles English and Kiswahili. The
   transcript is shown on the device.
3. **Route.** Short requests like *"Let's do Kiswahili"* or *"Tusome
   sayansi"* switch subject. Picture requests go through the picture
   pipeline first. Everything goes to the tutor pipeline.
4. **Speak.** The answer is cleaned of Markdown, split into sentences and
   synthesised with Edge voices (`sw-KE-ZuriNeural` for Kiswahili,
   `en-KE-AsiliaNeural` for English) in parallel. Each sentence is encoded to
   Opus and streamed in order, paced just ahead of playback so the student
   can interrupt at any time.
5. **Show the text.** With each sentence the device receives everything said
   so far, so the screen shows the answer as a paragraph that grows while
   Buddy speaks.
6. **Log** the interaction in the background.

### 4. Picture pipeline

When a student asks to see something — *"Show me a picture of a magnet"*,
*"Nionyeshe mchoro wa moyo"* ([services/images.py](app/services/images.py)):

1. **Detect.** A keyword check (picture, diagram, photo, show me, picha,
   mchoro, nionyeshe, …) decides whether to look further, so ordinary
   questions cost nothing extra.
2. **Understand.** A short DeepSeek call returns what to show as an English
   Wikipedia search term (translating from Kiswahili) and whether it is
   suitable for a 13-year-old. Unsuitable requests (nudity, reproductive
   anatomy, gore, violence, weapons, drugs) are refused and Buddy explains in
   words instead.
3. **Find.** Wikipedia search returns the lead image of the best-matching
   article, restricted to freely licensed images.
4. **Fit.** The image is scaled to the device's picture box (240×147),
   placed on white (many diagrams are transparent) and encoded as PNG — the
   firmware decodes PNG but not JPEG.
5. **Serve.** It is stored in memory under a random ID and served at
   `/buddy/img/<id>.png` for 10 minutes.
6. **Show.** The server sends the device an MCP `tools/call` for
   `self.screen.preview_image` with that URL; the device downloads and shows
   the picture for 60 s.
7. **Explain.** The tutor is told what is on screen, so Buddy says to look at
   the screen and explains the picture. If none was found it says so and
   explains in words.

---

## API

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /` | — | Service status |
| `GET /health` | — | API and database status |
| `POST /api/v1/ask` | `X-Device-ID` + `X-Device-Key` | Text question → answer, strand, diagrams |
| `POST /api/v1/students` | `X-Admin-Key` | Create a student (grades 7–9) |
| `GET /api/v1/students?org_id=` | `X-Admin-Key` | List an organisation's students |
| `POST /buddy/ota/` | Registered MAC (`Device-Id`) | Device boot: WebSocket URL + token |
| `WS /buddy/ws` | `Authorization: Bearer <token>` | Device voice session |
| `GET /buddy/img/<id>.png` | Unguessable ID | Pictures for the device screen |

Interactive docs at `/docs` when the server is running.

`POST /api/v1/ask` body:

```json
{ "question": "What is a mixture?", "subject": "science", "grade": 7, "student_id": null }
```

## Data (Supabase)

| Table | Used for |
|---|---|
| `organisations` | Schools; health check |
| `devices` | `device_id` (text clients: any ID with `device_key_hash`; Buddy devices: lowercase MAC), `org_id`, `is_active`, `last_seen_at` |
| `users` | Students (`role = student`) |
| `interactions` | Every question and answer: transcript, language, subject, grade, strand, response, latency, model |

---

## Running locally

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# create .env (see below), then:
cd app
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Run uvicorn from the `app` folder. `--host 0.0.0.0` lets Buddy devices on
the same Wi-Fi reach the server; allow Python through Windows Firewall on
private networks. Check http://localhost:8000/health.

### Environment

| Variable | Required | Notes |
|---|---|---|
| `SUPABASE_URL`, `SUPABASE_KEY` | yes | |
| `DEEPSEEK_API_KEY` | yes | Tutor model |
| `ADMIN_API_KEY` | yes | Admin endpoints |
| `DEVICE_TOKEN_SECRET` | for voice | Long random string; signs device tokens |
| `GROQ_API_KEY` | for voice | Speech-to-text; free key at console.groq.com |
| `WIKIMEDIA_CONTACT` | for pictures | Email or URL put in the User-Agent; Wikipedia blocks anonymous clients |
| `STT_MODEL` | no | Default `whisper-large-v3-turbo` |
| `TTS_VOICE_EN`, `TTS_VOICE_SW` | no | Defaults `en-KE-AsiliaNeural`, `sw-KE-ZuriNeural` |
| `DEFAULT_DEVICE_SUBJECT` | no | `science` (default), `kiswahili` or `ai_literacy` |
| `PUBLIC_BASE_URL` | no | e.g. `https://buddy.up.railway.app`; otherwise taken from the request |

## Deployment

Deployed on Railway with the [Procfile](Procfile) (Python 3.12). Audio uses
PyAV wheels, which bundle FFmpeg and Opus, and images use Pillow, so no
system packages are needed. Railway supports WebSockets; devices use
`https://<domain>/buddy/ota/` as their OTA URL.

Pictures are held in memory, so they assume a single server instance.

## Buddy devices

1. Build and flash [Buddy firmware](../buddy-firmware) with its OTA URL
   pointing at this backend.
2. On first boot, join the device's **Buddy-XXXX** hotspot and choose the
   school Wi-Fi.
3. Register the device: the backend logs
   `Buddy firmware: unregistered device <mac> requested OTA`. Add a `devices`
   row with `device_id` = that MAC (lowercase, colon-separated), the
   school's `org_id` and `is_active = true`, then restart the device.
4. Say "Hi ESP" or press button A and ask a question.

---

## Project layout

```
app/
  main.py               FastAPI app and routers
  config.py             Environment settings
  auth.py               Device keys, admin key, Buddy device tokens
  database.py           Supabase client
  routers/
    ask.py              POST /api/v1/ask
    students.py         Student admin
    firmware.py         Buddy devices: OTA, voice WebSocket, pictures
  services/
    ai.py               Subject prompts and DeepSeek call
    tutor.py            Strand detection, interaction logging
    content_loader.py   Curriculum loading, prompt summaries, language guess
    stt.py              Speech-to-text (Groq Whisper)
    tts.py              Text-to-speech (Edge voices)
    audio.py            Opus/MP3/WAV conversion, end-of-speech detection
    images.py           Picture pipeline (Wikipedia, PNG for the device)
  content/              Curriculum JSON per subject
```

## Known limitations

- Each question is answered on its own — no conversation memory, and voice
  interactions are not yet linked to a student.
- Edge voices use an unofficial endpoint and may change without notice;
  Groq's free tier is rate-limited.
- Pictures come from Wikipedia lead images: good for objects, organs and
  apparatus, weaker for abstract topics.
- Voice devices are identified by MAC address; an activation-code flow
  would be stronger.
- `GET /api/v1/students` returns student PINs, which are stored unhashed.
