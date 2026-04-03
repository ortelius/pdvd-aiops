"""Tests for the Flask E2E app."""

import json
import pytest
from app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_index(client):
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json()["healthy"] is True


def test_echo(client):
    payload = {"name": "Widget", "price": 9.99}
    resp = client.post(
        "/echo",
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["echo"]["name"] == "Widget"
    assert data["echo"]["price"] == 9.99
