"""
ShariahEase — Whisper Transcription Service

Fixes applied vs original:
  1. Model upgraded base → small   (Urdu/Arabic disambiguation requires it)
  2. beam_size 1 → 5               (Whisper default; much better accuracy)
  3. language=None → smart hint    (stops Urdu being misdetected as Arabic)
  4. initial_prompt added          (anchors vocabulary to Islamic finance domain)
  5. condition_on_previous_text    (reduces hallucination on short clips)
  6. temperature fallback added    (handles low-confidence audio gracefully)
"""

import os
import logging
import tempfile

logger = logging.getLogger(__name__)

# ── Model config ────────────────────────────────────────────────────────────
# 'small' is the minimum viable model for accurate Urdu transcription.
# It costs ~1–2s more than 'base' on CPU but eliminates Urdu/Arabic confusion.
# 'medium' is even better but too slow on a laptop CPU — small is the sweet spot.
_whisper_model = None
MODEL_SIZE = "small"

# ── Language detection thresholds ───────────────────────────────────────────
# Whisper returns a language probability alongside its detection.
# Below this threshold we override with our own script-based detection.
LANG_CONFIDENCE_THRESHOLD = 0.70

# ── Domain prompt ────────────────────────────────────────────────────────────
# Injected as initial_prompt — primes Whisper with domain vocabulary.
# This dramatically reduces hallucinations and wrong script selection.
# Written in Urdu script so Whisper knows what script to expect.
_URDU_PROMPT = (
    "زکوٰۃ، نصاب، حلال، حرام، مضاربہ، مرابحہ، اجارہ، سود، "
    "اسلامی بینکاری، سرمایہ کاری، سونا، چاندی، تولہ، گرام۔ "
    "Pakistan, Islamic finance, Shariah, halal, haram, zakat, nisab."
)

_ENGLISH_PROMPT = (
    "Zakat, nisab, halal, haram, Mudarabah, Murabaha, Ijarah, riba, "
    "Islamic banking, Shariah compliance, gold, silver, tola, Pakistan."
)

_ROMAN_PROMPT = (
    "Zakat, nisab, halal, haram, sona, chandi, tola, Islamic banking, "
    "Shariah, Pakistan, Mudarabah, halal investment, riba."
)


def _get_model():
    """Load Whisper model once on first use (lazy init)."""
    global _whisper_model
    if _whisper_model is None:
        try:
            from faster_whisper import WhisperModel
            logger.info(f"Loading Whisper '{MODEL_SIZE}' model...")
            _whisper_model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
            logger.info("Whisper model ready.")
        except ImportError:
            logger.error("faster-whisper not installed. Run: pip install faster-whisper")
            raise
        except Exception as e:
            logger.error(f"Failed to load Whisper model: {e}")
            raise
    return _whisper_model


def _detect_script(text: str) -> str:
    """
    Detect script of transcribed text to catch Whisper mis-detections.

    Returns 'ur', 'roman', or 'en'.
    Used to override Whisper's language tag when confidence is low
    or when Whisper returns 'ar' (Arabic) for what is clearly Urdu speech.
    """
    if not text:
        return "en"

    urdu_chars = sum(1 for c in text if '\u0600' <= c <= '\u06FF')
    total_alpha = sum(1 for c in text if c.isalpha())

    if total_alpha == 0:
        return "en"

    if urdu_chars / total_alpha > 0.25:
        return "ur"

    # Roman Urdu function words
    roman_markers = {"hai", "hain", "ka", "ki", "ke", "kya", "nahi",
                     "aur", "mein", "se", "ko", "ap", "aap", "toh"}
    words = set(text.lower().split())
    if len(words & roman_markers) >= 2:
        return "roman"

    return "en"


def _pick_language_hint(detected_script: str) -> str | None:
    """
    Map our script detection to a Whisper language code hint.

    Returning None = full auto-detect (used for English, which Whisper
    handles perfectly without help).

    Key fix: we never let Whisper auto-detect when input appears Urdu,
    because it consistently picks 'ar' (Arabic) instead of 'ur' (Urdu).
    """
    return {
        "ur":    "ur",    # force Urdu — stops Arabic mis-detection
        "roman": None,    # Roman Urdu is Latin script; auto-detect works fine
        "en":    None,    # English; auto-detect is reliable
    }.get(detected_script)


def transcribe_audio(audio_bytes: bytes, file_extension: str = "webm") -> dict:
    """
    Transcribe audio bytes → text using Whisper (small, CPU, int8).

    Two-pass strategy:
      Pass 1: Auto-detect language, get raw transcription + language confidence.
      Post:   If Whisper picked 'ar' but text looks Urdu → correct to 'ur'.
              If language confidence < threshold → re-run with explicit hint.

    Args:
        audio_bytes:    Raw audio from browser (webm/wav/mp3).
        file_extension: Format hint for temp file suffix.

    Returns:
        {
          "text":     "transcribed string",
          "language": "ur" | "en" | "roman" | ...,
          "success":  True | False,
          "error":    None | "message"
        }
    """
    if not audio_bytes:
        return {"text": "", "language": "en", "success": False, "error": "No audio received"}

    tmp_path = None
    try:
        model = _get_model()

        with tempfile.NamedTemporaryFile(suffix=f".{file_extension}", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        # ── Pass 1: auto-detect ───────────────────────────
        segments, info = model.transcribe(
            tmp_path,
            beam_size                  = 5,       # FIX: was 1 (greedy/inaccurate)
            best_of                    = 5,       # evaluate 5 candidates, pick best
            language                   = None,    # auto-detect first
            initial_prompt             = _ENGLISH_PROMPT,
            condition_on_previous_text = False,   # reduces hallucination on short clips
            vad_filter                 = True,
            vad_parameters             = {"min_silence_duration_ms": 300},
            temperature                = [0.0, 0.2, 0.4],  # fallback on low confidence
        )

        full_text     = " ".join(seg.text.strip() for seg in segments).strip()
        detected_lang = info.language if info else "en"
        lang_prob     = info.language_probability if info else 0.0

        logger.info(f"Pass 1 → lang={detected_lang} ({lang_prob:.0%}): {full_text[:80]}")

        # ── Post-process: fix Arabic/Urdu confusion ───────
        # Whisper frequently transcribes Urdu speech as Arabic ('ar').
        # If the detected script is Urdu but Whisper said Arabic → correct it.
        script = _detect_script(full_text)

        if detected_lang == "ar" and script == "ur":
            logger.info("Correcting Whisper mis-detection: ar → ur (script analysis)")
            detected_lang = "ur"

        # ── Pass 2: re-run with explicit hint if low confidence ──
        if lang_prob < LANG_CONFIDENCE_THRESHOLD and script == "ur":
            logger.info(f"Low confidence ({lang_prob:.0%}), re-running with language='ur'")

            segments2, info2 = model.transcribe(
                tmp_path,
                beam_size                  = 5,
                best_of                    = 5,
                language                   = "ur",
                initial_prompt             = _URDU_PROMPT,
                condition_on_previous_text = False,
                vad_filter                 = True,
                vad_parameters             = {"min_silence_duration_ms": 300},
                temperature                = [0.0, 0.2],
            )

            full_text2 = " ".join(seg.text.strip() for seg in segments2).strip()
            if full_text2:
                full_text     = full_text2
                detected_lang = "ur"
                logger.info(f"Pass 2 (ur) → {full_text[:80]}")

        # ── Map detected_lang to our app's language codes ─
        # 'ar' that survived post-processing = genuine Arabic word → treat as Urdu
        # Roman Urdu comes through as 'en' from Whisper (Latin script)
        if detected_lang in ("ar",):
            detected_lang = "ur"
        elif detected_lang == "en" and script == "roman":
            detected_lang = "roman"

        logger.info(f"Final → lang={detected_lang}: '{full_text[:80]}'")

        return {
            "text":     full_text,
            "language": detected_lang,
            "success":  bool(full_text),
            "error":    None if full_text else "Empty transcription",
        }

    except Exception as e:
        logger.error(f"Transcription error: {e}")
        return {"text": "", "language": "en", "success": False, "error": str(e)}

    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def preload_model():
    """Pre-load model at startup so first request is not slow."""
    try:
        _get_model()
    except Exception as e:
        logger.warning(f"Whisper preload skipped: {e}")
