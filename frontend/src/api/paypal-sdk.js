// PayPal SDK singleton loader — guarantees window.paypal is loaded before React mounts it.
// Loads once at module import (module side effect), NOT inside any React lifecycle.
// Downstream components call ensurePaypalSdk() to await global window.paypal;

let paypalSdkPromise = null;

/**
 * Ensures the PayPal JS SDK is loaded and available on window.paypal.
 * Call this BEFORE mounting PayPal buttons.
 * Returns a Promise resolving to window.paypal.
 */
export function ensurePaypalSdk() {
  if (typeof window !== "undefined" && window.paypal) {
    return Promise.resolve(window.paypal);
  }

  if (paypalSdkPromise) {
    return paypalSdkPromise;
  }

  paypalSdkPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = `https://www.paypal.com/sdk/js?client-id=${import.meta.env.VITE_PAYPAL_CLIENT_ID || "sb"}&currency=EUR`;
    script.async = true;
    script.crossOrigin = "anonymous";
    script.onload = () => {
      if (window.paypal) {
        resolve(window.paypal);
      } else {
        reject(new Error("PayPal SDK loaded but not available on window.paypal"));
      }
    };
    script.onerror = (e) => {
      paypalSdkPromise = null; // allow retry
      reject(new Error(`Failed to load PayPal SDK: ${e.message || "unknown"}`));
    };
    document.body.appendChild(script);
  });

  return paypalSdkPromise;
}

/** Reset cache (for tests). */
export function resetPaypalSdkCache() {
  paypalSdkPromise = null;
}
