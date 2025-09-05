from __future__ import annotations
import os
from typing import Iterable, Dict, Any
from openai import OpenAI

DEFAULT_MODEL = "gpt-4.1-mini"

class ChatIO:
    """Thin wrapper for streaming chat completions."""
    def __init__(self, model: str = DEFAULT_MODEL):
        if "OPENAI_API_KEY" not in os.environ:
            raise RuntimeError("OPENAI_API_KEY not set in environment")
        self.client = OpenAI()
        self.model = model

    def stream(self, messages: Iterable[Dict[str, Any]]):
        return self.client.chat.completions.create(
            model=self.model,
            messages=list(messages),
            stream=True,
        )
