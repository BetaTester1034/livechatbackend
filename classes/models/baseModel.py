from bson import ObjectId
from pymongo.collection import Collection
from datetime import datetime
from classes.db import db

class Model:
    collection_name: str = None

    @classmethod
    def collection(cls) -> Collection:
        if cls.collection_name is None:
            raise NotImplementedError("Define 'collection_name' in subclass.")
        return db[cls.collection_name]

    @classmethod
    def create(cls, data: dict):
        now = datetime.utcnow()
        data["created_at"] = now
        data["updated_at"] = now
        result = cls.collection().insert_one(data)
        data["_id"] = str(result.inserted_id)
        return data

    @classmethod
    def find_by_id(cls, id: str):
        doc = cls.collection().find_one({"_id": ObjectId(id)})
        if doc:
            doc["_id"] = str(doc["_id"])
        return doc

    @classmethod
    def find_one(cls, query: dict):
        doc = cls.collection().find_one(query)
        if doc:
            doc["_id"] = str(doc["_id"])
        return doc

    @classmethod
    def find_many(cls, query: dict = {}, sort: list = None, limit: int = 0):
        cursor = cls.collection().find(query)
        if sort:
            cursor = cursor.sort(sort)
        if limit:
            cursor = cursor.limit(limit)
        return [{**doc, "_id": str(doc["_id"])} for doc in cursor]

    @classmethod
    def update(cls, id: str, data: dict):
        data["updated_at"] = datetime.utcnow()
        cls.collection().update_one({"_id": ObjectId(id)}, {"$set": data})
        return cls.find_by_id(id)

    @classmethod
    def delete(cls, id: str):
        return cls.collection().delete_one({"_id": ObjectId(id)})

    @classmethod
    def drop(cls):
        cls.collection().drop()