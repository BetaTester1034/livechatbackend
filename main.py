from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
from pymongo import MongoClient
from dotenv import load_dotenv
from classes.connectionManager import ConnectionManager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException, status, Response
from classes.models.message import Message as MessageModel
import datetime
from classes.models.user import User as UserModel
from classes.models.room import Room as RoomModel
import bcrypt
import jwt
import os

from jwt.exceptions import ExpiredSignatureError, InvalidTokenError


load_dotenv()
DB_URI = os.getenv("DB_URI")
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
mongo_client = MongoClient(DB_URI)
db = mongo_client.rbschatroom
messages_collection = db.messages
messages_collection.create_index([("room_id", 1), ("timestamp", -1)])

def verify_token(token: str):
    try:
        payload = jwt.decode(token, ENCRYPTION_KEY, algorithms=['HS256'])
        return payload, None  # Valid token, no error
    except ExpiredSignatureError:
        return None, "Token expired"
    except InvalidTokenError:
        return None, "Invalid token"

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/new-account")
async def login(request: Request):
    data = await request.json()
    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="All fields must be filled"
        )

    elif UserModel.find_one({'username': data.get('username')}):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists"
        )

    elif len(password) < 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be atleast 6 characters long"
        )

    elif len(username) < 3:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username must be atleast 3 characters long"
        )

    hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())

    user = UserModel.create({'username': username, 'password': hashed_password, 'creationDate': datetime.datetime.now()})

    payload = {
        "username": user['username'],
        "creationDate": user['creationDate'].isoformat(),
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=1)
    }
    token = jwt.encode(payload, ENCRYPTION_KEY, algorithm="HS256")
    return {
        "status": "success",
        "token": token,
        "user": {
            "username": user['username'],
            "creationDate": user['creationDate'].isoformat()
        }
    }

@app.post("/login")
async def login(request: Request):
    data = await request.json()
    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="All fields must be filled")

    user = UserModel.find_one({'username': username})

    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")

    if not bcrypt.checkpw(password.encode('utf-8'), user['password']):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")

    payload = {
        "username": user['username'],
        "creationDate": user['creationDate'].isoformat(),
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=1)
    }
    token = jwt.encode(payload, ENCRYPTION_KEY, algorithm="HS256")

    return {
        "status": "success",
        "token": token,
        "user": {
            "username": user['username'],
            "creationDate": user['creationDate'].isoformat()
        }
    }

manager = ConnectionManager()

async def handle_client(websocket: WebSocket, room_id: str):
    authentication = websocket.query_params.get("token")
    joinOption = websocket.query_params.get("method").lower()

    if not authentication:
        await websocket.send_json({"type": "error", "message": "No authentication parameter found"})
        return

    token = authentication.removeprefix("Bearer ").strip()
    payload, error = verify_token(token)

    if error == "Token expired":
        await websocket.send_json({"type": "error", "message": "Token expired"})
        return

    if error == "Invalid token":
        await websocket.send_json({"type": "error", "message": "Invalid token"})
        return

    if not joinOption:
        await websocket.send_json({'type': "error", "message": 'You must provide an option'})

    username = payload.get('username')
    if any(conn['username'] == username and conn['room_id'] == room_id for conn in manager.active_connections.values()):
        await websocket.send_json({
            "type": "error",
            "message": "You are already connected."
        })
        await websocket.close()
        return

    if joinOption == "connect":
        results = RoomModel.find_one({'room_id': room_id})
        if results:
            await websocket.send_json({'type': 'info', 'data': {
                "room_id": room_id,
                "room_creator": results['room_creator']
            }})
            await manager.connect(websocket, username, room_id)
        else:
            await websocket.send_json({"type": "error", "message": "Room does not exist"})
    else:
        # User wants to create a room
        results = RoomModel.find_one({'room_id': room_id})
        if results is not None:
            return await websocket.send_json({"type": "error", "message": "Room already exists"})

        room = RoomModel.create({'room_creator': results['room_creator'], 'room_id': room_id, 'createdAt': datetime.datetime.utcnow()})
        await websocket.send_json({'type': 'info', 'data': {
            "room_id": room_id,
            "room_creator": username
        }})
        await manager.connect(websocket, username, room_id)


    try:
        # Fetch last 50 messages for this room, sorted oldest first for frontend
        history_cursor = MessageModel.find_many(
            {"room_id": room_id},
            sort=[("timestamp", 1)],
            limit=50
        )

        history = list(history_cursor)
        for msg in history:
            await websocket.send_json({
                "username": msg["username"],
                "content": msg["content"],
                "room_id": msg["room_id"],
                "timestamp": msg["timestamp"],
                "system": msg.get("system", False),
                "type": "message"
            })

        while True:
            data = await websocket.receive_json()
            option = data.get("option")

            if option == "connection":
                print(f"Connection message from {username} in room {room_id}")
                # No duplicate message here, just log or process if needed

            elif option == "message":
                message = {
                    "username": username,
                    "content": data.get("content"),
                    "room_id": room_id,
                    "timestamp": datetime.datetime.utcnow().isoformat(),
                    "system": False
                }
                await manager.broadcast(message, room_id)

            elif option == "kick":
                target_username = data.get("target")
                results = RoomModel.find_one({'room_id': room_id})

                if username != results['room_creator']:
                    await websocket.send_json({"type": "error", "message": "Only the room owner can perform this action"})
                
                elif username == target_username:
                    await websocket.send_json({"type": "error", "message": "You cannot kick yourself"})

                if target_username:
                    await manager.kick_user(target_username, room_id)
                else:
                    await websocket.send_json({"type": "error", "message": "Please provide a valid target to kick"})
            else:
                print(f"Unknown option: {option}")

    except WebSocketDisconnect:
        left_user = manager.disconnect(websocket)
        if left_user:
            await manager.broadcast_system_message(f"{left_user[0]} left the chat", room_id)
            await manager.send_user_list(room_id)
    finally:
        if websocket in manager.active_connections:
            manager.disconnect(websocket)
            await manager.send_user_list(room_id)

@app.websocket("/ws/{room_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: str):
    await websocket.accept()

    await handle_client(websocket, room_id)