"""Local stand-in for the ElevenLabs Text to Dialogue WebSocket, to dry-run the benchmark offline.

It follows the protocol as documented on 19 Oct 2026 (register voices, then `inputs` with a
`flush`, base64 audio frames, a final frame) and refuses what the real service refuses:
a missing key, or a model that is not eleven_v3* / eleven_v4*. The audio is a quiet tone whose
length follows the text. This validates the benchmark's logic, never ElevenLabs' behaviour.

    .venv/bin/python scripts/spikes/fake_elevenlabs_server.py [--port 8765] [--no-final]
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import math
import struct
from urllib.parse import parse_qs, urlparse

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

RATE = 24_000
FIRST_AUDIO_DELAY = 0.12  # seconds between the text and the first chunk
CHUNK_SECONDS = 0.1
SECONDS_PER_CHARACTER = 0.06


def tone(seconds: float) -> bytes:
    count = int(RATE * seconds)
    return b"".join(
        struct.pack("<h", int(800 * math.sin(2 * math.pi * 220 * i / RATE)))
        for i in range(count)
    )


def screen(connection: ServerConnection, request: Request) -> Response | None:
    parsed = urlparse(request.path)
    model = parse_qs(parsed.query).get("model_id", [""])[0]
    if "xi-api-key" not in request.headers:
        return connection.respond(401, "Missing xi-api-key\n")
    if not model.startswith(("eleven_v3", "eleven_v4")):
        return connection.respond(
            400, f"Model '{model}' is not supported on the text-to-dialogue endpoint.\n"
        )
    return None


async def handle(connection: ServerConnection, *, send_final: bool) -> None:
    registered = False
    try:
        async for raw in connection:
            message = json.loads(raw)
            if not registered:
                if "voices" not in message:
                    await connection.send(
                        json.dumps({"error": "first message must register voices"})
                    )
                    await connection.close()
                    return
                registered = True
                continue
            for item in message.get("inputs", []):
                text = item.get("text", "")
                await asyncio.sleep(FIRST_AUDIO_DELAY)
                remaining = max(0.2, len(text) * SECONDS_PER_CHARACTER)
                while remaining > 0:
                    step = min(CHUNK_SECONDS, remaining)
                    audio = base64.b64encode(tone(step)).decode("ascii")
                    await connection.send(json.dumps({"audio": audio}))
                    remaining -= step
                    await asyncio.sleep(0.03)
            if send_final:
                await connection.send(json.dumps({"isFinal": True}))
    except ConnectionClosed:
        return


async def main(port: int, send_final: bool) -> None:
    async def handler(connection: ServerConnection) -> None:
        await handle(connection, send_final=send_final)

    async with serve(handler, "127.0.0.1", port, process_request=screen):
        print(
            f"Faux serveur ElevenLabs sur ws://127.0.0.1:{port}/v1/text-to-dialogue/stream-input"
        )
        await asyncio.Future()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--no-final", action="store_true", help="never send the final frame"
    )
    options = parser.parse_args()
    asyncio.run(main(options.port, not options.no_final))
