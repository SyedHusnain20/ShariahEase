import httpx, os
from groq import AsyncGroq

groq = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
WA_URL = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"
HEADERS = {
    "Authorization": f"Bearer {WHATSAPP_TOKEN}",
    "Content-Type": "application/json"
}

SYSTEM_PROMPT = """You are Shariah Ease — an expert Islamic finance assistant. 
Answer questions about halal banking, zakat, riba, murabaha, musharaka, 
sukuk, and Islamic economic principles. Always cite Quran or hadith where 
relevant. Be concise, clear, and helpful."""

async def handle_message(sender: str, text: str):
    reply = await get_reply(text)
    await send_text(sender, reply)

async def get_reply(user_input: str) -> str:
    response = await groq.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_input}
        ]
    )
    return response.choices[0].message.content

async def send_text(to: str, text: str):
    async with httpx.AsyncClient() as client:
        await client.post(WA_URL, headers=HEADERS, json={
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": text}
        })