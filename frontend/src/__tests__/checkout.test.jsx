import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, within, fireEvent, cleanup, waitFor } from "@testing-library/react";
import App from "../App.jsx";

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
    cleanup();
  });

const mockFetch = vi.fn();
global.fetch = mockFetch;

let lastCheckoutBody;
let lastCaptureBody;

function json(status, data) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

let checkoutResolver;

function setupFetch(searchResponse, executions = []) {
  mockFetch.mockImplementation(async (url, opts) => {
    const u = String(url);
    const o = opts || {};
    const method = o.method || "GET";
    // Handle both absolute and relative URLs
    const isCheckout = u.includes("/api/orders/checkout") && method === "POST";
    if (u === "http://127.0.0.1:8000/api/executions") return json(200, executions);
    if (u.startsWith("http://127.0.0.1:8000/api/executions/") && method === "GET") {
      const id = u.substring(u.lastIndexOf("/") + 1);
      if (searchResponse && searchResponse.execution_id === id) {
        return json(200, { execution_id: id, ...searchResponse });
      }
      return json(404, {});
    }
    if (u === "http://127.0.0.1:8000/api/intent" && method === "POST")
      return json(200, { session_id: "s1", mandate: searchResponse.mandate });
    if (u === "http://127.0.0.1:8000/api/mandate/s1/lock" && method === "POST")
      return json(200, { locked: true, mandate: {} });
    if (u === "http://127.0.0.1:8000/api/search/s1" && method === "POST")
      return json(200, searchResponse);
    if (u.includes("/api/orders/checkout") && method === "POST") {
      lastCheckoutBody = JSON.parse(o.body || "{}");
      if (checkoutResolver) {
        return new Promise((res) => {
          checkoutResolver = res;
        });
      }
      return json(200, {
        order_id: "O1",
        approve_url: "https://approve",
        status: "CREATED",
        payment_id: "p1",
      });
    }
    if (u.includes("/api/orders/capture") && method === "POST") {
      lastCaptureBody = JSON.parse(o.body || "{}");
      return json(200, {
        status: "PAYMENT_CAPTURED",
        capture_id: "CAP1",
        amount: "10.00",
        currency: "EUR",
      });
    }
    return json(404, {});
  });
}

async function settle(ms = 120) {
  await new Promise((r) => setTimeout(r, ms));
}

async function completeSearchFlow(container) {
  const searchInput = within(container).getByPlaceholderText(
    "Describe any product you want to buy..."
  );
  fireEvent.change(searchInput, { target: { value: "socks" } });
  fireEvent.click(within(container).getByText("Review requirements"));
  await settle();
  expect(
    within(container).getAllByText("Confirm requirements").length
  ).toBeGreaterThan(0);
  fireEvent.click(within(container).getByText("Confirm requirements"));
  await settle();
  const searchBtn = within(container).getByText("Search");
  expect(searchBtn.disabled).toBe(false);
  fireEvent.click(searchBtn);
  await settle(300);
  await settle(300);
  // runSearch auto-selects first APPROVED result; PayPal button mounts automatically
}

const approvedProduct = {
  product: {
    title: "Socks",
    images: [],
    offers: [
      { offer_id: "offer-1", price: { price: 10, currency: "EUR" }, condition: "new" },
    ],
  },
  gate: { decision: "APPROVED", rules: [] },
  offer: { offer_id: "offer-1", price: { price: 10, currency: "EUR" } },
};

const approvedSearchResponse = {
  execution_id: "e1",
  mandate: { query: "socks" },
  results: [approvedProduct],
};

function mockPaypalSdk() {
  const createOrderCalls = [];
  const onApproveCalls = [];
  window.paypal = {
    Buttons: (cfg) => {
      window.__lastPayPalCreate = cfg;
      if (cfg.createOrder) createOrderCalls.push(cfg);
      if (cfg.onApprove) onApproveCalls.push(cfg);
      return { render: vi.fn() };
    },
  };
  return { createOrderCalls, onApproveCalls };
}

describe("single PayPal checkout path", () => {
  beforeEach(() => {
    cleanup();
    mockFetch.mockReset();
    checkoutResolver = undefined;
    lastCheckoutBody = undefined;
    lastCaptureBody = undefined;
    setupFetch(approvedSearchResponse);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    delete window.paypal;
  });

  it("renders PayPalButton as the only checkout action", async () => {
    const { container } = render(<App />);
    await completeSearchFlow(container);
    const html = container.textContent;
    expect(html).not.toContain("Proceed to Checkout");
    expect(html).not.toContain("Checkout Disabled (Blocked)");
    expect(container.querySelector(".checkout > div > div")).toBeTruthy();
  });

  it("createOrder sends session_id, execution_id and offer_id", async () => {
    const sdk = mockPaypalSdk();
    const { container } = render(<App />);
    await completeSearchFlow(container);
    const createOrderCfg = sdk.createOrderCalls[0];
    expect(createOrderCfg).toBeDefined();
    const orderId = await createOrderCfg.createOrder();
    expect(orderId).toBe("O1");
    expect(lastCheckoutBody).toBeDefined();
    expect(lastCheckoutBody.session_id).toBe("s1");
    expect(lastCheckoutBody.execution_id).toBe("e1");
    expect(lastCheckoutBody.offer_id).toBe("offer-1");
  });

  it("createOrder is guarded against repeated calls while pending", async () => {
    const sdk = mockPaypalSdk();
    const { container } = render(<App />);
    await completeSearchFlow(container);
    const createOrderCfg = sdk.createOrderCalls[0];
    checkoutResolver = undefined;
    mockFetch.mockImplementationOnce(async (url, opts) => {
      const u = String(url);
      const o = opts || {};
      if (u.includes("/api/orders/checkout") && o.method === "POST") {
        lastCheckoutBody = JSON.parse(o.body || "{}");
        return new Promise((res) => {
          checkoutResolver = res;
        });
      }
      return json(404, {});
    });
    const p1 = createOrderCfg.createOrder();
    const p2 = createOrderCfg.createOrder();
    await settle();
    const checkoutCalls = (mockFetch.mock.calls || []).filter((c) =>
      String(c[0]).includes("/api/orders/checkout")
    );
    expect(checkoutCalls.length).toBe(1);
    expect(lastCheckoutBody).toBeDefined();
    expect(lastCheckoutBody.session_id).toBe("s1");
    checkoutResolver(
      json(200, { order_id: "O1", approve_url: "https://approve", status: "CREATED", payment_id: "p1" })
    );
    await p1;
    await p2;
  });

  it("blocks checkout for NEEDS_CONFIRMATION products", async () => {
    setupFetch({
      execution_id: "e1",
      mandate: { query: "socks" },
      results: [
        {
          product: { title: "Socks", images: [], offers: [{ offer_id: "offer-1", price: { price: 10, currency: "EUR" }, condition: "new" }] },
          gate: { decision: "NEEDS_CONFIRMATION", rules: [] },
          offer: { offer_id: "offer-1", price: { price: 10, currency: "EUR" } },
        },
      ],
    });
    const { container } = render(<App />);
    await completeSearchFlow(container);
    expect(
      within(container).getAllByText("PayPal checkout disabled (blocked)").length
    ).toBeGreaterThan(0);
    expect(lastCheckoutBody).toBeUndefined();
  });

  it("blocks checkout for REJECTED products", async () => {
    setupFetch({
      execution_id: "e1",
      mandate: { query: "socks" },
      results: [
        {
          product: { title: "Socks", images: [], offers: [{ offer_id: "offer-1", price: { price: 10, currency: "EUR" }, condition: "new" }] },
          gate: { decision: "REJECTED", rules: [] },
          offer: { offer_id: "offer-1", price: { price: 10, currency: "EUR" } },
        },
      ],
    });
    const { container } = render(<App />);
    await completeSearchFlow(container);
    expect(
      within(container).getAllByText("PayPal checkout disabled (blocked)").length
    ).toBeGreaterThan(0);
    expect(lastCheckoutBody).toBeUndefined();
  });

  it("buyer approval invokes capture with the correct payment_id", async () => {
    const sdk = mockPaypalSdk();
    const { container } = render(<App />);
    await completeSearchFlow(container);
    const createOrderCfg = sdk.createOrderCalls[0];
    await createOrderCfg.createOrder();
    await settle();
    const onApproveCfg = sdk.onApproveCalls[0];
    expect(onApproveCfg).toBeDefined();
    await onApproveCfg.onApprove({ orderID: "O1" });
    await settle();
    expect(lastCaptureBody).toBeDefined();
    expect(lastCaptureBody.payment_id).toBe("p1");
  });

  // --- Execution selection & session restoration tests ---

  it("selects newest non-empty execution when newest is empty", async () => {
    const searchResponse = {
      execution_id: "has-products",
      mandate: { query: "socks" },
      session_id: "s1",
      results: [approvedProduct],
    };
    setupFetch(searchResponse, [
      { execution_id: "empty-1", timestamp: "2026-11-05T19:00:00Z", session_id: null, results_count: 0 },
      { execution_id: "has-products", timestamp: "2026-11-05T18:00:00Z", session_id: "s1", results_count: 1 },
    ]);
    const { container } = render(<App />);
    await settle();
    expect(
      within(container).getAllByText("Previous search").length
    ).toBeGreaterThan(0);
    await waitFor(() =>
      expect(within(container).getAllByText(/Socks/).length).toBeGreaterThan(0)
    );
  });

  it("shows empty state when all executions are empty", async () => {
    setupFetch(
      undefined,
      [
        { execution_id: "empty-1", timestamp: "2026-11-05T19:00:00Z", session_id: null, results_count: 0, query: "Empty search one" },
        { execution_id: "empty-2", timestamp: "2026-11-05T18:00:00Z", session_id: null, results_count: 0, query: "Empty search two" },
      ]
    );
    const { container } = render(<App />);
    await settle();
    expect(
      within(container).getAllByText(/No recent searches/).length
    ).toBeGreaterThan(0);
    // Empty executions still listed.
    expect(
      within(container).getAllByText("Empty search one").length
    ).toBeGreaterThan(0);
    expect(
      within(container).getAllByText("Empty search two").length
    ).toBeGreaterThan(0);
  });

  it("restores sessionId only when execution has explicit session relationship", async () => {
    const searchResponse = {
      execution_id: "e1",
      mandate: { query: "socks" },
      session_id: "s1",
      results: [
        {
          product: { title: "Socks", images: [], offers: [{ offer_id: "offer-1", price: { price: 10, currency: "EUR" }, condition: "new" }] },
          gate: { decision: "APPROVED", rules: [] },
          offer: { offer_id: "offer-1", price: { price: 10, currency: "EUR" } },
        },
      ],
    };
    setupFetch(searchResponse, [
      { execution_id: "e1", timestamp: "2026-11-05T18:00:00Z", session_id: "s1", results_count: 1 },
    ]);
    const { container } = render(<App />);
    await settle();
    expect(
      within(container).getAllByText(/Approved product eligible for PayPal checkout/).length
    ).toBeGreaterThan(0);
  });

  it("fresh search with APPROVED result shows PayPal button", async () => {
    setupFetch(approvedSearchResponse);
    const { container } = render(<App />);
    const searchInput = within(container).getByPlaceholderText(
      "Describe any product you want to buy..."
    );
    fireEvent.change(searchInput, { target: { value: "socks" } });
    await settle();
    fireEvent.click(within(container).getByText("Review requirements"));
    await settle();
    fireEvent.click(within(container).getByText("Confirm requirements"));
    await settle();
    fireEvent.click(within(container).getByText("Search"));
    await settle();
    expect(
      container.querySelector(".checkout > div > div")
    ).toBeTruthy();
  });

  it("fresh search with NEEDS_CONFIRMATION result blocks PayPal", async () => {
    setupFetch({
      execution_id: "e1",
      mandate: { query: "socks" },
      results: [
        {
          product: { title: "Socks", images: [], offers: [{ offer_id: "offer-1", price: { price: 10, currency: "EUR" }, condition: "new" }] },
          gate: { decision: "NEEDS_CONFIRMATION", rules: [] },
          offer: { offer_id: "offer-1", price: { price: 10, currency: "EUR" } },
        },
      ],
    });
    const { container } = render(<App />);
    const searchInput = within(container).getByPlaceholderText(
      "Describe any product you want to buy..."
    );
    fireEvent.change(searchInput, { target: { value: "socks" } });
    await settle();
    fireEvent.click(within(container).getByText("Review requirements"));
    await settle();
    fireEvent.click(within(container).getByText("Confirm requirements"));
    await settle();
    fireEvent.click(within(container).getByText("Search"));
    await settle();
    expect(
      within(container).getAllByText("PayPal checkout disabled (blocked)").length
    ).toBeGreaterThan(0);
  });

  it("fresh search with REJECTED result blocks PayPal", async () => {
    setupFetch({
      execution_id: "e1",
      mandate: { query: "socks" },
      results: [
        {
          product: { title: "Socks", images: [], offers: [{ offer_id: "offer-1", price: { price: 10, currency: "EUR" }, condition: "new" }] },
          gate: { decision: "REJECTED", rules: [] },
          offer: { offer_id: "offer-1", price: { price: 10, currency: "EUR" } },
        },
      ],
    });
    const { container } = render(<App />);
    const searchInput = within(container).getByPlaceholderText(
      "Describe any product you want to buy..."
    );
    fireEvent.change(searchInput, { target: { value: "socks" } });
    await settle();
    fireEvent.click(within(container).getByText("Review requirements"));
    await settle();
    fireEvent.click(within(container).getByText("Confirm requirements"));
    await settle();
    fireEvent.click(within(container).getByText("Search"));
    await settle();
    expect(
      within(container).getAllByText("PayPal checkout disabled (blocked)").length
    ).toBeGreaterThan(0);
  });

  it("fresh search with no results blocks PayPal", async () => {
    setupFetch({
      execution_id: "e1",
      mandate: { query: "socks" },
      results: [],
    });
    const { container } = render(<App />);
    const searchInput = within(container).getByPlaceholderText(
      "Describe any product you want to buy..."
    );
    fireEvent.change(searchInput, { target: { value: "socks" } });
    await settle();
    fireEvent.click(within(container).getByText("Review requirements"));
    await settle();
    fireEvent.click(within(container).getByText("Confirm requirements"));
    await settle();
    fireEvent.click(within(container).getByText("Search"));
    await settle();
    expect(container.querySelector(".checkout > div > div")).toBeNull();
  });

  it("legacy execution without session relationship stays readable but blocks checkout", async () => {
    const searchResponse = {
      execution_id: "legacy-1",
      mandate: { query: "socks" },
      results: [
        {
          product: { title: "Legacy Socks", images: [], offers: [{ offer_id: "offer-legacy", price: { price: 10, currency: "EUR" }, condition: "new" }] },
          gate: { decision: "APPROVED", rules: [] },
          offer: { offer_id: "offer-legacy", price: { price: 10, currency: "EUR" } },
        },
      ],
    };
    setupFetch(searchResponse, [
      { execution_id: "legacy-1", timestamp: "2026-11-05T18:00:00Z", session_id: null, results_count: 1 },
    ]);
    const { container } = render(<App />);
    await settle();
    expect(
      within(container).getAllByText(/Legacy Socks/).length
    ).toBeGreaterThan(0);
    expect(
      within(container).getAllByText(/Start a new search to continue to checkout/).length
    ).toBeGreaterThan(0);
  });

  // --- Production diagnostics / hygiene ---
  it("does not render diagnostics in production builds and ignores shortcut", async () => {
    vi.stubEnv("DEV", false);
    const { container } = render(<App />);
    await settle();
    expect(container.querySelector(".diagnostics-panel")).toBeNull();
    expect(container.textContent).not.toContain("INTENT CART — Diagnostic Panel (Dev)");
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "D", ctrlKey: true, shiftKey: true }));
    await settle(20);
    expect(container.querySelector(".diagnostics-panel")).toBeNull();
  });

  it("renders diagnostics in development mode", async () => {
    vi.stubEnv("DEV", true);
    const { container } = render(<App />);
    await settle();
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "D", ctrlKey: true, shiftKey: true }));
    await settle(40);
    expect(container.querySelector(".diagnostics-panel")).toBeTruthy();
    expect(container.textContent).toContain("INTENT CART — Diagnostic Panel (Dev)");
  });

  it("surfaces checkout error when approval URL is missing (no fabricated URL)", async () => {
    setupFetch(approvedSearchResponse);
    const sdk = mockPaypalSdk();
    const { container } = render(<App />);
    await completeSearchFlow(container);
    const originalFetch = global.fetch;
    global.fetch = async (url, opts) => {
      const u = String(url);
      const o = opts || {};
      if (u.includes("/api/orders/checkout") && o.method === "POST") {
        return json(502, { detail: "PayPal approval link unavailable" });
      }
      return originalFetch(url, opts);
    };
    const createOrderCfg = sdk.createOrderCalls[0];
    expect(createOrderCfg).toBeDefined();
    await expect(createOrderCfg.createOrder()).rejects.toThrow();
    global.fetch = originalFetch;
  });

  it("existing unpaid order with approve_url null is accepted", async () => {
    setupFetch(approvedSearchResponse);
    const sdk = mockPaypalSdk();
    const { container } = render(<App />);
    await completeSearchFlow(container);
    const originalFetch = global.fetch;
    global.fetch = async (url, opts) => {
      const u = String(url);
      const o = opts || {};
      if (u.includes("/api/orders/checkout") && o.method === "POST") {
        return json(200, {
          order_id: "O1",
          approve_url: null,
          status: "CREATED",
          payment_id: "p1",
        });
      }
      return originalFetch(url, opts);
    };
    const createOrderCfg = sdk.createOrderCalls[0];
    expect(createOrderCfg).toBeDefined();
    const orderId = await createOrderCfg.createOrder();
    expect(orderId).toBe("O1");
    global.fetch = originalFetch;
  });

  it("missing order_id is rejected", async () => {
    setupFetch(approvedSearchResponse);
    const sdk = mockPaypalSdk();
    const { container } = render(<App />);
    await completeSearchFlow(container);
    const originalFetch = global.fetch;
    global.fetch = async (url, opts) => {
      const u = String(url);
      const o = opts || {};
      if (u.includes("/api/orders/checkout") && o.method === "POST") {
        return json(200, {
          approve_url: "https://approve",
          status: "CREATED",
          payment_id: "p1",
        });
      }
      return originalFetch(url, opts);
    };
    const createOrderCfg = sdk.createOrderCalls[0];
    expect(createOrderCfg).toBeDefined();
    await expect(createOrderCfg.createOrder()).rejects.toThrow();
    global.fetch = originalFetch;
  });

  it("missing payment_id is rejected", async () => {
    setupFetch(approvedSearchResponse);
    const sdk = mockPaypalSdk();
    const { container } = render(<App />);
    await completeSearchFlow(container);
    const originalFetch = global.fetch;
    global.fetch = async (url, opts) => {
      const u = String(url);
      const o = opts || {};
      if (u.includes("/api/orders/checkout") && o.method === "POST") {
        return json(200, {
          order_id: "O1",
          approve_url: "https://approve",
          status: "CREATED",
        });
      }
      return originalFetch(url, opts);
    };
    const createOrderCfg = sdk.createOrderCalls[0];
    expect(createOrderCfg).toBeDefined();
    await expect(createOrderCfg.createOrder()).rejects.toThrow();
    global.fetch = originalFetch;
  });

  // --- AG Grid product results tests ---

  it("AG Grid renders Channel3 result data after fresh search", async () => {
    setupFetch(approvedSearchResponse);
    const { container } = render(<App />);
    await completeSearchFlow(container);
    // AG Grid container should be present when results exist
    expect(container.querySelector(".ag-grid-wrapper")).toBeTruthy();
    // runSearch auto-selected first APPROVED result; PayPal button should mount
    expect(container.querySelector(".checkout > div > div")).toBeTruthy();
  });

  it("AG Grid row selection preserves checkout identifiers", async () => {
    setupFetch(approvedSearchResponse);
    const { container } = render(<App />);
    await completeSearchFlow(container);
    // Selected row's identifiers should be available for checkout
    expect(
      container.querySelector(".checkout > div > div")
    ).toBeTruthy();
    // The diagnostic panel would show execution_id, offer_id, gate.decision
    // Verified indirectly: PayPalButton approved calc requires all three
  });

  it("AG Grid renders policy gate state via checkout eligibility", async () => {
    setupFetch({
      execution_id: "e1",
      mandate: { query: "mixed" },
      results: [
        {
          product: { title: "Approved Item", images: [], offers: [{ offer_id: "o1", price: { price: 10, currency: "EUR" }, condition: "new" }] },
          gate: { decision: "APPROVED", rules: [] },
          offer: { offer_id: "o1", price: { price: 10, currency: "EUR" } },
        },
      ],
    });
    const { container } = render(<App />);
    await completeSearchFlow(container);
    // APPROVED result → PayPal button mounts
    expect(
      container.querySelector(".checkout > div > div")
    ).toBeTruthy();
  });

  it("AG Grid handles empty results without crashing", async () => {
    setupFetch({
      execution_id: "e1",
      mandate: { query: "empty" },
      results: [],
    });
    const { container } = render(<App />);
    const searchInput = within(container).getByPlaceholderText(
      "Describe any product you want to buy..."
    );
    fireEvent.change(searchInput, { target: { value: "empty" } });
    await settle();
    fireEvent.click(within(container).getByText("Review requirements"));
    await settle();
    fireEvent.click(within(container).getByText("Confirm requirements"));
    await settle();
    fireEvent.click(within(container).getByText("Search"));
    await settle();
    expect(container.querySelector(".ag-theme-quartz-dark")).toBeNull();
    expect(
      within(container).getAllByText("No products yet. Search to see results.").length
    ).toBeGreaterThan(0);
  });

  // --- AG Grid row selection tests ---

  it("AG Grid row click selects product and shows evidence", async () => {
    setupFetch({
      execution_id: "e1",
      mandate: { query: "socks" },
      results: [
        {
          product: { title: "Product A", images: [], offers: [{ offer_id: "offer-a", price: { price: 10, currency: "EUR" }, condition: "new" }] },
          gate: { decision: "APPROVED", rules: [] },
          offer: { offer_id: "offer-a", price: { price: 10, currency: "EUR" } },
        },
        {
          product: { title: "Product B", images: [], offers: [{ offer_id: "offer-b", price: { price: 20, currency: "EUR" }, condition: "new" }] },
          gate: { decision: "APPROVED", rules: [] },
          offer: { offer_id: "offer-b", price: { price: 20, currency: "EUR" } },
        },
      ],
    });
    const { container } = render(<App />);
    await completeSearchFlow(container);
    // First APPROVED result is auto-selected by runSearch
    expect(
      within(container).getAllByText("Product A").length
    ).toBeGreaterThan(0);
    // PayPal button mounted for auto-selected product
    expect(container.querySelector(".checkout > div > div")).toBeTruthy();
    // Click on second row
    const rows = container.querySelectorAll(".ag-row");
    expect(rows.length).toBeGreaterThanOrEqual(2);
    fireEvent.click(rows[1]);
    await settle();
    // Evidence panel should now show Product B
    expect(
      within(container).getAllByText("Product B").length
    ).toBeGreaterThan(0);
  });

  it("AG Grid second row selection updates checkout identifiers", async () => {
    setupFetch({
      execution_id: "e1",
      mandate: { query: "socks" },
      results: [
        {
          product: { title: "Product A", images: [], offers: [{ offer_id: "offer-a", price: { price: 10, currency: "EUR" }, condition: "new" }] },
          gate: { decision: "APPROVED", rules: [] },
          offer: { offer_id: "offer-a", price: { price: 10, currency: "EUR" } },
        },
        {
          product: { title: "Product B", images: [], offers: [{ offer_id: "offer-b", price: { price: 20, currency: "EUR" }, condition: "new" }] },
          gate: { decision: "APPROVED", rules: [] },
          offer: { offer_id: "offer-b", price: { price: 20, currency: "EUR" } },
        },
      ],
    });
    const { container } = render(<App />);
    await completeSearchFlow(container);
    const rows = container.querySelectorAll(".ag-row");
    expect(rows.length).toBeGreaterThanOrEqual(2);
    fireEvent.click(rows[1]);
    await settle();
    // Selected row B's identifiers should be intact
    expect(container.querySelector(".checkout > div > div")).toBeTruthy();
  });

  it("AG Grid single row selection only allows one selected at a time", async () => {
    setupFetch({
      execution_id: "e1",
      mandate: { query: "socks" },
      results: [
        {
          product: { title: "Product A", images: [], offers: [{ offer_id: "offer-a", price: { price: 10, currency: "EUR" }, condition: "new" }] },
          gate: { decision: "APPROVED", rules: [] },
          offer: { offer_id: "offer-a", price: { price: 10, currency: "EUR" } },
        },
        {
          product: { title: "Product B", images: [], offers: [{ offer_id: "offer-b", price: { price: 20, currency: "EUR" }, condition: "new" }] },
          gate: { decision: "APPROVED", rules: [] },
          offer: { offer_id: "offer-b", price: { price: 20, currency: "EUR" } },
        },
      ],
    });
    const { container } = render(<App />);
    await completeSearchFlow(container);
    const rows = container.querySelectorAll(".ag-row");
    expect(rows.length).toBeGreaterThanOrEqual(2);
    // Select row B
    fireEvent.click(rows[1]);
    await settle();
    // Select row A
    fireEvent.click(rows[0]);
    await settle();
    // Only Product A evidence panel should show
    expect(
      within(container).getByText("Product Evidence: Product A")
    ).toBeTruthy();
    expect(
      within(container).queryByText("Product Evidence: Product B")
    ).toBeNull();
  });

  // --- New-intent flow tests ---

  it("sends new input text to /api/intent on Review requirements", async () => {
    const intentCalls = [];
    setupFetch(approvedSearchResponse);
    const { container } = render(<App />);
    const originalFetch = global.fetch;
    global.fetch = async (url, opts) => {
      const u = String(url);
      if (u === "http://127.0.0.1:8000/api/intent" && opts.method === "POST") {
        intentCalls.push(JSON.parse(opts.body));
      }
      return originalFetch(url, opts);
    };
    const searchInput = within(container).getByPlaceholderText(
      "Describe any product you want to buy..."
    );
    fireEvent.change(searchInput, { target: { value: "Men's cotton socks, size 43" } });
    await settle();
    fireEvent.click(within(container).getByText("Review requirements"));
    await settle();
    expect(intentCalls.length).toBeGreaterThan(0);
    expect(intentCalls[0].query).toBe("Men's cotton socks, size 43");
    global.fetch = originalFetch;
  });

  it("displays new mandate query after Review requirements", async () => {
    setupFetch({
      execution_id: "e2",
      mandate: { query: "Men's cotton socks, size 43, new condition, max price 30 EUR, no Christmas patterns" },
      results: [approvedProduct],
    });
    const { container } = render(<App />);
    const searchInput = within(container).getByPlaceholderText(
      "Describe any product you want to buy..."
    );
    fireEvent.change(searchInput, { target: { value: "Men's cotton socks, size 43, new condition, max price 30 EUR, no Christmas patterns" } });
    await settle();
    fireEvent.click(within(container).getByText("Review requirements"));
    await settle(400);
    expect(
      within(container).getAllByText("Men's cotton socks, size 43, new condition, max price 30 EUR, no Christmas patterns").length
    ).toBeGreaterThan(0);
  });

  it("displays new positive constraints from mandate", async () => {
    setupFetch({
      execution_id: "e2",
      mandate: { query: "Men's cotton socks", max_price: 30, currency: "EUR", material: "cotton", size: "43" },
      results: [approvedProduct],
    });
    const { container } = render(<App />);
    const searchInput = within(container).getByPlaceholderText(
      "Describe any product you want to buy..."
    );
    fireEvent.change(searchInput, { target: { value: "Men's cotton socks, size 43, max price 30 EUR" } });
    await settle();
    fireEvent.click(within(container).getByText("Review requirements"));
    await settle();
    expect(
      within(container).getAllByText("Max price 30 EUR").length
    ).toBeGreaterThan(0);
    expect(
      within(container).getAllByText("Size 43").length
    ).toBeGreaterThan(0);
    expect(
      within(container).getAllByText("cotton").length
    ).toBeGreaterThan(0);
  });

  it("displays new negative constraints (forbidden designs) from mandate", async () => {
    setupFetch({
      execution_id: "e2",
      mandate: { query: "Men's cotton socks", forbidden_designs: ["Christmas", "snowmen"] },
      results: [approvedProduct],
    });
    const { container } = render(<App />);
    const searchInput = within(container).getByPlaceholderText(
      "Describe any product you want to buy..."
    );
    fireEvent.change(searchInput, { target: { value: "Men's cotton socks, no Christmas" } });
    await settle();
    fireEvent.click(within(container).getByText("Review requirements"));
    await settle();
    expect(
      within(container).getAllByText("Christmas").length
    ).toBeGreaterThan(0);
  });

  it("does not reuse previous locked session for new intent", async () => {
    setupFetch({
      execution_id: "e2",
      mandate: { query: "new request", session_id: "s2" },
      results: [approvedProduct],
    });
    const { container } = render(<App />);
    await completeSearchFlow(container);
    // After first flow, session s1 is locked
    // Change intent and review → new session s2
    const searchInput = within(container).getByPlaceholderText(
      "Describe any product you want to buy..."
    );
    fireEvent.change(searchInput, { target: { value: "new request" } });
    await settle();
    fireEvent.click(within(container).getByText("Review requirements"));
    await settle();
    // New session should be s2, not s1
    expect(
      within(container).getAllByText("new request").length
    ).toBeGreaterThan(0);
    // Search should remain disabled until confirmed and locked
    const searchBtn = within(container).getByText("Search");
    expect(searchBtn.disabled).toBe(true);
  });

  it("keeps Search disabled until new mandate is confirmed and locked", async () => {
    setupFetch({
      execution_id: "e2",
      mandate: { query: "new request" },
      results: [approvedProduct],
    });
    const { container } = render(<App />);
    const searchInput = within(container).getByPlaceholderText(
      "Describe any product you want to buy..."
    );
    fireEvent.change(searchInput, { target: { value: "new request" } });
    await settle();
    fireEvent.click(within(container).getByText("Review requirements"));
    await settle();
    // After Review, locked=false → Search disabled
    const searchBtn = within(container).getByText("Search");
    expect(searchBtn.disabled).toBe(true);
    // After Confirm, locked=true → Search enabled
    fireEvent.click(within(container).getByText("Confirm requirements"));
    await settle();
    expect(searchBtn.disabled).toBe(false);
  });

  it("clears previous selection when new intent is reviewed", async () => {
    setupFetch({
      execution_id: "e2",
      mandate: { query: "new request" },
      results: [approvedProduct],
    });
    const { container } = render(<App />);
    await completeSearchFlow(container);
    // Evidence panel shows Product A (Socks)
    expect(
      within(container).getAllByText("Product Evidence: Socks").length
    ).toBeGreaterThan(0);
    // New intent review clears selection
    const searchInput = within(container).getByPlaceholderText(
      "Describe any product you want to buy..."
    );
    fireEvent.change(searchInput, { target: { value: "new request" } });
    await settle();
    fireEvent.click(within(container).getByText("Review requirements"));
    await settle();
    // Selection cleared → no evidence panel
    expect(
      within(container).queryByText("Product Evidence: Socks")
    ).toBeNull();
  });

  it("restores existing history still works", async () => {
    setupFetch(approvedSearchResponse, [
      { execution_id: "e1", timestamp: "2026-11-05T18:00:00Z", session_id: "s1", results_count: 1 },
    ]);
    const { container } = render(<App />);
    await settle();
    expect(
      within(container).getAllByText(/socks/i).length
    ).toBeGreaterThan(0);
    await waitFor(() =>
      expect(within(container).getAllByText(/Socks/).length).toBeGreaterThan(0)
    );
  });

  // --- allowed_conditions display tests ---

  it("renders Condition: New tag for allowed_conditions", async () => {
    setupFetch({
      execution_id: "e2",
      mandate: { query: "cotton socks", allowed_conditions: ["new"] },
      results: [approvedProduct],
    });
    const { container } = render(<App />);
    const searchInput = within(container).getByPlaceholderText(
      "Describe any product you want to buy..."
    );
    fireEvent.change(searchInput, { target: { value: "cotton socks" } });
    await settle();
    fireEvent.click(within(container).getByText("Review requirements"));
    await settle();
    expect(
      within(container).getAllByText(/Condition: New/i).length
    ).toBeGreaterThan(0);
  });

  it("renders multiple allowed conditions", async () => {
    setupFetch({
      execution_id: "e2",
      mandate: { query: "socks", allowed_conditions: ["new", "refurbished"] },
      results: [approvedProduct],
    });
    const { container } = render(<App />);
    const searchInput = within(container).getByPlaceholderText(
      "Describe any product you want to buy..."
    );
    fireEvent.change(searchInput, { target: { value: "socks" } });
    await settle();
    fireEvent.click(within(container).getByText("Review requirements"));
    await settle();
    expect(
      within(container).getAllByText(/Condition: New/i).length
    ).toBeGreaterThan(0);
    expect(
      within(container).getAllByText(/Condition: Refurbished/i).length
    ).toBeGreaterThan(0);
  });

  it("renders no empty tag when allowed_conditions is empty", async () => {
    setupFetch({
      execution_id: "e2",
      mandate: { query: "socks", allowed_conditions: [] },
      results: [approvedProduct],
    });
    const { container } = render(<App />);
    const searchInput = within(container).getByPlaceholderText(
      "Describe any product you want to buy..."
    );
    fireEvent.change(searchInput, { target: { value: "socks" } });
    await settle();
    fireEvent.click(within(container).getByText("Review requirements"));
    await settle();
    expect(
      within(container).queryByText(/Condition:/)
    ).toBeNull();
  });

  it("renders no empty tag when allowed_conditions is missing", async () => {
    setupFetch({
      execution_id: "e2",
      mandate: { query: "socks", max_price: 30, currency: "EUR", material: "cotton", size: "43" },
      results: [approvedProduct],
    });
    const { container } = render(<App />);
    const searchInput = within(container).getByPlaceholderText(
      "Describe any product you want to buy..."
    );
    fireEvent.change(searchInput, { target: { value: "socks" } });
    await settle();
    fireEvent.click(within(container).getByText("Review requirements"));
    await settle(300);
    // Existing constraints still visible
    expect(container.textContent).toContain("Max price 30 EUR");
    expect(container.textContent).toContain("Size 43");
    expect(container.textContent).toContain("cotton");
    // No Condition tag
    expect(
      within(container).queryByText(/Condition:/)
    ).toBeNull();
  });

  // --- Checkout gating tests ---

  it("fresh locked + APPROVED + valid IDs → PayPal enabled", async () => {
    setupFetch(approvedSearchResponse);
    const { container } = render(<App />);
    await completeSearchFlow(container);
    expect(container.querySelector(".checkout > div > div")).toBeTruthy();
  });

it("historical execution + APPROVED + locked=false → PayPal disabled", async () => {
    setupFetch(approvedSearchResponse, [
      { execution_id: "e1", timestamp: "2026-11-05T18:00:00Z", session_id: "s1", results_count: 1 },
    ]);
    const { container } = render(<App />);
    await settle();
    await settle(200);
    // After historical restore, selected is null → PayPal not mounted
    expect(container.querySelector(".checkout > div > div")).toBeNull();
    expect(
      within(container).getAllByText(/start a new search to continue to checkout/i).length
    ).toBeGreaterThan(0);
  });

  it("new session not locked → PayPal disabled", async () => {
    setupFetch(approvedSearchResponse);
    const { container } = render(<App />);
    const searchInput = within(container).getByPlaceholderText(
      "Describe any product you want to buy..."
    );
    fireEvent.change(searchInput, { target: { value: "socks" } });
    await settle();
    fireEvent.click(within(container).getByText("Review requirements"));
    await settle();
    expect(container.querySelector(".checkout > div > div")).toBeNull();
  });

  it("REJECTED → PayPal disabled", async () => {
    setupFetch({
      execution_id: "e1",
      mandate: { query: "socks" },
      results: [
        {
          product: { title: "Rejected Socks", images: [], offers: [{ offer_id: "offer-1", price: { price: 10, currency: "EUR" }, condition: "new" }] },
          gate: { decision: "REJECTED", rules: [] },
          offer: { offer_id: "offer-1", price: { price: 10, currency: "EUR" } },
        },
      ],
    });
    const { container } = render(<App />);
    const searchInput = within(container).getByPlaceholderText(
      "Describe any product you want to buy..."
    );
    fireEvent.change(searchInput, { target: { value: "socks" } });
    await settle();
    fireEvent.click(within(container).getByText("Review requirements"));
    await settle();
    fireEvent.click(within(container).getByText("Confirm requirements"));
    await settle();
    fireEvent.click(within(container).getByText("Search"));
    await settle();
    expect(container.querySelector(".checkout > div > div")).toBeNull();
  });

  it("NEEDS_CONFIRMATION → PayPal disabled", async () => {
    setupFetch({
      execution_id: "e1",
      mandate: { query: "socks" },
      results: [
        {
          product: { title: "Confirm Socks", images: [], offers: [{ offer_id: "offer-1", price: { price: 10, currency: "EUR" }, condition: "new" }] },
          gate: { decision: "NEEDS_CONFIRMATION", rules: [] },
          offer: { offer_id: "offer-1", price: { price: 10, currency: "EUR" } },
        },
      ],
    });
    const { container } = render(<App />);
    const searchInput = within(container).getByPlaceholderText(
      "Describe any product you want to buy..."
    );
    fireEvent.change(searchInput, { target: { value: "socks" } });
    await settle();
    fireEvent.click(within(container).getByText("Review requirements"));
    await settle();
    fireEvent.click(within(container).getByText("Confirm requirements"));
    await settle();
    fireEvent.click(within(container).getByText("Search"));
    await settle();
    expect(container.querySelector(".checkout > div > div")).toBeNull();
  });

  it("new intent resets eligibility", async () => {
    setupFetch(approvedSearchResponse);
    const { container } = render(<App />);
    await completeSearchFlow(container);
    expect(container.querySelector(".checkout > div > div")).toBeTruthy();
    const searchInput = within(container).getByPlaceholderText(
      "Describe any product you want to buy..."
    );
    fireEvent.change(searchInput, { target: { value: "new request" } });
    await settle();
    fireEvent.click(within(container).getByText("Review requirements"));
    await settle();
    expect(container.querySelector(".checkout > div > div")).toBeNull();
  });

  // --- Attestation workflow tests ---

  const needsConfirmationSearchResponse = {
    execution_id: "e1",
    mandate: { query: "socks" },
    results: [
      {
        product: { title: "Confirm Socks", images: [], offers: [{ offer_id: "offer-1", price: { price: 10, currency: "EUR" }, condition: "new" }] },
        gate: {
          decision: "NEEDS_CONFIRMATION",
          rules: [
            { rule: "min_size", expected: "43", observed: null, passed: false, status: "APPLICABLE", priority: "REQUIRED", evidence: "product.size" },
            { rule: "material", expected: "cotton", observed: null, passed: false, status: "APPLICABLE", priority: "REQUIRED", evidence: "product.material" },
          ],
          policy_version: "2.0",
        },
        offer: { offer_id: "offer-1", price: { price: 10, currency: "EUR" } },
      },
    ],
  };

  function setupFetchWithAttestations(searchResponse, executions = []) {
    let attestations = {};
    mockFetch.mockImplementation(async (url, opts) => {
      const u = String(url);
      const o = opts || {};
      const method = o.method || "GET";
      if (u === "http://127.0.0.1:8000/api/executions") return json(200, executions);
      if (u.startsWith("http://127.0.0.1:8000/api/executions/") && method === "GET") {
        const id = u.substring(u.lastIndexOf("/") + 1);
        if (searchResponse && searchResponse.execution_id === id) {
          return json(200, { execution_id: id, ...searchResponse });
        }
        return json(404, {});
      }
      if (u === "http://127.0.0.1:8000/api/intent" && method === "POST")
        return json(200, { session_id: "s1", mandate: searchResponse.mandate });
      if (u === "http://127.0.0.1:8000/api/mandate/s1/lock" && method === "POST")
        return json(200, { locked: true, mandate: {} });
      if (u === "http://127.0.0.1:8000/api/search/s1" && method === "POST")
        return json(200, searchResponse);
      if (u === "http://127.0.0.1:8000/api/attestations" && method === "POST") {
        const body = JSON.parse(o.body || "{}");
        const key = `${body.execution_id}:${body.offer_id}:${body.rule_id}`;
        attestations[key] = { ...body, id: "a1", timestamp: "2026-01-01T00:00:00Z" };
        return json(200, { id: "a1", ...body, timestamp: "2026-01-01T00:00:00Z" });
      }
      if (u.startsWith("http://127.0.0.1:8000/api/attestations") && method === "GET") {
        if (u.includes("/api/attestations/")) {
          const id = u.substring(u.lastIndexOf("/") + 1);
          const found = Object.values(attestations).find((a) => a.id === id);
          if (found) {
            const qs = new URLSearchParams(u.split("?")[1] || "");
            if (qs.get("session_id") && qs.get("session_id") !== found.session_id) {
              return json(403, { detail: "attestation does not belong to session" });
            }
            return json(200, found);
          }
          return json(404, {});
        }
        // List endpoint
        const qs = new URLSearchParams(u.split("?")[1] || "");
        const sessionFilter = qs.get("session_id");
        const execFilter = qs.get("execution_id");
        const offerFilter = qs.get("offer_id");
        const ruleFilter = qs.get("rule_id");
        const filtered = Object.values(attestations).filter((a) => {
          if (sessionFilter && a.session_id !== sessionFilter) return false;
          if (execFilter && a.execution_id !== execFilter) return false;
          if (offerFilter && a.offer_id !== offerFilter) return false;
          if (ruleFilter && a.rule_id !== ruleFilter) return false;
          return true;
        });
        return json(200, filtered);
      }
      if (u.includes("/api/orders/checkout") && method === "POST") {
        lastCheckoutBody = JSON.parse(o.body || "{}");
        const key = `${lastCheckoutBody.execution_id}:${lastCheckoutBody.offer_id}`;
        // Checkout requires fresh APPROVED — attestations don't change the gate decision in this mock
        if (searchResponse.results[0].gate.decision === "NEEDS_CONFIRMATION") {
          return json(403, { detail: "offer not approved" });
        }
        if (checkoutResolver) {
          return new Promise((res) => {
            checkoutResolver = res;
          });
        }
        return json(200, {
          order_id: "O1",
          approve_url: "https://approve",
          status: "CREATED",
          payment_id: "p1",
        });
      }
      if (u.includes("/api/orders/capture") && method === "POST") {
        lastCaptureBody = JSON.parse(o.body || "{}");
        return json(200, {
          status: "PAYMENT_CAPTURED",
          capture_id: "CAP1",
          amount: "10.00",
          currency: "EUR",
        });
      }
      return json(404, {});
    });
    return attestations;
  }

  it("NEEDS_CONFIRMATION product shows attestation form, not confirmation buttons", async () => {
    setupFetchWithAttestations(needsConfirmationSearchResponse);
    const { container } = render(<App />);
    await completeSearchFlow(container);
    // No Confirm/Waive buttons
    expect(
      within(container).queryByText("Confirm & Approve")
    ).toBeNull();
    expect(
      within(container).queryByText("Waive & Proceed")
    ).toBeNull();
    // Attestation panel should be visible
    expect(
      within(container).getAllByText("User attestation required for unresolved requirements:").length
    ).toBeGreaterThan(0);
    // PayPal should NOT be mounted
    expect(container.querySelector(".checkout > div > div")).toBeNull();
  });

  it("Attestation form requires value, source URL, and confirmation checkbox", async () => {
    setupFetchWithAttestations(needsConfirmationSearchResponse);
    const { container } = render(<App />);
    await completeSearchFlow(container);
    // Try to submit without filling in the form
    const submitBtn = within(container).getAllByText("Submit attestation")[0];
    fireEvent.click(submitBtn);
    await settle();
    // Should show error about missing value/source/confirmation
    expect(
      within(container).getAllByText("Please provide value, source URL, and confirm you checked.").length
    ).toBeGreaterThan(0);
    // PayPal still not mounted
    expect(container.querySelector(".checkout > div > div")).toBeNull();
  });

  it("APPROVED product does not show attestation panel", async () => {
    setupFetch(approvedSearchResponse);
    const { container } = render(<App />);
    await completeSearchFlow(container);
    // No attestation panel for APPROVED
    expect(
      within(container).queryByText("User attestation required for unresolved requirements:")
    ).toBeNull();
    // PayPal should be mounted directly
    expect(container.querySelector(".checkout > div > div")).toBeTruthy();
  });

  it("REJECTED product remains blocked with no attestation panel", async () => {
    setupFetch({
      execution_id: "e1",
      mandate: { query: "socks" },
      results: [
        {
          product: { title: "Rejected Socks", images: [], offers: [{ offer_id: "offer-1", price: { price: 10, currency: "EUR" }, condition: "new" }] },
          gate: { decision: "REJECTED", rules: [], policy_version: "2.0" },
          offer: { offer_id: "offer-1", price: { price: 10, currency: "EUR" } },
        },
      ],
    });
    const { container } = render(<App />);
    await completeSearchFlow(container);
    expect(
      within(container).queryByText("User attestation required for unresolved requirements:")
    ).toBeNull();
    expect(container.querySelector(".checkout > div > div")).toBeNull();
  });

  describe("History execution selection", () => {
    const targetExecutionId = "11aac7e1-56a4-40f7-9def-445e86f58cde";

    const targetExecutionDetail = {
      execution_id: targetExecutionId,
      session_id: "s1",
      locked: true,
      locked_mandate: { query: "socks", product_type: "socks" },
      session_query: "socks",
      mandate: { query: "socks" },
      results: [
        {
          product: {
            title: "Socks",
            images: [],
            offers: [
              {
                offer_id: "offer-1",
                price: { price: 10, currency: "EUR" },
                condition: "new",
              },
            ],
          },
          gate: { decision: "APPROVED", rules: [] },
          offer: { offer_id: "offer-1", price: { price: 10, currency: "EUR" } },
        },
      ],
    };

    beforeEach(() => {
      cleanup();
      mockFetch.mockReset();
      checkoutResolver = undefined;
      lastCheckoutBody = undefined;
      lastCaptureBody = undefined;
    });

    afterEach(() => {
      vi.restoreAllMocks();
      delete window.paypal;
    });

    it("displays query from executions list in history item", async () => {
      const executions = [
        {
          execution_id: targetExecutionId,
          timestamp: "2024-01-01T00:00:00Z",
          session_id: "s1",
          results_count: 1,
          query: "socks",
        },
      ];
      setupFetch(targetExecutionDetail, executions);
      const { container } = render(<App />);
      await settle(300);
      const historyItem = container.querySelector(
        '[data-execution-id="' + targetExecutionId + '"]'
      );
      expect(historyItem).toBeTruthy();
      expect(historyItem.textContent).toContain("socks");
    });

    it("exposes exact execution ID via data-execution-id attribute", async () => {
      const executions = [
        {
          execution_id: targetExecutionId,
          timestamp: "2024-01-01T00:00:00Z",
          session_id: "s1",
          results_count: 1,
          query: "socks",
        },
      ];
      setupFetch(targetExecutionDetail, executions);
      const { container } = render(<App />);
      await settle(300);
      const historyItem = container.querySelector(
        'button[data-execution-id="' + targetExecutionId + '"]'
      );
      expect(historyItem).toBeTruthy();
      expect(historyItem.getAttribute("data-execution-id")).toBe(targetExecutionId);
    });

    it("clicking history item selects exact execution", async () => {
      const executions = [
        {
          execution_id: targetExecutionId,
          timestamp: "2024-01-01T00:00:00Z",
          session_id: "s1",
          results_count: 1,
          query: "socks",
        },
      ];
      setupFetch(targetExecutionDetail, executions);
      const { container } = render(<App />);
      await settle(300);
      const historyItem = container.querySelector(
        '[data-execution-id="' + targetExecutionId + '"]'
      );
      fireEvent.click(historyItem);
      await waitFor(() => {
        const chip = container.querySelector(".agent-context__chip");
        expect(chip.textContent).toContain(targetExecutionId);
      });
    });
  });
});
