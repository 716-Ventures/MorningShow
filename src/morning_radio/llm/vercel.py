"""Vercel AI Gateway chat completions using project-local gateway credentials."""

from typing import ClassVar

from morning_radio.llm.openai import OpenAIClient


class VercelClient(OpenAIClient):
    provider = "vercel"
    label = "Vercel AI Gateway"
    credential = "AI_GATEWAY_API_KEY"
    endpoint = "https://ai-gateway.vercel.sh/v1/"
    completion_options: ClassVar[dict[str, object]] = {"max_tokens": 12000}
