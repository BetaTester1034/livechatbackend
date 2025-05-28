from fastapi import WebSocket, WebSocketDisconnect
from datetime import datetime
from classes.models.message import Message as MessageModel



class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[WebSocket, dict[str, str]] = {}

    async def connect(self, websocket: WebSocket, username: str, room_id: str):
        self.active_connections[websocket] = {"username": username, "room_id": room_id}
        await self.broadcast_system_message(f"{username} joined the chat", room_id)
        await self.send_user_list(room_id)

    def get_user_websocket(self, username: str, room_id: str):
        for websocket, data in self.active_connections.items():
            if data["username"] == username and data["room_id"] == room_id:
                return websocket
        return None

    def disconnect(self, websocket: WebSocket):
        data = self.active_connections.get(websocket)
        if data:
            username = data["username"]
            room_id = data["room_id"]
            del self.active_connections[websocket]
            return username, room_id
        return None

    async def kick_user(self, username: str, room_id: str):
        ws = self.get_user_websocket(username, room_id)
        if ws:
            # Send kick notification BEFORE closing
            await ws.send_json({
                "type": "kicked",
                "message": "You have been kicked from the room."
            })
            await ws.close(code=1000, reason="You have been kicked.")
            self.disconnect(ws)
            await self.broadcast_system_message(f"{username} has been kicked from the chat", room_id)
            await self.send_user_list(room_id)

    async def broadcast(self, message: dict, room_id: str):
        # Save message to DB
        MessageModel.create({
            "username": message["username"],
            "content": message["content"],
            "room_id": room_id,
            "timestamp": message["timestamp"],
            "system": message["system"]
        })

        # Broadcast only to users in the same room
        for connection, data in self.active_connections.items():
            if data['room_id'] == room_id:
                await connection.send_json({"type": 'message', **message})

    async def broadcast_system_message(self, content: str, room_id: str):
        message = {
            "username": "System",
            "content": content,
            "room_id": room_id,
            "timestamp": datetime.utcnow().isoformat(),
            "system": True
        }
        await self.broadcast(message, room_id)

    async def send_user_list(self, room_id: str):
        users = list({data["username"] for data in self.active_connections.values() if data["room_id"] == room_id})
        message = {
            "type": "users",
            "users": users
        }
        for connection, data in self.active_connections.items():
            if data["room_id"] == room_id:
                await connection.send_json(message)