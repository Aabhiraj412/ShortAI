from fastapi import Depends
from fastapi.testclient import TestClient

from backend.app import main as main_module


def _register_loopback_probe():
    async def probe(_: None = Depends(main_module.require_local_control)):
        return {"authorized": True}

    main_module.app.add_api_route(
        "/api/test/local-control/probe",
        probe,
        methods=["POST"],
        include_in_schema=False,
    )


def test_loopback_dashboard_controls_require_no_login_token_or_session():
    _register_loopback_probe()
    client = TestClient(
        main_module.app,
        base_url="http://127.0.0.1:8000",
        client=("127.0.0.1", 50000),
    )

    probe = client.post("/api/test/local-control/probe", json={})
    dashboard = client.get("/dashboard")
    legacy_session = client.get("/api/local-session")

    assert probe.status_code == 200
    assert probe.json() == {"authorized": True}
    assert dashboard.status_code == 200
    assert legacy_session.status_code == 404


def test_non_loopback_requests_to_control_routes_are_rejected():
    _register_loopback_probe()
    client = TestClient(
        main_module.app,
        base_url="http://127.0.0.1:8000",
        client=("198.51.100.24", 50000),
    )

    denied = client.post("/api/test/local-control/probe", json={})

    assert denied.status_code == 403
