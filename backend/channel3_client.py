import asyncio
import httpx

from .config import settings

# Canonical color name → sRGB hex mapping for Channel3 filters.colors.palette.
# Only well-established mappings are included; unknown colors are omitted
# rather than inventing a hex value.
_COLOR_TO_HEX = {
    "black": "#000000",
    "white": "#ffffff",
    "red": "#ff0000",
    "blue": "#0000ff",
    "green": "#008000",
    "yellow": "#ffff00",
    "orange": "#ffa500",
    "purple": "#800080",
    "pink": "#ffc0cb",
    "brown": "#a52a2a",
    "gray": "#808080",
    "grey": "#808080",
    "navy": "#000080",
    "cyan": "#00ffff",
    "magenta": "#ff00ff",
    "lime": "#00ff00",
    "olive": "#808000",
    "teal": "#008080",
    "maroon": "#800000",
    "silver": "#c0c0c0",
    "gold": "#ffd700",
    "beige": "#f5f5dc",
    "cream": "#fffdd0",
    "khaki": "#f0e68c",
    "salmon": "#fa8072",
    "coral": "#ff7f50",
    "turquoise": "#40e0d0",
    "indigo": "#4b0082",
    "violet": "#ee82ee",
    "ivory": "#fffff0",
    "plum": "#dda0dd",
    "orchid": "#da70d6",
    "crimson": "#dc143c",
    "scarlet": "#ff2400",
    "ruby": "#e0115f",
    "emerald": "#50c878",
    "jade": "#00a86b",
    "mint": "#98ff98",
    "peach": "#ffcc99",
    "apricot": "#ffb347",
    "tan": "#d2b48c",
    "taupe": "#483c32",
    "charcoal": "#36454f",
    "slate": "#708090",
    "lavender": "#e6e6fa",
    "periwinkle": "#ccccff",
    "bronze": "#cd7f32",
    "copper": "#b87333",
}


def color_to_hex(color_name: str) -> str | None:
    """Map a color name to its sRGB hex code for Channel3 filters.colors.palette.

    Returns None for unknown colors — callers must not invent hex values.
    """
    if not color_name or not isinstance(color_name, str):
        return None
    return _COLOR_TO_HEX.get(color_name.strip().lower())


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


def build_channel3_filters(mandate: dict) -> dict | None:
    """Build Channel3 search filters from a locked mandate.

    Only filters with a known official format are included:
    - price.max_price (numeric)
    - colors.palette ([{"hex": "#000000"}] — objects, never bare strings)

    Non-color attributes (size, style, pack quantity) are NOT passed as
    structured filters because their Channel3 handles must be discovered
    from the category catalog first. They remain in the query text so the
    upstream can still match them semantically.
    """
    filters = {}

    max_price = mandate.get("max_price")
    if isinstance(max_price, (int, float)) and not isinstance(max_price, bool) and max_price > 0:
        filters["price"] = {"max_price": float(max_price)}

    color = mandate.get("color")
    hex_color = color_to_hex(color) if color else None
    if hex_color:
        filters["colors"] = {"palette": [{"hex": hex_color}]}

    return filters if filters else None


class Channel3Client:
    def __init__(self, timeout: float = 15.0):
        self.base_url = settings.channel3_base_url.rstrip("/")
        self.timeout = timeout

    def _headers(self) -> dict:
        return {"x-api-key": settings.channel3_api_key, "Content-Type": "application/json"}

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        if not settings.channel3_api_key:
            raise RuntimeError("CHANNEL3_API_KEY not configured")
        url = f"{self.base_url}{path}"
        headers = dict(self._headers())
        if "headers" in kwargs:
            headers.update(kwargs.pop("headers"))
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.request(method, url, headers=headers, **kwargs)
        return resp

    async def search(
        self,
        query: str,
        filters: dict | None = None,
        country: str = "IT",
        currency: str = "EUR",
        language: str = "en",
    ) -> list:
        """Search products on Channel3.

        Raises httpx.HTTPStatusError on non-2xx responses (including 422),
        so callers can distinguish a malformed filter from an empty result.
        """
        body = {"query": query, "config": {"country": country, "currency": currency, "language": language}}
        if filters:
            body["filters"] = filters
        resp = await self._request("POST", "/v1/search", json=body)
        resp.raise_for_status()
        data = resp.json()
        return data.get("results", data.get("products", []))

    async def resolve_category_id(self, category_slug: str) -> str | None:
        """Discover the Channel3 category ID for a given slug via /v1/categories/search.

        Never invent category IDs. Returns None if the category is not found
        or the API is unavailable.
        """
        try:
            resp = await self._request(
                "GET", "/v1/categories/search", params={"slug": category_slug}
            )
        except Exception:
            return None
        if resp.status_code != 200:
            return None
        try:
            data = resp.json()
        except Exception:
            return None
        categories = data.get("categories") or data.get("results") or []
        for cat in categories:
            if isinstance(cat, dict) and cat.get("slug") == category_slug:
                return cat.get("id") or cat.get("category_id")
        return None

    async def get_product_details(
        self,
        product_id: str,
        country: str = "IT",
        currency: str = "EUR",
        language: str = "en",
        variant_options: dict | None = None,
    ) -> dict | None:
        """Fetch full product details via GET /v1/products/{id}.

        Returns the product dict, or None if not found (404) or unavailable.
        Distinguishes 404 (not found) from transient errors (raises).

        When ``variant_options`` is provided (e.g. {"Size": "10.5-12"}), the
        returned product's ``variants`` is narrowed to the matching option so
        callers can verify the requested variant's availability without
        inferring from unselected options.
        """
        params = {"country": country, "currency": currency, "language": language}
        resp = await self._request("GET", f"/v1/products/{product_id}", params=params)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        try:
            data = resp.json()
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        if variant_options:
            data = self._apply_variant_options(data, variant_options)
        return data

    @staticmethod
    def _is_size_option_name(name: str) -> bool:
        return isinstance(name, str) and name.strip().lower() in ("size", "taglia", "taille")

    @staticmethod
    def _find_size_option_name(variants: dict | None) -> str | None:
        """Find the variant option key that represents size (e.g. "Taille", "Size", "Taglia")."""
        if not isinstance(variants, dict):
            return None
        options = variants.get("options") or []
        if not isinstance(options, list):
            return None
        for opt in options:
            if not isinstance(opt, dict):
                continue
            name = opt.get("name")
            if Channel3Client._is_size_option_name(name):
                return name
        return None

    @staticmethod
    def _apply_variant_options(product: dict, variant_options: dict | None) -> dict:
        """Narrow product variants to the requested option(s).

        Returns a shallow copy of the product with variants.options filtered
        to only the requested option name/value and variants.selected updated
        when the requested value is found among the options.
        """
        if not variant_options or not isinstance(product, dict):
            return product
        import copy

        result = copy.deepcopy(product)
        variants = result.get("variants")
        if not isinstance(variants, dict):
            return result
        options = variants.get("options")
        if not isinstance(options, list):
            return result

        matched_options = []
        for opt in options:
            if not isinstance(opt, dict):
                matched_options.append(opt)
                continue
            opt_name = opt.get("name")
            if not isinstance(opt_name, str):
                matched_options.append(opt)
                continue

            # Find the requested value for this option, matching size-related
            # names semantically (Size/Taille/Taglia).
            requested_value = None
            for req_key, req_val in variant_options.items():
                if not isinstance(req_key, str):
                    continue
                if Channel3Client._is_size_option_name(req_key) and Channel3Client._is_size_option_name(opt_name):
                    requested_value = str(req_val).strip() if req_val is not None else None
                    break
                elif req_key.strip().lower() == opt_name.strip().lower():
                    requested_value = str(req_val).strip() if req_val is not None else None
                    break

            if requested_value is None:
                matched_options.append(opt)
                continue

            filtered_values = []
            for val in opt.get("values") or []:
                if not isinstance(val, dict):
                    continue
                label = val.get("label")
                if label is not None and str(label).strip() == requested_value:
                    filtered_values.append(val)
            matched = dict(opt)
            matched["values"] = filtered_values
            matched_options.append(matched)

        result_variants = dict(variants)
        result_variants["options"] = matched_options
        result["variants"] = result_variants
        return result

    async def get_product_details_batch(
        self,
        product_ids: list[str],
        max_concurrency: int = 5,
        country: str = "IT",
        currency: str = "EUR",
        language: str = "en",
        variant_options: dict | None = None,
    ) -> dict[str, dict | None]:
        """Fetch details for multiple products with bounded concurrency.

        Returns {product_id: product_dict_or_None}.
        Retries only on transient errors (429, 5xx), respects Retry-After.
        """
        semaphore = asyncio.Semaphore(max_concurrency)
        transient = {429, 500, 502, 503, 504}
        max_retries = 2

        async def fetch_one(pid: str) -> tuple[str, dict | None]:
            async with semaphore:
                for attempt in range(max_retries + 1):
                    try:
                        resp = await self._request(
                            "GET",
                            f"/v1/products/{pid}",
                            params={"country": country, "currency": currency, "language": language},
                        )
                    except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout):
                        if attempt < max_retries:
                            await asyncio.sleep(min(2 ** attempt, 5))
                            continue
                        return pid, None
                    except httpx.HTTPStatusError as exc:
                        if exc.response.status_code == 404:
                            return pid, None
                        if exc.response.status_code in transient and attempt < max_retries:
                            retry_after = exc.response.headers.get("Retry-After")
                            delay = float(retry_after) if retry_after else min(2 ** attempt, 5)
                            await asyncio.sleep(min(delay, 5))
                            continue
                        return pid, None
                    if resp.status_code == 404:
                        return pid, None
                    resp.raise_for_status()
                    try:
                        data = resp.json()
                    except Exception:
                        return pid, None
                    if not isinstance(data, dict):
                        return pid, None
                    if variant_options:
                        data = self._apply_variant_options(data, variant_options)
                    return pid, data
                return pid, None

        results = await asyncio.gather(*[fetch_one(pid) for pid in product_ids])
        return dict(results)
