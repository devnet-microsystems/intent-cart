import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, cleanup, within } from "@testing-library/react";
import AgentCouncil from "../AgentCouncil.jsx";

const agents = [
  {
    id: "agent-compliance",
    name: "Compliance Agent",
    status: "NO_CONCERN_FOUND",
    summary: "All purchasing mandate constraints are satisfied by the selected offer.",
    findings: [],
  },
  {
    id: "agent-fraud",
    name: "Fraud Agent",
    status: "CONCERN",
    summary: "Billing address does not match the payment method on file.",
    findings: [{ severity: "high", message: "Billing address mismatch detected." }],
  },
  {
    id: "agent-inventory",
    name: "Inventory Agent",
    status: "INSUFFICIENT_DATA",
    summary: "Stock feed did not respond in time; availability cannot be verified.",
    findings: [{ severity: "medium", message: "Inventory snapshot older than 24 hours." }],
  },
];

function renderCouncil(props = {}) {
  const utils = render(<AgentCouncil agents={agents} policyGateDecision="APPROVED" {...props} />);
  return { container: utils.container, ...utils };
}

describe("AgentCouncil", () => {
  beforeEach(() => {
    cleanup();
  });

  afterEach(() => {
    cleanup();
  });

  it("renders the three agent cards with name, status, summary, and findings", () => {
    const { container } = renderCouncil();
    const articles = container.querySelectorAll(".agent-card");
    expect(articles).toHaveLength(3);

    expect(within(container).getByText("Compliance Agent")).toBeTruthy();
    expect(within(container).getByText("Fraud Agent")).toBeTruthy();
    expect(within(container).getByText("Inventory Agent")).toBeTruthy();
    expect(within(container).getByText("No concern found")).toBeTruthy();
    expect(within(container).getByText("Concern")).toBeTruthy();
    expect(within(container).getByText("Insufficient data")).toBeTruthy();
    expect(within(container).getByText("All purchasing mandate constraints are satisfied by the selected offer.")).toBeTruthy();
    expect(within(container).getByText("Billing address mismatch detected.")).toBeTruthy();
  });

  it("shows running state for every agent when isLoading=true", () => {
    const { container } = renderCouncil({ isLoading: true });
    const articles = container.querySelectorAll(".agent-card--running");
    expect(articles).toHaveLength(3);
    expect(within(container).getAllByText("Running")).toHaveLength(3);
    const spinners = container.querySelectorAll(".agent-spinner");
    expect(spinners).toHaveLength(3);
    expect(within(container).queryByText("Billing address mismatch detected.")).toBeNull();
  });

  it("renders the advisory warning prominently", () => {
    const { container } = renderCouncil();
    const warning = container.querySelector(".agent-council__warning");
    expect(warning).toBeTruthy();
    expect(warning.getAttribute("role")).toBe("status");
    expect(warning.textContent).toBe("AI agent findings are advisory. Only the deterministic Policy Gate can authorize checkout.");
    expect(warning.className).toContain("agent-council__warning");
  });

  it("shows distinct colors per agent status", () => {
    const { container } = renderCouncil();
    const compliance = within(container).getByText("Compliance Agent").closest(".agent-card");
    const fraud = within(container).getByText("Fraud Agent").closest(".agent-card");
    const inventory = within(container).getByText("Inventory Agent").closest(".agent-card");

    const complianceBadge = compliance.querySelector(".agent-badge");
    const fraudBadge = fraud.querySelector(".agent-badge");
    const inventoryBadge = inventory.querySelector(".agent-badge");

    expect(compliance.classList.contains("agent-card--ok")).toBe(true);
    expect(complianceBadge.classList.contains("agent-badge--ok")).toBe(true);
    expect(fraud.classList.contains("agent-card--concern")).toBe(true);
    expect(fraudBadge.classList.contains("agent-badge--concern")).toBe(true);
    expect(inventory.classList.contains("agent-card--insufficient")).toBe(true);
    expect(inventoryBadge.classList.contains("agent-badge--insufficient")).toBe(true);
  });

  it("shows unavailable status with dark red styling", () => {
    const unavailable = [{ id: "a", name: "Pricing Agent", status: "UNAVAILABLE", summary: "Pricing service is offline.", findings: [] }];
    const { container } = render(<AgentCouncil agents={unavailable} />);
    const card = container.querySelector(".agent-card");
    const badge = card.querySelector(".agent-badge");
    expect(card.classList.contains("agent-card--unavailable")).toBe(true);
    expect(badge.classList.contains("agent-badge--unavailable")).toBe(true);
    expect(within(container).getByText("Unavailable")).toBeTruthy();
  });

  it("does not expose raw JSON as the main experience", () => {
    const { container } = renderCouncil();
    expect(container.querySelector("pre")).toBeNull();
    expect(container.textContent).not.toContain(JSON.stringify(agents));
    expect(container.textContent).not.toContain('"status"');
    expect(container.textContent).not.toContain('"findings"');
  });

  it("distinguishes AI recommendations from deterministic Policy Gate authorization", () => {
    const { container } = renderCouncil({ policyGateDecision: "APPROVED" });
    expect(within(container).getByText(/Policy Gate — deterministic authorization/i)).toBeTruthy();
    expect(within(container).getByText("Server-side policy evaluation. AI findings above are advisory and cannot authorize checkout.")).toBeTruthy();
    expect(within(container).getByText("Approved")).toBeTruthy();
    const gateSection = container.querySelector(".agent-council__gate");
    expect(gateSection).toBeTruthy();
    expect(gateSection.querySelectorAll(".agent-card")).toHaveLength(0);
  });

  it("runs the council callback via the run button", () => {
    const onRun = vi.fn();
    render(<AgentCouncil agents={agents} onRun={onRun} />);
    expect(onRun).not.toHaveBeenCalled();
    // find the run button by its accessible name
    const buttons = document.querySelectorAll("button");
    const runButton = Array.from(buttons).find((b) => b.textContent === "Run council");
    expect(runButton).toBeTruthy();
    runButton.click();
    expect(onRun).toHaveBeenCalledTimes(1);
  });
});
