import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')  # NUEVO

if not OPENAI_API_KEY:
    print("⚠️  WARNING: OPENAI_API_KEY no está configurada")
else:
    print(f"✅ OPENAI_API_KEY cargada: {OPENAI_API_KEY[:10]}...")
