"""The only AI step: one extraction call per proposal, structured JSON out."""
import json
import os

from dotenv import load_dotenv
from openai import OpenAI

from prompts import build_messages, build_schema

load_dotenv()


def extract(config, proposal_id, proposal_text):
    client = OpenAI()  # reads OPENAI_API_KEY from the environment / .env
    resp = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        temperature=0,
        seed=7,
        response_format={"type": "json_schema", "json_schema": build_schema(config)},
        messages=build_messages(config, proposal_id, proposal_text),
    )
    return json.loads(resp.choices[0].message.content)
