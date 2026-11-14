"""Record a reusable demo briefing with the voice selected from the user's agent."""

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.config import ROOT, Settings
from shared.http import CachedHTTP


def main():
    settings = Settings.load()
    client, db = settings.connect()
    try:
        result = CachedHTTP(db).request(
            "POST",
            "https://api.elevenlabs.io/v1/text-to-speech/" + settings.get("ELEVENLABS_VOICE_ID"),
            headers={"xi-api-key": settings.get("ELEVENLABS_API_KEY")},
            params={"output_format": "mp3_44100_128"},
            json_body={
                "text": "This is a simulated Wander travel update. Your trip may be affected. Review the options on your board and tap to confirm a fix. No plans change until you choose.",
                "model_id": settings.get("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5"),
            },
            ttl=7 * 86400,
        )
        folder = ROOT / "fixtures" / "voice"
        folder.mkdir(parents=True, exist_ok=True)
        audio = base64.b64decode(result["base64"])
        (folder / "briefing.mp3").write_bytes(audio)
        print(f"OK recorded demo briefing ({len(audio)} bytes)")
    finally:
        client.close()


if __name__ == "__main__":
    main()
