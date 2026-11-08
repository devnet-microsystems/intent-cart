import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe("api client base URL", () => {
  it("uses localhost default in development when unset", async () => {
    vi.stubEnv("DEV", true);
    vi.stubEnv("VITE_API_BASE_URL", "");
    const mod = await import("../client.js");
    expect(mod.getApiBaseUrl()).toBe("http://127.0.0.1:8000");
    expect(mod.apiUrl("/api/health")).toBe("http://127.0.0.1:8000/api/health");
  });

  it("uses configured public API origin in production", async () => {
    vi.stubEnv("DEV", false);
    vi.stubEnv("VITE_API_BASE_URL", "https://api.example.com/");
    const mod = await import("../client.js");
    expect(mod.getApiBaseUrl()).toBe("https://api.example.com");
    expect(mod.apiUrl("api/intent")).toBe("https://api.example.com/api/intent");
  });

  it("fails explicitly in production when API base URL is missing and never contacts localhost", async () => {
    vi.stubEnv("DEV", false);
    vi.stubEnv("VITE_API_BASE_URL", "");
    const fetchMock = vi.fn();
    global.fetch = fetchMock;
    const mod = await import("../client.js");
    expect(() => mod.getApiBaseUrl()).toThrow(/VITE_API_BASE_URL/);
    await expect(mod.apiFetch("/api/executions")).rejects.toThrow(/VITE_API_BASE_URL/);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
