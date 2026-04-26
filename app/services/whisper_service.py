"""
ShariahEase — Whisper Transcription Service
Uses faster-whisper (CPU-optimized) to convert speech to text.
Model: 'base' — best balance of speed and accuracy on i5-7200U.
Supports Urdu, English, and Roman Urdu speech automatically.
"""

import os
import logging
import tempfile

logger = logging.getLogger(__name__)

# Lazy-loaded to avoid slow startup — model loads on first use
_whisper_model = None
MODEL_SIZE = "base"    # tiny=fast/weak  base=balanced  small=slow/accurate


def _get_model():
    """Load the Whisper model once and reuse it for all requests."""
    global _whisper_model
    if _whisper_model is None:
        try:
            from faster_whisper import WhisperModel
            logger.info(f"Loading Whisper '{MODEL_SIZE}' model (first-time download may take a moment)...")
            # cpu + int8 quantization = fastest possible on CPU, no GPU needed
            _whisper_model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
            logger.info("Whisper model loaded and ready.")
        except ImportError:
            logger.error("faster-whisper not installed. Run: pip install faster-whisper")
            raise
        except Exception as e:
            logger.error(f"Failed to load Whisper model: {e}")
            raise
    return _whisper_model


def transcribe_audio(audio_bytes: bytes, file_extension: str = "webm") -> dict:
    """
    Transcribe audio bytes to text using Whisper.

    Args:
        audio_bytes:    Raw audio bytes from the browser (webm/wav/mp3).
        file_extension: Format hint — 'webm', 'wav', or 'mp3'.

    Returns:
        {
          "text":     "transcribed text string",
          "language": "en" | "ur" | "ro" etc,
          "success":  True | False,
          "error":    None | "error message"
        }
    """
    if not audio_bytes:
        return {"text": "", "language": "en", "success": False, "error": "No audio received"}

    tmp_path = None
    try:
        model = _get_model()

        # Write bytes to a temp file — faster-whisper needs a file path
        with tempfile.NamedTemporaryFile(
            suffix=f".{file_extension}", delete=False
        ) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        # Transcribe — beam_size=1 is fastest on CPU, still accurate for this use case
        segments, info = model.transcribe(
            tmp_path,
            beam_size       = 1,
            vad_filter      = True,     # skip silence
            vad_parameters  = {"min_silence_duration_ms": 500},
            language        = None,     # auto-detect language
        )

        # Collect all segment texts
        full_text = " ".join(seg.text.strip() for seg in segments).strip()

        detected_lang = info.language if info else "en"
        logger.info(f"Transcribed ({detected_lang}): {full_text[:80]}")

        return {
            "text":     full_text,
            "language": detected_lang,
            "success":  True,
            "error":    None,
        }

    except Exception as e:
        logger.error(f"Transcription error: {e}")
        return {
            "text":     "",
            "language": "en",
            "success":  False,
            "error":    str(e),
        }
    finally:
        # Always clean up temp file
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def preload_model():
    """
    Called at app startup to pre-load the model so the first user
    request is not slow. Non-blocking — logs a warning if it fails.
    """
    try:
        _get_model()
    except Exception as e:
        logger.warning(f"Whisper preload skipped: {e}")
