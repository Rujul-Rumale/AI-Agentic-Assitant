"""Ad-hoc: test NVIDIA NIM connectivity with the configured key/model."""
import asyncio
import os

from dotenv import load_dotenv

load_dotenv()


async def main() -> None:
    from agent.context.resolver import ResolvedContext
    from agent.llm.nim import NimLLMProvider

    provider = NimLLMProvider(model=os.getenv("NVIDIA_NIM_MODEL", ""))
    print("configured:", provider.configured, "model:", provider.model)
    print("healthcheck:", await provider.healthcheck())

    ctx = ResolvedContext(
        message_id="t1", chat_id="t", chat_type="PRIVATE",
        current_text="Can you send me the latest telemetry report please?",
    )
    cls = await provider.classify(ctx)
    print("classification:", cls.model_dump())


asyncio.run(main())