from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
from pymongo import MongoClient
from dotenv import load_dotenv
import os

# --- Load .env and setup DB ---
load_dotenv()
DB_URI = os.getenv("DB_URI")
mongo_client = MongoClient(DB_URI)
db = mongo_client.rbschatroom
messages_collection = db.messages
messages_collection.create_index([("room_id", 1), ("timestamp", -1)])  # Optimize queries

# --- FastAPI setup ---
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Connection Manager ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[WebSocket, dict[str, str]] = {}

    async def connect(self, websocket: WebSocket, username: str, room_id: str):
        await websocket.accept()
        self.active_connections[websocket] = {"username": username, "room_id": room_id}
        await self.broadcast_system_message(f"{username} joined the chat", room_id)
        await self.send_user_list(room_id)

    def disconnect(self, websocket: WebSocket):
        data = self.active_connections.get(websocket)
        if data:
            username = data["username"]
            room_id = data["room_id"]
            del self.active_connections[websocket]
            return username, room_id
        return None

    async def broadcast(self, message: dict, room_id: str):
        # Save message to MongoDB
        messages_collection.insert_one({
            "username": message["username"],
            "content": message["content"],
            "room_id": room_id,
            "timestamp": message["timestamp"],
            "system": message["system"]
        })

        # Send message to everyone in same room
        for connection, data in self.active_connections.items():
            if data['room_id'] == room_id:
                await connection.send_json(message)

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

manager = ConnectionManager()

# --- WebSocket Endpoint ---
@app.websocket("/ws/{username}/{room_id}")
async def websocket_endpoint(websocket: WebSocket, username: str, room_id: str):

    # Prevent duplicate connection
    if any(conn['username'] == username and conn['room_id'] == room_id for conn in manager.active_connections.values()):
        await websocket.accept()
        await websocket.send_json({
            "type": "error",
            "message": "You are already connected."
        })
        await websocket.close()
        return

    await manager.connect(websocket, username, room_id)

    try:
        # Send last 50 messages from MongoDB to new client
        history = messages_collection.find({"room_id": room_id}).sort("timestamp", -1).limit(50)
        for msg in reversed(list(history)):
            await websocket.send_json({
                "username": msg["username"],
                "content": msg["content"],
                "room_id": msg["room_id"],
                "timestamp": msg["timestamp"],
                "system": msg.get("system", False)
            })

        # Receive messages from client
        while True:
            data = await websocket.receive_json()
            message = {
                "username": username,
                "content": data.get("content"),
                "room_id": room_id,
                "timestamp": datetime.utcnow().isoformat(),
                "system": False
            }
            await manager.broadcast(message, room_id)

    except WebSocketDisconnect:
        left_user = manager.disconnect(websocket)
        if left_user:
            await manager.broadcast_system_message(f"{left_user[0]} left the chat", room_id)
            await manager.send_user_list(room_id)
