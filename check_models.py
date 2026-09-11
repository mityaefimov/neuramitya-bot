import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=os.getenv("GROQ_API_KEY"),
)

print("Доступные модели на твоем аккаунте Groq:")
models = client.models.list()
for m in models.data:
    print(f"- {m.id}")