import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, within, fireEvent, cleanup } from "@testing-library/react";
import App from "../App.jsx";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
  cleanup();
});

const mockFetch = vi.fn();
global.fetch = mockFetch;

function json(status, data) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

let councilFixture;
let councilBody;
let councilResolver;

function setupFetch(searchResponse, executions = []) {
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
    if (u.startsWith("http://127.0.0.1:8000/api/executions/") && u.endsWith("/agent-council") && method === "POST") {
      councilBody = JSON.parse(o.body || "{}");
      return new Promise((res) => {
        councilResolver = res;
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
  fireEvent.click(within(container).getByText("Confirm requirements"));
  await settle();
  fireEvent.click(within(container).getByText("Search"));
  await settle(300);
  await settle(300);
}

const productA = {
  product: {
    title: "Product A",
    images: [],
    offers: [{ offer_id: "offer-a", price: { price: 10, currency: "EUR" }, condition: "new" }],
  },
  gate: { decision: "APPROVED", rules: [] },
  offer: { offer_id: "offer-a", price: { price: 10, currency: "EUR" } },
};

const productB = {
  product: {
    title: "Product B",
    images: [],
    offers: [{ offer_id: "offer-b", price: { price: 20, currency: "EUR" }, condition: "new" }],
  },
  gate: { decision: "APPROVED", rules: [] },
  offer: { offer_id: "offer-b", price: { price: 20, currency: "EUR" } },
};

const councilReport = {
  execution_id: "e1",
  offer_id: "offer-a",
  advisory_only: true,
  policy_gate_decision: "APPROVED",
  agents: [
    { id: "intent_auditor", name: "Intent Auditor", status: "NO_CONCERN_FOUND", summary: "All purchasing mandate constraints are satisfied by the selected offer.", findings: [] },
    { id: "evidence_analyst", name: "Evidence Analyst", status: "CONCERN", summary: "Some evidence could not be verified against the locked mandate.", findings: [{ severity: "HIGH", requirement: "material", source_ref: "product.material", message: "Material not explicitly evidenced." }] },
    { id: "red_team_auditor", name: "Red Team Auditor", status: "NO_CONCERN_FOUND", summary: "No contradictions or suspicious claims detected.", findings: [] },
  ],
  duration_ms: 450,
};

describe("Agent Council integration", () => {
  beforeEach(() => {
    cleanup();
    mockFetch.mockReset();
    councilFixture = councilReport;
    councilBody = undefined;
    councilResolver = undefined;
    setupFetch({ execution_id: "e1", mandate: { query: "socks" }, results: [productA] });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the Run council button after an APPROVED product is selected", async () => {
    const { container } = render(<App />);
    await completeSearchFlow(container);
    expect(within(container).getByText("Run council")).toBeTruthy();
  });

  it("does not show the button when no product is selected", async () => {
    setupFetch({ execution_id: "e1", mandate: { query: "socks" }, results: [] });
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
    await settle(300);
    await settle(300);
    expect(
      within(container).queryByText("Run council")
    ).toBeNull();
  });

  it("calls POST /api/executions/{execution_id}/agent-council with executionId and offerId", async () => {
    const { container } = render(<App />);
    await completeSearchFlow(container);
    fireEvent.click(within(container).getByText("Run council"));
    await settle();
    const councilCalls = mockFetch.mock.calls.filter((c) =>
      String(c[0]).includes("/agent-council")
    );
    expect(councilCalls.length).toBe(1);
    expect(String(councilCalls[0][0])).toContain("/api/executions/e1/agent-council");
    expect(councilCalls[0][1].method).toBe("POST");
    expect(councilBody).toEqual({ offer_id: "offer-a" });
    councilResolver(json(200, councilFixture));
    await settle();
  });

  it("shows running state while the council request is in flight", async () => {
    const { container } = render(<App />);
    await completeSearchFlow(container);
    fireEvent.click(within(container).getByText("Run council"));
    await settle();
    expect(container.textContent).toMatch(/Running…/);
    councilResolver(json(200, councilFixture));
    await settle();
    expect(container.textContent).not.toMatch(/Running…/);
  });

  it("shows the three agent reports when the report arrives", async () => {
    const { container } = render(<App />);
    await completeSearchFlow(container);
    fireEvent.click(within(container).getByText("Run council"));
    await settle();
    councilResolver(json(200, councilFixture));
    await settle();
    expect(within(container).getAllByText("Intent Auditor").length).toBeGreaterThan(0);
    expect(within(container).getAllByText("Evidence Analyst").length).toBeGreaterThan(0);
    expect(within(container).getAllByText("Red Team Auditor").length).toBeGreaterThan(0);
    expect(
      within(container).getAllByText("All purchasing mandate constraints are satisfied by the selected offer.").length
    ).toBeGreaterThan(0);
    expect(within(container).getAllByText(/Some evidence could not be verified/).length).toBeGreaterThan(0);
    expect(within(container).getAllByText("No contradictions or suspicious claims detected.").length).toBeGreaterThan(0);
  });

  it("resets the council report when the selected offer changes", async () => {
    setupFetch({ execution_id: "e1", mandate: { query: "socks" }, results: [productA, productB] });
    const { container } = render(<App />);
    await completeSearchFlow(container);
    expect(within(container).getByText("Run council")).toBeTruthy();
    fireEvent.click(within(container).getByText("Run council"));
    await settle();
    councilResolver(json(200, councilFixture));
    await settle();
    expect(within(container).getAllByText("Intent Auditor").length).toBeGreaterThan(0);

    const rows = container.querySelectorAll(".ag-row");
    expect(rows.length).toBeGreaterThanOrEqual(2);
    fireEvent.click(rows[1]);
    await settle();
    await settle();

    expect(within(container).queryByText("Intent Auditor")).toBeNull();
    expect(within(container).queryByText("Evidence Analyst")).toBeNull();
    expect(within(container).queryByText("Red Team Auditor")).toBeNull();
    expect(within(container).getByText("Product Evidence: Product B")).toBeTruthy();
  });

  it("does not submit duplicate council requests while one is in flight", async () => {
    const { container } = render(<App />);
    await completeSearchFlow(container);
    const button = within(container).getByText("Run council");
    fireEvent.click(button);
    fireEvent.click(button);
    await settle();
    const councilCalls = mockFetch.mock.calls.filter((c) =>
      String(c[0]).includes("/agent-council")
    );
    expect(councilCalls.length).toBe(1);
    councilResolver(json(200, councilFixture));
    await settle();
  });

  it("leaves PayPal checkout conditions unchanged", async () => {
    const { container } = render(<App />);
    await completeSearchFlow(container);
    expect(container.querySelector(".checkout > div > div")).toBeTruthy();
    expect(container.textContent).not.toMatch(/PayPal checkout disabled \(blocked\)/);
    expect(container.textContent).not.toMatch(/Checkout unavailable/);
    fireEvent.click(within(container).getByText("Run council"));
    await settle();
    councilResolver(json(200, councilFixture));
    await settle();
    expect(container.querySelector(".checkout > div > div")).toBeTruthy();
    expect(container.textContent).not.toMatch(/PayPal checkout disabled \(blocked\)/);
    expect(container.textContent).not.toMatch(/Checkout unavailable/);
  });
});
