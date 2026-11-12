import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

const MODULE_PATH = "../paypal-sdk.js";

async function importModuleFresh() {
  vi.resetModules();
  return await import(MODULE_PATH);
}

describe("paypal-sdk currency configuration", () => {
  beforeEach(() => {
    vi.unstubAllEnvs();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("defaults to USD when VITE_PAYPAL_CURRENCY is unset", async () => {
    const mod = await importModuleFresh();
    expect(mod.getPaypalSdkCurrency()).toBe("USD");
  });

  it("uses VITE_PAYPAL_CURRENCY=USD", async () => {
    vi.stubEnv("VITE_PAYPAL_CURRENCY", "USD");
    const mod = await importModuleFresh();
    expect(mod.getPaypalSdkCurrency()).toBe("USD");
  });

  it("uses VITE_PAYPAL_CURRENCY=EUR", async () => {
    vi.stubEnv("VITE_PAYPAL_CURRENCY", "EUR");
    const mod = await importModuleFresh();
    expect(mod.getPaypalSdkCurrency()).toBe("EUR");
  });

  it("setPaypalSdkCurrency overrides before SDK load", async () => {
    const mod = await importModuleFresh();
    mod.setPaypalSdkCurrency("EUR");
    expect(mod.getPaypalSdkCurrency()).toBe("EUR");
    mod.resetPaypalSdkCache();
  });

  it("resetPaypalSdkCache restores default from env", async () => {
    vi.stubEnv("VITE_PAYPAL_CURRENCY", "USD");
    const mod = await importModuleFresh();
    mod.setPaypalSdkCurrency("EUR");
    expect(mod.getPaypalSdkCurrency()).toBe("EUR");
    mod.resetPaypalSdkCache();
    expect(mod.getPaypalSdkCurrency()).toBe("USD");
    mod.resetPaypalSdkCache();
  });
});
