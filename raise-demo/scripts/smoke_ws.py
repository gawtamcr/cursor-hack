import asyncio
import collections
import json

import websockets


async def main():
    counts = collections.Counter()
    last_health = None

    async with websockets.connect("ws://localhost:8000/ws") as ws:

        async def recv_for(seconds):
            nonlocal last_health
            end = asyncio.get_event_loop().time() + seconds
            while True:
                remaining = end - asyncio.get_event_loop().time()
                if remaining <= 0:
                    return
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    return
                msg = json.loads(raw)
                counts[msg.get("type")] += 1
                t = msg.get("type")
                if t in ("incident", "car_state", "fault_result", "tool_call", "tool_result"):
                    print("EVENT:", json.dumps(msg)[:300], flush=True)
                elif t == "agent_msg":
                    print("AGENT:", msg.get("text", "")[:400].replace("\n", " | "), flush=True)
                elif t == "health":
                    key = (msg.get("status"), msg.get("symptom"))
                    if key != last_health:
                        last_health = key
                        print("HEALTH:", key, flush=True)

        # Webots + steering-monitor warm-up is slower than the old mock camera.
        print("--- baseline (25s) ---", flush=True)
        await recv_for(25)
        print("--- inject steer_reversed ---", flush=True)
        await ws.send(json.dumps({"type": "inject_fault", "fault": "steer_reversed"}))
        await recv_for(60)
        print("--- clear fault (operator power-cycles steering ECU) ---", flush=True)
        await ws.send(json.dumps({"type": "inject_fault", "fault": "none"}))
        await recv_for(10)
        print("--- operator clicks: Done, check again ---", flush=True)
        await ws.send(json.dumps({"type": "check_again"}))
        await recv_for(60)

    print("--- message type counts ---", flush=True)
    for key, value in sorted(counts.items()):
        print(f"{key}: {value}", flush=True)


asyncio.run(main())
