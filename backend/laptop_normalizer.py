"""Pure, deterministic, fail-closed normalizer for laptop specifications.

Extracts only verifiable evidence from Channel3-shaped product/evidence
structures. Missing, ambiguous, or conflicting attributes are NOT_VERIFIED
with value None.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional


LAPTOP_FIELDS = (
    "computer_form",
    "ram_gb",
    "storage_gb",
    "storage_interface",
    "processor_family",
    "operating_system",
)

_ACCESSORY_RE = re.compile(
    r"\b(sleeve|bag|case|cover|skin|backpack|briefcase|pouch|dock(?:ing)?\s*station|"
    r"stand|cooler|cooling\s*pad|charger|adapter|cable|keyboard\s*cover|screen\s*protector|"
    r"mouse\s*pad|hub|carrying)\b",
    re.I,
)
_COMPUTER_FORM_POSITIVE_RE = re.compile(
    r"\b(notebook|laptop|ultrabook|chromebook)\b", re.I
)
_DESKTOP_RE = re.compile(
    r"\b(desktop|tower|all[- ]?in[- ]?one|aio|workstation\s*pc|mini\s*pc|small\s*form\s*factor\s*pc|sff\s*pc)\b",
    re.I,
)
_DESKTOP_CATEGORY_RE = re.compile(r"\b(desktop-computers|desktops|tower-computers)\b", re.I)
_ACCESSORY_CATEGORY_RE = re.compile(
    r"\b(laptop-sleeves|laptop-bags|laptop-cases|computer-covers-skins|docking-stations|"
    r"computer-accessories|chargers|adapters)\b",
    re.I,
)
_NOTEBOOK_CATEGORY_RE = re.compile(r"\b(laptops|notebooks|ultrabooks|chromebooks)\b", re.I)
_GPU_EVIDENCE_RE = re.compile(
    r"\b(arc\s+graphics|geforce|rtx|gtx|radeon(?:\s+(?:rx|graphics|vega))?|"
    r"uhd\s+graphics|iris\s*(?:xe|plus)?\s*graphics|graphics\s+\d{3,4}[A-Za-z]*|"
    r"gpu|video\s*card|graphics\s*card)\b",
    re.I,
)
_CPU_CONTEXT_RE = re.compile(
    r"\b(processor|cpu|intel\s+core|intel\s+xeon|amd\s+ryzen|snapdragon\s+x|apple\s+m\d)\b",
    re.I,
)

_RAM_CONTEXT_RE = re.compile(
    r"(?P<val>\d+)\s*(?:GB|G)\s*(?:\((?P<config>\d+\s*x\s*\d+\s*(?:GB|G)?)\))?\s*"
    r"(?:(?:of\s+)?(?:DDR\d*|LPDDR\d*|SDRAM|SODIMM)\s*)?"
    r"(?:RAM|memory|system\s+memory)\b",
    re.I,
)
_RAM_BEFORE_RE = re.compile(
    r"\b(?:RAM|memory|system\s+memory)\s*[:\-]?\s*(?P<val>\d+)\s*(?:GB|G)"
    r"(?:\s*\((?P<config>\d+\s*x\s*\d+\s*(?:GB|G)?)\))?",
    re.I,
)
_RAM_CONFIG_TOTAL_RE = re.compile(r"(\d+)\s*x\s*(\d+)\s*(?:GB|G)?", re.I)

_STORAGE_MAX_RE = re.compile(
    r"\b(?:up\s*to|expandable\s*(?:to|up\s*to)?|max(?:imum)?\s*(?:storage|capacity)?\s*(?:of|to)?|"
    r"supports?\s*up\s*to)\b",
    re.I,
)
_STORAGE_CONTEXT_RE = re.compile(
    r"(?P<val>\d+(?:\.\d+)?)\s*(?P<unit>TB|GB)\s*"
    r"(?:(?:PCIe|PCIE)\s*(?:Gen\s*\d+\s*)?)?"
    r"(?:(?:NVMe|NVME)\s*)?"
    r"(?:(?:M\.?2\s*)?)?"
    r"(?:(?:SATA|SSD|HDD|solid\s*state|hard\s*drive|storage|drive|flash))"
    r"(?:\s+(?:SSD|HDD|storage|drive|flash))?\b",
    re.I,
)
_STORAGE_BEFORE_RE = re.compile(
    r"\b(?:SSD|HDD|storage|hard\s*drive|solid\s*state\s*drive)\s*[:\-]?\s*"
    r"(?P<val>\d+(?:\.\d+)?)\s*(?P<unit>TB|GB)\b",
    re.I,
)

_INTERFACE_RE = re.compile(r"\b(NVMe|NVME|PCIe\s*/?\s*NVMe|PCIe|PCIE|SATA)\b", re.I)

_PROCESSOR_FAMILY_KEYS = {
    "processor-family", "processor_family", "cpu-family", "cpu_family",
    "processor family", "cpu family",
}
_PROCESSOR_EXPLICIT_RE = re.compile(
    r"\b((?:Intel(?:\s+Core)?(?:\s+Ultra)?(?:\s+i[3579])?|AMD\s+Ryzen(?:\s+AI)?(?:\s+\d+)?|"
    r"Apple\s+M[1-4](?:\s+(?:Pro|Max|Ultra))?|Qualcomm\s+Snapdragon(?:\s+X)?(?:\s+(?:Plus|Elite))?)"
    r"(?:\s+[A-Za-z0-9\-]+){0,4})\b",
    re.I,
)

_OS_KEYS = {
    "operating-system", "operating_system", "os", "operating system",
}
_OS_EXPLICIT_RE = re.compile(
    r"\b(Windows\s*1[01](?:\s*(?:Home|Pro|S|in\s*S\s*mode))?|Windows(?!\s+users?\b)|Chrome\s*OS|ChromeOS|"
    r"macOS(?:\s+\w+)?|Ubuntu(?:\s+Linux)?|Linux|FreeDOS|DOS)\b",
    re.I,
)


def normalize_laptop_specs(product_or_evidence: Any) -> Dict[str, Dict[str, Any]]:
    """Normalize laptop specs from product/evidence.

    Returns the 6 existing normalized fields plus an additive
    `product_classification` diagnostic field. Existing field contracts are
    preserved for backwards compatibility.
    """
    data = product_or_evidence if isinstance(product_or_evidence, dict) else {}
    result = {field: _extract_field(field, data) for field in LAPTOP_FIELDS}
    result["product_classification"] = _extract_product_classification(data)
    return result


def _nv(value: Any = None, source: Optional[str] = None, excerpt: Any = None,
        conflicts: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "value": value,
        "status": "NOT_VERIFIED",
        "evidence_source": source,
        "evidence_excerpt": _excerpt_str(excerpt) if excerpt is not None else None,
    }
    if conflicts:
        result["conflicts"] = conflicts
    return result


def _verified(value: Any, source: str, excerpt: Any) -> Dict[str, Any]:
    return {
        "value": value,
        "status": "VERIFIED",
        "evidence_source": source,
        "evidence_excerpt": _excerpt_str(excerpt),
    }


def _excerpt_str(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _resolve(candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not candidates:
        return _nv()
    distinct: Dict[str, Dict[str, Any]] = {}
    for c in candidates:
        key = repr(c["value"])
        if key not in distinct:
            distinct[key] = c
    if len(distinct) == 1:
        c = candidates[0]
        return _verified(c["value"], c["source"], c["excerpt"])
    conflicts = [
        {"value": c["value"], "evidence_source": c["source"], "evidence_excerpt": _excerpt_str(c["excerpt"])}
        for c in candidates
    ]
    sources = [c["source"] for c in candidates]
    excerpts = [_excerpt_str(c["excerpt"]) for c in candidates]
    return _nv(conflicts=conflicts, source="; ".join(sources), excerpt=" | ".join(excerpts))


def _get_specs(data: Dict[str, Any]) -> Dict[str, Any]:
    s = data.get("specs")
    return s if isinstance(s, dict) else {}


def _get_sa(data: Dict[str, Any]) -> Dict[str, Any]:
    sa = data.get("structured_attributes")
    return sa if isinstance(sa, dict) else {}


def _get_kf(data: Dict[str, Any]) -> List[str]:
    kf = data.get("key_features")
    if not isinstance(kf, list):
        return []
    return [str(x) for x in kf if x is not None]


def _get_text(data: Dict[str, Any], key: str) -> str:
    v = data.get(key)
    return v if isinstance(v, str) else ""


def _first_scalar(value: Any) -> Optional[str]:
    if isinstance(value, list):
        return str(value[0]) if value else None
    if value is None:
        return None
    return str(value)


def _extract_field(field: str, data: Dict[str, Any]) -> Dict[str, Any]:
    if field == "computer_form":
        return _extract_computer_form(data)
    if field == "ram_gb":
        return _extract_ram(data)
    if field == "storage_gb":
        return _extract_storage(data)
    if field == "storage_interface":
        return _extract_interface(data)
    if field == "processor_family":
        return _extract_processor(data)
    if field == "operating_system":
        return _extract_os(data)
    return _nv()


def _extract_computer_form(data: Dict[str, Any]) -> Dict[str, Any]:
    candidates: List[Dict[str, Any]] = []
    accessory_evidence: List[Dict[str, Any]] = []
    # specs
    specs = _get_specs(data)
    for key in ("computer_form", "computer-form", "form_factor", "product_type", "type"):
        if key in specs and specs[key] is not None:
            raw = _first_scalar(specs[key])
            if raw:
                c = _classify_computer_form(raw, f"product.specs.{key}")
                _collect_form_candidate(c, candidates, accessory_evidence)
    # structured_attributes
    sa = _get_sa(data)
    for key in ("computer-form", "computer_form", "form-factor", "form_factor", "product-type"):
        if key in sa and sa[key] is not None:
            raw = _first_scalar(sa[key])
            if raw:
                c = _classify_computer_form(raw, f"product.structured_attributes.{key}")
                _collect_form_candidate(c, candidates, accessory_evidence)
    # key_features
    for i, feat in enumerate(_get_kf(data)):
        c = _classify_computer_form(feat, f"product.key_features[{i}]")
        _collect_form_candidate(c, candidates, accessory_evidence)
    # title / description
    for key in ("title", "description"):
        text = _get_text(data, key)
        if text:
            c = _classify_computer_form(text, f"product.{key}")
            _collect_form_candidate(c, candidates, accessory_evidence)
    # Accessory evidence means we must not verify the product as a Notebook.
    if accessory_evidence and candidates:
        conflicts = [
            {"value": "Notebook", "evidence_source": c["source"], "evidence_excerpt": _excerpt_str(c["excerpt"])}
            for c in candidates
        ] + [
            {"value": "ACCESSORY", "evidence_source": c["source"], "evidence_excerpt": _excerpt_str(c["excerpt"])}
            for c in accessory_evidence
        ]
        return _nv(
            value=None,
            source="; ".join([c["source"] for c in candidates] + [c["source"] for c in accessory_evidence]),
            excerpt=" | ".join([_excerpt_str(c["excerpt"]) for c in accessory_evidence]),
            conflicts=conflicts,
        )
    if accessory_evidence:
        first = accessory_evidence[0]
        return _nv(value=None, source=first["source"], excerpt=first["excerpt"])
    return _resolve(candidates)


def _collect_form_candidate(
    c: Optional[Dict[str, Any]],
    candidates: List[Dict[str, Any]],
    accessory_evidence: List[Dict[str, Any]],
) -> None:
    if not c:
        return
    if c.get("value") == "__ACCESSORY__":
        accessory_evidence.append(c)
    else:
        candidates.append(c)


def _classify_computer_form(text: str, source: str) -> Optional[Dict[str, Any]]:
    # Accessories must never be classified as computers, even if "laptop" appears.
    if _ACCESSORY_RE.search(text):
        return {"value": "__ACCESSORY__", "source": source, "excerpt": text}
    if _COMPUTER_FORM_POSITIVE_RE.search(text):
        return {"value": "Notebook", "source": source, "excerpt": text}
    return None


def _extract_ram(data: Dict[str, Any]) -> Dict[str, Any]:
    candidates: List[Dict[str, Any]] = []
    specs = _get_specs(data)
    for key in ("ram_gb", "ram", "memory_gb", "memory", "system_memory"):
        if key in specs and specs[key] is not None:
            raw = specs[key]
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                candidates.append({"value": int(raw), "source": f"product.specs.{key}", "excerpt": raw})
            elif isinstance(raw, str):
                c = _parse_ram_text(raw, f"product.specs.{key}")
                if c:
                    candidates.append(c)
    sa = _get_sa(data)
    for key in ("ram", "memory", "system-memory", "installed-ram", "ram-capacity"):
        if key in sa and sa[key] is not None:
            raw = _first_scalar(sa[key])
            if raw:
                c = _parse_ram_text(raw, f"product.structured_attributes.{key}")
                if c:
                    candidates.append(c)
    for i, feat in enumerate(_get_kf(data)):
        c = _parse_ram_text(feat, f"product.key_features[{i}]")
        if c:
            candidates.append(c)
    for key in ("title", "description"):
        text = _get_text(data, key)
        if text:
            c = _parse_ram_text(text, f"product.{key}")
            if c:
                candidates.append(c)
    return _resolve(candidates)


def _parse_ram_text(text: str, source: str) -> Optional[Dict[str, Any]]:
    # Must identify system memory; never GPU/storage.
    if re.search(r"\b(?:GDDR\d*|VRAM|video\s*memory|graphics\s*memory)\b", text, re.I):
        # only reject if there's no clear RAM/memory token; keep simple and fail-closed
        if not re.search(r"\b(?:RAM|system\s*memory)\b", text, re.I):
            return None
    m = _RAM_CONTEXT_RE.search(text)
    if not m:
        m = _RAM_BEFORE_RE.search(text)
    if not m:
        # allow "16GB memory" only when clearly memory (not storage drive)
        m = re.search(r"(?P<val>\d+)\s*(?:GB|G)\s*(?:\((?P<config>\d+\s*x\s*\d+\s*(?:GB|G)?)\))?\s*system\s*memory\b", text, re.I)
    if not m:
        return None
    # avoid storage contexts masquerading: only reject when storage tokens are
    # tightly attached to the same capacity phrase, not elsewhere in the text.
    local_after = text[m.end():m.end() + 12]
    local_before = text[max(0, m.start() - 12):m.start()]
    if re.search(r"^\s*(?:SSD|HDD|NVMe|SATA|storage|hard\s*drive)\b", local_after, re.I):
        return None
    if re.search(r"\b(?:SSD|HDD|NVMe|SATA|hard\s*drive)\s*$", local_before, re.I):
        return None
    config = m.groupdict().get("config")
    val = int(m.group("val"))
    if config:
        cm = _RAM_CONFIG_TOTAL_RE.search(config)
        if cm:
            total = int(cm.group(1)) * int(cm.group(2))
            # config must be consistent with stated capacity when both clear
            if total == val:
                val = total
            # if inconsistent, still treat stated total as ambiguous? 32GB(2x16GB) consistent.
    return {"value": val, "source": source, "excerpt": m.group(0)}


def _extract_storage(data: Dict[str, Any]) -> Dict[str, Any]:
    candidates: List[Dict[str, Any]] = []
    specs = _get_specs(data)
    for key in ("storage_gb", "storage", "ssd", "ssd_gb", "hard_drive", "disk"):
        if key in specs and specs[key] is not None:
            raw = specs[key]
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                candidates.append({"value": int(raw), "source": f"product.specs.{key}", "excerpt": raw})
            elif isinstance(raw, str):
                c = _parse_storage_text(raw, f"product.specs.{key}")
                if c:
                    candidates.append(c)
    sa = _get_sa(data)
    for key in ("storage", "hard-drive-capacity", "ssd-capacity", "storage-capacity", "disk-size"):
        if key in sa and sa[key] is not None:
            raw = _first_scalar(sa[key])
            if raw:
                c = _parse_storage_text(raw, f"product.structured_attributes.{key}")
                if c:
                    candidates.append(c)
    for i, feat in enumerate(_get_kf(data)):
        c = _parse_storage_text(feat, f"product.key_features[{i}]")
        if c:
            candidates.append(c)
    for key in ("title", "description"):
        text = _get_text(data, key)
        if text:
            c = _parse_storage_text(text, f"product.{key}")
            if c:
                candidates.append(c)
    return _resolve(candidates)


def _parse_storage_text(text: str, source: str) -> Optional[Dict[str, Any]]:
    # Installed capacity only; reject max/expandable/up-to contexts near the value.
    for rx in (_STORAGE_CONTEXT_RE, _STORAGE_BEFORE_RE):
        for m in rx.finditer(text):
            window_before = text[max(0, m.start() - 40):m.start()]
            # "up to 512GB" / "expandable to 512GB" qualify THIS capacity as
            # non-installed. A later clause like ", expandable up to 2TB"
            # refers to a different capacity and must not disqualify this one.
            window_after_immediate = text[m.end():m.end() + 12]
            if _STORAGE_MAX_RE.search(window_before):
                continue
            if re.search(r"^(?:\s*)\b(?:maximum|max)\b", window_after_immediate, re.I):
                continue
            # avoid RAM confusion
            if re.search(r"\bRAM\b", m.group(0), re.I):
                continue
            val = float(m.group("val"))
            unit = m.group("unit").upper()
            gb = int(round(val * 1024)) if unit == "TB" else int(round(val))
            return {"value": gb, "source": source, "excerpt": m.group(0)}
    # numeric-only specs-like strings "512 GB" in explicit storage attr handled above; be conservative elsewhere
    m = re.search(r"^(?P<val>\d+(?:\.\d+)?)\s*(?P<unit>TB|GB)$", text.strip(), re.I)
    if m and re.search(r"storage|ssd|hdd|disk|drive", source, re.I):
        val = float(m.group("val"))
        unit = m.group("unit").upper()
        gb = int(round(val * 1024)) if unit == "TB" else int(round(val))
        return {"value": gb, "source": source, "excerpt": text.strip()}
    return None


def _extract_interface(data: Dict[str, Any]) -> Dict[str, Any]:
    candidates: List[Dict[str, Any]] = []
    specs = _get_specs(data)
    for key in ("storage_interface", "storage-interface", "interface", "ssd_interface", "drive_interface"):
        if key in specs and specs[key] is not None:
            raw = _first_scalar(specs[key])
            if raw and _INTERFACE_RE.search(raw):
                candidates.append({"value": _normalize_interface(raw), "source": f"product.specs.{key}", "excerpt": raw})
    sa = _get_sa(data)
    for key in ("storage-interface", "storage_interface", "hard-drive-interface", "ssd-interface"):
        if key in sa and sa[key] is not None:
            raw = _first_scalar(sa[key])
            if raw and _INTERFACE_RE.search(raw):
                candidates.append({"value": _normalize_interface(raw), "source": f"product.structured_attributes.{key}", "excerpt": raw})
    for i, feat in enumerate(_get_kf(data)):
        m = _INTERFACE_RE.search(feat)
        if m and re.search(r"\b(?:SSD|storage|drive)\b", feat, re.I):
            candidates.append({"value": _normalize_interface(m.group(0)), "source": f"product.key_features[{i}]", "excerpt": feat})
    for key in ("title", "description"):
        text = _get_text(data, key)
        if text:
            m = _INTERFACE_RE.search(text)
            if m and re.search(r"\b(?:SSD|storage|drive)\b", text, re.I):
                candidates.append({"value": _normalize_interface(m.group(0)), "source": f"product.{key}", "excerpt": m.group(0)})
    return _resolve(candidates)


def _normalize_interface(raw: str) -> str:
    r = raw.strip()
    if re.search(r"NVMe", r, re.I):
        if re.search(r"PCIe", r, re.I):
            return "PCIe NVMe"
        return "NVMe"
    if re.search(r"PCIe", r, re.I):
        return "PCIe"
    if re.search(r"SATA", r, re.I):
        return "SATA"
    return r


def _extract_processor(data: Dict[str, Any]) -> Dict[str, Any]:
    candidates: List[Dict[str, Any]] = []
    specs = _get_specs(data)
    for key in ("processor_family", "processor-family", "cpu_family", "cpu", "processor"):
        if key in specs and specs[key] is not None:
            raw = _first_scalar(specs[key])
            if raw:
                val = _parse_processor(raw)
                if val:
                    candidates.append({"value": val, "source": f"product.specs.{key}", "excerpt": raw})
    sa = _get_sa(data)
    for key, val_raw in sa.items():
        if key in _PROCESSOR_FAMILY_KEYS or "processor" in key or key in ("cpu",):
            raw = _first_scalar(val_raw)
            if raw:
                val = _parse_processor(raw)
                if val:
                    candidates.append({"value": val, "source": f"product.structured_attributes.{key}", "excerpt": raw})
    for i, feat in enumerate(_get_kf(data)):
        val = _parse_processor(feat)
        if val and re.search(r"\b(?:processor|CPU|Intel|AMD|Ryzen|Core|Snapdragon|Apple\s+M)\b", feat, re.I):
            candidates.append({"value": val, "source": f"product.key_features[{i}]", "excerpt": feat})
    for key in ("title", "description"):
        text = _get_text(data, key)
        if text:
            val = _parse_processor(text)
            if val:
                candidates.append({"value": val, "source": f"product.{key}", "excerpt": val})
    return _resolve(candidates)


def _parse_processor(text: str) -> Optional[str]:
    m = _PROCESSOR_EXPLICIT_RE.search(text)
    if not m:
        return None
    # return explicit evidence string (trimmed), not an invented exact SKU beyond evidence
    return " ".join(m.group(1).split())


def _extract_os(data: Dict[str, Any]) -> Dict[str, Any]:
    candidates: List[Dict[str, Any]] = []
    specs = _get_specs(data)
    for key in ("operating_system", "operating-system", "os"):
        if key in specs and specs[key] is not None:
            raw = _first_scalar(specs[key])
            if raw:
                val = _parse_os(raw)
                if val:
                    candidates.append({"value": val, "source": f"product.specs.{key}", "excerpt": raw, "desc": _os_descriptor(val)})
    sa = _get_sa(data)
    for key, val_raw in sa.items():
        if key in _OS_KEYS or "operating" in key:
            raw = _first_scalar(val_raw)
            if raw:
                val = _parse_os(raw)
                if val:
                    candidates.append({"value": val, "source": f"product.structured_attributes.{key}", "excerpt": raw, "desc": _os_descriptor(val)})
    for i, feat in enumerate(_get_kf(data)):
        val = _parse_os(feat)
        if val:
            candidates.append({"value": val, "source": f"product.key_features[{i}]", "excerpt": feat, "desc": _os_descriptor(val)})
    for key in ("title", "description"):
        text = _get_text(data, key)
        if text and re.search(r"\bWindows\s+users?\b", text, re.I) and not re.search(r"\bWindows\s*1[01]\b|\bWindows\s*(?:Home|Pro)\b", text, re.I):
            # Do not infer installed OS from marketing phrases like "for Windows users".
            continue
        if text:
            val = _parse_os(text)
            if val:
                candidates.append({"value": val, "source": f"product.{key}", "excerpt": val, "desc": _os_descriptor(val)})

    if not candidates:
        return _nv()

    # Deduplicate by (source, original excerpt, display) but preserve every
    # contributing source+excerpt in provenance.
    seen = set()
    items: List[Dict[str, Any]] = []
    for c in candidates:
        k = (c["source"], _excerpt_str(c["excerpt"]), c["desc"].get("display"))
        if k not in seen:
            seen.add(k)
            items.append(c)

    # Genuine contradiction (e.g. Home vs Pro, Win10 vs Win11, Windows vs Linux):
    # fail closed, preserve all conflicting source paths and excerpts.
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if _os_conflict(items[i]["desc"], items[j]["desc"]):
                conflicts = [
                    {"value": c["desc"].get("display") or c["value"], "evidence_source": c["source"], "evidence_excerpt": _excerpt_str(c["excerpt"])}
                    for c in items
                ]
                return _nv(
                    value=None,
                    source="; ".join(c["source"] for c in items),
                    excerpt=" | ".join(_excerpt_str(c["excerpt"]) for c in items),
                    conflicts=conflicts,
                )

    # Compatible specificity chain: verify most-specific value, keep all provenance.
    best = max(items, key=lambda c: (_os_specificity(c["desc"]), 0))
    all_sources = "; ".join(c["source"] for c in items)
    all_excerpts = " | ".join(_excerpt_str(c["excerpt"]) for c in items)
    res = _verified(best["desc"].get("display") or best["value"], all_sources, all_excerpts)
    res["evidence_sources"] = [
        {"value": c["desc"].get("display") or c["value"], "evidence_source": c["source"], "evidence_excerpt": _excerpt_str(c["excerpt"])}
        for c in items
    ]
    return res


def _parse_os(text: str) -> Optional[str]:
    m = _OS_EXPLICIT_RE.search(text)
    if not m:
        return None
    v = " ".join(m.group(1).split())
    # "Copilot+PC"/AI features are not OS editions; regex already stops at edition.
    if v.lower() == "windows in s mode":
        return "Windows in S mode"
    return v


def _os_descriptor(value: str) -> Dict[str, Optional[str]]:
    n = " ".join((value or "").strip().split())
    low = n.lower()
    if low == "windows":
        return {"family": "windows", "major": None, "edition": None, "display": "Windows"}
    m = re.match(r"^windows\s*(1[01])(?:\s*(home|pro|s|in\s*s\s*mode))?$", low)
    if m:
        major = m.group(1)
        ed = (m.group(2) or "").replace("in s mode", "s").strip() or None
        if ed == "home":
            display = f"Windows {major} Home"
        elif ed == "pro":
            display = f"Windows {major} Pro"
        elif ed == "s":
            display = f"Windows {major} in S mode"
        else:
            display = f"Windows {major}"
            ed = None
        return {"family": "windows", "major": major, "edition": ed, "display": display}
    if "chrome" in low:
        return {"family": "chromeos", "major": None, "edition": None, "display": "ChromeOS"}
    if "macos" in low or "mac os" in low:
        return {"family": "macos", "major": None, "edition": None, "display": n}
    if "linux" in low or "ubuntu" in low or "freedos" in low or low == "dos":
        # Treat Linux/Ubuntu/FreeDOS/DOS as the linux_like family bucket only for
        # the exact normalized display match? Keep fail-closed: different concrete
        # values are conflicting unless identical normalized display.
        return {"family": "linux_like", "major": None, "edition": None, "display": n}
    return {"family": low, "major": None, "edition": None, "display": n}


def _os_refines_or_equal(generic: Dict[str, Optional[str]], specific: Dict[str, Optional[str]]) -> bool:
    """True when `specific` is the same as or a compatible more-specific form of `generic`."""
    if generic.get("family") != specific.get("family"):
        return False
    if generic.get("family") != "windows":
        return (generic.get("display") or "").lower() == (specific.get("display") or "").lower()
    gmaj, smaj = generic.get("major"), specific.get("major")
    ged, sed = generic.get("edition"), specific.get("edition")
    if gmaj is not None and smaj is not None and gmaj != smaj:
        return False
    if ged is not None and sed is not None and ged != sed:
        return False
    # specific must not be less specific than generic in any stated dimension
    if gmaj is not None and smaj is None:
        return False
    if ged is not None and sed is None:
        return False
    return True


def _os_conflict(a: Dict[str, Optional[str]], b: Dict[str, Optional[str]]) -> bool:
    return not (_os_refines_or_equal(a, b) or _os_refines_or_equal(b, a))


def _os_specificity(d: Dict[str, Optional[str]]) -> int:
    score = 0
    if d.get("family"): score += 1
    if d.get("major"): score += 2
    if d.get("edition"): score += 3
    return score


def _category_text(data: Dict[str, Any]) -> str:
    cat = data.get("category")
    if isinstance(cat, str):
        return cat
    if isinstance(cat, dict):
        parts = [str(cat.get("slug") or ""), str(cat.get("title") or "")]
        for p in cat.get("path") or []:
            if isinstance(p, dict):
                parts.extend([str(p.get("slug") or ""), str(p.get("title") or "")])
            elif isinstance(p, str):
                parts.append(p)
        return " ".join(parts)
    if isinstance(cat, list):
        return " ".join(_category_text({"category": c}) for c in cat)
    return ""


def _extract_product_classification(data: Dict[str, Any]) -> Dict[str, Any]:
    notebook: List[Dict[str, Any]] = []
    desktop: List[Dict[str, Any]] = []
    accessory: List[Dict[str, Any]] = []
    compatibility_refs: List[Dict[str, Any]] = []

    cat_text = _category_text(data)
    has_explicit_accessory_category = bool(cat_text and _ACCESSORY_CATEGORY_RE.search(cat_text))
    has_explicit_accessory_title = bool(_ACCESSORY_RE.search(_get_text(data, "title")))
    if cat_text:
        if has_explicit_accessory_category:
            accessory.append({"source": "product.category", "excerpt": cat_text, "via": "category", "strong": True})
        if _DESKTOP_CATEGORY_RE.search(cat_text) and not has_explicit_accessory_category:
            desktop.append({"source": "product.category", "excerpt": cat_text, "via": "category"})
        if _NOTEBOOK_CATEGORY_RE.search(cat_text) and not has_explicit_accessory_category:
            notebook.append({"source": "product.category", "excerpt": cat_text, "via": "category"})

    for key in ("title", "description"):
        text = _get_text(data, key)
        if not text:
            continue
        src = f"product.{key}"
        text_has_accessory = bool(_ACCESSORY_RE.search(text))
        text_has_notebook = bool(_COMPUTER_FORM_POSITIVE_RE.search(text))
        if text_has_accessory:
            accessory.append({"source": src, "excerpt": text, "via": "text", "strong": key == "title"})
            # Notebook/laptop mentions in accessory descriptions are compatibility
            # references, not evidence that the product itself is a notebook.
            if text_has_notebook and has_explicit_accessory_category and (key != "title" or has_explicit_accessory_title):
                compatibility_refs.append({"evidence_source": src, "evidence_excerpt": text, "kind": "compatible_notebook_reference"})
                text_has_notebook = False
        if _DESKTOP_RE.search(text) and not text_has_accessory:
            desktop.append({"source": src, "excerpt": text, "via": "text"})
        if text_has_notebook:
            notebook.append({"source": src, "excerpt": text, "via": "text"})

    for i, feat in enumerate(_get_kf(data)):
        src = f"product.key_features[{i}]"
        feat_has_accessory = bool(_ACCESSORY_RE.search(feat))
        feat_has_notebook = bool(_COMPUTER_FORM_POSITIVE_RE.search(feat))
        if feat_has_accessory:
            accessory.append({"source": src, "excerpt": feat, "via": "text", "strong": False})
            if feat_has_notebook and has_explicit_accessory_category:
                compatibility_refs.append({"evidence_source": src, "evidence_excerpt": feat, "kind": "compatible_notebook_reference"})
                feat_has_notebook = False
        if _DESKTOP_RE.search(feat) and not feat_has_accessory:
            desktop.append({"source": src, "excerpt": feat, "via": "text"})
        if feat_has_notebook:
            notebook.append({"source": src, "excerpt": feat, "via": "text"})

    sa = _get_sa(data)
    sa_notebook: List[Dict[str, Any]] = []
    sa_desktop: List[Dict[str, Any]] = []
    for key in ("computer-form", "computer_form", "form-factor", "form_factor", "product-type", "product_type"):
        if key in sa and sa[key] is not None:
            raw = _first_scalar(sa[key]) or ""
            src = f"product.structured_attributes.{key}"
            if _ACCESSORY_RE.search(raw):
                accessory.append({"source": src, "excerpt": raw, "via": "attribute", "strong": True})
            elif _DESKTOP_RE.search(raw):
                sa_desktop.append({"source": src, "excerpt": raw, "via": "attribute"})
                desktop.append({"source": src, "excerpt": raw, "via": "attribute"})
            elif _COMPUTER_FORM_POSITIVE_RE.search(raw):
                sa_notebook.append({"source": src, "excerpt": raw, "via": "attribute"})
                notebook.append({"source": src, "excerpt": raw, "via": "attribute"})

    has_accessory = bool(accessory)
    has_desktop = bool(desktop)
    has_notebook = bool(notebook)
    kinds = sum([has_accessory, has_desktop, has_notebook])

    # Genuine contradictory explicit structured computer-form evidence remains
    # ambiguous: do not let a weak/strong accessory signal override it, and do
    # not infer DESKTOP/ACCESSORY from weak cues.
    if sa_notebook and has_explicit_accessory_category:
        conflicts = []
        for kind, items in (("NOTEBOOK", notebook), ("DESKTOP", desktop), ("ACCESSORY", accessory)):
            for e in items:
                conflicts.append({"value": kind, "evidence_source": e["source"], "evidence_excerpt": _excerpt_str(e["excerpt"])})
        res = _nv(
            value="UNKNOWN",
            source="; ".join(e["source"] for e in (notebook + desktop + accessory)),
            excerpt=" | ".join(_excerpt_str(e["excerpt"]) for e in (accessory + sa_notebook)[:3]),
            conflicts=conflicts,
        )
        if compatibility_refs:
            res["compatibility_references"] = compatibility_refs
        return res

    # Independent explicit accessory category + accessory product title (or
    # explicit accessory structured text/title agreement with category) => ACCESSORY/VERIFIED.
    strong_accessory = [e for e in accessory if e.get("strong") or e.get("source") == "product.category" or e.get("source") == "product.title"]
    if has_explicit_accessory_category and has_explicit_accessory_title and not sa_notebook and not sa_desktop:
        primary = next((e for e in strong_accessory if e.get("source") == "product.category"), accessory[0])
        res = _verified("ACCESSORY", "product.category; product.title", f"{cat_text} | {_get_text(data, 'title')}")
        res["evidence_sources"] = [
            {"value": "ACCESSORY", "evidence_source": e["source"], "evidence_excerpt": _excerpt_str(e["excerpt"])}
            for e in accessory
        ]
        if compatibility_refs:
            res["compatibility_references"] = compatibility_refs
        return res

    if kinds == 0:
        return _nv(value="UNKNOWN")
    if kinds == 1:
        if has_accessory:
            # Avoid classifying as verified ACCESSORY from weak keyword-only
            # evidence without explicit accessory category+title agreement.
            has_strong = has_explicit_accessory_category and has_explicit_accessory_title
            if has_strong or (has_explicit_accessory_category and any(e.get("strong") for e in accessory)):
                e = accessory[0]
                res = _verified("ACCESSORY", e["source"], e["excerpt"])
                if compatibility_refs:
                    res["compatibility_references"] = compatibility_refs
                return res
            e = accessory[0]
            return _nv(value="UNKNOWN", source=e["source"], excerpt=e["excerpt"])
        if has_desktop:
            e = desktop[0]
            return _verified("DESKTOP", e["source"], e["excerpt"])
        e = notebook[0]
        return _verified("NOTEBOOK", e["source"], e["excerpt"])

    # Conflicting/ambiguous signals: preserve conflicts; never guess.
    conflicts = []
    for kind, items in (("NOTEBOOK", notebook), ("DESKTOP", desktop), ("ACCESSORY", accessory)):
        for e in items:
            conflicts.append({"value": kind, "evidence_source": e["source"], "evidence_excerpt": _excerpt_str(e["excerpt"])})
    all_items = notebook + desktop + accessory
    res = _nv(
        value="UNKNOWN",
        source="; ".join(e["source"] for e in all_items),
        excerpt=" | ".join(_excerpt_str(e["excerpt"]) for e in all_items[:3]),
        conflicts=conflicts,
    )
    if compatibility_refs:
        res["compatibility_references"] = compatibility_refs
    return res


def _cpu_tiers(text: str) -> Optional[Dict[str, Optional[str]]]:
    t = " ".join(text.replace("™", " ").replace("®", " ").split())
    m = re.search(
        r"\bAMD\s+Ryzen\s+(AI)\s+(\d+)(?:\s+(?:PRO\s+)?)?(\d{3,4}[A-Za-z0-9]*)?\b", t, re.I
    )
    if m:
        family = "AMD Ryzen AI"
        subfamily = f"AMD Ryzen AI {m.group(2)}"
        model = f"{subfamily} {m.group(3)}" if m.group(3) else None
        return {"family": family, "subfamily": subfamily, "model": model}
    m = re.search(r"\bAMD\s+Ryzen\s+(AI)\b(?:\s+Max)?\b", t, re.I)
    if m:
        return {"family": "AMD Ryzen AI", "subfamily": None, "model": None}
    m = re.search(r"\bAMD\s+Ryzen\s+(\d)\b(?:\s+(\d{3,4}[A-Za-z0-9]*))?", t, re.I)
    if m:
        family = f"AMD Ryzen {m.group(1)}"
        model = f"{family} {m.group(2)}" if m.group(2) else None
        return {"family": family, "subfamily": None, "model": model}
    m = re.search(r"\bIntel\s+Core\s+Ultra\s+([3579])\b(?:\s+(\d{3,4}[A-Za-z]*))?", t, re.I)
    if m:
        family = f"Intel Core Ultra {m.group(1)}"
        model = f"{family} {m.group(2)}" if m.group(2) else None
        return {"family": family, "subfamily": None, "model": model}
    m = re.search(r"\bIntel\s+Core\s+(i[3579])\b(?:[- ](\d{4,5}[A-Za-z]*))?", t, re.I)
    if m:
        family = f"Intel Core {m.group(1)}"
        model = f"{family}-{m.group(2)}" if m.group(2) else None
        return {"family": family, "subfamily": None, "model": model}
    m = re.search(r"\bQualcomm\s+Snapdragon\s+X\b(?:\s+(Plus|Elite))?", t, re.I)
    if m:
        family = "Qualcomm Snapdragon X"
        subfamily = f"Qualcomm Snapdragon X {m.group(1)}" if m.group(1) else None
        return {"family": family, "subfamily": subfamily, "model": None}
    m = re.search(r"\bApple\s+(M[1-4])\b(?:\s+(Pro|Max|Ultra))?", t, re.I)
    if m:
        family = f"Apple {m.group(1)}"
        subfamily = f"Apple {m.group(1)} {m.group(2)}" if m.group(2) else None
        return {"family": family, "subfamily": subfamily, "model": None}
    return None


def _is_gpu_only_evidence(text: str) -> bool:
    if not text:
        return False
    # Intel Arc Graphics / GeForce / Radeon etc. are GPU evidence.
    if not _GPU_EVIDENCE_RE.search(text):
        return False
    # If a CPU tier is also clearly present, keep it for CPU parsing.
    if _cpu_tiers(text):
        return False
    return True


def _compatible_cpu_tiers(items: List[Dict[str, Any]]) -> bool:
    tiers = [i["tiers"] for i in items]
    families = {t.get("family") for t in tiers if t.get("family")}
    if len(families) != 1:
        return False
    subfamilies = {t.get("subfamily") for t in tiers if t.get("subfamily")}
    if len(subfamilies) > 1:
        return False
    if subfamilies:
        sub = next(iter(subfamilies))
        # all subfamilies (when present) must be compatible with the common one
        for t in tiers:
            if t.get("subfamily") and t["subfamily"] != sub:
                return False
            if t.get("model") and not str(t["model"]).startswith(sub):
                return False
    models = {t.get("model") for t in tiers if t.get("model")}
    if len(models) > 1:
        return False
    return True


def _merge_cpu_items(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    best = None
    best_score = -1
    for i in items:
        t = i["tiers"]
        score = (1 if t.get("family") else 0) + (1 if t.get("subfamily") else 0) + (2 if t.get("model") else 0)
        if score > best_score:
            best_score = score
            best = i
    assert best is not None
    tiers = best["tiers"]
    family = tiers.get("family")
    display = tiers.get("model") or tiers.get("subfamily") or family or _excerpt_str(best["excerpt"])
    res = _verified(display, best["source"], best["excerpt"])
    res["cpu_hierarchy"] = {"family": family, "subfamily": tiers.get("subfamily"), "model": tiers.get("model")}
    # preserve all original evidence
    res["evidence_sources"] = [
        {"value": i["compat_value"], "evidence_source": i["source"], "evidence_excerpt": _excerpt_str(i["excerpt"])}
        for i in items
    ]
    return res


def _extract_processor(data: Dict[str, Any]) -> Dict[str, Any]:
    cpu_candidates: List[Dict[str, Any]] = []
    gpu_evidence: List[Dict[str, Any]] = []

    def add_raw(raw: Any, source: str, require_cpu_context: bool = False) -> None:
        text = _first_scalar(raw)
        if not text:
            return
        if _is_gpu_only_evidence(text):
            gpu_evidence.append({"evidence_source": source, "evidence_excerpt": _excerpt_str(text), "value": _excerpt_str(text)})
            return
        if require_cpu_context and not (_CPU_CONTEXT_RE.search(text) or _cpu_tiers(text)):
            return
        tiers = _cpu_tiers(text)
        if not tiers:
            # fallback: keep prior explicit extraction for compatibility where
            # pattern exists but tiers are not modeled; still avoid GPU-only.
            legacy = _parse_processor(text)
            if legacy and not _is_gpu_only_evidence(legacy):
                cpu_candidates.append({
                    "compat_value": legacy,
                    "tiers": {"family": legacy, "subfamily": None, "model": None},
                    "source": source,
                    "excerpt": text,
                })
            return
        compat_value = tiers.get("model") or tiers.get("subfamily") or tiers["family"]
        cpu_candidates.append({"compat_value": compat_value, "tiers": tiers, "source": source, "excerpt": text})

    specs = _get_specs(data)
    for key in ("processor_family", "processor-family", "cpu_family", "cpu", "processor"):
        if key in specs and specs[key] is not None:
            add_raw(specs[key], f"product.specs.{key}")
    sa = _get_sa(data)
    for key, val_raw in sa.items():
        if key in _PROCESSOR_FAMILY_KEYS or "processor" in key or key in ("cpu",):
            add_raw(val_raw, f"product.structured_attributes.{key}")
    for i, feat in enumerate(_get_kf(data)):
        add_raw(feat, f"product.key_features[{i}]", require_cpu_context=True)
    for key in ("title", "description"):
        text = _get_text(data, key)
        if text:
            add_raw(text, f"product.{key}", require_cpu_context=True)

    if not cpu_candidates:
        res = _nv()
        if gpu_evidence:
            res["gpu_evidence"] = gpu_evidence
        return res

    # de-duplicate by compat value + source to keep provenance stable
    distinct: Dict[str, Dict[str, Any]] = {}
    for c in cpu_candidates:
        k = repr((c["compat_value"], c["source"], _excerpt_str(c["excerpt"])))
        distinct[k] = c
    items = list(distinct.values())

    if len(items) == 1 or _compatible_cpu_tiers(items):
        res = _merge_cpu_items(items)
        if gpu_evidence:
            res["gpu_evidence"] = gpu_evidence
        return res

    conflicts = [
        {"value": c["compat_value"], "evidence_source": c["source"], "evidence_excerpt": _excerpt_str(c["excerpt"])}
        for c in items
    ]
    res = _nv(
        value=None,
        source="; ".join(c["source"] for c in items),
        excerpt=" | ".join(_excerpt_str(c["excerpt"]) for c in items),
        conflicts=conflicts,
    )
    if gpu_evidence:
        res["gpu_evidence"] = gpu_evidence
    return res


def _parse_processor(text: str) -> Optional[str]:
    # Backwards-compatible helper: return best CPU-compatible explicit string;
    # never return GPU-only evidence.
    if _is_gpu_only_evidence(text):
        return None
    tiers = _cpu_tiers(text)
    if tiers:
        return tiers.get("model") or tiers.get("subfamily") or tiers.get("family")
    m = _PROCESSOR_EXPLICIT_RE.search(text)
    if not m:
        return None
    return " ".join(m.group(1).split())
