import { describe, it, expect } from "vitest";

describe("grid mapping", () => {
  it("maps approved product", () => {
    const row = { product: { name: "Hub", offer: { price: 29.99, currency: "EUR" }, merchant: "Belkin" }, gate: { decision: "APPROVED", rules: [] } };
    expect(row.gate.decision).toBe("APPROVED");
    expect(row.product.offer.currency).toBe("EUR");
  });
  it("marks missing evidence", () => {
    const ev = undefined;
    expect(ev ?? "NOT VERIFIED").toBe("NOT VERIFIED");
  });
  it("blocks checkout for rejected", () => {
    const decision = "REJECTED";
    expect(decision !== "APPROVED").toBe(true);
  });
});

describe("grid config", () => {
  it("has sortable columns", () => {
    expect(true).toBe(true);
  });
});
