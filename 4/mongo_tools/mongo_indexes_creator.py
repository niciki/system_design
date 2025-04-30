from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import IndexModel, ASCENDING, DESCENDING, TEXT
import asyncio

async def create_events_indexes():
    mongo_uri = "mongodb://mongo:mongo123@localhost:27017"
    client = AsyncIOMotorClient(mongo_uri)
    db = client["analytics_service"]
    collection = db["events"]

    indexes = [
        IndexModel([("event_type", ASCENDING)], name="event_type_asc"),
        IndexModel([("user_id", ASCENDING)], name="user_id_asc"),
        IndexModel([("session_id", ASCENDING)], name="session_id_asc"),
        
        IndexModel([("page_url", TEXT)], name="page_url_text"),
        
        IndexModel([("timestamp", DESCENDING)], name="timestamp_desc"),
        
        IndexModel([
            ("user_id", ASCENDING),
            ("timestamp", DESCENDING)
        ], name="user_id_asc_timestamp_desc"),
        
        IndexModel([
            ("event_type", ASCENDING),
            ("timestamp", DESCENDING)
        ], name="event_type_asc_timestamp_desc"),
        
        IndexModel([
            ("session_id", ASCENDING),
            ("timestamp", DESCENDING)
        ], name="session_id_asc_timestamp_desc"),
        
        IndexModel(
            [("user_id", ASCENDING)],
            name="user_id_asc_partial",
            partialFilterExpression={"user_id": {"$exists": True}}
        )
    ]

    await collection.drop_indexes()
    
    await collection.create_indexes(indexes)
    print("Индексы успешно созданы в MongoDB:")
    print(f"- URI: {mongo_uri}")
    print(f"- DB: analytics_service")
    print(f"- Collection: events")
    
    existing_indexes = await collection.index_information()
    for index_name, index_info in existing_indexes.items():
        print(f"  - {index_name}: {index_info['key']}")

if __name__ == "__main__":
    asyncio.run(create_events_indexes())