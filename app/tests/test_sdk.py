from fastapi.testclient import TestClient

from app.main import app
from app.sdk.python_sdk.devplatform.client import DevPlatformClient


def test_sdk_flows() -> None:
    test_client = TestClient(app)

    class BoundClient(DevPlatformClient):
        def _request(self, method: str, path: str, **kwargs):
            response = test_client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()

    client = BoundClient(base_url="http://testserver")
    apps = client.list_apps()
    assert isinstance(apps, list)

    run = client.run_agent("agent-ops", {"question": "Summarize current system health"})
    assert run["status"] == "completed"