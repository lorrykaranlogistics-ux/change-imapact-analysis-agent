from app.services.dependency_parser import DependencyParser


def test_extract_service_calls_from_http_urls(tmp_path):
    svc_dir = tmp_path / "order-service" / "src"
    svc_dir.mkdir(parents=True)
    src_file = svc_dir / "controller.js"
    src_file.write_text(
        """
        const x = require('../../../shared/httpClient');
        const res = await request('order-service', 'POST', 'http://payment-service:3003/payments/authorize', {});
        """,
        encoding="utf-8",
    )

    parser = DependencyParser()
    dep_map, services = parser.parse_project(str(tmp_path))

    assert "order-service" in services
    key = "order-service/src/controller.js"
    assert key in dep_map
    assert "service://payment-service" in dep_map[key]
