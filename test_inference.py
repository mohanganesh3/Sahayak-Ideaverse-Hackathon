import os
import argparse

from dotenv import load_dotenv
from openai import OpenAI

from pathlib import Path
_ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(_ENV_PATH)

parser = argparse.ArgumentParser(description="Test LLM inference against OpenAI-compatible providers.")
parser.add_argument("--provider", choices=["auto", "openai", "groq"], default="auto")
parser.add_argument("--model", default="")
args = parser.parse_args()

openai_api_key = os.getenv("OPENAI_API_KEY") or os.getenv("openai_api_key")
groq_api_key = os.getenv("GROQ_API_KEY")

if args.provider == "openai":
    if not openai_api_key:
        print("Error: OPENAI_API_KEY is not configured in .env")
        raise SystemExit(1)
    client = OpenAI(api_key=openai_api_key)
    model = args.model or "gpt-4o-mini"
    provider = "OpenAI"
elif args.provider == "groq":
    if not groq_api_key:
        print("Error: GROQ_API_KEY is not configured in .env")
        raise SystemExit(1)
    client = OpenAI(
        api_key=groq_api_key,
        base_url="https://api.groq.com/openai/v1",
    )
    model = args.model or "llama-3.3-70b-versatile"
    provider = "Groq"
elif openai_api_key:
    client = OpenAI(api_key=openai_api_key)
    model = args.model or "gpt-4o-mini"
    provider = "OpenAI"
elif groq_api_key:
    client = OpenAI(
        api_key=groq_api_key,
        base_url="https://api.groq.com/openai/v1",
    )
    model = args.model or "llama-3.3-70b-versatile"
    provider = "Groq"
else:
    print("Error: neither OPENAI_API_KEY nor GROQ_API_KEY is configured in .env")
    raise SystemExit(1)

try:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You are a helpful geriatric care assistant named Sahayak."},
            {"role": "user", "content": "Hello Sahayak! Please give me a warm greeting as a health assistant."}
        ],
        max_tokens=60
    )
    print(f"\n--- Sahayak Greeting ({provider}) ---")
    print(response.choices[0].message.content)
    print("------------------------\n")
except Exception as e:
    print(f"Inference failed: {e}")
    raise SystemExit(1)
