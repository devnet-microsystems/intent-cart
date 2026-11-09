import { useState, useEffect } from "react";
import { AgGridReact } from "ag-grid-react";
import { themeQuartz, colorSchemeDarkBlue, ModuleRegistry, AllCommunityModule } from "ag-grid-community";

ModuleRegistry.registerModules([AllCommunityModule]);
import StatusPill from "./StatusPill.jsx";
import PayPalButton from "./PayPalButton.jsx";
import { apiFetch } from "./api/client.js";
import "./App.css";

const intentCartTheme = themeQuartz
  .withPart(colorSchemeDarkBlue)
  .withParams({
    headerBackgroundColor: "#0d1420",
    headerTextColor: "#f4f7fb",
    borderColor: "#1c2740",
    rowHoverColor: "#111a2b",
  });

function normalizeResult(r) {
  const p = r.product || {};
  const offer = Array.isArray(p.offers) && p.offers.length ? p.offers[0] : null;
  return {
    ...r,
    product: {
      ...p,
      name: p.title ?? p.name,
      retailer: (Array.isArray(p.brands) && p.brands[0]?.name) || p.retailer,
      offer: offer ? { offer_id: offer.offer_id, price: offer.price?.price, currency: offer.price?.currency } : p.offer,
      condition: offer?.condition ?? p.condition,
      availability: offer?.availability ?? p.availability,
    },
  };
}

export default function App() {
  const [rows, setRows] = useState([]);
  const [selected, setSelected] = useState(null);
  const [executions, setExecutions] = useState([]);
  const [current, setCurrent] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [searchText, setSearchText] = useState("");
  const [sessionId, setSessionId] = useState(null);
  const [mandate, setMandate] = useState(null);
  const [locked, setLocked] = useState(false);
  const [lockedMandate, setLockedMandate] = useState(null);
  const [lockedMandateText, setLockedMandateText] = useState(null);
  const [mandateOriginalText, setMandateOriginalText] = useState(null);
  const mandateView = mandate || lockedMandate || current?.mandate;
  const mandateViewSource = mandate ? "mandate" : lockedMandate ? "lockedMandate" : current?.mandate ? "current" : "none";
  const [busy, setBusy] = useState(false);
  const [showTechnical, setShowTechnical] = useState(false);
  const [category, setCategory] = useState("other");
  const [filters, setFilters] = useState({ product_type: "", size: "", material: "", material_percentage: "", style: "", forbidden_designs: "", max_price: "", currency: "EUR", color: "" });
  const [showAll, setShowAll] = useState(false);
  const [isSearching, setIsSearching] = useState(false);
  const [showDiagnostics, setShowDiagnostics] = useState(false);
  const [ppReady, setPpReady] = useState(false);
  const [ppLoading, setPpLoading] = useState(false);
  const [ppSdkLoaded, setPpSdkLoaded] = useState(false);
  const [ppSdkError, setPpSdkError] = useState(null);

  function summarizeStatus(results) {
    if (!results || results.length === 0) return null;
    const approved = results.filter((r) => r.gate?.decision === "APPROVED").length;
    const rejected = results.filter((r) => r.gate?.decision === "REJECTED").length;
    const needs = results.filter((r) => r.gate?.decision === "NEEDS_CONFIRMATION").length;
    const parts = [];
    if (approved) parts.push(`${approved} approved`);
    if (needs) parts.push(`${needs} needs confirmation`);
    if (rejected) parts.push(`${rejected} blocked`);
    return parts.length ? parts.join(" · ") : "evaluated";
  }

  function formatTimestamp(iso) {
    if (!iso) return "";
    try {
      return new Date(iso).toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch (e) {
      return iso;
    }
  }

  function enrichExecution(entry, detail) {
    return {
      ...entry,
      query: detail.mandate?.query,
      product_count: detail.results?.length ?? 0,
      status_summary: summarizeStatus(detail.results),
    };
  }

  const visibleSearches = [...executions]
    .sort((a, b) => (b.timestamp || "").localeCompare(a.timestamp || ""))
    .slice(0, showAll ? executions.length : 5);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        setLoading(true);
        const res = await apiFetch("/api/executions");
        if (!res.ok) throw new Error("Failed to load executions");
        const list = await res.json();
        if (cancelled) return;
        setExecutions(list);
        if (list.length > 0) {
          // Select the newest execution that has actual products.
          const withResults = list.filter((e) => (e.results_count ?? 0) > 0);
          if (withResults.length > 0) {
            const toLoad = withResults[0];
            const detailRes = await apiFetch(`/api/executions/${toLoad.execution_id}`);
            if (!detailRes.ok) throw new Error("Failed to load execution");
            const detail = await detailRes.json();
            if (cancelled) return;
            setCurrent(detail);
            setRows((detail.results || []).map((r, i) => ({ ...normalizeResult(r), execution_id: detail.execution_id })));
            setExecutions((prev) => prev.map((e) => (e.execution_id === detail.execution_id ? enrichExecution(e, detail) : e)));
            // Restore sessionId only when the execution has an explicit session relationship.
            if (detail.session_id) {
              setSessionId(detail.session_id);
            } else {
              setSessionId(null);
            }
          }
        }
      } catch (e) {
        if (!cancelled) setError(e.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!import.meta.env.DEV) return;
    const handleKey = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === "D" || e.key === "d")) {
        e.preventDefault();
        setShowDiagnostics((prev) => !prev);
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, []);

  const runIntent = async () => {
    if (!searchText.trim()) return;
    setBusy(true); setError(null);
    try {
      const structured = category === "clothing_accessories" ? {
        product_type: filters.product_type || null,
        size: filters.size || null,
        material: filters.material || null,
        material_percentage: filters.material_percentage ? Number(filters.material_percentage) : null,
        style: filters.style || null,
        color: filters.color || null,
        forbidden_designs: filters.forbidden_designs ? filters.forbidden_designs.split(",").map(s=>s.trim()).filter(Boolean) : [],
        max_price: filters.max_price === "" ? null : Number(filters.max_price),
        currency: filters.currency || "EUR",
      } : null;
      const res = await apiFetch("/api/intent", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ query: searchText, category, structured_filters: structured }) });
      if (!res.ok) throw new Error("Intent extraction failed");
      const data = await res.json();
      if (import.meta.env.DEV) {
        console.log("[DEBUG] runIntent response mandate:", JSON.stringify(data.mandate, null, 2));
        console.log("[DEBUG] runIntent allowed_conditions:", JSON.stringify(data.mandate?.allowed_conditions));
      }
      setSessionId(data.session_id);
      setMandate(data.mandate);
      setMandateOriginalText(searchText);
      setLocked(false);
      setLockedMandate(null);
      setLockedMandateText(null);
      setSelected(null);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  const lockMandate = async () => {
    if (!sessionId) return;
    setBusy(true); setError(null);
    try {
      const res = await apiFetch(`/api/mandate/${sessionId}/lock`, { method: "POST" });
      if (!res.ok) throw new Error("Lock failed");
      const data = await res.json();
      setMandate(data.mandate);
      setLockedMandate(data.mandate);
      setLockedMandateText(searchText);
      setLocked(Boolean(data.locked));
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  const runSearch = async () => {
    if (!sessionId || !locked) { setError("Lock your purchasing mandate before searching."); return; }
    if (lockedMandateText !== searchText) { setError("Intent changed since lock. Review requirements again."); return; }
    if (isSearching) return;
    setIsSearching(true);
    setBusy(true); setError(null);
    setSelected(null);
    try {
      const res = await apiFetch(`/api/search/${sessionId}`, { method: "POST" });
      if (!res.ok) throw new Error("Search failed");
      const data = await res.json();
      const normalized = (data.results || []).map((r, i) => ({ ...normalizeResult(r), execution_id: data.execution_id }));
      setRows(normalized);
      setSelected(
        normalized.find((r) => r.gate?.decision === "APPROVED") || normalized[0] || null
      );
      setExecutions((prev) => {
        const entry = {
          execution_id: data.execution_id,
          timestamp: new Date().toISOString(),
          query: mandate?.query,
          product_count: data.results?.length ?? 0,
          status_summary: summarizeStatus(data.results),
        };
        return [entry, ...prev.filter((e) => e.execution_id !== data.execution_id)];
      });
    } catch (e) { setError(e.message); }
    finally { setBusy(false); setIsSearching(false); }
  };

  const selectExecution = async (id) => {
    try {
      setLoading(true);
      const res = await apiFetch(`/api/executions/${id}`);
      if (!res.ok) throw new Error("Failed to load execution");
      const detail = await res.json();
          setCurrent(detail);
          setRows((detail.results || []).map((r, i) => ({ ...normalizeResult(r), execution_id: detail.execution_id })));
          setExecutions((prev) => prev.map((e) => (e.execution_id === detail.execution_id ? enrichExecution(e, detail) : e)));
          if (detail.session_id) {
            setSessionId(detail.session_id);
          } else {
            setSessionId(null);
          }
          // Auto-select first product when loading from history
          if (detail.results && detail.results.length > 0) {
            const normalized = (detail.results || []).map((r, i) => ({ ...normalizeResult(r), execution_id: detail.execution_id }));
            setSelected(normalized[0]);
          }
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const approved = rows.filter((r) => r.gate?.decision === "APPROVED").length;
  const blocked = rows.filter((r) => r.gate?.decision === "REJECTED").length;
  const isApproved = selected?.gate?.decision === "APPROVED";
  const needsConfirmation = selected?.gate?.decision === "NEEDS_CONFIRMATION";
  const ppApproved = locked === true && selected?.gate?.decision === "APPROVED" && Boolean(sessionId) && Boolean(selected?.execution_id) && Boolean(selected?.offer?.offer_id);
  let ppReason = "";
  if (!selected) ppReason = "PayPalButton not mounted: selected is null";
  else if (!sessionId) ppReason = "sessionId is empty";
  else if (!selected?.execution_id) ppReason = "execution_id is empty";
  else if (!selected?.offer?.offer_id) ppReason = "offer_id is empty";
  else if (locked !== true) ppReason = "mandate is not locked — a new locked mandate is required";
  else if (selected?.gate?.decision !== "APPROVED") ppReason = `gate.decision is "${selected?.gate?.decision}", not APPROVED`;
  else ppReason = "All conditions met — PayPal should render";

  function ProductCellRenderer({ value, data }) {
    const p = value || (data && data.product) || {};
    const img = Array.isArray(p.images) && p.images.length ? p.images[0] : null;
    const src = img?.cleaned_url || img?.url;
    const name = p.name || p.title || "Product";
    const brand = p.retailer || (Array.isArray(p.brands) && p.brands[0]?.name) || "\u2014";
    return (
      <div className="ag-product-cell">
        <div className="ag-thumb">
          {src ? (
            <img src={src} alt={name} className="ag-thumb-img" />
          ) : (
            <div className="ag-thumb-placeholder">No image</div>
          )}
        </div>
        <div className="ag-info">
          <div className="ag-name">{name}</div>
          <div className="ag-brand">{brand}</div>
        </div>
      </div>
    );
  }

  function PriceCellRenderer({ value, data }) {
    const price = value || (data && data.product?.offer?.price);
    const currency = (data && data.product?.offer?.currency) || "EUR";
    return (
      <div className="ag-price-cell">
        {price != null ? <span className="ag-price-value">{price}</span> : "\u2014"}
        <span className="ag-price-currency">{currency}</span>
      </div>
    );
  }

  function GateCellRenderer({ value, data }) {
    const decision = value || (data && data.gate?.decision) || "\u2014";
    const cls = decision === "APPROVED" ? "ag-gate-approved" : decision === "REJECTED" ? "ag-gate-rejected" : "ag-gate-confirm";
    const label = decision === "APPROVED" ? "Approved" : decision === "REJECTED" ? "Rejected" : "Needs confirmation";
    return <span className={`ag-gate-badge ${cls}`}>{label}</span>;
  }

  function ActionCellRenderer({ data }) {
    const row = data || {};
    const p = row.product || {};
    const merchantUrl = p.url;
    const hasMerchant = Boolean(merchantUrl);
    return (
      <div className="ag-action-cell">
        <span className="ag-action-label">Select</span>
        {hasMerchant ? (
          <a
            href={merchantUrl}
            target="_blank"
            rel="noreferrer"
            className="ag-merchant-link"
            onClick={(e) => e.stopPropagation()}
          >
            Merchant
          </a>
        ) : (
          <span className="ag-no-merchant">\u2014</span>
        )}
      </div>
    );
  }

  const columnDefs = [
    { headerName: "Product", field: "product", width: 280, cellRenderer: ProductCellRenderer, suppressSizeToFit: true, cellDataType: false },
    { headerName: "Price", field: "product.offer.price", type: "numericColumn", width: 110, cellRenderer: PriceCellRenderer, sortable: true },
    { headerName: "Policy Gate", field: "gate.decision", width: 150, cellRenderer: GateCellRenderer, suppressSizeToFit: true },
    { headerName: "Action", field: "product.url", width: 140, cellRenderer: ActionCellRenderer, suppressSizeToFit: true },
  ];

  const onSelectionChanged = (event) => {
    const selectedRow = event.api.getSelectedRows()[0];
    setSelected(selectedRow || null);
  };

  const onGridReady = (event) => {
    const rowSel = event.api.isModuleRegistered("RowSelectionModule");
    const cellStyle = event.api.isModuleRegistered("CellStyleModule");
    if (process.env.NODE_ENV === "development") {
      console.log("[AG Grid] RowSelectionModule registered:", rowSel);
      console.log("[AG Grid] CellStyleModule registered:", cellStyle);
    }
  };

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">IC</div>
          <div>
            <div className="brand-name">INTENT CART</div>
            <div className="brand-sub">AI finds the products. Evidence decides what you can buy.</div>
          </div>
        </div>
        {import.meta.env.DEV && (
          <button
            className="debug-toggle"
            onClick={() => setShowDiagnostics((prev) => !prev)}
            style={{ marginLeft: "auto", padding: "6px 12px", fontSize: 12, fontWeight: 700, background: "#1a2332", color: "#94a3b8", border: "1px solid #2d3a50", borderRadius: 6, cursor: "pointer" }}
          >
            DEBUG / Diagnostics
          </button>
        )}
      </header>

      {/* Shopping request — focal point */}
      <section className="card search-section">
        <div className="search-wrap">
          <div className="search-intro">
            Describe what you need. INTENT CART extracts a purchasing mandate, evaluates products against it, and only approves what it can prove.
          </div>
          <div className="search-controls">
            <div className="category-wrap">
              <span className="category-label">Category</span>
              <select className="category-select" value={category} onChange={(e) => setCategory(e.target.value)}>
                <option value="clothing_accessories">Clothing &amp; Accessories</option>
                <option value="footwear">Footwear (Coming soon)</option>
                <option value="electronics">Electronics (Coming soon)</option>
                <option value="home_kitchen">Home &amp; Kitchen (Coming soon)</option>
                <option value="sports_outdoors">Sports &amp; Outdoors (Coming soon)</option>
                <option value="other">Other / Free Search</option>
              </select>
            </div>
            <div className="search-row">
              <input
                className="search-input"
                value={searchText}
                onChange={(e) => setSearchText(e.target.value)}
                placeholder="Describe any product you want to buy..."
              />
              <button className="primary" onClick={runIntent} disabled={busy}>
                Review requirements
              </button>
            </div>
          </div>
          {category === "clothing_accessories" && (
            <div className="filters-wrap">
              <div className="filter-field">
                <label className="filter-label">Product type</label>
                <input className="filter-input" placeholder="e.g. socks" value={filters.product_type} onChange={(e)=>setFilters({...filters,product_type:e.target.value})} />
              </div>
              <div className="filter-field">
                <label className="filter-label">Size</label>
                <input className="filter-input" placeholder="e.g. 43" value={filters.size} onChange={(e)=>setFilters({...filters,size:e.target.value})} />
              </div>
              <div className="filter-field">
                <label className="filter-label">Material</label>
                <input className="filter-input" placeholder="e.g. cotton" value={filters.material} onChange={(e)=>setFilters({...filters,material:e.target.value})} />
              </div>
              <div className="filter-field">
                <label className="filter-label">Material %</label>
                <input className="filter-input" placeholder="e.g. 100" value={filters.material_percentage} onChange={(e)=>setFilters({...filters,material_percentage:e.target.value})} />
              </div>
              <div className="filter-field">
                <label className="filter-label">Style</label>
                <input className="filter-input" placeholder="e.g. mismatched" value={filters.style} onChange={(e)=>setFilters({...filters,style:e.target.value})} />
              </div>
              <div className="filter-field">
                <label className="filter-label">Color</label>
                <input className="filter-input" placeholder="e.g. black" value={filters.color} onChange={(e)=>setFilters({...filters,color:e.target.value})} />
              </div>
              <div className="filter-field">
                <label className="filter-label">Excluded designs</label>
                <input className="filter-input" placeholder="e.g. Christmas" value={filters.forbidden_designs} onChange={(e)=>setFilters({...filters,forbidden_designs:e.target.value})} />
              </div>
              <div className="filter-field">
                <label className="filter-label">Max price</label>
                <input className="filter-input" placeholder="e.g. 30" value={filters.max_price} onChange={(e)=>setFilters({...filters,max_price:e.target.value})} />
              </div>
              <div className="filter-field">
                <label className="filter-label">Currency</label>
                <input className="filter-input" placeholder="EUR" value={filters.currency} onChange={(e)=>setFilters({...filters,currency:e.target.value})} />
              </div>
            </div>
          )}
          <div className="action-row">
            <button className="primary" onClick={lockMandate} disabled={locked || busy}>Confirm requirements</button>
            <button className="primary" onClick={runSearch} disabled={!locked || busy || isSearching}>
            {isSearching ? (
              <span className="search-btn-loading">
                <span className="search-spinner" aria-hidden="true" />
                Searching...
              </span>
            ) : (
              "Search"
            )}
          </button>
          </div>
          {mandate && (
            <div className="mandate-wrap">
              <div className="mandate-column full">
                <div className="constraint-label">Original user request</div>
                <p style={{ margin: 0, color: "var(--text-body)", whiteSpace: "pre-wrap" }}>{mandateOriginalText ?? mandateView?.query ?? "No execution loaded"}</p>
              </div>
              <div className="mandate-column full">
                <div className="constraint-label">Parsed purchasing requirements</div>
                <p style={{ margin: "0 0 10px", fontSize: 12, color: "var(--text-muted)" }}>
                  Structured summary derived from the backend Mandate. Only extracted requirements are shown.
                </p>
                {mandateView?.product_type === "laptop" ? (
                  <div className="parsed-requirements" aria-label="Parsed laptop purchasing requirements">
                    <div className="parsed-requirements__grid">
                      <div className="parsed-req parsed-req--required">
                        <span className="parsed-req__label">Product type</span>
                        <span className="parsed-req__value">Laptop / Notebook</span>
                      </div>
                      {mandateView?.min_ram_gb != null && (
                        <div className="parsed-req parsed-req--required">
                          <span className="parsed-req__label">Minimum RAM</span>
                          <span className="parsed-req__value">{mandateView.min_ram_gb} GB</span>
                        </div>
                      )}
                      {mandateView?.min_storage_gb != null && (
                        <div className="parsed-req parsed-req--required">
                          <span className="parsed-req__label">Minimum installed storage</span>
                          <span className="parsed-req__value">{mandateView.min_storage_gb} GB</span>
                        </div>
                      )}
                      {mandateView?.required_storage_interface != null && String(mandateView.required_storage_interface).trim() !== "" && (
                        <div className="parsed-req parsed-req--required">
                          <span className="parsed-req__label">Required storage interface</span>
                          <span className="parsed-req__value">{mandateView.required_storage_interface}</span>
                        </div>
                      )}
                      {(mandateView?.required_processor_family != null || mandateView?.required_processor_subfamily != null || mandateView?.required_processor_model != null) && (
                        <div className="parsed-req parsed-req--required">
                          <span className="parsed-req__label">Processor requirement</span>
                          <span className="parsed-req__value">
                            {[
                              mandateView.required_processor_family,
                              mandateView.required_processor_subfamily && mandateView.required_processor_subfamily !== mandateView.required_processor_family ? mandateView.required_processor_subfamily : null,
                              mandateView.required_processor_model,
                            ].filter(Boolean).filter((v, i, a) => a.indexOf(v) === i).join(" · ") || "Specified"}
                          </span>
                        </div>
                      )}
                      {mandateView?.required_operating_system != null && String(mandateView.required_operating_system).trim() !== "" && (
                        <div className="parsed-req parsed-req--required">
                          <span className="parsed-req__label">Operating system requirement</span>
                          <span className="parsed-req__value">{mandateView.required_operating_system}</span>
                        </div>
                      )}
                      {mandateView?.max_price != null && (
                        <div className="parsed-req parsed-req--required">
                          <span className="parsed-req__label">Maximum price</span>
                          <span className="parsed-req__value">{mandateView.max_price} {mandateView.currency || "EUR"}</span>
                        </div>
                      )}
                      {(mandateView?.allowed_conditions || []).filter((c) => c != null && String(c).trim() !== "").length > 0 && (
                        <div className="parsed-req parsed-req--required">
                          <span className="parsed-req__label">Allowed condition</span>
                          <span className="parsed-req__value">{(mandateView.allowed_conditions || []).filter((c) => c != null && String(c).trim() !== "").join(", ")}</span>
                        </div>
                      )}
                    </div>
                  </div>
                ) : (
                  <>
                    <div className="mandate-wrap mandate-wrap--nested">
                      <div className="mandate-column">
                        <div className="constraint-label">Positive constraints</div>
                        <div className="constraint-list">
                          {mandateView?.max_price != null && (
                            <span className="tag priority-required kind-required">Max price {mandateView.max_price} {mandateView.currency}</span>
                          )}
                          {mandateView?.material && (
                            <span className="tag priority-required kind-required">{mandateView.material}</span>
                          )}
                          {mandateView?.size && (
                            <span className="tag priority-required kind-required">Size {mandateView.size}</span>
                          )}
                          {mandateView?.material_percentage != null && (
                            <span className="tag priority-required kind-required">{mandateView.material_percentage}%</span>
                          )}
                          {mandateView?.style && (
                            <span className="tag priority-preferred kind-preferred">{mandateView.style}</span>
                          )}
                          {(mandateView?.allowed_conditions || []).map((c, i) => (
                            <span key={i} className="tag priority-required kind-required">Condition: {c}</span>
                          ))}
                        </div>
                      </div>
                      <div className="mandate-column">
                        <div className="constraint-label">Negative constraints</div>
                        <div className="constraint-list">
                          {(mandateView?.negative_constraints || mandateView?.forbidden_designs || []).map((c, i) => (
                            <span key={i} className="tag negative">{c}</span>
                          ))}
                        </div>
                      </div>
                    </div>
                  </>
                )}
              </div>
              <div className="mandate-column full">
                <div className="constraint-label">Review state</div>
                <p style={{ margin: 0, color: "#10b981", fontWeight: 600 }}>Locked — server-side snapshot persisted</p>
              </div>
            </div>
          )}
        </div>
      </section>

      {/* Search history */}
      <section className="card">
        <div className="section-header">
          <h2>Recent Searches</h2>
          <div className="history-controls">
            <span style={{ fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
              {showAll ? "All searches" : "Recent searches"} ({visibleSearches.length} of {executions.length})
            </span>
            {executions.length > 5 && (
              <button className="history-toggle" onClick={() => setShowAll(!showAll)}>
                {showAll ? "Show fewer" : "View all searches"}
              </button>
            )}
          </div>
        </div>
        {loading && <p style={{ color: "var(--text-muted)", fontSize: 13 }}>Loading…</p>}
        {error && <p style={{ color: "var(--state-rejected)", fontSize: 13 }}>{error}</p>}
{executions.length === 0
          ? <p className="history-empty">
              No recent searches. Start by describing what you’re looking for.
            </p>
          : visibleSearches.every((e) => (e.results_count ?? 0) === 0) && (
              <p className="history-empty">
                No recent searches. Start by describing what you’re looking for.
              </p>
            )}
        {executions.length > 0 && (
          <div className="history-list">
            {visibleSearches.map((e) => {
              const isActive = current?.execution_id === e.execution_id;
              const query = e.query || "Previous search";
              return (
                <button
                  key={e.execution_id}
                  className={`history-item ${isActive ? "is-active" : ""}`}
                  style={{}}
                  onClick={() => selectExecution(e.execution_id)}
                >
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 14, fontWeight: 700, color: "var(--text-navy)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{query}</div>
                    <div className="history-item__meta">
                      {e.timestamp ? formatTimestamp(e.timestamp) : ""}
                      {e.product_count != null && <span>{e.product_count} product{e.product_count !== 1 ? "s" : ""}</span>}
                      {e.status_summary && <span>{e.status_summary}</span>}
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </section>

      {/* Search progress */}
      {isSearching && (
        <section className="card search-progress" aria-live="polite" aria-busy="true">
          <div className="search-progress-inner">
            <div className="search-progress-spinner" aria-hidden="true" />
            <div>
              <div className="search-progress-title">Searching Channel3...</div>
              <div className="search-progress-subtitle">
                Discovering offers and evaluating product evidence against your locked mandate.
              </div>
              <div className="search-progress-stages">
                <span className="search-progress-stage active">Channel3 product discovery</span>
                <span className="search-progress-stage active">Policy Gate evaluation</span>
                <span className="search-progress-stage active">Saving execution results</span>
              </div>
            </div>
          </div>
        </section>
      )}

      {/* Product results */}
      <section className="card">
        <div className="section-header">
          <h2>Product Evaluation</h2>
          <div className="stats-bar">
            <div className="stat">
              <div className="stat__value">{rows.length}</div>
              <div className="stat__label">Found</div>
            </div>
            <div className="stat">
              <div className="stat__value approved">{approved}</div>
              <div className="stat__label">Approved</div>
            </div>
            <div className="stat">
              <div className="stat__value rejected">{blocked}</div>
              <div className="stat__label">Blocked</div>
            </div>
          </div>
        </div>
        <div className="ag-grid-wrapper">
          {rows.length === 0 ? (
            <div style={{ padding: 40, textAlign: "center", color: "var(--text-muted)" }}>
              No products yet. Search to see results.
            </div>
          ) : (
            <AgGridReact
              theme={intentCartTheme}
              rowData={rows}
              columnDefs={columnDefs}
              rowSelection={{ mode: "singleRow", enableClickSelection: true, checkboxes: false }}
              onSelectionChanged={onSelectionChanged}
              onGridReady={onGridReady}
              suppressCellFocus={true}
              suppressScrollOnNewData
              domLayout="autoHeight"
              rowHeight={110}
              headerHeight={40}
              suppressMovableColumns
            />
          )}
        </div>
      </section>

      {/* Selected product evidence + checkout */}
      {selected && (
        <section className="card evidence-card">
          <div className="evidence-header">
            <h2 className="evidence-title">Product Evidence: {selected.product?.name}</h2>
            <span className={`decision-badge ${selected.gate?.decision === "APPROVED" ? "approved" : selected.gate?.decision === "NEEDS_CONFIRMATION" ? "confirm" : "rejected"}`}>
              {selected.gate?.decision === "APPROVED" ? "Approved" : selected.gate?.decision === "NEEDS_CONFIRMATION" ? "Needs confirmation" : "Rejected"}
            </span>
          </div>
          <div className="rules-list">
            {(selected.gate?.rules || []).map((r, i) => {
              const passed = r.passed === true;
              const verified = r.observed !== null && r.observed !== undefined;
              const status = r.status === "NOT_APPLICABLE" ? "NOT APPLICABLE" : passed ? "PASS" : verified ? "FAIL" : "NOT VERIFIED";
              const statusClass = passed ? "pass" : verified ? "fail" : r.status === "NOT_APPLICABLE" ? "not-applicable" : "not-verified";
              const label = String(r.rule || "").replace(/_/g, " ").replace(/^min /, "min ").toUpperCase();
              return (
                <div key={i} className="rule-item">
                  <div className="rule-item__header">
                    <strong className="rule-item__name">{label}</strong>
                    <span className={`rule-item__status ${statusClass}`}>{status}</span>
                  </div>
                  <p className="rule-item__evidence">
                    Expected: {JSON.stringify(r.expected)} &middot; Observed: {r.observed === null || r.observed === undefined ? "NOT VERIFIED" : JSON.stringify(r.observed)}
                  </p>
                  <p style={{ margin: 0, fontSize: 11, color: "var(--text-muted)" }}>{r.evidence}</p>
                </div>
              );
            })}
          </div>
          <div className="final-decision">
            <span style={{ fontWeight: 700 }}>Final decision:</span>
            {selected.gate?.decision === "APPROVED" ? "APPROVED" : selected.gate?.decision === "NEEDS_CONFIRMATION" ? "NEEDS_CONFIRMATION" : "REJECTED"}
          </div>
          <button className="technical-toggle" onClick={() => setShowTechnical(!showTechnical)}>
            {showTechnical ? "Hide technical" : "Technical evidence"}
          </button>
          {showTechnical && (
            <div className="technical-block">
              <pre>{JSON.stringify(selected.gate, null, 2)}</pre>
            </div>
          )}
        </section>
      )}
          <div className="checkout-note">
            {!sessionId
              ? current ? "Start a new search to continue to checkout." : "Lock your purchasing mandate before checkout."
              : !selected
              ? current?.results?.some(r => r.gate?.decision === "APPROVED")
                ? "Approved product eligible for PayPal checkout. PayPal buyer approval still required."
                : "Select a product to continue to checkout."
: selected.gate?.decision === "APPROVED"
              ? locked
                ? "Approved product eligible for PayPal checkout. PayPal buyer approval still required."
                : "Checkout unavailable: a new locked mandate is required. Review and confirm requirements again."
              : "Checkout unavailable: product does not satisfy the locked purchasing mandate."}
      </div>
      <div className="checkout">
        {selected && (
          <PayPalButton sessionId={sessionId} executionId={selected?.execution_id} offerId={selected?.offer?.offer_id} decision={selected?.gate?.decision} locked={locked} onReadyChange={setPpReady} onLoadingChange={setPpLoading} onSdkError={setPpSdkError} onSdkLoaded={setPpSdkLoaded} />
        )}
      </div>
      {import.meta.env.DEV && showDiagnostics && (
        <div className="diagnostics-panel">
          <div className="diagnostics-header">
            <h3>INTENT CART — Diagnostic Panel (Dev)</h3>
            <button onClick={() => setShowDiagnostics(false)}>Close</button>
          </div>
          <table>
            <tbody>
              <tr><td>locked</td><td>{String(locked)}</td></tr>
              <tr><td>sessionId exists</td><td>{String(Boolean(sessionId))}</td></tr>
              <tr><td>sessionId value</td><td>{sessionId || "—"}</td></tr>
              <tr><td>selected exists</td><td>{String(Boolean(selected))}</td></tr>
              <tr><td>selected product title</td><td>{selected?.product?.name || "—"}</td></tr>
              <tr><td>selected.gate.decision</td><td>{selected?.gate?.decision || "—"}</td></tr>
              <tr><td>selected.execution_id</td><td>{selected?.execution_id || "—"}</td></tr>
              <tr><td>selected.offer.offer_id</td><td>{selected?.offer?.offer_id || "—"}</td></tr>
              <tr><td>PayPal approved calc</td><td>{String(ppApproved)}</td></tr>
              <tr><td>PayPal client ID</td><td>{import.meta.env.VITE_PAYPAL_CLIENT_ID || "sb (default)"}</td></tr>
              <tr><td>PayPal SDK loaded</td><td>{String(ppSdkLoaded)}</td></tr>
              <tr><td>PayPal SDK ready</td><td>{String(ppReady)}</td></tr>
              <tr><td>PayPal loading</td><td>{String(ppLoading)}</td></tr>
              <tr><td>PayPal SDK error</td><td>{ppSdkError || "—"}</td></tr>
              <tr><td>PayPal mount state</td><td>{String(Boolean(selected))}</td></tr>
              <tr><td>Reason hidden/disabled</td><td>{ppReason}</td></tr>
              <tr><td>mandate.allowed_conditions</td><td>{mandate?.allowed_conditions === undefined ? "undefined" : JSON.stringify(mandate?.allowed_conditions)}</td></tr>
              <tr><td>lockedMandate.allowed_conditions</td><td>{lockedMandate?.allowed_conditions === undefined ? "undefined" : JSON.stringify(lockedMandate?.allowed_conditions)}</td></tr>
              <tr><td>current.mandate.allowed_conditions</td><td>{current?.mandate?.allowed_conditions === undefined ? "undefined" : JSON.stringify(current?.mandate?.allowed_conditions)}</td></tr>
              <tr><td>mandateView.allowed_conditions</td><td>{mandateView?.allowed_conditions === undefined ? "undefined" : JSON.stringify(mandateView?.allowed_conditions)}</td></tr>
              <tr><td>mandateView source</td><td>{mandateViewSource}</td></tr>
              <tr><td>mandateView raw (JSON)</td><td><pre style={{ fontSize: 10, whiteSpace: "pre-wrap", maxHeight: 200, overflow: "auto" }}>{JSON.stringify(mandateView, null, 2)}</pre></td></tr>
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
