"""
ShariahEase — Text-to-Speech Service
Uses Microsoft edge-tts (completely free, no API key needed).
Selects the correct voice based on detected language.

Voices used:
  Urdu   → ur-PK-AsadNeural   (male, natural Pakistani accent)
  English → en-US-AriaNeural  (female, clear neutral accent)
  Roman  → en-US-AriaNeural   (English voice reads Roman Urdu acceptably)
"""

import logging
import asyncio
import tempfile
import os

logger = logging.getLogger(__name__)

# Voice mapping — one per detected language code
VOICE_MAP = {
    "ur":     "ur-PK-AsadNeural",    # Pakistani Urdu male voice
    "roman":  "en-US-AriaNeural",    # English voice for Roman Urdu
    "en":     "en-US-AriaNeural",    # English female voice
}
DEFAULT_VOICE = "en-US-AriaNeural"


def _pick_voice(language: str) -> str:
    """Return the best edge-tts voice for the given language code."""
    return VOICE_MAP.get(language, DEFAULT_VOICE)


async def text_to_speech_bytes(text: str, language: str = "en") -> bytes:
    """
    Convert text to MP3 audio bytes using edge-tts.

    Args:
        text:     The answer text to speak.
        language: 'ur' | 'roman' | 'en'

    Returns:
        MP3 audio as bytes, or empty bytes on failure.
    """
    if not text or not text.strip():
        return b""

    try:
        import edge_tts
    except ImportError:
        logger.error("edge-tts not installed. Run: pip install edge-tts")
        return b""

    voice    = _pick_voice(language)
    tmp_path = None

    try:
        # edge-tts requires a file path output (not in-memory stream)
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            tmp_path = tmp.name

        communicate = edge_tts.Communicate(text=text, voice=voice)
        await communicate.save(tmp_path)

        with open(tmp_path, "rb") as f:
            audio_bytes = f.read()

        logger.info(f"TTS generated {len(audio_bytes)} bytes | voice={voice}")
        return audio_bytes

    except Exception as e:
        logger.error(f"TTS error (voice={voice}): {e}")
        return b""

    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def text_to_speech_sync(text: str, language: str = "en") -> bytes:
    """
    Synchronous wrapper around the async TTS function.
    Used when called from a non-async context.
    """
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(text_to_speech_bytes(text, language))
    except Exception as e:
        logger.error(f"TTS sync error: {e}")
        return b""
    finally:
        loop.close()
