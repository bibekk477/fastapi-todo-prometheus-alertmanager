from fastapi import FastAPI, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from prometheus_client import Counter, Gauge, Histogram, generate_latest, REGISTRY
from bson.objectid import ObjectId
import os
import time
from contextlib import asynccontextmanager
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===== Prometheus Metrics Setup =====
http_request_duration = Histogram(
    "http_request_duration_seconds",
    "Duration of HTTP requests in seconds",
    labelnames=["method", "endpoint", "status_code"],
    buckets=(0.1, 0.5, 1, 2, 5),
)

todos_created = Counter("todos_created_total", "Total number of todos created")

todos_deleted = Counter("todos_deleted_total", "Total number of todos deleted")

todos_completed = Counter(
    "todos_completed_total", "Total number of todos marked as completed"
)

active_todos = Gauge(
    "active_todos_count", "Current number of active (incomplete) todos"
)

db_errors = Counter(
    "db_errors_total", "Total number of database errors", labelnames=["operation"]
)

mongo_connections = Gauge(
    "mongo_connections_active",
    "MongoDB connection status (1=connected, 0=disconnected)",
)

# ===== MongoDB Setup =====
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = "todo-app"
COLLECTION_NAME = "todos"

client = None
db = None
todos_collection = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    global client, db, todos_collection

    try:
        client = MongoClient(MONGO_URI)
        db = client[DB_NAME]
        todos_collection = db[COLLECTION_NAME]

        # Verify connection
        client.admin.command("ping")
        mongo_connections.set(1)
        logger.info("MongoDB connected")

        # Update active todos on startup
        await update_active_todos_count()

    except PyMongoError as e:
        logger.error(f"Failed to connect to MongoDB: {e}")
        mongo_connections.set(0)
        raise

    yield

    # Shutdown
    if client:
        client.close()
        mongo_connections.set(0)
        logger.info("MongoDB disconnected")


app = FastAPI(lifespan=lifespan)


# ===== Pydantic Models =====
class TodoCreate(BaseModel):
    title: str
    description: str = None
    completed: bool = False


class TodoUpdate(BaseModel):
    title: str = None
    description: str = None
    completed: bool = None


class TodoResponse(BaseModel):
    id: str
    title: str
    description: str = None
    completed: bool
    createdAt: str


# ===== Helper Functions =====
async def update_active_todos_count():
    """Update the gauge with current active todos count"""
    try:
        count = todos_collection.count_documents({"completed": False})
        active_todos.set(count)
    except PyMongoError as e:
        db_errors.labels(operation="count").inc()
        logger.error(f"Error counting active todos: {e}")


def serialize_todo(todo):
    """Convert MongoDB document to response format"""
    return {
        "id": str(todo["_id"]),
        "title": todo["title"],
        "description": todo.get("description"),
        "completed": todo.get("completed", False),
        "createdAt": str(todo.get("createdAt", "")),
    }


def track_request_time(method: str, endpoint: str, status_code: int):
    """Decorator to track request duration"""

    def decorator(func):
        async def wrapper(*args, **kwargs):
            start = time.time()
            try:
                result = await func(*args, **kwargs)
                duration = time.time() - start
                http_request_duration.labels(
                    method=method, endpoint=endpoint, status_code=200
                ).observe(duration)
                return result
            except Exception as e:
                duration = time.time() - start
                http_request_duration.labels(
                    method=method, endpoint=endpoint, status_code=500
                ).observe(duration)
                raise

        return wrapper

    return decorator


# ===== Routes =====


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "ok"}


@app.get("/live")
async def live():
    """Check if FastAPI is running (liveness probe)"""
    return {"status": "alive"}


@app.get("/ready")
async def ready():
    """Check if FastAPI can connect to MongoDB (readiness probe)"""
    try:
        client.admin.command("ping")
        return {"status": "ready"}
    except Exception:
        raise HTTPException(status_code=503, detail="MongoDB not ready")


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint"""
    return Response(generate_latest(REGISTRY), media_type="text/plain; charset=utf-8")


@app.get("/todos")
async def get_todos():
    """Get all todos"""
    try:
        todos = list(todos_collection.find())
        return [serialize_todo(todo) for todo in todos]
    except PyMongoError as e:
        db_errors.labels(operation="find").inc()
        logger.error(f"Error fetching todos: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch todos",
        )


@app.post("/todos", status_code=status.HTTP_201_CREATED)
async def create_todo(todo: TodoCreate):
    """Create a new todo"""
    try:
        todo_dict = todo.dict()
        todo_dict["completed"] = False

        result = todos_collection.insert_one(todo_dict)
        todos_created.inc()
        await update_active_todos_count()

        created_todo = todos_collection.find_one({"_id": result.inserted_id})
        return serialize_todo(created_todo)
    except PyMongoError as e:
        db_errors.labels(operation="create").inc()
        logger.error(f"Error creating todo: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create todo",
        )


@app.put("/todos/{todo_id}")
async def update_todo(todo_id: str, todo_update: TodoUpdate):
    """Update an existing todo"""
    try:
        # Get existing todo to check completion status
        existing_todo = todos_collection.find_one({"_id": ObjectId(todo_id)})

        if not existing_todo:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Todo not found"
            )

        update_dict = {k: v for k, v in todo_update.dict().items() if v is not None}

        if not update_dict:
            return serialize_todo(existing_todo)

        # Track completion
        if update_dict.get("completed") and not existing_todo.get("completed", False):
            todos_completed.inc()

        todos_collection.update_one({"_id": ObjectId(todo_id)}, {"$set": update_dict})

        updated_todo = todos_collection.find_one({"_id": ObjectId(todo_id)})
        await update_active_todos_count()

        return serialize_todo(updated_todo)
    except PyMongoError as e:
        db_errors.labels(operation="update").inc()
        logger.error(f"Error updating todo: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update todo",
        )
    except Exception as e:
        logger.error(f"Invalid todo ID: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid todo ID"
        )


@app.delete("/todos/{todo_id}")
async def delete_todo(todo_id: str):
    """Delete a todo"""
    try:
        result = todos_collection.delete_one({"_id": ObjectId(todo_id)})

        if result.deleted_count == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Todo not found"
            )

        todos_deleted.inc()
        await update_active_todos_count()

        return {"message": "Todo deleted"}
    except PyMongoError as e:
        db_errors.labels(operation="delete").inc()
        logger.error(f"Error deleting todo: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete todo",
        )
    except Exception as e:
        logger.error(f"Invalid todo ID: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid todo ID"
        )


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 3000))
    uvicorn.run(app, host="0.0.0.0", port=port)
