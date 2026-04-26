"""
Deterministic post-processor for Urdu chatbot responses.
Removes Hindi words, Devanagari script, and redirect sentences.
"""

import re

DEVANAGARI = re.compile(r'[\u0900-\u097F]+')

HINDI_TO_URDU = {
    "آدھار":   "بنیاد",
    "جانکاری":  "معلومات",
    "شروعات":   "آغاز",
    "سنہری":   "سونا",
    "دھیان":   "توجہ",
    "پیسہ":    "رقم",
    "دھن":     "مال",
    "جرورت":   "ضرورت",
    "سلور":    "چاندی",
    "گولڈ":    "سونا",
    "پرافٹ":   "منافع",
    "لون":     "قرضہ",
    "پیمنٹ":   "ادائیگی",
    "کیلکولیشن": "حساب",
    "بلکل":    "بالکل",
}

# Any sentence containing these phrases gets removed entirely
REDIRECT_PHRASES = [
    "وزارت مذہبی امور",
    "وزارت کی ویب سائٹ",
    "ویب سائٹ دیکھ",
    "ویب سائٹ پر",
    "اسلامی بینک کے ذریعے موجودہ نصاب",
    "اسلامی بینک سے معلومات",
    "بینک سے پوچھ",
    "ministry of religious",
    "check.*website",
    "visit.*website",
]


def remove_redirect_sentences(text: str) -> str:
    """
    Split by sentence-ending punctuation, remove any sentence
    that contains a redirect phrase, then rejoin.
    """
    # Split on Urdu full stop, English period, or newline
    sentences = re.split(r'(۔|\.\s|\n)', text)
    cleaned = []
    for part in sentences:
        part_lower = part.lower()
        has_redirect = any(
            re.search(phrase, part_lower if 'ministry' in phrase or 'website' in phrase else part)
            for phrase in REDIRECT_PHRASES
        )
        if not has_redirect:
            cleaned.append(part)
    return ''.join(cleaned).strip()


def filter_urdu_response(text: str) -> str:
    # 1. Remove Devanagari
    text = DEVANAGARI.sub('', text)
    # 2. Replace Hindi words
    for hindi, urdu in HINDI_TO_URDU.items():
        text = text.replace(hindi, urdu)
    # 3. Remove redirect sentences
    text = remove_redirect_sentences(text)
    # 4. Clean whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'  +', ' ', text)
    return text.strip()


def filter_response(text: str, language: str) -> str:
    if language == "ur":
        return filter_urdu_response(text)
    return text
