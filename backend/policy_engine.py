from typing import Any, Dict, List

POLICY_VERSION = "2.0"


def evaluate(mandate: Dict[str, Any], product: Dict[str, Any]) -> Dict[str, Any]:
    rules: List[Dict[str, Any]] = []
    approved = True

    # price
    price = product.get("price")
    max_price = mandate.get("max_price")
    r = {"rule": "max_price", "expected": max_price, "observed": price, "passed": False, "evidence": "offer.price"}
    if price is not None and max_price is not None and float(price) <= float(max_price):
        r["passed"] = True
    else:
        approved = False
    rules.append(r)

    # condition
    condition = (product.get("condition") or "").lower()
    allowed = [c.lower() for c in mandate.get("allowed_conditions", [])]
    forbidden = [c.lower() for c in mandate.get("forbidden_conditions", [])]
    r = {"rule": "condition", "expected": allowed, "observed": condition, "passed": False, "evidence": "product.condition"}
    if condition and condition not in forbidden and (not allowed or condition in allowed):
        r["passed"] = True
    else:
        approved = False
    rules.append(r)

    # explicit mandate constraints
    # size
    if "size" in mandate:
        r = {"rule": "size", "expected": mandate["size"], "observed": product.get("size"), "passed": False, "evidence": "product.size"}
        if product.get("size") is not None and str(product.get("size")) == str(mandate["size"]):
            r["passed"] = True
        else:
            approved = False
        rules.append(r)
    # material
    if "material" in mandate:
        r = {"rule": "material", "expected": mandate["material"], "observed": product.get("material"), "passed": False, "evidence": "product.material"}
        obs = (product.get("material") or "").lower()
        if mandate["material"].lower() in obs:
            r["passed"] = True
        else:
            approved = False
        rules.append(r)
    # composition
    if "material_percentage" in mandate:
        r = {"rule": "composition", "expected": mandate["material_percentage"], "observed": product.get("material_percentage"), "passed": False, "evidence": "product.material_percentage"}
        obs = product.get("material_percentage")
        if obs is not None and float(obs) >= float(mandate["material_percentage"]):
            r["passed"] = True
        else:
            approved = False
        rules.append(r)
    # style
    if "style" in mandate:
        r = {"rule": "style", "expected": mandate["style"], "observed": product.get("style"), "passed": False, "evidence": "product.style"}
        if product.get("style") == mandate["style"]:
            r["passed"] = True
        else:
            approved = False
        rules.append(r)
    # forbidden designs
    for fd in mandate.get("forbidden_designs", []):
        r = {"rule": f"forbidden_design_{fd}", "expected": f"not {fd}", "observed": product.get("design"), "passed": False, "evidence": "product.design"}
        design = (product.get("design") or "").lower()
        if fd.lower() not in design:
            r["passed"] = True
        else:
            approved = False
        rules.append(r)

    # power delivery semantics
    pd_mode = mandate.get("pd_mode", "PASS_THROUGH")
    requested = mandate.get("min_specs", {}).get("power_delivery_w")
    if requested is not None:
        pd_input = product.get("pd_input_max_w")
        host_output = product.get("host_output_max_w")
        pass_through = product.get("pd_pass_through_supported", False)
        r = {"rule": "power_delivery", "expected": requested, "observed": None, "passed": False, "evidence": "product.pd"}
        if pd_mode == "HOST_OUTPUT":
            r["observed"] = host_output
            if host_output is not None and float(host_output) >= float(requested):
                r["passed"] = True
            else:
                approved = False
        else:
            r["observed"] = pd_input
            if pd_input is not None and float(pd_input) >= float(requested) and pass_through:
                r["passed"] = True
            else:
                approved = False
        rules.append(r)

    # specs (excluding power_delivery handled above)
    for spec, expected in mandate.get("min_specs", {}).items():
        if spec == "power_delivery_w":
            continue
        observed = product.get("specs", {}).get(spec)
        r = {"rule": f"min_{spec}", "expected": expected, "observed": observed, "passed": False, "evidence": f"product.specs.{spec}"}
        if observed is not None and float(observed) >= float(expected):
            r["passed"] = True
        else:
            approved = False
        rules.append(r)

    return {"decision": "APPROVED" if approved else "REJECTED", "rules": rules, "policy_version": POLICY_VERSION}
