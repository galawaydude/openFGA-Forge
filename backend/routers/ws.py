import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from services import compiler_service

router = APIRouter()


@router.websocket("/ws/compile")
async def websocket_compile(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "request_id": None, "message": "Invalid JSON"})
                continue

            rid = msg.get("request_id")
            mt = msg.get("type")
            md = msg.get("model")

            if mt not in ("compile", "validate"):
                await ws.send_json({"type": "error", "request_id": rid, "message": f"Unknown type: {mt}"})
                continue
            if not md or not isinstance(md, dict):
                await ws.send_json({"type": "error", "request_id": rid, "message": "Missing model"})
                continue

            try:
                if mt == "compile":
                    await ws.send_json({"type": "compile_result", "request_id": rid,
                                        **compiler_service.compile_model(md)})
                else:
                    await ws.send_json({"type": "validate_result", "request_id": rid,
                                        **compiler_service.validate_model(md)})
            except Exception as e:
                await ws.send_json({"type": "error", "request_id": rid, "message": str(e)})
    except WebSocketDisconnect:
        pass
