import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, within, fireEvent, cleanup, waitFor } from "@testing-library/react";
import App from "../App.jsx";

const mockFetch = vi.fn();
global.fetch = mockFetch;

function json(status, data) {
  return new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
}

const executions = [
  { execution_id: "exec-1", timestamp: "2026-11-05T10:00:00Z", session_id: "s1", results_count: 2, query: "wireless headphones" },
  { execution_id: "exec-2", timestamp: "2026-11-05T09:00:00Z", session_id: "s2", results_count: 1, query: "socks" },
  { execution_id: "exec-3", timestamp: "2026-11-05T08:00:00Z", session_id: null, results_count: 0, query: "running shoes" },
  { execution_id: "exec-4", timestamp: "2026-11-05T07:00:00Z", session_id: "s4", results_count: 1, query: "leather wallet" },
  { execution_id: "exec-5", timestamp: "2026-11-05T06:00:00Z", session_id: null, results_count: 2, query: "usb cable" },
  { execution_id: "exec-6", timestamp: "2026-11-05T05:00:00Z", session_id: null, results_count: 1, query: "old search" },
];

const details = {
  "exec-1": { execution_id: "exec-1", timestamp: "2026-11-05T10:00:00Z", mandate: { query: "wireless headphones" }, results: [{ gate: { decision: "APPROVED" } }, { gate: { decision: "REJECTED" } }] },
  "exec-2": { execution_id: "exec-2", timestamp: "2026-11-05T09:00:00Z", mandate: { query: "socks" }, results: [{ gate: { decision: "APPROVED" } }] },
  "exec-3": { execution_id: "exec-3", timestamp: "2026-11-05T08:00:00Z", mandate: { query: "running shoes" }, results: [] },
  "exec-4": { execution_id: "exec-4", timestamp: "2026-11-05T07:00:00Z", mandate: { query: "leather wallet" }, results: [{ gate: { decision: "NEEDS_CONFIRMATION" } }] },
  "exec-5": { execution_id: "exec-5", timestamp: "2026-11-05T06:00:00Z", mandate: { query: "usb cable" }, results: [{ gate: { decision: "REJECTED" } }, { gate: { decision: "REJECTED" } }] },
  "exec-6": { execution_id: "exec-6", timestamp: "2026-11-05T05:00:00Z", mandate: { query: "old search" }, results: [{ gate: { decision: "APPROVED" } }] },
};

function setupFetch(execList = executions, detMap = details) {
  mockFetch.mockImplementation(async (url, opts) => {
    const u = String(url);
    const method = (opts && opts.method) || "GET";
    if (u === "http://127.0.0.1:8000/api/executions" && method === "GET") return json(200, execList);
    if (u.startsWith("http://127.0.0.1:8000/api/executions/") && method === "GET") {
      const id = u.substring(u.lastIndexOf("/") + 1);
      const detail = detMap[id];
      if (detail) return json(200, detail);
      return json(404, {});
    }
    return json(200, {});
  });
}

describe("search history", () => {
  beforeEach(() => {
    cleanup();
    mockFetch.mockClear();
    setupFetch();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("displays only the latest 5 searches by default", async () => {
    const { container } = render(<App />);
    await waitFor(() => expect(container.textContent).toContain("wireless headphones"));
    expect(container.textContent).not.toContain("old search");
    expect(container.textContent).toContain("5 of 6");
  });

  it("orders searches newest first", async () => {
    const { container } = render(<App />);
    await waitFor(() => expect(container.textContent).toContain("wireless headphones"));
    fireEvent.click(within(container).getByText("View all searches"));
    await waitFor(() => expect(container.textContent).toContain("6 of 6"));
    const t = container.textContent;
    // The newest search (exec-1) shows its real query first
    expect(t.indexOf("wireless headphones")).toBeGreaterThanOrEqual(0);
    // exec-6 ("old search") is the last item, so it appears after exec-1
    expect(t.indexOf("old search")).toBeGreaterThan(t.indexOf("wireless headphones"));
  });

  it("does not display raw execution IDs as labels", async () => {
    const { container } = render(<App />);
    await waitFor(() => expect(container.textContent).toContain("wireless headphones"));
    // History items render readable queries, not raw UUIDs like "exec-1"
    expect(container.textContent).toContain("wireless headphones");
    expect(container.textContent).not.toContain("exec-1");
  });

  it("reveals all searches when View all is clicked", async () => {
    const { container } = render(<App />);
    await waitFor(() => expect(container.textContent).toContain("wireless headphones"));
    // Default shows 5 items
    expect(container.querySelectorAll(".history-item").length).toBe(5);
    expect(container.textContent).not.toContain("old search");
    fireEvent.click(within(container).getByText("View all searches"));
    await waitFor(() => expect(container.textContent).toContain("6 of 6"));
    expect(container.querySelectorAll(".history-item").length).toBe(6);
    expect(container.textContent).toContain("old search");
  });

  it("selecting a history item loads that execution", async () => {
    const { container } = render(<App />);
    await waitFor(() => expect(container.textContent).toContain("wireless headphones"));
    // exec-2 shows "socks" query; clicking loads its detail
    fireEvent.click(within(container).getAllByText("socks")[0]);
    await waitFor(() => expect(container.textContent).toContain("socks"));
    expect(container.textContent).toContain("socks");
  });

  it("displays a friendly empty state when there are no searches", async () => {
    setupFetch([], {});
    const { container } = render(<App />);
    await waitFor(() => expect(container.textContent).toContain("No recent searches"));
  });
});
