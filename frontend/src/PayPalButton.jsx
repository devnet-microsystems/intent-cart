import { useEffect, useRef, useState } from "react";
import { ensurePaypalSdk } from "./api/paypal-sdk.js";

export default function PayPalButton({ sessionId, executionId, offerId, decision, locked, onReadyChange, onLoadingChange, onSdkError, onSdkLoaded, onError }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [capture, setCapture] = useState(null);
  const [ready, setReady] = useState(false);
  const [mounted, setMounted] = useState(false);
  const containerRef = useRef(null);
  const mountedRef = useRef(false);
  const mostRecentApproval = useRef(null);
  const createOrderInFlight = useRef(false); // prevents duplicate createOrder executions across StrictMode double-render

  const approved = locked === true && decision === "APPROVED" && Boolean(sessionId) && Boolean(executionId) && Boolean(offerId);

  // Notify parent of state changes (React StrictMode-safe)
  useEffect(() => { onReadyChange?.(ready); }, [ready]);
  useEffect(() => { onLoadingChange?.(loading); }, [loading]);
  useEffect(() => { onSdkError?.(error); }, [error]);
  useEffect(() => { onSdkLoaded?.(ready && !!window.paypal); }, [ready]);

  useEffect(() => {
    if (!approved || !containerRef.current || mountedRef.current) return;
    mountedRef.current = true;

    const init = async () => {
      try {
        setLoading(true);
        setError(null);
        setReady(false);

        await ensurePaypalSdk();
        setReady(true);
        onSdkLoaded?.(true);

        if (containerRef.current && containerRef.current.childNodes.length === 0) {
          window.paypal.Buttons({
            createOrder: async () => {
              if (createOrderInFlight.current) {
                // Already in progress — don't start another one for the same outcome
                return mostRecentApproval.current?.order_id || "";
              }
              createOrderInFlight.current = true;
              try {
              const res = await fetch("/api/orders/checkout", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ session_id: sessionId, execution_id: executionId, offer_id: offerId }),
              });
              if (!res.ok) {
                // Fetch failed — parameterized validation happens after parse anyway
                const text = await res.text();
                const body = text ? (() => { try { return JSON.parse(text); } catch { return "-"; } })() : "-";
                throw new Error(`/api/orders/checkout: HTTP ${res.status} body=${body}`);
              }
              const data = await res.json();
                if (!data.order_id || typeof data.order_id !== "string") {
                  onError?.(data.detail || "Checkout order unavailable");
                  throw new Error("Checkout order unavailable");
                }
                if (!data.payment_id || typeof data.payment_id !== "string") {
                  onError?.(data.detail || "Checkout payment unavailable");
                  throw new Error("Checkout payment unavailable");
                }
                mostRecentApproval.current = data;
                return data.order_id;
              } finally {
                createOrderInFlight.current = false;
              }
            },
            onApprove: async () => {
              if (!mostRecentApproval.current?.payment_id) {
                throw new Error("No payment_id for capture");
              }
              const res = await fetch("/api/orders/capture", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ payment_id: mostRecentApproval.current.payment_id }),
              });
              const cap = await res.json();
              if (!res.ok) {
                onError?.(cap.detail || "Capture failed");
                throw new Error("Capture failed");
              }
              setCapture(cap);
            },
            onError: (err) => {
              console.error("PayPal checkout error:", err);
              const msg = err.message || "PayPal checkout error";
              setError(msg);
              onError?.(msg);
            },
            onCancel: () => {
              console.log("PayPal checkout cancelled");
            },
          }).render(containerRef.current);
          setMounted(true);
        }
        setLoading(false);
      } catch (err) {
        console.error("PayPalButton init error:", err);
        setError(err.message || "Failed to initialize PayPal");
        onSdkError?.(err.message);
        setLoading(false);
        onSdkLoaded?.(false);
        mountedRef.current = false;
      }
    };

    init();
  }, [approved]);

  if (capture) {
    return (
      <div style={{ padding: 12, background: "#22c55e22", borderRadius: 8 }}>
        <strong>PAYMENT_CAPTURED</strong> {capture.capture_id} {capture.amount} {capture.currency}
      </div>
    );
  }

  if (!approved) {
    return (
      <div style={{ padding: 12, background: "#ef444422", borderRadius: 8 }}>
        PayPal checkout disabled (blocked)
      </div>
    );
  }

  // Original proven render path (simplified)
  return (
    <div style={{ marginTop: 12 }}>
      {loading && <span>Loading…</span>}
      {error && <div style={{ color: "red" }}>{error}</div>}
      <div ref={containerRef} />
    </div>
  );
}
