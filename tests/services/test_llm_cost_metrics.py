from app.services.metrics_service import MetricsService


def test_record_llm_cost_usd():
    svc = MetricsService()
    svc.record_llm_cost_usd(0.002, tenant_id="t1", user_id="u1")
    svc.record_llm_cost_usd(0.003, tenant_id="t1", user_id="u1")
