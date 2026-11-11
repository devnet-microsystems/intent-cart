from typing import Any, Dict, List
import re

POLICY_VERSION = "2.0"


def _priority(mandate, rule_name):
    p = (mandate.get("constraint_priorities") or {}).get(rule_name)
    if p not in ("REQUIRED", "PREFERRED"):
        return "REQUIRED"
    return p


def _first(value):
    """Return the first element of a list-like value, or the value itself."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


def build_evidence(product):
    """Normalize a Channel3 product (or simplified test fixture) into the
    flat evidence dict consumed by evaluate(). Preserves source provenance
    for every derived field so rules can report where a value came from.

    Returns a new dict; the original product is never mutated.
    Unknown or ambiguous fields are omitted — never inferred.
    """
    if not isinstance(product, dict):
        return {}

    ev = {}

    # Pass through already-normalized flat fields (backwards compatibility
    # with test fixtures that pass price/condition/size directly).
    for key in ("price", "condition", "size", "material", "material_percentage",
                "style", "design", "specs", "variants", "pd_input_max_w", "host_output_max_w",
                "pd_pass_through_supported"):
        if key in product:
            ev[key] = product[key]

    # Record provenance of raw Channel3 fields so rules can cite their source.
    sa = product.get("structured_attributes")
    kf = product.get("key_features")
    materials = product.get("materials")
    title = product.get("title")
    brands = product.get("brands")

    ev["_provenance"] = {
        "structured_attributes": sa if isinstance(sa, dict) else {},
        "key_features": kf if isinstance(kf, list) else [],
        "materials": materials if isinstance(materials, list) else [],
        "title": title if isinstance(title, str) else "",
        "brands": [b.get("name") if isinstance(b, dict) else b for b in brands]
        if isinstance(brands, list) else [],
        "raw_specs": dict(product.get("specs")) if isinstance(product.get("specs"), dict) else (product.get("specs") if "specs" in product else None),
    }

    # Map Channel3 fields to flat evidence keys when the flat key is
    # missing or null. A derived value may fill a None flat field, but an
    # explicit non-null flat field always wins (backwards compatibility).
    if (ev.get("material") is None) and isinstance(materials, list) and materials:
        ev["material"] = _first(materials)
        ev["_provenance"]["material_source"] = "product.materials"

    if isinstance(sa, dict):
        if ev.get("material") is None and sa.get("material"):
            ev["material"] = _first(sa["material"])
            ev["_provenance"]["material_source"] = "product.structured_attributes.material"

        if "design" not in ev and sa.get("pattern"):
            ev["design"] = _first(sa["pattern"])
            ev["_provenance"]["design_source"] = "product.structured_attributes.pattern"

    # Laptop specification normalization (P1, evidence-only). Run on the
    # original Channel3 evidence so provenance points at real product paths.
    try:
        from backend.laptop_normalizer import normalize_laptop_specs
        normalized_laptop_specs = normalize_laptop_specs(product)
    except Exception:
        normalized_laptop_specs = {}
    ev["normalized_laptop_specs"] = normalized_laptop_specs

    # Policy-facing numeric compatibility for the generic min_specs mechanism.
    # Preserve unrelated existing specs/backward compatibility, but never let
    # conflicting or NOT_VERIFIED laptop numeric evidence pass through.
    policy_specs = dict(ev.get("specs")) if isinstance(ev.get("specs"), dict) else ({} if ev.get("specs") is None else {})
    for numeric_key in ("ram_gb", "storage_gb"):
        field = normalized_laptop_specs.get(numeric_key) if isinstance(normalized_laptop_specs, dict) else None
        if isinstance(field, dict) and field.get("status") == "VERIFIED" and field.get("value") is not None:
            policy_specs[numeric_key] = field["value"]
            ev["_provenance"][f"{numeric_key}_source"] = field.get("evidence_source")
            ev["_provenance"][f"{numeric_key}_excerpt"] = field.get("evidence_excerpt")
        elif isinstance(field, dict):
            # Missing/ambiguous/conflicting: suppress raw spec value so evaluate()
            # cannot approve via the generic min_specs path.
            policy_specs.pop(numeric_key, None)
            if field.get("evidence_source") or field.get("conflicts") or field.get("status") == "NOT_VERIFIED":
                ev["_provenance"][f"{numeric_key}_source"] = field.get("evidence_source")
                ev["_provenance"][f"{numeric_key}_excerpt"] = field.get("evidence_excerpt")
                if field.get("conflicts"):
                    ev["_provenance"][f"{numeric_key}_conflicts"] = field.get("conflicts")
    ev["specs"] = policy_specs

    # Color evidence extraction (product-level and variant-level).
    color_value = None
    color_sources: list[str] = []
    color_conflicts: list[str] = []

    flat_color = product.get("color")
    if flat_color is None:
        flat_color = product.get("colour")
    if flat_color is not None:
        color_value = flat_color
        color_sources.append("product.color")

    sa = product.get("structured_attributes")
    if isinstance(sa, dict):
        sa_color = sa.get("color") or sa.get("colour")
        if sa_color is not None:
            if color_value is None:
                color_value = sa_color
                color_sources.append("product.structured_attributes.color")
            elif str(sa_color).lower() != str(color_value).lower():
                color_conflicts.append(f"structured_attributes.color: {sa_color}")

    variants = product.get("variants")
    if isinstance(variants, list) and variants:
        variant_colors: list[str] = []
        for idx, v in enumerate(variants):
            if isinstance(v, dict):
                vc = v.get("color") or v.get("colour")
                if vc is not None:
                    variant_colors.append(str(vc))
                    if color_value is None:
                        color_value = vc
                        color_sources.append(f"product.variants[{idx}].color")
                    elif str(vc).lower() != str(color_value).lower():
                        color_conflicts.append(f"variants[{idx}].color: {vc}")
        unique_vc = set(c.lower() for c in variant_colors)
        if len(unique_vc) > 1:
            color_conflicts.append(f"variants.colors: {sorted(set(variant_colors))}")

    ev["color"] = color_value
    if color_sources:
        ev["_provenance"]["color_source"] = color_sources
    if color_conflicts:
        ev["_provenance"]["color_conflicts"] = color_conflicts
    # Preserve any pre-existing color conflicts carried on the input product.
    input_provenance = product.get("_provenance")
    input_conflicts = input_provenance.get("color_conflicts") if isinstance(input_provenance, dict) else None
    if input_conflicts:
        existing = ev["_provenance"].get("color_conflicts", [])
        ev["_provenance"]["color_conflicts"] = list(dict.fromkeys(existing + list(input_conflicts)))

    return ev


def build_product_evidence_payload(product, offer=None):
    """Build the Policy Gate evidence payload from a product (and optional offer).

    This is the single source of truth for evidence construction, used by both
    Channel3 search evaluation and authorize_purchase. Handles missing or
    non-dictionary product data safely by returning an empty dict.

    Preserves: price, condition, specs, title, description, brands,
    structured_attributes, key_features, materials, variants, category, color
    and provenance where available.
    """
    if not isinstance(product, dict):
        return {}

    payload = {}

    # Price from offer (preferred) or product-level price field.
    if isinstance(offer, dict):
        price_obj = offer.get("price")
        if isinstance(price_obj, dict):
            payload["price"] = price_obj.get("price")
        else:
            payload["price"] = offer.get("price")
    else:
        payload["price"] = product.get("price")

    # Condition from offer (preferred) or product.
    if isinstance(offer, dict):
        payload["condition"] = offer.get("condition") or product.get("condition")
    else:
        payload["condition"] = product.get("condition")

    # Specs.
    specs = product.get("specs")
    payload["specs"] = specs if isinstance(specs, dict) else ({} if specs is None else {})

    # Title / description.
    payload["title"] = product.get("title")
    payload["description"] = product.get("description")

    # Brands / structured_attributes / key_features / materials / variants / category.
    payload["brands"] = product.get("brands")
    payload["structured_attributes"] = product.get("structured_attributes")
    payload["key_features"] = product.get("key_features")
    payload["materials"] = product.get("materials")
    payload["variants"] = product.get("variants")
    payload["category"] = product.get("category")

    # Color.
    payload["color"] = product.get("color") or product.get("colour")

    # Provenance.
    payload["_provenance"] = product.get("_provenance") if isinstance(product.get("_provenance"), dict) else None

    return payload


def _laptop_rule(rule, expected, observed, passed, status, evidence, source, excerpt, conflicts=None, extra=None):
    r = {
        "rule": rule,
        "expected": expected,
        "observed": observed,
        "passed": passed,
        "status": status,
        "evidence": evidence,
        "evidence_source": source,
        "evidence_excerpt": excerpt,
        "priority": "REQUIRED",
    }
    if conflicts:
        r["conflicts"] = conflicts
    if extra:
        r.update(extra)
    return r


def _norm_token(value):
    if value is None:
        return ""
    return " ".join(str(value).strip().lower().split())


def _storage_interface_compatible(required, observed):
    req = _norm_token(required)
    obs = _norm_token(observed)
    if not req or not obs:
        return False
    # Explicitly justified equivalences only.
    if req == obs:
        return True
    req_nvme = "nvme" in req
    obs_nvme = "nvme" in obs
    req_pcie = "pcie" in req
    obs_pcie = "pcie" in obs
    req_sata = "sata" in req
    obs_sata = "sata" in obs
    # NVMe-family equivalence: NVMe <-> PCIe NVMe (and explicit NVMe wording).
    if req_sata or obs_sata:
        return req_sata and obs_sata and req_nvme == obs_nvme and req_pcie == obs_pcie
    if req_nvme and obs_nvme:
        return True
    return False


def _os_edition(os_name):
    n = _norm_token(os_name)
    if "windows 11 pro" in n or "win 11 pro" in n:
        return "windows_11_pro"
    if "windows 11 home" in n or "win 11 home" in n:
        return "windows_11_home"
    if "windows 11" in n or "win 11" in n:
        return "windows_11_generic"
    if "windows 10 pro" in n:
        return "windows_10_pro"
    if "windows 10 home" in n:
        return "windows_10_home"
    if "windows 10" in n:
        return "windows_10_generic"
    if "chrome os" in n or "chromeos" in n:
        return "chromeos"
    if "macos" in n or "mac os" in n:
        return "macos"
    if "linux" in n or "ubuntu" in n or "freedos" in n or n == "dos":
        return "linux_like"
    return n


def _os_compatible(required, observed):
    req = _os_edition(required)
    obs = _os_edition(observed)
    if not req or not obs:
        return False
    # Generic "Windows" requirement accepts explicitly verified Windows 10/11
    # editions (Home/Pro/generic). It never accepts Linux, macOS, ChromeOS, etc.
    if req == "windows":
        return obs in (
            "windows_10_generic", "windows_10_home", "windows_10_pro",
            "windows_11_generic", "windows_11_home", "windows_11_pro",
        )
    # Generic Windows 11 requirement may accept verified Win11 Pro/Home edition.
    if req == "windows_11_generic":
        return obs in ("windows_11_generic", "windows_11_home", "windows_11_pro")
    if req == "windows_10_generic":
        return obs in ("windows_10_generic", "windows_10_home", "windows_10_pro")
    # Specific edition requirement must match that edition (Pro must not accept Home).
    return req == obs


def _cpu_field(normalized):
    return (normalized or {}).get("processor_family") or {}


def _cpu_match(required_family, required_subfamily, required_model, cpu):
    if not isinstance(cpu, dict):
        return None, "insufficient_evidence", {}
    status = cpu.get("status")
    hierarchy = cpu.get("cpu_hierarchy") or {}
    obs = {
        "family": hierarchy.get("family") or cpu.get("value"),
        "subfamily": hierarchy.get("subfamily"),
        "model": hierarchy.get("model"),
        "raw": cpu.get("value"),
    }
    if status != "VERIFIED":
        return None, "insufficient_evidence", obs
    fam = _norm_token(obs.get("family"))
    sub = _norm_token(obs.get("subfamily"))
    model = _norm_token(obs.get("model"))
    raw = _norm_token(obs.get("raw"))
    if required_model:
        req = _norm_token(required_model)
        # Generic family never satisfies exact model; need verified exact model evidence.
        if model == req or raw == req:
            return True, "matched_model", obs
        if fam and fam != req and sub != req and model and model != req:
            # If verified hierarchy/family is incompatible with requested model's implied text? be conservative.
            pass
        # verified but requested exact model not evidenced
        if not model:
            return None, "insufficient_evidence", obs
        return False, "incompatible_model", obs
    if required_subfamily:
        req = _norm_token(required_subfamily)
        if sub == req or model == req or model.startswith(req + " ") or raw == req:
            return True, "matched_subfamily", obs
        # more-specific verified family but different subfamily?
        if fam and req.startswith(fam) and sub and sub != req:
            return False, "incompatible_subfamily", obs
        if sub and not req.startswith(sub) and sub != req:
            return False, "incompatible_subfamily", obs
        if fam and not req.startswith(fam):
            return False, "incompatible_subfamily", obs
        return None, "insufficient_evidence", obs
    if required_family:
        req = _norm_token(required_family)
        if fam == req or sub.startswith(req) or model.startswith(req) or raw == req or raw.startswith(req):
            return True, "matched_family", obs
        # verified CPU but different family
        if fam and fam != req and not fam.startswith(req + " "):
            return False, "incompatible_family", obs
        if fam:
            return None, "insufficient_evidence", obs
        return False, "incompatible_family", obs
    return True, "not_required", obs


def _evaluate_laptop_rules(mandate, product):
    rules = []
    if mandate.get("product_type") != "laptop":
        return rules
    normalized = product.get("normalized_laptop_specs") or {}
    classification = normalized.get("product_classification") or {}
    cval = classification.get("value")
    cstatus = classification.get("status")
    csource = classification.get("evidence_source")
    cexcerpt = classification.get("evidence_excerpt")
    cconflicts = classification.get("conflicts")
    if cstatus == "VERIFIED" and cval == "NOTEBOOK":
        rules.append(_laptop_rule(
            "laptop_product_classification", "NOTEBOOK", "NOTEBOOK", True, "APPLICABLE",
            "product.product_classification", csource, cexcerpt, cconflicts,
        ))
    elif cstatus == "VERIFIED" and cval in ("DESKTOP", "ACCESSORY"):
        rules.append(_laptop_rule(
            "laptop_product_classification", "NOTEBOOK", cval, False, "APPLICABLE",
            "product.product_classification", csource, cexcerpt, cconflicts,
        ))
    else:
        # UNKNOWN / NOT_VERIFIED / missing -> NEEDS_CONFIRMATION
        rules.append(_laptop_rule(
            "laptop_product_classification", "NOTEBOOK", None, False, "APPLICABLE",
            "product.product_classification", csource, cexcerpt or cval, cconflicts,
            extra={"observed_classification": cval},
        ))

    # RAM / storage: conjunctive evaluation of generic min_specs and new laptop fields.
    specs = product.get("specs") if isinstance(product.get("specs"), dict) else {}
    prov = product.get("_provenance") if isinstance(product.get("_provenance"), dict) else {}
    for attr, laptop_key in (("ram_gb", "min_ram_gb"), ("storage_gb", "min_storage_gb")):
        thresholds = []
        if (mandate.get("min_specs") or {}).get(attr) is not None:
            thresholds.append(("min_specs", mandate["min_specs"][attr]))
        if mandate.get(laptop_key) is not None:
            thresholds.append((laptop_key, mandate[laptop_key]))
        if not thresholds:
            continue
        observed = specs.get(attr)
        field = normalized.get(attr) or {}
        source = field.get("evidence_source") or prov.get(f"{attr}_source") or f"product.specs.{attr}"
        excerpt = field.get("evidence_excerpt") if field.get("evidence_excerpt") is not None else prov.get(f"{attr}_excerpt")
        conflicts = field.get("conflicts") or prov.get(f"{attr}_conflicts")
        for origin, expected in thresholds:
            extra = {"requirement_origin": origin, "all_requirement_thresholds": {o: v for o, v in thresholds}}
            if observed is not None:
                try:
                    passed = float(observed) >= float(expected)
                except Exception:
                    passed = False
                rules.append(_laptop_rule(
                    f"min_{attr}" if origin == "min_specs" else laptop_key,
                    expected, observed, passed, "APPLICABLE",
                    f"product.specs.{attr}", source, excerpt, conflicts, extra,
                ))
            else:
                rules.append(_laptop_rule(
                    f"min_{attr}" if origin == "min_specs" else laptop_key,
                    expected, None, False, "APPLICABLE",
                    f"product.specs.{attr}", source, excerpt, conflicts, extra,
                ))

    # Storage interface
    if mandate.get("required_storage_interface") is not None:
        req = mandate.get("required_storage_interface")
        field = normalized.get("storage_interface") or {}
        obs = field.get("value") if field.get("status") == "VERIFIED" else None
        if field.get("status") == "VERIFIED" and obs is not None:
            passed = _storage_interface_compatible(req, obs)
            rules.append(_laptop_rule(
                "required_storage_interface", req, obs, passed, "APPLICABLE",
                "product.storage_interface", field.get("evidence_source"), field.get("evidence_excerpt"), field.get("conflicts"),
            ))
        else:
            rules.append(_laptop_rule(
                "required_storage_interface", req, None, False, "APPLICABLE",
                "product.storage_interface", field.get("evidence_source"), field.get("evidence_excerpt") or field.get("value"), field.get("conflicts"),
                extra={"observed_detail": field.get("value")},
            ))

    # Processor hierarchy
    r_fam = mandate.get("required_processor_family")
    r_sub = mandate.get("required_processor_subfamily")
    r_model = mandate.get("required_processor_model")
    if r_fam is not None or r_sub is not None or r_model is not None:
        cpu = _cpu_field(normalized)
        match, reason, obs = _cpu_match(r_fam, r_sub, r_model, cpu)
        expected = {"family": r_fam, "subfamily": r_sub, "model": r_model}
        # GPU evidence must never satisfy or contradict CPU requirements.
        cpu_conflicts = cpu.get("conflicts")
        extra = {"cpu_hierarchy": cpu.get("cpu_hierarchy"), "gpu_evidence_ignored": cpu.get("gpu_evidence"), "match_reason": reason}
        if match is True:
            rules.append(_laptop_rule(
                "required_processor", expected, obs, True, "APPLICABLE",
                "product.processor_family", cpu.get("evidence_source"), cpu.get("evidence_excerpt"), cpu_conflicts, extra,
            ))
        elif match is False:
            rules.append(_laptop_rule(
                "required_processor", expected, obs, False, "APPLICABLE",
                "product.processor_family", cpu.get("evidence_source"), cpu.get("evidence_excerpt"), cpu_conflicts, extra,
            ))
        else:
            rules.append(_laptop_rule(
                "required_processor", expected, None, False, "APPLICABLE",
                "product.processor_family", cpu.get("evidence_source"), cpu.get("evidence_excerpt"), cpu_conflicts, {**extra, "observed_detail": obs},
            ))

    # Operating system
    if mandate.get("required_operating_system") is not None:
        req = mandate.get("required_operating_system")
        field = normalized.get("operating_system") or {}
        if field.get("status") == "VERIFIED" and field.get("value") is not None:
            passed = _os_compatible(req, field.get("value"))
            rules.append(_laptop_rule(
                "required_operating_system", req, field.get("value"), passed, "APPLICABLE",
                "product.operating_system", field.get("evidence_source"), field.get("evidence_excerpt"), field.get("conflicts"),
            ))
        else:
            rules.append(_laptop_rule(
                "required_operating_system", req, None, False, "APPLICABLE",
                "product.operating_system", field.get("evidence_source"), field.get("evidence_excerpt") or field.get("value"), field.get("conflicts"),
                extra={"observed_detail": field.get("value")},
            ))
    return rules


def evaluate(mandate: Dict[str, Any], product: Dict[str, Any]) -> Dict[str, Any]:
    # Normalize the incoming product through the evidence adapter so that
    # Channel3 fields (structured_attributes, key_features, materials, …)
    # are available to every rule with provenance recorded. Do not mutate
    # the original mandate or persisted objects.
    product = build_evidence(product)
    rules: List[Dict[str, Any]] = []
    approved = True

    # price
    price = product.get("price")
    max_price = mandate.get("max_price")
    if max_price is None:
        r = {"rule": "max_price", "expected": None, "observed": price, "passed": True, "status": "NOT_APPLICABLE", "evidence": "offer.price", "evidence_source": "offer.price", "evidence_excerpt": None}
        rules.append(r)
    else:
        r = {"rule": "max_price", "expected": max_price, "observed": price, "passed": False, "status": "APPLICABLE", "evidence": "offer.price", "evidence_source": "offer.price", "evidence_excerpt": str(price) if price is not None else None}
        if price is not None and float(price) <= float(max_price):
            r["passed"] = True
        else:
            approved = False
        rules.append(r)

    # condition
    condition = (product.get("condition") or "").lower()
    allowed = [c.lower() for c in (mandate.get("allowed_conditions") or [])]
    forbidden = [c.lower() for c in (mandate.get("forbidden_conditions") or [])]
    raw_condition = product.get("condition")
    r = {"rule": "condition", "expected": allowed, "observed": condition, "passed": False, "status": "APPLICABLE", "evidence": "product.condition", "evidence_source": "offer.condition", "evidence_excerpt": raw_condition if isinstance(raw_condition, str) else None}
    if condition and condition not in forbidden and (not allowed or condition in allowed):
        r["passed"] = True
    else:
        approved = False
    rules.append(r)

    # explicit mandate constraints
    # size
    if mandate.get("size") is not None:
        observed_size = product.get("size")
        r = {"rule": "size", "expected": mandate["size"], "observed": observed_size, "passed": False, "status": "APPLICABLE", "evidence": "product.size", "evidence_source": "product.structured_attributes.size", "evidence_excerpt": str(observed_size) if observed_size is not None else None}
        if observed_size is not None and str(observed_size) == str(mandate["size"]):
            r["passed"] = True
        else:
            approved = False
        rules.append(r)
    # material
    if mandate.get("material") is not None:
        observed_material = product.get("material")
        obs = (observed_material or "").lower()
        r = {"rule": "material", "expected": mandate["material"], "observed": observed_material, "passed": False, "status": "APPLICABLE", "evidence": "product.material", "evidence_source": product.get("_provenance", {}).get("material_source", "product.material"), "evidence_excerpt": str(observed_material) if observed_material is not None else None}
        if mandate["material"].lower() in obs:
            r["passed"] = True
        else:
            approved = False
        rules.append(r)
    # composition
    if mandate.get("material_percentage") is not None:
        observed_pct = product.get("material_percentage")
        r = {"rule": "composition", "expected": mandate["material_percentage"], "observed": observed_pct, "passed": False, "status": "APPLICABLE", "evidence": "product.material_percentage", "evidence_source": "product.material_percentage", "evidence_excerpt": str(observed_pct) if observed_pct is not None else None}
        if observed_pct is not None and float(observed_pct) >= float(mandate["material_percentage"]):
            r["passed"] = True
        else:
            approved = False
        rules.append(r)
    # style
    if mandate.get("style") is not None:
        observed_style = product.get("style")
        r = {"rule": "style", "expected": mandate["style"], "observed": observed_style, "passed": False, "status": "APPLICABLE", "evidence": "product.style", "evidence_source": "product.structured_attributes.style", "evidence_excerpt": str(observed_style) if observed_style is not None else None}
        if observed_style == mandate["style"]:
            r["passed"] = True
        else:
            approved = False
        rules.append(r)
    # forbidden designs
    for fd in (mandate.get("forbidden_designs") or []):
        observed_design = product.get("design")
        design = (observed_design or "").lower()
        r = {"rule": f"forbidden_design_{fd}", "expected": f"not {fd}", "observed": observed_design, "passed": False, "status": "APPLICABLE", "evidence": "product.design", "evidence_source": product.get("_provenance", {}).get("design_source", "product.design"), "evidence_excerpt": str(observed_design) if observed_design is not None else None}
        if design and fd.lower() not in design:
            r["passed"] = True
        else:
            approved = False
        rules.append(r)

    # color
    required_color = mandate.get("color")
    if required_color is not None:
        observed_color = product.get("color")
        color_conflicts = product.get("_provenance", {}).get("color_conflicts")
        color_source = product.get("_provenance", {}).get("color_source")

        # Variant-aware matching: if both size and color are required,
        # the same variant must satisfy both.
        variants = product.get("variants")
        required_size = mandate.get("size")
        if isinstance(variants, list) and variants:
            matching_variant = None
            for v in variants:
                if isinstance(v, dict):
                    v_color = v.get("color") or v.get("colour")
                    v_size = v.get("size")
                    color_match = v_color is not None and str(v_color).lower() == str(required_color).lower()
                    size_match = required_size is None or (
                        v_size is not None and str(v_size) == str(required_size)
                    )
                    if color_match and size_match:
                        matching_variant = v
                        break
            if matching_variant is not None:
                observed_color = matching_variant.get("color") or matching_variant.get("colour")
                color_conflicts = None
            else:
                # Variants exist but none matches both size and color.
                observed_color = None

        if color_conflicts:
            r = {
                "rule": "color",
                "expected": required_color,
                "observed": None,
                "passed": False,
                "status": "APPLICABLE",
                "evidence": "product.color",
                "evidence_source": color_source,
                "evidence_excerpt": str(observed_color) if observed_color else None,
                "conflicts": color_conflicts,
            }
            approved = False
        elif observed_color is None:
            r = {
                "rule": "color",
                "expected": required_color,
                "observed": None,
                "passed": False,
                "status": "APPLICABLE",
                "evidence": "product.color",
                "evidence_source": color_source,
                "evidence_excerpt": None,
            }
        elif str(observed_color).lower() == str(required_color).lower():
            r = {
                "rule": "color",
                "expected": required_color,
                "observed": observed_color,
                "passed": True,
                "status": "APPLICABLE",
                "evidence": "product.color",
                "evidence_source": color_source,
                "evidence_excerpt": str(observed_color),
            }
        else:
            r = {
                "rule": "color",
                "expected": required_color,
                "observed": observed_color,
                "passed": False,
                "status": "APPLICABLE",
                "evidence": "product.color",
                "evidence_source": color_source,
                "evidence_excerpt": str(observed_color),
            }
            approved = False
        rules.append(r)

    # power delivery semantics
    pd_mode = mandate.get("pd_mode", "PASS_THROUGH")
    requested = mandate.get("min_specs", {}).get("power_delivery_w")
    if requested is not None:
        pd_input = product.get("pd_input_max_w")
        host_output = product.get("host_output_max_w")
        pass_through = product.get("pd_pass_through_supported", False)
        r = {"rule": "power_delivery", "expected": requested, "observed": None, "passed": False, "status": "APPLICABLE", "evidence": "product.pd"}
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
    laptop_mandate = mandate.get("product_type") == "laptop"
    for spec, expected in (mandate.get("min_specs") or {}).items():
        if spec == "power_delivery_w":
            continue
        # Laptop mandates evaluate ram_gb/storage_gb conjunctively (min_specs +
        # laptop fields) in _evaluate_laptop_rules to preserve both requirement
        # origins/diagnostics without mutating the mandate.
        if laptop_mandate and spec in ("ram_gb", "storage_gb"):
            continue
        observed = product.get("specs", {}).get(spec)
        r = {"rule": f"min_{spec}", "expected": expected, "observed": observed, "passed": False, "status": "APPLICABLE", "evidence": f"product.specs.{spec}"}
        if observed is not None and float(observed) >= float(expected):
            r["passed"] = True
        else:
            approved = False
        rules.append(r)

    # Laptop vertical (P1 Step 4B): mandatory classification + conditional
    # laptop constraints. Activated only for product_type=="laptop".
    rules.extend(_evaluate_laptop_rules(mandate, product))


    for r in rules:
        if "priority" not in r:
            r["priority"] = _priority(mandate, r["rule"].replace("min_", "").replace("forbidden_design_", "forbidden_designs"))

    has_fail = any((not r.get("passed", False)) and r.get("status") != "NOT_APPLICABLE" and r.get("observed") not in (None, "") and r.get("priority") == "REQUIRED" for r in rules)
    has_unverified = any((not r.get("passed", False)) and r.get("status") != "NOT_APPLICABLE" and (r.get("observed") is None or r.get("observed") == "") and r.get("priority") == "REQUIRED" for r in rules)
    if not rules:
        decision = "NEEDS_CONFIRMATION"
    elif has_fail:
        decision = "REJECTED"
    elif has_unverified:
        decision = "NEEDS_CONFIRMATION"
    else:
        decision = "APPROVED"
    result = {"decision": decision, "rules": rules, "policy_version": POLICY_VERSION}
    if not rules:
        result["no_evaluable_rules"] = True
    return result
