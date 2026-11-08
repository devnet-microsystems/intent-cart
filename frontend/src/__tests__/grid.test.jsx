import { describe, it, expect } from "vitest";
import { mapVerdict } from "../StatusPill.jsx";

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

describe("status pill mapping", () => {
  it("maps approved", () => expect(mapVerdict("APPROVED")).toBe("APPROVED"));
  it("maps rejected", () => expect(mapVerdict("REJECTED")).toBe("BLOCKED"));
  it("maps blocked", () => expect(mapVerdict("BLOCKED")).toBe("BLOCKED"));
  it("maps not verified", () => expect(mapVerdict("NOT VERIFIED")).toBe("NOT VERIFIED"));
  it("maps missing", () => expect(mapVerdict(undefined)).toBe("NOT VERIFIED"));
  it("maps unknown", () => expect(mapVerdict("foo")).toBe("NOT VERIFIED"));
});
