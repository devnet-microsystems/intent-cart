import json
import re
import time
import random
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from .config import settings


class Mandate(BaseModel):
    model_config = {'extra': 'allow'}
    query: str = Field(min_length=1)
    max_price: Optional[float] = None
    currency: str = "EUR"

    @field_validator("currency", mode="before")
    @classmethod
    def _currency_default(cls, v):
        if v is None or v == "":
            return "EUR"
        return v
    allowed_conditions: List[str] = Field(default_factory=list)
    forbidden_conditions: List[str] = Field(default_factory=list)
    min_specs: Dict[str, Any] = Field(default_factory=dict)
    size: Optional[str] = None
    material: Optional[str] = None
    material_percentage: Optional[int] = None
    color: Optional[str] = None
    clarification_required: Optional[Any] = None
    condition: Optional[str] = None
    # Laptop vertical (P1 Step 4A): additive, optional, schema-only. Do NOT map
    # these fields into min_specs or policy decisions until Step 4B.
    product_type: Optional[str] = None
    min_ram_gb: Optional[int] = None
    min_storage_gb: Optional[int] = None
    required_storage_interface: Optional[str] = None
    required_processor_family: Optional[str] = None
    required_processor_subfamily: Optional[str] = None
    required_processor_model: Optional[str] = None
    required_operating_system: Optional[str] = None

    @field_validator("min_specs", mode="before")
    @classmethod
    def _min_specs_default(cls, v):
        # Real Gemini responses may return null or an empty list when there are no
        # spec constraints. Treat only these semantically empty values as absence
        # ({}); never silently coerce malformed non-dictionary/non-empty values.
        if v is None:
            return {}
        if isinstance(v, list) and len(v) == 0:
            return {}
        return v

    @field_validator("max_price", mode="before")
    @classmethod
    def _validate_price(cls, v):
        if isinstance(v, str):
            raise ValueError("max_price must be numeric, not currency text")
        return v

    @field_validator("min_ram_gb", "min_storage_gb")
    @classmethod
    def _validate_positive_capacity(cls, v):
        if v is None:
            return v
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError("capacity must be a positive integer")
        if v <= 0:
            raise ValueError("capacity must be a positive integer")
        return v

    @model_validator(mode="after")
    def normalize_condition(self):
        """Move any explicit `condition` value into the canonical `allowed_conditions` list."""
        raw_condition = self.__dict__.get("condition")
        if raw_condition and isinstance(raw_condition, str) and raw_condition.strip():
            cond = raw_condition.strip()
            existing = [c.lower() if isinstance(c, str) else c for c in (self.allowed_conditions or [])]
            if cond.lower() not in existing:
                self.allowed_conditions = [*self.allowed_conditions, cond]
            # Preserve original wording separately
            self.condition_original = cond
            # Clear the non-canonical field so it cannot silently weaken the intent
            self.condition = None
        return self
def _normalize_storage_interface_requirement(value: Any, user_text: str = "") -> Optional[str]:
    """Only keep explicit storage interface requirements (NVMe/SATA/PCIe NVMe).

    SSD/HDD describe storage media, not an interface; never infer NVMe from SSD.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    low = raw.lower()
    has_nvme = "nvme" in low
    has_pcie = "pcie" in low or "pci-e" in low or "pci express" in low
    has_sata = "sata" in low
    # Reject generic media labels when no explicit interface is present.
    if not (has_nvme or has_pcie or has_sata):
        if low in {"ssd", "hdd", "hard drive", "hard disk", "solid state drive"}:
            return None
        # Unknown/ambiguous value from Gemini: keep only if user text explicitly
        # states an interface token; otherwise fail closed to unspecified.
        user_low = (user_text or "").lower()
        if not re.search(r"\b(nvme|pcie|pci-e|pci express|sata)\b", user_low):
            return None
        return raw
    if has_sata:
        return "SATA"
    if has_nvme and has_pcie:
        return "PCIe NVMe"
    if has_nvme:
        return "NVMe"
    if has_pcie:
        return "PCIe"
    return None


def _canonicalize_cpu_requirements(data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize processor requirements to the existing laptop hierarchy.

    Preserves explicit user intent (family requested => family kept). Does not
    invent exact model suffixes and keeps GPU names out of CPU requirements.
    Handles malformed Gemini splits (e.g. family="Intel", subfamily="Core Ultra",
    model="Ultra 7") by reconstructing canonical hierarchy from explicit text.
    """
    fam = data.get("required_processor_family")
    sub = data.get("required_processor_subfamily")
    model = data.get("required_processor_model")
    if not any(isinstance(x, str) and x.strip() for x in (fam, sub, model)):
        data["required_processor_family"] = fam if isinstance(fam, str) and fam.strip() else None
        data["required_processor_subfamily"] = sub if isinstance(sub, str) and sub.strip() else None
        data["required_processor_model"] = model if isinstance(model, str) and model.strip() else None
        return data

    def norm(s):
        return " ".join(str(s).replace("™", " ").replace("®", " ").split()).strip()

    fam_n = norm(fam) if isinstance(fam, str) else ""
    sub_n = norm(sub) if isinstance(sub, str) else ""
    model_n = norm(model) if isinstance(model, str) else ""
    combined = " ".join(x for x in (fam_n, sub_n, model_n) if x).strip()

    # Reject GPU-only accidental assignments; keep explicit CPU text.
    if re.search(r"\b(arc\s+graphics|geforce|rtx|gtx|radeon)\b", combined, re.I) and not re.search(
        r"\b(core\s+ultra|core\s+i\d|ryzen|snapdragon\s+x|apple\s+m\d|xeon)\b", combined, re.I
    ):
        data["required_processor_family"] = None
        data["required_processor_subfamily"] = None
        data["required_processor_model"] = None
        return data

    # AMD Ryzen AI hierarchy (parse from combined explicit text).
    # "AMD Ryzen AI" = family only; "AMD Ryzen AI 5" = family+subfamily;
    # "AMD Ryzen AI 5 340" = family+subfamily+model.
    amd_matches = list(re.finditer(r"\bAMD\s+Ryzen\s+AI\b(?:\s+(\d+))?(?:\s+(\d{3,4}[A-Za-z0-9]*))?", combined, re.I))
    # Also capture fragments Gemini may emit without the AMD prefix
    # (e.g. model="Ryzen AI 5 340" alongside family="AMD Ryzen AI").
    amd_frag_matches = list(re.finditer(r"\bRyzen\s+AI\s+(\d+)(?:\s+(\d{3,4}[A-Za-z0-9]*))?", combined, re.I))
    if amd_matches or amd_frag_matches or re.search(r"\bAMD\s+Ryzen\s+AI\b", combined, re.I):
        best = (None, None)
        best_score = -1
        for mm in amd_matches:
            series = mm.group(1)
            sku = mm.group(2)
            score = (2 if sku else 0) + (1 if series else 0)
            if score > best_score:
                best_score = score
                best = (series, sku)
        for mm in amd_frag_matches:
            series = mm.group(1)
            sku = mm.group(2)
            score = (2 if sku else 0) + (1 if series else 0)
            if score > best_score:
                best_score = score
                best = (series, sku)
        series, sku = best
        out_fam = "AMD Ryzen AI"
        out_sub = f"AMD Ryzen AI {series}" if series else None
        out_model = f"{out_sub} {sku}" if series and sku else None
        # Preserve explicit intent: bare "AMD Ryzen AI" family request.
        if re.fullmatch(r"AMD\s+Ryzen\s+AI", fam_n or "", re.I) and not sub_n and not model_n and not amd_frag_matches:
            out_sub = None
            out_model = None
        data.update({"required_processor_family": out_fam, "required_processor_subfamily": out_sub, "required_processor_model": out_model})
        return data

    # Intel Core Ultra: family is the series (Intel Core Ultra 7).
    # Only an explicit alnum SKU like 268V becomes model; "Ultra 7"/"7" alone
    # identifies the series, not a model SKU. Handles malformed splits such as
    # family="Intel", subfamily="Core Ultra", model="Ultra 7".
    m = re.search(r"\b(?:Intel\s+)?Core\s+Ultra\s+([3579])\b(?:\s+(\d{3,4}[A-Za-z]+))?", combined, re.I)
    if not m:
        # malformed split reconstruction
        mu = re.search(r"\b(?:Intel\s+)?Core\s+Ultra\b", combined, re.I)
        ms = re.search(r"\bUltra\s+([3579])\b|\b([3579])\s*(\d{3,4}[A-Za-z]+)\b", combined, re.I)
        sku_m = re.search(r"\b(\d{3,4}[A-Za-z]+)\b", combined)
        series = ms.group(1) if ms and ms.group(1) else (ms.group(2) if ms else None)
        sku = sku_m.group(1) if sku_m else None
        if mu and series:
            m = True
            out_fam = f"Intel Core Ultra {series}"
            data.update({"required_processor_family": out_fam, "required_processor_subfamily": None, "required_processor_model": f"{out_fam} {sku}" if sku else None})
            return data
    if m and m is not True:
        series = m.group(1)
        sku = m.group(2)
        out_fam = f"Intel Core Ultra {series}"
        out_sub = None
        out_model = f"{out_fam} {sku}" if sku else None
        data.update({"required_processor_family": out_fam, "required_processor_subfamily": out_sub, "required_processor_model": out_model})
        return data

    # Intel Core i5/i7 etc.
    m = re.search(r"\bIntel\s+Core\s+(i[3579])\b(?:[- ](\d{4,5}[A-Za-z]*))?", combined, re.I)
    if m:
        out_fam = f"Intel Core {m.group(1)}"
        out_sub = None
        out_model = f"{out_fam}-{m.group(2)}" if m.group(2) else None
        data.update({"required_processor_family": out_fam, "required_processor_subfamily": out_sub, "required_processor_model": out_model})
        return data

    # Generic/other explicit CPU text: preserve as family-level when unmodeled.
    data["required_processor_family"] = fam_n or None
    data["required_processor_subfamily"] = sub_n or None
    data["required_processor_model"] = model_n or None
    return data


def _normalize_extracted_mandate(data: Dict[str, Any], user_text: str = "") -> Dict[str, Any]:
    if not isinstance(data, dict):
        return data
    out = dict(data)
    # min_specs:null/[] handled by Mandate validator too, but normalize early for
    # consistent behavior. Only semantically empty values become {}; malformed
    # non-empty structures must fail validation.
    ms = out.get("min_specs")
    if ms is None or (isinstance(ms, list) and len(ms) == 0):
        out["min_specs"] = {}
    out["required_storage_interface"] = _normalize_storage_interface_requirement(
        out.get("required_storage_interface"), user_text
    )
    # Normalize British spelling to canonical `color`.
    if out.get("colour") is not None and out.get("color") is None:
        out["color"] = out.pop("colour")
    elif out.get("colour") is not None and out.get("color") is not None:
        # Both present: keep canonical `color`, drop `colour` to avoid ambiguity.
        out.pop("colour")
    out = _canonicalize_cpu_requirements(out)
    return out


def parse_intent(text: str) -> dict:
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY not configured")
    try:
        from google import genai
        from google.genai import types
    except Exception as exc:
        raise RuntimeError("google-genai not installed") from exc

    client = genai.Client(api_key=settings.gemini_api_key, http_options=types.HttpOptions(timeout=30000, retry_options=types.HttpRetryOptions(attempts=1)))
    prompt = (
        "Extract a purchasing mandate as JSON matching the provided schema. "
        "query must be a non-empty search string. max_price must be a number without currency symbols, or null if the user did not specify a budget. "
        "currency must be an ISO code such as EUR. "
        "Extract every explicit constraint: size, material, material_percentage, style, color, forbidden designs, condition, and technical specs. "
        "Laptop/tech constraints (set only when explicitly stated): set product_type=\"laptop\" only when the user explicitly requests a laptop, notebook, or ThinkPad; "
        "set min_ram_gb/min_storage_gb as positive integers only for explicit minimum capacities (parse units like GB/TB into integer GB values where unambiguous); "
        "set required_storage_interface only when an explicit interface is stated (NVMe, PCIe NVMe, SATA); SSD/HDD describe storage media, not interfaces — never set required_storage_interface to SSD/HDD and never infer NVMe from SSD; "
        "set required_processor_family/subfamily/model canonically from explicitly stated processor details: "
        "for \"Intel Core Ultra 7\" use family=\"Intel Core Ultra 7\", subfamily=null, model=null unless an exact model like 268V is specified; "
        "for \"Intel Core Ultra 7 268V\" use family=\"Intel Core Ultra 7\", model=\"Intel Core Ultra 7 268V\"; "
        "for \"AMD Ryzen AI 5 340\" use family=\"AMD Ryzen AI\", subfamily=\"AMD Ryzen AI 5\", model=\"AMD Ryzen AI 5 340\". "
        "Do not invent processor model details and keep GPU names separate from CPU requirements; "
        "set required_operating_system only when explicitly stated, and Do not infer Windows merely because the user asked for a laptop. "
        "Do not map min_ram_gb/min_storage_gb into min_specs. "
        "Do not invent missing requirements. Leave unspecified constraints as null. "
        "If a mandatory requirement cannot be represented, set clarification_required. "
        "User request: " + text
    )
    transient = {408, 429, 500, 502, 503, 504}
    last_exc = None
    deadline = time.time() + 90
    for attempt in range(5):
        if time.time() >= deadline:
            break
        try:
            resp = client.models.generate_content(
                model=settings.gemini_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                ),
            )
            last_exc = None
            break
        except Exception as exc:
            status = getattr(exc, "code", None) or getattr(getattr(exc, "response", None), "status_code", None)
            if status in transient and attempt < 4 and time.time() < deadline:
                last_exc = exc
                remaining = max(0, deadline - time.time())
                delay = min(remaining, min(8, (2 ** attempt)) + random.random())
                time.sleep(delay)
                continue
            raise RuntimeError(f"Gemini request failed: {exc}") from exc
    if last_exc is not None:
        raise RuntimeError(f"Gemini request failed: {last_exc}") from last_exc

    try:
        data = resp.parsed
        if data is None:
            data = json.loads(resp.text)
        if isinstance(data, Mandate):
            data = data.model_dump()
        elif isinstance(data, dict):
            data = _normalize_extracted_mandate(data, text)
            data = Mandate.model_validate(data).model_dump()
        else:
            raise ValueError("invalid structured response")
    except Exception as exc:
        # Fail gracefully on invalid Gemini output; API layer converts to HTTP error.
        raise RuntimeError(f"Invalid Gemini response: {exc}") from exc
    return data
