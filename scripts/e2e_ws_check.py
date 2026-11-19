import asyncio
import json
import websockets


async def main():
    url = "ws://127.0.0.1:8012/ws/stream?scene=hospital"
    async with websockets.connect(url) as ws:
        for word in ["我", "头疼", "两天"]:
            await ws.send(json.dumps({"type": "gloss", "text": word, "score": 0.9}, ensure_ascii=False))
        await ws.send(json.dumps({"type": "control", "action": "sentence_end"}))
        received = []
        try:
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=4))
                received.append(msg)
                if msg.get("type") == "sentence" and msg.get("stage") == "refined":
                    break
        except asyncio.TimeoutError:
            pass
        print("EVENTS:", [(m.get("type"), m.get("stage") or "", m.get("text", "")[:24]) for m in received])
        sentences = [m for m in received if m.get("type") == "sentence"]
        audios = [m for m in received if m.get("type") == "audio"]
        assert sentences, "no sentence event"
        assert sentences[0]["text"].startswith("医生"), sentences[0]
        assert audios and audios[0]["url"].startswith("data:audio/wav"), audios[:1]
        print("WS EDGE MODE OK: fast=", sentences[0]["text"], "| events=", len(received))


asyncio.run(main())
