import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, within, fireEvent, cleanup } from "@testing-library/react";
import App from "../App.jsx";

const mockFetch = vi.fn();
global.fetch = mockFetch;

function json(status, data) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function setupFetch(intentMandate) {
  mockFetch.mockImplementation(async (url, opts) => {
    const u = String(url);
    const o = opts || {};
    const method = o.method || "GET";
    if (u === "http://127.0.0.1:8000/api/executions") return json(200, []);
    if (u === "http://127.0.0.1:8000/api/intent" && method === "POST") {
      return json(200, { session_id: "s-laptop", mandate: intentMandate });
    }
    if (u.startsWith("http://127.0.0.1:8000/api/mandate/") && u.endsWith("/lock") && method === "POST") {
      return json(200, { locked: true, mandate: intentMandate });
    }
    return json(404, {});
  });
}

async function settle(ms = 120) {
  await new Promise((r) => setTimeout(r, ms));
}

async function reviewIntent(container, text) {
  const searchInput = within(container).getByPlaceholderText(
    "Describe any product you want to buy..."
  );
  fireEvent.change(searchInput, { target: { value: text } });
  fireEvent.click(within(container).getByText("Review requirements"));
  await settle();
}

describe("structured mandate review", () => {
  beforeEach(() => {
    cleanup();
    mockFetch.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("laptop mandate displays classification, RAM, storage, and OS from backend mandate", async () => {
    const original = "Find a new Windows laptop with at least 16GB RAM and 512GB SSD, maximum 1200 EUR.";
    setupFetch({
      query: "windows laptop",
      product_type: "laptop",
      min_ram_gb: 16,
      min_storage_gb: 512,
      required_operating_system: "Windows",
      max_price: 1200,
      currency: "EUR",
      allowed_conditions: ["new"],
    });
    const { container } = render(<App />);
    await reviewIntent(container, original);

    expect(within(container).getByText("Original user request")).toBeTruthy();
    expect(within(container).getByText("Parsed purchasing requirements")).toBeTruthy();
    expect(within(container).getByText(original)).toBeTruthy();

    const panel = within(within(container).getByLabelText("Parsed laptop purchasing requirements"));
    expect(panel.getByText("Laptop / Notebook")).toBeTruthy();
    expect(panel.getByText("16 GB")).toBeTruthy();
    expect(panel.getByText("512 GB")).toBeTruthy();
    expect(panel.getByText("Windows")).toBeTruthy();
    expect(panel.getByText("1200 EUR")).toBeTruthy();
    expect(panel.getByText(/new/i)).toBeTruthy();
  });

  it("shows explicit CPU and storage interface requirements when present", async () => {
    setupFetch({
      query: "ThinkPad",
      product_type: "laptop",
      min_ram_gb: 32,
      min_storage_gb: 1024,
      required_storage_interface: "NVMe",
      required_processor_family: "Intel Core Ultra 7",
      required_processor_model: "Intel Core Ultra 7 268V",
      required_operating_system: "Windows 11 Pro",
      max_price: 1800,
      currency: "EUR",
      allowed_conditions: ["new"],
    });
    const { container } = render(<App />);
    await reviewIntent(container, "ThinkPad Ultra 7 268V 32GB 1TB NVMe Windows 11 Pro");

    const panel = within(within(container).getByLabelText("Parsed laptop purchasing requirements"));
    expect(panel.getByText("NVMe")).toBeTruthy();
    expect(panel.getByText(/Intel Core Ultra 7/)).toBeTruthy();
    expect(panel.getByText(/268V/)).toBeTruthy();
    expect(panel.getByText("Windows 11 Pro")).toBeTruthy();
  });

  it("does not invent unspecified laptop fields", async () => {
    setupFetch({
      query: "windows laptop",
      product_type: "laptop",
      min_ram_gb: 16,
      required_operating_system: "Windows",
      max_price: 1200,
      currency: "EUR",
      allowed_conditions: ["new"],
      min_storage_gb: null,
      required_storage_interface: null,
      required_processor_family: null,
      required_processor_subfamily: null,
      required_processor_model: null,
    });
    const { container } = render(<App />);
    await reviewIntent(container, "windows laptop 16GB");

    const panel = within(within(container).getByLabelText("Parsed laptop purchasing requirements"));
    expect(panel.getByText("16 GB")).toBeTruthy();
    expect(panel.queryByText(/Minimum installed storage/i)).toBeNull();
    expect(panel.queryByText(/Required storage interface/i)).toBeNull();
    expect(panel.queryByText(/Processor requirement/i)).toBeNull();
    // Ensure no generic invented placeholders leak
    expect(panel.queryByText(/^null$/i)).toBeNull();
    expect(panel.queryByText(/undefined/i)).toBeNull();
  });

  it("keeps original natural-language request visible for laptop mandates", async () => {
    const original = "I need a Windows laptop for coding, 16GB RAM and 512GB SSD, under 1200 euros.";
    setupFetch({
      query: "windows coding laptop",
      product_type: "laptop",
      min_ram_gb: 16,
      min_storage_gb: 512,
      required_operating_system: "Windows",
      max_price: 1200,
      currency: "EUR",
      allowed_conditions: ["new"],
    });
    const { container } = render(<App />);
    await reviewIntent(container, original);
    expect(within(container).getByText(original)).toBeTruthy();
    expect(within(container).getByText("Original user request")).toBeTruthy();
  });

  it("non-laptop mandates retain existing positive/negative constraint layout and show no laptop fields", async () => {
    setupFetch({
      query: "men's cotton socks",
      product_type: null,
      max_price: 30,
      currency: "EUR",
      material: "cotton",
      size: "43",
      allowed_conditions: ["new"],
      forbidden_designs: ["Christmas"],
      min_ram_gb: null,
      min_storage_gb: null,
      required_operating_system: null,
    });
    const { container } = render(<App />);
    await reviewIntent(container, "men's cotton socks size 43 no Christmas");

    expect(within(container).getByText("Positive constraints")).toBeTruthy();
    expect(within(container).getByText("Negative constraints")).toBeTruthy();
    expect(within(container).getByText("Max price 30 EUR")).toBeTruthy();
    expect(within(container).getByText("cotton")).toBeTruthy();
    expect(within(container).getByText("Size 43")).toBeTruthy();
    expect(within(container).getAllByText(/Condition: new/i).length).toBeGreaterThan(0);
    expect(within(container).getByText("Christmas")).toBeTruthy();

    expect(within(container).queryByLabelText("Parsed laptop purchasing requirements")).toBeNull();
    expect(within(container).queryByText("Laptop / Notebook")).toBeNull();
    expect(within(container).queryByText(/Minimum RAM/i)).toBeNull();
    expect(within(container).queryByText(/Minimum installed storage/i)).toBeNull();
  });

  it("lock action uses existing session flow and does not change parsed mandate", async () => {
    const mandate = {
      query: "windows laptop",
      product_type: "laptop",
      min_ram_gb: 16,
      min_storage_gb: 512,
      required_operating_system: "Windows",
      max_price: 1200,
      currency: "EUR",
      allowed_conditions: ["new"],
    };
    setupFetch(mandate);
    const { container } = render(<App />);
    await reviewIntent(container, "windows laptop 16/512");

    // Requirements visible before lock
    expect(within(container).getByLabelText("Parsed laptop purchasing requirements")).toBeTruthy();
    expect(within(container).getByText("Confirm requirements")).toBeTruthy();

    fireEvent.click(within(container).getByText("Confirm requirements"));
    await settle();

    const lockCalls = mockFetch.mock.calls.filter((c) => String(c[0]).includes("/api/mandate/s-laptop/lock"));
    expect(lockCalls.length).toBe(1);
    expect((lockCalls[0][1] || {}).method).toBe("POST");

    // Still shows same parsed backend values (no frontend reinterpretation)
    const panel = within(within(container).getByLabelText("Parsed laptop purchasing requirements"));
    expect(panel.getByText("16 GB")).toBeTruthy();
    expect(panel.getByText("512 GB")).toBeTruthy();
    expect(panel.getByText("Windows")).toBeTruthy();
    expect(panel.getByText("1200 EUR")).toBeTruthy();
  });
});
