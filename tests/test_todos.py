import pytest
from fastapi.testclient import TestClient
from main import app, todos_collection, mongo_connections

client = TestClient(app)


@pytest.fixture(autouse=True)
def cleanup():
    """Clean up database before each test"""
    todos_collection.delete_many({})
    yield
    todos_collection.delete_many({})


def test_health_check():
    """Test health check endpoint"""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_get_empty_todos():
    """Test getting todos when database is empty"""
    response = client.get("/todos")
    assert response.status_code == 200
    assert response.json() == []


def test_create_todo():
    """Test creating a new todo"""
    todo_data = {"title": "Learn FastAPI", "description": "Study FastAPI documentation"}
    response = client.post("/todos", json=todo_data)
    assert response.status_code == 201

    data = response.json()
    assert data["title"] == "Learn FastAPI"
    assert data["description"] == "Study FastAPI documentation"
    assert data["completed"] == False
    assert "id" in data


def test_get_todos():
    """Test getting all todos"""
    # Create two todos
    client.post("/todos", json={"title": "Todo 1"})
    client.post("/todos", json={"title": "Todo 2"})

    response = client.get("/todos")
    assert response.status_code == 200
    assert len(response.json()) == 2


def test_update_todo():
    """Test updating a todo"""
    # Create a todo
    create_response = client.post("/todos", json={"title": "Original"})
    todo_id = create_response.json()["id"]

    # Update it
    update_data = {"title": "Updated", "completed": True}
    response = client.put(f"/todos/{todo_id}", json=update_data)
    assert response.status_code == 200

    data = response.json()
    assert data["title"] == "Updated"
    assert data["completed"] == True


def test_delete_todo():
    """Test deleting a todo"""
    # Create a todo
    create_response = client.post("/todos", json={"title": "To Delete"})
    todo_id = create_response.json()["id"]

    # Delete it
    response = client.delete(f"/todos/{todo_id}")
    assert response.status_code == 200
    assert response.json() == {"message": "Todo deleted"}

    # Verify it's deleted
    get_response = client.get("/todos")
    assert len(get_response.json()) == 0


def test_update_nonexistent_todo():
    """Test updating a non-existent todo"""
    response = client.put("/todos/123456789012345678901234", json={"title": "Updated"})
    assert response.status_code == 404


def test_delete_nonexistent_todo():
    """Test deleting a non-existent todo"""
    response = client.delete("/todos/123456789012345678901234")
    assert response.status_code == 404


def test_metrics_endpoint():
    """Test metrics endpoint"""
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "http_request_duration_seconds" in response.text
