// PayPal SDK singleton loader — guarantees window.paypal is loaded before React mounts it.
// Loads once at module import (module side effect), NOT inside any React lifecycle.
// Downstream components call ensurePaypalSdk() to await global window.paypal.

let paypalSdkPromise = null;

/**
 * Configured currency for the PayPal SDK button display.
 * Reads VITE_PAYPAL_CURRENCY at module load, defaults to "USD".
 * Can be overridden at runtime via setPaypalSdkCurrency() before the SDK loads.
 * Once the SDK has loaded, currency changes are ignored (window.paypal is reused).
 */
let paypalSdkCurrency = null;

function _defaultCurrency() {
  if (typeof import.meta !== "undefined" && import.meta.env) {
    const envCurrency = import.meta.env.VITE_PAYPAL_CURRENCY;
    if (envCurrency) return String(envCurrency);
  }
  return "USD";
}

paypalSdkCurrency = _defaultCurrency();

/**
 * Set the currency used when loading the PayPal JS SDK.
 * Must be called before ensurePaypalSdk() triggers the script load.
 * After the SDK has loaded, subsequent calls have no effect.
 */
export function setPaypalSdkCurrency(currency) {
  if (paypalSdkPromise) return; // SDK already loaded; do not change mid-flight
  paypalSdkCurrency = String(currency || paypalSdkCurrency);
}

/** Returns the currently configured SDK currency. */
export function getPaypalSdkCurrency() {
  return paypalSdkCurrency;
}

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
    script.src = `https://www.paypal.com/sdk/js?client-id=${import.meta.env.VITE_PAYPAL_CLIENT_ID || "sb"}&currency=${encodeURIComponent(paypalSdkCurrency)}`;
    script.async = true;
    script.crossOrigin = "anonymous";
    script.onload = () => {
      if (window.paypal) {
        resolve(window.paypal);
      } else {
        paypalSdkPromise = null;
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
  paypalSdkCurrency = _defaultCurrency();
}
