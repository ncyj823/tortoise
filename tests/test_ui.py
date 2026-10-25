from fastapi.testclient import TestClient

from agent_team.main import app


def test_ui_page_is_served():
    r = TestClient(app).get("/ui")
    assert r.status_code == 200
    assert "<title>Tortoise, demo</title>" in r.text
    assert "Nothing is published. Every item is a draft for human approval." in r.text
    assert "Sample data is fictional (Brightleaf Coffee). Nothing is ever published; every item is a draft for human approval." in r.text
    assert "Generate package" in r.text
    assert "Tidier" in r.text


def test_home_still_serves_the_full_app():
    r = TestClient(app).get("/")
    assert r.status_code == 200
    assert "Sign in with your work email." in r.text
