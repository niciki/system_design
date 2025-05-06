import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime, timedelta
import random
from faker import Faker

fake = Faker()

async def generate_test_data():
    mongo_uri = "mongodb://mongo:mongo123@localhost:27017"
    client = AsyncIOMotorClient(mongo_uri)
    db = client["analytics_service"]
    collection = db["events"]

    await collection.delete_many({})

    NUM_DOCUMENTS = 1_000  
    EVENT_TYPES = ["page_view", "button_click", "form_submit", "product_view", "checkout_start"]
    USER_IDS = list(range(1, 101)) 
    SESSION_IDS = [fake.uuid4() for _ in range(200)] 

    print(f"Генерация {NUM_DOCUMENTS} тестовых документов...")

    documents = []
    for i in range(NUM_DOCUMENTS):
        
        doc = {
            "event_type": random.choice(EVENT_TYPES),
            "user_id": random.choice(USER_IDS),
            "session_id": random.choice(SESSION_IDS),
            "page_url": fake.uri_path(),
            "referrer_url": fake.uri() if random.random() > 0.3 else None,
            "user_agent": fake.user_agent(),
            "ip_address": fake.ipv4(),
            "timestamp": fake.date_time_between(start_date="-30d", end_date="now"),
            "metadata": {}
        }

        if doc["event_type"] == "product_view":
            doc["metadata"] = {
                "product_id": random.randint(1000, 9999),
                "category": random.choice(["electronics", "clothing", "books", "home"])
            }
        elif doc["event_type"] == "button_click":
            doc["metadata"] = {
                "button_id": f"btn_{random.randint(1, 20)}",
                "page_section": random.choice(["header", "footer", "sidebar", "main"])
            }

        documents.append(doc)

        if len(documents) % 100 == 0:
            await collection.insert_many(documents)
            documents = []
            print(f"Добавлено {i+1} документов...")

    if documents:
        await collection.insert_many(documents)

    print(f"Успешно загружено {NUM_DOCUMENTS} тестовых документов")
    print(f"Статистика коллекции: {await collection.count_documents({})} документов")

    client.close()

if __name__ == "__main__":
    asyncio.run(generate_test_data())