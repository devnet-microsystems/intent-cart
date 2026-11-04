from backend.policy_engine import evaluate

def test_pass_through_approved():
    mandate = {"max_price": 90, "allowed_conditions": ["new"], "forbidden_conditions": [], "min_specs": {"power_delivery_w": 100}, "pd_mode": "PASS_THROUGH"}
    product = {"price": 79.95, "condition": "new", "pd_input_max_w": 100, "host_output_max_w": None, "pd_pass_through_supported": True, "specs": {}}
    assert evaluate(mandate, product)["decision"] == "APPROVED"

def test_host_output_rejects_unknown():
    mandate = {"max_price": 90, "allowed_conditions": ["new"], "forbidden_conditions": [], "min_specs": {"power_delivery_w": 100}, "pd_mode": "HOST_OUTPUT"}
    product = {"price": 79.95, "condition": "new", "pd_input_max_w": 100, "host_output_max_w": None, "pd_pass_through_supported": True, "specs": {}}
    assert evaluate(mandate, product)["decision"] == "REJECTED"

def test_host_output_rejects_85w():
    mandate = {"max_price": 90, "allowed_conditions": ["new"], "forbidden_conditions": [], "min_specs": {"power_delivery_w": 100}, "pd_mode": "HOST_OUTPUT"}
    product = {"price": 79.99, "condition": "new", "pd_input_max_w": 100, "host_output_max_w": 85, "pd_pass_through_supported": True, "specs": {}}
    assert evaluate(mandate, product)["decision"] == "REJECTED"
