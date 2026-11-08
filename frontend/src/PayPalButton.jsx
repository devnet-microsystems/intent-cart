import { useEffect, useRef, useState } from "react";

const CLIENT_ID = import.meta.env.VITE_PAYPAL_CLIENT_ID || "sb";

export default function PayPalButton({ sessionId, executionId, offerId, decision, locked, onCaptured, onError, onCancel, onReadyChange, onLoadingChange, onSdkError, onSdkLoaded }) {
  const containerRef = useRef(null);
  const paymentIdRef = useRef(null);
  const creatingOrderRef = useRef(false);
  const [ready, setReady] = useState(false);
  const [loading, setLoading] = useState(false);
  const [capture, setCapture] = useState(null);
  const approved = locked === true && decision === "APPROVED" && Boolean(sessionId) && Boolean(executionId) && Boolean(offerId);

  useEffect(() => {
    if (!approved) return;
    if (window.paypal) { setReady(true); onSdkLoaded?.(true); onReadyChange?.(true); return; }
    const script = document.createElement("script");
    script.src = `https://www.paypal.com/sdk/js?client-id=${CLIENT_ID}&currency=EUR`;
    script.async = true;
    script.onload = () => { setReady(true); onSdkLoaded?.(true); onReadyChange?.(true); };
    script.onerror = () => { onError?.("Failed to load PayPal SDK"); onSdkError?.("Failed to load PayPal SDK"); };
    document.body.appendChild(script);
    return () => { script.remove(); };
  }, [approved, onError]);

  useEffect(() => {
    if (!ready || !containerRef.current || !window.paypal) return;
    containerRef.current.innerHTML = "";
    window.paypal.Buttons({
      createOrder: async () => {
        // Missing-identifier guard: never initiate checkout without the
        // persisted server-side identifiers.
        if (!sessionId || !executionId || !offerId) {
          onError?.("Checkout non disponibile: identificativi sessione, esecuzione o offerta mancanti.");
          return;
        }
        // Synchronous guard against duplicate checkout requests while one
        // is already in flight (a ref is read synchronously, so it works
        // even when createOrder is invoked repeatedly without re-render).
        if (creatingOrderRef.current) {
          return;
        }
        creatingOrderRef.current = true;
        setLoading(true);
        onLoadingChange?.(true);
        try {
          const res = await fetch("http://127.0.0.1:8000/api/orders/checkout", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session_id: sessionId, execution_id: executionId, offer_id: offerId }),
          });
          const data = await res.json();
          if (!res.ok) { onError?.(data.detail || "Checkout failed"); throw new Error(data.detail || "Checkout failed"); }
          if (!data.approve_url || typeof data.approve_url !== "string") {
            onError?.(data.detail || "PayPal approval link unavailable");
            throw new Error("PayPal approval link unavailable");
          }
          paymentIdRef.current = data.payment_id;
          return data.order_id;
        } finally {
        creatingOrderRef.current = false;
        setLoading(false);
        onLoadingChange?.(false);
        }
      },
      onApprove: async (data) => {
        const payRes = await fetch("http://127.0.0.1:8000/api/orders/capture", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ payment_id: paymentIdRef.current }),
        });
        // payment_id must be stored from checkout response; fallback fetch not available
        const cap = await payRes.json();
        if (!payRes.ok) { onError?.(cap.detail || "Capture failed"); return; }
        setCapture(cap);
        onCaptured?.(cap);
        setLoading(false);
      },
      onCancel: () => { onCancel?.(); setLoading(false); },
      onError: (err) => { onError?.(String(err)); setLoading(false); },
    }).render(containerRef.current);
  }, [ready, sessionId, executionId, offerId, approved, onCaptured, onError, onCancel]);

  if (!approved) return <div>PayPal checkout disabled (blocked)</div>;
  if (capture) return <div>PAYMENT_CAPTURED {capture.capture_id} {capture.amount} {capture.currency}</div>;
  return <div><div ref={containerRef} />{loading && <span>Loading...</span>}</div>;
}
