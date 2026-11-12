from typing import Any, Dict, List, Optional
import hashlib
import json
import re

POLICY_VERSION = "2.0"


# Pattern descriptors that describe a color fill or weave but do NOT prove the
# absence of logos, branding, or other forbidden design elements. A product
# with design="Solid" may still carry a visible logo; such values must not
# trigger an auto-PASS for any forbidden_design rule.
_NON_INFORMATIVE_PATTERNS = {
    "solid", "plain", "heather", "heathered", "self", "tonal",
    "monochrome", "self-color", "self colour",
    "texture", "plaid", "striped", "patterned",
}


def hex_is_near(hex_value: str, target_name: str) -> bool:
    """Conservative chromatic check: does a hex value look like the target color?

    Only well-established near-equivalences are accepted. Unknown hex values
    return False — never guess.
    """
    if not hex_value or not isinstance(hex_value, str):
        return False
    h = hex_value.strip().lstrip("#").lower()
    if len(h) not in (3, 6):
        return False
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        r = int(h[0:2], 16)
        g = int(h[2:4], 16)
        b = int(h[4:6], 16)
    except ValueError:
        return False
    target = target_name.strip().lower()
    if target == "black":
        return r < 40 and g < 40 and b < 40
    if target == "grey" or target == "gray":
        return abs(r - g) < 25 and abs(g - b) < 25 and abs(r - b) < 25 and 40 <= r < 200
    if target == "blue":
        return b > 100 and b > r + 40 and b > g + 40
    return False


def canonical_fingerprint(obj) -> str:
    raw = json.dumps(obj, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# Backward-compatible alias for internal callers.
_fingerprint = canonical_fingerprint


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
                "style", "design", "pack_quantity", "pack_unit", "currency",
                "availability",
                "specs", "variants",
                "pd_input_max_w", "host_output_max_w",
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

    # Size evidence extraction: flat field first, then structured_attributes,
    # then variants. An explicit non-null flat field always wins.
    sa = product.get("structured_attributes")
    size_value = product.get("size")
    size_sources: list[str] = []
    if size_value is not None:
        size_sources.append("product.size")

    if isinstance(sa, dict) and size_value is None:
        for sk in ("size", "size-eu", "size-eu-eur", "size_eu", "size_eu_eur"):
            if sa.get(sk) is not None:
                size_value = sa[sk]
                size_sources.append(f"product.structured_attributes.{sk}")
                break

    if size_value is not None:
        ev["size"] = size_value
        ev["_provenance"]["size_source"] = size_sources

    # Style evidence extraction: flat field first, then structured_attributes.
    style_value = product.get("style")
    style_sources: list[str] = []
    if style_value is not None:
        style_sources.append("product.style")

    if isinstance(sa, dict) and style_value is None and sa.get("style") is not None:
        style_value = sa["style"]
        style_sources.append("product.structured_attributes.style")

    if style_value is not None:
        ev["style"] = style_value
        ev["_provenance"]["style_source"] = style_sources

    # Style from explicit text evidence (title/description/key_features)
    # when not already determined from flat field or structured_attributes.
    if ev.get("style") is None:
        text_parts = []
        title = product.get("title")
        if isinstance(title, str):
            text_parts.append(title)
        description = product.get("description")
        if isinstance(description, str):
            text_parts.append(description)
        key_features = product.get("key_features")
        if isinstance(key_features, list):
            text_parts.extend(kf for kf in key_features if isinstance(kf, str))
        text = " ".join(text_parts)
        style_from_text = _extract_style_from_text(text)
        if style_from_text is not None:
            ev["style"] = style_from_text

    # Color — extract from all Channel3 sources with hex→name normalization.
    raw_color = product.get("color") or product.get("colour")
    color_prov = {"original": raw_color, "normalized": None, "method": None, "sources": []}
    color_value = None

    if isinstance(raw_color, str) and raw_color.strip():
        color_value = raw_color.strip()
        color_prov["normalized"] = color_value
        color_prov["method"] = "product.color"
        color_prov["sources"].append("product.color")

    # Channel3 colors[] array with hex codes.
    colors = product.get("colors")
    if isinstance(colors, list) and colors:
        first = colors[0]
        if isinstance(first, dict) and first.get("hex"):
            hex_val = first["hex"]
            color_prov["sources"].append(f"product.colors[0].hex={hex_val}")
            if not color_value:
                color_value = hex_val
                color_prov["normalized"] = hex_val
                color_prov["method"] = "product.colors[0].hex"
        elif isinstance(first, str):
            color_prov["sources"].append(f"product.colors[0]={first}")
            if not color_value:
                color_value = first
                color_prov["normalized"] = first
                color_prov["method"] = "product.colors[0]"

    # structured_attributes color.
    sa = product.get("structured_attributes")
    if isinstance(sa, dict):
        for sk in ("color", "colour"):
            if sa.get(sk) and not color_value:
                color_value = sa[sk]
                color_prov["normalized"] = color_value
                color_prov["method"] = f"product.structured_attributes.{sk}"
                color_prov["sources"].append(color_prov["method"])
                break

    # variants.selected color (Channel3 detail format).
    variants = product.get("variants")
    if isinstance(variants, dict):
        selected = variants.get("selected") or []
        if isinstance(selected, list):
            for sel in selected:
                if isinstance(sel, dict) and sel.get("name") and sel.get("label"):
                    if str(sel["name"]).strip().lower() in ("color", "colour"):
                        color_prov["sources"].append(f"product.variants.selected.{sel['name']}={sel['label']}")
                        if not color_value:
                            color_value = sel["label"]
                            color_prov["normalized"] = sel["label"]
                            color_prov["method"] = f"product.variants.selected.{sel['name']}"
                        break

    # Hex → name normalization.
    if color_value and isinstance(color_value, str):
        stripped = color_value.strip().lstrip("#")
        if len(stripped) in (3, 6):
            for name in ("black", "white", "red", "blue", "green", "yellow",
                         "orange", "purple", "pink", "brown", "gray", "grey",
                         "navy"):
                if hex_is_near(color_value, name):
                    color_prov["normalized"] = name
                    color_prov["method"] = color_prov["method"] or "hex_to_color_name"
                    break

    ev["color"] = color_prov["normalized"] if color_prov["normalized"] else ev.get("color")
    ev["_color_provenance"] = color_prov

    # Size provenance.
    raw_size = ev.get("size") or product.get("size")
    size_prov = {"original": raw_size, "normalized": None, "method": None, "sources": []}
    if raw_size and isinstance(raw_size, str):
        size_prov["normalized"] = raw_size.strip()
        size_prov["method"] = "product.size"
        size_prov["sources"].append("product.size")

    variants_for_size = product.get("variants")
    if isinstance(variants_for_size, dict):
        selected = variants_for_size.get("selected") or []
        if isinstance(selected, list):
            for sel in selected:
                if isinstance(sel, dict) and sel.get("name") and sel.get("label"):
                    if str(sel["name"]).strip().lower() in ("size", "taglia"):
                        size_prov["normalized"] = str(sel["label"]).strip()
                        size_prov["method"] = f"product.variants.selected.{sel['name']}"
                        size_prov["sources"].append(f"product.variants.selected.size={sel['label']}")
                        break

    if size_prov["normalized"]:
        ev["size"] = size_prov["normalized"]
    ev["_size_provenance"] = size_prov

    # Pack quantity provenance.
    raw_pq = product.get("pack_quantity")
    pq_prov = {"original": raw_pq, "normalized": None, "method": None, "sources": []}
    pq_value = None
    pq_unit = product.get("pack_unit")
    if isinstance(raw_pq, (int, float)) and not isinstance(raw_pq, bool):
        pq_value = int(raw_pq)
        pq_prov["normalized"] = pq_value
        pq_prov["method"] = "product.pack_quantity"
        pq_prov["sources"].append("product.pack_quantity")

    if isinstance(variants_for_size, dict):
        selected = variants_for_size.get("selected") or []
        if isinstance(selected, list):
            for sel in selected:
                if isinstance(sel, dict) and sel.get("name") and sel.get("label"):
                    name_l = str(sel["name"]).strip().lower()
                    if name_l in ("pack count", "pack_quantity", "pack", "quantity"):
                        import re as _re
                        m = _re.match(r"\s*(\d+)\s*", str(sel["label"]))
                        if m:
                            pq_prov["sources"].append(f"product.variants.selected.{sel['name']}={sel['label']}")
                            if pq_value is None:
                                pq_value = int(m.group(1))
                                pq_prov["normalized"] = pq_value
                                pq_prov["method"] = f"product.variants.selected.{sel['name']}"
                                pq_unit = pq_unit or str(sel["label"]).split()[0] if str(sel["label"]).split() else None

    if pq_value is not None:
        ev["pack_quantity"] = pq_value
    if pq_unit:
        ev["pack_unit"] = pq_unit
    ev["_pack_quantity_provenance"] = pq_prov

    # Pack quantity from explicit text evidence (title/description) when not
    # already determined from flat field or variants.selected.
    if ev.get("pack_quantity") is None:
        text_parts = []
        title = product.get("title")
        if isinstance(title, str):
            text_parts.append(title)
        description = product.get("description")
        if isinstance(description, str):
            text_parts.append(description)
        text = " ".join(text_parts)
        pq_from_text = _extract_pack_quantity_from_text(text)
        if pq_from_text is not None:
            ev["pack_quantity"] = pq_from_text
            ev["_pack_quantity_provenance"] = {
                "original": text,
                "normalized": pq_from_text,
                "method": "product.title_or_description_text",
                "sources": ["product.title", "product.description"],
            }

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
    # Always preserve the offer's currency — never assume the mandate currency.
    offer_currency = None
    if isinstance(offer, dict):
        price_obj = offer.get("price")
        if isinstance(price_obj, dict):
            payload["price"] = price_obj.get("price")
            offer_currency = price_obj.get("currency")
        else:
            payload["price"] = offer.get("price")
            offer_currency = offer.get("currency")
    else:
        payload["price"] = product.get("price")
        offer_currency = product.get("currency")

    # Currency from offer (preferred) or product, never defaulted to mandate currency.
    payload["currency"] = offer_currency

    # Condition from offer (preferred) or product.
    if isinstance(offer, dict):
        payload["condition"] = offer.get("condition") or product.get("condition")
    else:
        payload["condition"] = product.get("condition")

    # Material from product-level materials array or flat field.
    materials = product.get("materials")
    if isinstance(materials, list) and materials:
        payload["material"] = materials[0]
    else:
        payload["material"] = product.get("material")
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

    # Color — extract from all Channel3 sources, never assume flat field.
    raw_color = product.get("color") or product.get("colour")
    color_sources = []
    color_value = raw_color
    color_method = None
    color_original = raw_color

    if isinstance(raw_color, str) and raw_color.strip():
        color_value = raw_color.strip()
        color_method = "product.color"
        color_sources.append("product.color")

    # Channel3 colors[] array with hex codes.
    colors = product.get("colors")
    if isinstance(colors, list) and colors:
        first = colors[0]
        if isinstance(first, dict) and first.get("hex"):
            hex_val = first["hex"]
            color_sources.append(f"product.colors[0].hex={hex_val}")
            color_original = hex_val
            if not color_value:
                color_value = hex_val
                color_method = "product.colors[0].hex"
        elif isinstance(first, str):
            color_sources.append(f"product.colors[0]={first}")
            if not color_value:
                color_value = first
                color_method = "product.colors[0]"

    # structured_attributes color.
    sa = product.get("structured_attributes")
    if isinstance(sa, dict):
        for sk in ("color", "colour"):
            if sa.get(sk) and not color_value:
                color_value = sa[sk]
                color_method = f"product.structured_attributes.{sk}"
                color_sources.append(color_method)
                break

    # variants.selected / variants.options color.
    variants = product.get("variants")
    if isinstance(variants, dict):
        selected = variants.get("selected") or []
        if isinstance(selected, list):
            for sel in selected:
                if isinstance(sel, dict) and sel.get("name") and sel.get("label"):
                    if str(sel["name"]).strip().lower() in ("color", "colour"):
                        color_sources.append(f"product.variants.selected.{sel['name']}={sel['label']}")
                        if not color_value:
                            color_value = sel["label"]
                            color_method = f"product.variants.selected.{sel['name']}"
                        break

    # Normalize hex → color name when we have a hex but no name.
    normalized_color = color_value
    if color_value and isinstance(color_value, str):
        stripped = color_value.strip().lstrip("#")
        if len(stripped) in (3, 6):
            # Try to match against known color names via chromatic check.
            for name in ("black", "white", "red", "blue", "green", "yellow", "orange", "purple", "pink", "brown", "gray", "grey", "navy"):
                if hex_is_near(color_value, name):
                    normalized_color = name
                    color_method = color_method or "hex_to_color_name"
                    break

    payload["color"] = normalized_color if normalized_color else (raw_color if raw_color else None)
    payload["_color_provenance"] = {
        "original": color_original,
        "normalized": normalized_color,
        "method": color_method,
        "sources": color_sources,
    }

    # Pack quantity — extract from product flat field or variants.selected.
    raw_pack_qty = product.get("pack_quantity")
    pack_sources = []
    pack_method = None
    pack_value = raw_pack_qty
    pack_unit = product.get("pack_unit")

    if isinstance(raw_pack_qty, (int, float)) and not isinstance(raw_pack_qty, bool):
        pack_value = int(raw_pack_qty)
        pack_method = "product.pack_quantity"
        pack_sources.append("product.pack_quantity")

    # variants.selected for Pack Count.
    if isinstance(variants, dict):
        selected = variants.get("selected") or []
        if isinstance(selected, list):
            for sel in selected:
                if isinstance(sel, dict) and sel.get("name") and sel.get("label"):
                    name_l = str(sel["name"]).strip().lower()
                    if name_l in ("pack count", "pack_quantity", "pack", "quantity"):
                        pack_sources.append(f"product.variants.selected.{sel['name']}={sel['label']}")
                        if pack_value is None:
                            # Parse "5-pack" → 5, "5 paia" → 5, etc.
                            import re as _re
                            m = _re.match(r"\s*(\d+)\s*", str(sel["label"]))
                            if m:
                                pack_value = int(m.group(1))
                                pack_method = f"product.variants.selected.{sel['name']}"
                                pack_unit = pack_unit or str(sel["label"]).split()[0] if str(sel["label"]).split() else None

    if pack_value is not None:
        payload["pack_quantity"] = pack_value
    if pack_unit:
        payload["pack_unit"] = pack_unit
    payload["_pack_quantity_provenance"] = {
        "original": raw_pack_qty,
        "normalized": pack_value,
        "method": pack_method,
        "sources": pack_sources,
    }

    # Variants — preserve original structure for variant-aware rules.
    payload["variants"] = product.get("variants")

    # Size — extract from flat field or variants.selected.
    raw_size = product.get("size")
    size_sources = []
    size_method = None
    size_value = raw_size

    if isinstance(raw_size, str) and raw_size.strip():
        size_value = raw_size.strip()
        size_method = "product.size"
        size_sources.append("product.size")

    if isinstance(variants, dict):
        selected = variants.get("selected") or []
        if isinstance(selected, list):
            for sel in selected:
                if isinstance(sel, dict) and sel.get("name") and sel.get("label"):
                    if str(sel["name"]).strip().lower() in ("size", "taglia"):
                        size_sources.append(f"product.variants.selected.{sel['name']}={sel['label']}")
                        if not size_value:
                            size_value = sel["label"]
                            size_method = f"product.variants.selected.{sel['name']}"
                            break

    if size_value:
        payload["size"] = size_value
    payload["_size_provenance"] = {
        "original": raw_size,
        "normalized": size_value,
        "method": size_method,
        "sources": size_sources,
    }

    # Provenance.
    payload["_provenance"] = product.get("_provenance") if isinstance(product.get("_provenance"), dict) else None

    # Availability from offer (always present for key parity).
    if isinstance(offer, dict):
        payload["availability"] = offer.get("availability")
    else:
        payload["availability"] = None
    payload["_availability_provenance"] = {
        "original": offer.get("availability") if isinstance(offer, dict) else None,
        "normalized": offer.get("availability") if isinstance(offer, dict) else None,
        "method": "offer.availability",
        "sources": ["offer.availability"],
    }

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


def _norm_number_with_units(value: Any) -> Optional[float]:
    """Normalize a value with optional units to a float.

    Accepts "17 inches", '17"', "16 GB", "100%", plain "17", etc.
    Returns None if the value cannot be parsed as a number.
    """
    if value is None:
        return None
    s = str(value).strip()
    if s == "":
        return None
    # Strip common unit suffixes and quotes.
    s = s.replace('"', "").replace("inches", "").replace("inch", "")
    s = s.replace("GB", "").replace("gb", "").replace("ram", "").replace("RAM", "")
    s = s.replace("%", "").replace("cm", "").replace("CM", "")
    s = s.strip()
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _extract_pack_quantity_from_text(text: str) -> Optional[int]:
    """Extract pack quantity from explicit text patterns.

    Recognizes: "5-pack", "5 pack", "pack of 5", "5 pairs",
    "confezione da 5", "paia 5". Never interprets generic numbers
    as pack quantity.
    """
    if not text:
        return None
    low = text.lower()
    patterns = [
        r"(\d+)\s*-?\s*pack\b",
        r"\bpack\s+of\s+(\d+)\b",
        r"(\d+)\s*pairs?\b",
        r"confezione\s+da\s+(\d+)\b",
        r"paia\s+(\d+)\b",
    ]
    for pattern in patterns:
        m = re.search(pattern, low)
        if m:
            return int(m.group(1))
    return None


def _extract_style_from_text(text: str) -> Optional[str]:
    """Extract style keywords from text.

    Recognizes: ribbed, crew, ankle, knee-high, no-show, low-cut.
    Returns found keywords joined in canonical order, or None.
    """
    if not text:
        return None
    low = text.lower()
    style_keywords = ["ribbed", "crew", "ankle", "knee-high", "knee high", "no-show", "low-cut", "low cut"]
    found = [kw for kw in style_keywords if kw in low]
    if not found:
        return None
    return " ".join(found)


def _extract_sizes_from_variants_options(variants: Any) -> Optional[str]:
    """Extract size labels from variants.options (Channel3 detail format).

    Looks for options with name "size", "taglia", or "taille".
    Returns the first size label found, or None.
    """
    if not isinstance(variants, dict):
        return None
    options = variants.get("options")
    if not isinstance(options, list):
        return None
    for opt in options:
        if not isinstance(opt, dict):
            continue
        name = opt.get("name")
        if isinstance(name, str) and name.strip().lower() in ("size", "taglia", "taille"):
            values = opt.get("values")
            if isinstance(values, list):
                for val in values:
                    if isinstance(val, dict) and val.get("label"):
                        return str(val["label"]).strip()
    return None


def _attested_value_satisfies(rule: Dict[str, Any], attested_value: Any) -> Optional[bool]:
    """Deterministically compare a user-attested value against a rule's
    expected requirement using the same comparator the Policy Gate applies.

    Returns True if the attested value satisfies the requirement, False if it
    does not, or None if the rule is unsupported for attestation (remains
    unresolved — fail closed). Never returns True for a non-empty value alone.
    """
    if attested_value is None or str(attested_value).strip() == "":
        return None
    rule_id = rule.get("rule", "")
    expected = rule.get("expected")
    observed = rule.get("observed")

    # --- Numeric >= comparators (min_*, composition) ---
    if rule_id.startswith("min_") or rule_id == "composition":
        if expected is None:
            return None
        att_num = _norm_number_with_units(attested_value)
        exp_num = _norm_number_with_units(expected)
        if att_num is None or exp_num is None:
            return False
        return att_num >= exp_num

    # --- max_price: attested value must be <= max_price ---
    if rule_id == "max_price":
        if expected is None:
            return None
        try:
            return float(attested_value) <= float(expected)
        except (TypeError, ValueError):
            return False

    # --- Exact string match with unit normalization (size, style) ---
    if rule_id in ("size", "style"):
        if expected is None:
            return None
        # Numeric-with-units equivalence: "17 inches" == '17"' == "17".
        att_num = _norm_number_with_units(attested_value)
        exp_num = _norm_number_with_units(expected)
        if att_num is not None and exp_num is not None:
            return att_num == exp_num
        return str(attested_value).strip() == str(expected)

    # --- Case-insensitive exact match (color) ---
    if rule_id == "color":
        if expected is None:
            return None
        return str(attested_value).strip().lower() == str(expected).lower()

    # --- Material: expected must be contained in attested value (case-insensitive) ---
    if rule_id == "material":
        if expected is None:
            return None
        return str(expected).lower() in str(attested_value).strip().lower()

    # --- Condition: attested value must be in allowed conditions ---
    if rule_id == "condition":
        allowed = expected if isinstance(expected, list) else [expected]
        allowed_lc = [str(a).lower() for a in allowed if a is not None]
        if not allowed_lc:
            return None
        return str(attested_value).strip().lower() in allowed_lc

    # --- Forbidden design: attested value must NOT contain the forbidden design ---
    if rule_id.startswith("forbidden_design_"):
        if expected is None:
            return None
        return str(expected).lower() not in str(attested_value).strip().lower()

    # --- Unsupported rule: remain unresolved (fail closed) ---
    return None


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


def evaluate(mandate: Dict[str, Any], product: Dict[str, Any], attestations: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    # Compute fingerprints from inputs BEFORE normalization so they match
    # exactly what create_attestation() stores (same locked mandate, same
    # normalized product evidence payload).
    mandate_fp = canonical_fingerprint(mandate)
    product_fp = canonical_fingerprint(product)

    # Normalize the incoming product through the evidence adapter so that
    # Channel3 fields (structured_attributes, key_features, materials, …)
    # are available to every rule with provenance recorded. Do not mutate
    # the original mandate or persisted objects.
    product = build_evidence(product)

    rules: List[Dict[str, Any]] = []
    approved = True

    # Build valid attestation lookup by rule_id, validating fingerprints and
    # policy version against the current context. Only attestations whose
    # mandate, offer/evidence and policy fingerprints match are eligible.
    # list_attestations() returns DESC (newest first); keep the first valid
    # attestation per rule_id so the latest one wins and never gets
    # overwritten by an older record. All historical records are preserved
    # in the audit table.
    valid_attestations: Dict[str, Dict[str, Any]] = {}
    for att in (attestations or []):
        rule_id = att.get("rule_id")
        if not rule_id:
            continue
        if rule_id in valid_attestations:
            continue
        if att.get("mandate_fingerprint") != mandate_fp:
            continue
        if att.get("offer_evidence_fingerprint") != product_fp:
            continue
        if att.get("policy_version") != POLICY_VERSION:
            continue
        valid_attestations[rule_id] = att

    # price
    price = product.get("price")
    price_currency = product.get("currency")
    mandate_currency = mandate.get("currency")  # None if not specified
    max_price = mandate.get("max_price")
    if max_price is None:
        # No budget constraint — but still record the observed price/currency.
        r = {"rule": "max_price", "expected": None, "observed": price, "passed": True, "status": "NOT_APPLICABLE", "evidence": "offer.price", "evidence_source": "offer.price", "evidence_excerpt": f"{price} {price_currency}" if price is not None else None}
        rules.append(r)
    else:
        # Currency safety: 12.99 USD must never be compared against 15 EUR.
        # Only enforce when the mandate explicitly specifies a currency.
        currency_mismatch = (
            price_currency is not None
            and mandate_currency is not None
            and str(price_currency).upper() != str(mandate_currency).upper()
        )
        currency_missing_when_mandate_has = (
            "currency" in product
            and product.get("currency") is None
            and mandate_currency is not None
        )
        r = {"rule": "max_price", "expected": max_price, "observed": price, "passed": False, "status": "APPLICABLE", "evidence": "offer.price", "evidence_source": "offer.price", "evidence_excerpt": f"{price} {price_currency}" if price is not None else None}
        if currency_mismatch:
            # Offer currency differs from mandate currency → explicitly incompatible.
            r["passed"] = False
            r["status"] = "APPLICABLE"
            r["evidence_excerpt"] = f"{price} {price_currency} (mandate currency: {mandate_currency}) — currency mismatch, price compliance unverifiable"
            approved = False
        elif currency_missing_when_mandate_has:
            # Mandate specifies a currency but offer doesn't → cannot verify → NEEDS_CONFIRMATION
            r["observed"] = None
            r["evidence_excerpt"] = f"{price} (currency not specified — mandate: {mandate_currency})"
        elif price is not None and float(price) <= float(max_price):
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

    # availability — from offer.availability
    availability_mandate = mandate.get("availability")
    if availability_mandate is not None:
        observed_availability = product.get("availability")
        availability_prov = product.get("_availability_provenance") or {}
        r = {
            "rule": "availability",
            "expected": availability_mandate,
            "observed": observed_availability,
            "passed": False,
            "status": "APPLICABLE",
            "evidence": "offer.availability",
            "evidence_source": availability_prov.get("method", "offer.availability"),
            "evidence_excerpt": str(observed_availability) if observed_availability else None,
        }
        if observed_availability and str(observed_availability).lower() == str(availability_mandate).lower():
            r["passed"] = True
        else:
            approved = False
        rules.append(r)

    # explicit mandate constraints
    # size — uses _size_provenance (variants.selected or flat field).
    if mandate.get("size") is not None:
        size_prov = product.get("_size_provenance") or {}
        observed_size = size_prov.get("normalized") or product.get("size")
        size_method = size_prov.get("method")
        size_sources = size_prov.get("sources") or []

        # Variant-aware: if no flat size but variants exist,
        # find the variant matching the required size (and color if also required).
        if observed_size is None:
            variants = product.get("variants")
            required_color = mandate.get("color")
            if isinstance(variants, dict):
                selected = variants.get("selected") or []
                if isinstance(selected, list):
                    sel_size = None
                    sel_color = None
                    for sel in selected:
                        if isinstance(sel, dict) and sel.get("name") and sel.get("label"):
                            name_l = str(sel["name"]).strip().lower()
                            if name_l in ("size", "taglia"):
                                sel_size = str(sel["label"]).strip()
                            if name_l in ("color", "colour"):
                                sel_color = str(sel["label"]).strip()
                    color_ok = required_color is None or (
                        sel_color is not None and str(sel_color).lower() == str(required_color).lower())
                    size_ok = sel_size is not None and str(sel_size) == str(mandate["size"])
                    if color_ok and size_ok:
                        observed_size = sel_size
            elif isinstance(variants, list):
                for v in variants:
                    if isinstance(v, dict) and v.get("size") is not None:
                        v_color = v.get("color") or v.get("colour")
                        color_ok = required_color is None or (
                            v_color is not None and str(v_color).lower() == str(required_color).lower())
                        size_ok = str(v["size"]) == str(mandate["size"])
                        if color_ok and size_ok:
                            observed_size = v["size"]
                            break

        r = {"rule": "size", "expected": mandate["size"], "observed": observed_size, "passed": False, "status": "APPLICABLE", "evidence": "product.size", "evidence_source": size_method or "product.size", "evidence_excerpt": str(observed_size) if observed_size is not None else None, "_size_original": size_prov.get("original"), "_size_method": size_method, "_size_sources": size_sources}
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
        if fd.lower() in design:
            # Design explicitly contains the forbidden element → FAIL
            approved = False
        elif design and design not in _NON_INFORMATIVE_PATTERNS:
            # Informative design that does not contain the forbidden element → PASS
            r["passed"] = True
        else:
            # No informative design evidence (missing, or non-informative
            # pattern like "Solid"/"Plain") → cannot prove absence of the
            # forbidden element. Leave observed=None so NEEDS_CONFIRMATION
            # applies instead of a false PASS.
            r["observed"] = None
        rules.append(r)

    # color — normalized via _color_provenance (hex→name or name→name).
    required_color = mandate.get("color")
    if required_color is not None:
        color_prov = product.get("_color_provenance") or {}
        observed_color = color_prov.get("normalized") or product.get("color")
        color_original = color_prov.get("original")
        color_method = color_prov.get("method")
        color_sources = color_prov.get("sources") or []

        # Color conflicts from provenance (e.g. structured_attributes disagrees
        # with flat color field) → cannot trust color evidence → NEEDS_CONFIRMATION.
        color_conflicts = product.get("_provenance", {}).get("color_conflicts")

        # Variant-aware matching: if both size and color are required,
        # the same variant must satisfy both. Supports variants as list
        # (simplified test fixtures) or dict with selected[] (Channel3 detail).
        variants = product.get("variants")
        required_size = mandate.get("size")
        matching_variant = None
        if isinstance(variants, dict):
            selected = variants.get("selected") or []
            if isinstance(selected, list):
                for sel in selected:
                    if isinstance(sel, dict) and sel.get("name") and sel.get("label"):
                        name_l = str(sel["name"]).strip().lower()
                        if name_l in ("color", "colour"):
                            sel_color = str(sel["label"]).strip()
                            color_match = str(sel_color).lower() == str(required_color).lower()
                            if not color_match and hex_is_near(sel_color, required_color):
                                color_match = True
                            sel_size = None
                            for s2 in selected:
                                if isinstance(s2, dict) and str(s2.get("name", "")).strip().lower() in ("size", "taglia"):
                                    sel_size = str(s2.get("label", "")).strip()
                                    break
                            size_match = required_size is None or (
                                sel_size is not None and str(sel_size) == str(required_size)
                            )
                            if color_match and size_match:
                                matching_variant = sel
                                break
        elif isinstance(variants, list):
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
            if isinstance(matching_variant, dict) and matching_variant.get("label"):
                observed_color = str(matching_variant.get("label")).strip()
            else:
                observed_color = matching_variant.get("color") or matching_variant.get("colour")
            color_source = f"product.variants.color={observed_color}"
            # A matching variant resolves the color regardless of conflicts
            # in other variants.
            color_conflicts = None
        else:
            color_source = color_method or "product.color"

        # Deterministic color comparison: exact name match OR hex→name chromatic match.
        color_matches = False
        if observed_color is not None:
            if str(observed_color).lower() == str(required_color).lower():
                color_matches = True
            elif hex_is_near(str(observed_color), required_color):
                color_matches = True

        if color_conflicts:
            r = {
                "rule": "color",
                "expected": required_color,
                "observed": None,
                "passed": False,
                "status": "APPLICABLE",
                "evidence": "product.color",
                "evidence_source": color_source,
                "evidence_excerpt": str(color_original) if color_original else None,
                "conflicts": color_conflicts,
                "_color_original": color_original,
                "_color_method": color_method,
                "_color_sources": color_sources,
            }
            approved = False
        elif not color_matches:
            r = {
                "rule": "color",
                "expected": required_color,
                "observed": observed_color,
                "passed": False,
                "status": "APPLICABLE",
                "evidence": "product.color",
                "evidence_source": color_source,
                "evidence_excerpt": str(color_original) if color_original else str(observed_color) if observed_color else None,
                "_color_original": color_original,
                "_color_method": color_method,
                "_color_sources": color_sources,
            }
            approved = False
        else:
            r = {
                "rule": "color",
                "expected": required_color,
                "observed": observed_color,
                "passed": True,
                "status": "APPLICABLE",
                "evidence": "product.color",
                "evidence_source": color_source,
                "evidence_excerpt": str(color_original) if color_original else str(observed_color),
                "_color_original": color_original,
                "_color_method": color_method,
                "_color_sources": color_sources,
            }
        rules.append(r)

    # pack quantity — uses _pack_quantity_provenance (variants.selected or flat field).
    # Never combine pack quantity from a different variant than size+color.
    required_pack_qty = mandate.get("pack_quantity")
    if required_pack_qty is not None:
        pack_prov = product.get("_pack_quantity_provenance") or {}
        observed_pack_qty = pack_prov.get("normalized")
        pack_method = pack_prov.get("method")
        pack_sources = pack_prov.get("sources") or []
        pack_unit = product.get("pack_unit")

        # Variant-aware: verify pack quantity on the variant matching size+color.
        variants = product.get("variants")
        required_size = mandate.get("size")
        required_color = mandate.get("color")
        matching_variant = None
        if isinstance(variants, dict):
            selected = variants.get("selected") or []
            if isinstance(selected, list):
                sel_size = None
                sel_color = None
                sel_pack = None
                for sel in selected:
                    if isinstance(sel, dict) and sel.get("name") and sel.get("label"):
                        name_l = str(sel["name"]).strip().lower()
                        if name_l in ("size", "taglia"):
                            sel_size = str(sel["label"]).strip()
                        if name_l in ("color", "colour"):
                            sel_color = str(sel["label"]).strip()
                        if name_l in ("pack count", "pack_quantity", "pack", "quantity"):
                            import re as _re
                            m = _re.match(r"\s*(\d+)\s*", str(sel["label"]))
                            if m:
                                sel_pack = int(m.group(1))
                color_ok = required_color is None or (
                    sel_color is not None and str(sel_color).lower() == str(required_color).lower())
                size_ok = required_size is None or (
                    sel_size is not None and str(sel_size) == str(required_size))
                if color_ok and size_ok:
                    matching_variant = {"pack_quantity": sel_pack}
        elif isinstance(variants, list):
            for v in variants:
                if isinstance(v, dict):
                    v_color = v.get("color") or v.get("colour")
                    v_size = v.get("size")
                    v_pq = v.get("pack_quantity")
                    color_ok = required_color is None or (
                        v_color is not None and str(v_color).lower() == str(required_color).lower())
                    size_ok = required_size is None or (
                        v_size is not None and str(v_size) == str(required_size))
                    if color_ok and size_ok and v_pq is not None:
                        matching_variant = {"pack_quantity": v_pq}
                        break

        if matching_variant is not None and matching_variant.get("pack_quantity") is not None:
            observed_pack_qty = matching_variant.get("pack_quantity")
            pack_source = pack_method or "product.variants.selected.pack_count"
        else:
            # Fall back to product-level pack_quantity (no matching variant,
            # or matching variant has no pack count field).
            observed_pack_qty = pack_prov.get("normalized")
            pack_source = pack_method or "product.pack_quantity"

        r = {"rule": "pack_quantity", "expected": required_pack_qty, "observed": observed_pack_qty, "passed": False, "status": "APPLICABLE", "evidence": "offer.pack_quantity", "evidence_source": pack_source, "evidence_excerpt": str(observed_pack_qty) if observed_pack_qty is not None else None, "_pack_unit": pack_unit, "_pack_sources": pack_sources}

        if observed_pack_qty is not None:
            try:
                if int(observed_pack_qty) >= int(required_pack_qty):
                    r["passed"] = True
                else:
                    # Explicitly insufficient pack quantity → REJECTED
                    approved = False
            except (TypeError, ValueError):
                # Non-numeric pack quantity → cannot verify → NEEDS_CONFIRMATION
                r["observed"] = None
        # If observed_pack_qty is None: not verifiable → NEEDS_CONFIRMATION
        # (do not set approved=False)
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

    # Apply valid user attestations to unresolved REQUIRED rules.
    # A valid attestation may satisfy its specific missing requirement but
    # must not override failures, conflicts, or other missing requirements.
    # The attested value must meet the rule's exact requirement via the same
    # deterministic comparator the Policy Gate applies — a non-empty value
    # alone is never sufficient.
    for r in rules:
        rule_id = r.get("rule")
        att = valid_attestations.get(rule_id)
        if not att:
            continue
        # Only REQUIRED, unresolved (not passed, no observed value) rules
        # are eligible for attestation. Failed rules are never overridable.
        if r.get("priority") != "REQUIRED":
            continue
        if r.get("passed") is True:
            continue
        if r.get("status") == "FAILED":
            continue
        if r.get("observed") not in (None, ""):
            # Already has an observed seller value — attestation cannot override.
            continue
        # Apply the deterministic comparator for this specific rule.
        satisfied = _attested_value_satisfies(r, att.get("attested_value"))
        if satisfied is not True:
            # Unsupported rule or value does not meet the requirement —
            # remains unresolved (fail closed). Never blindly pass.
            continue
        r["passed"] = True
        r["observed"] = att.get("attested_value")
        r["status"] = "APPLICABLE"
        r["verification_source"] = "USER_ATTESTED"
        r["evidence_source"] = "user_attestation"
        r["evidence_excerpt"] = f"User-attested value: {att.get('attested_value')} from {att.get('source_url')}"
        if att.get("explanation"):
            r["user_attestation_explanation"] = att.get("explanation")
        r["attestation_id"] = att.get("id")
        r["attestation_timestamp"] = att.get("timestamp")

    # Tag every rule with its verification source for transparency.
    for r in rules:
        if "verification_source" not in r:
            if r.get("passed") is True and r.get("observed") not in (None, ""):
                r["verification_source"] = "SELLER_VERIFIED"
            elif r.get("status") == "FAILED":
                r["verification_source"] = "FAILED"
            else:
                r["verification_source"] = "NOT_VERIFIED"

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
