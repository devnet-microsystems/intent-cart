export function mapVerdict(value) {
  const v = String(value ?? "").toUpperCase().trim();
  if (v === "APPROVED") return "APPROVED";
  if (v === "REJECTED" || v === "BLOCKED") return "BLOCKED";
  if (v === "NEEDS_CONFIRMATION" || v === "NEEDS CONFIRMATION") return "NEEDS CONFIRMATION";
  if (v === "NOT VERIFIED" || v === "NOT_VERIFIED") return "NOT VERIFIED";
  return "NOT VERIFIED";
}

const styles = {
  APPROVED: { bg: "rgba(16,185,129,0.12)", fg: "#10b981", border: "rgba(16,185,129,0.35)" },
  BLOCKED: { bg: "rgba(239,68,68,0.12)", fg: "#ef4444", border: "rgba(239,68,68,0.35)" },
  "NEEDS CONFIRMATION": { bg: "rgba(245,158,11,0.12)", fg: "#f59e0b", border: "rgba(245,158,11,0.35)" },
  "NOT VERIFIED": { bg: "rgba(245,158,11,0.12)", fg: "#f59e0b", border: "rgba(245,158,11,0.35)" },
};

export default function StatusPill({ value }) {
  const status = mapVerdict(value);
  const s = styles[status];
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        padding: "3px 8px",
        borderRadius: "999px",
        fontSize: "11px",
        fontWeight: 700,
        letterSpacing: "0.04em",
        background: s.bg,
        color: s.fg,
        border: `1px solid ${s.border}`,
      }}
    >
      {status}
    </span>
  );
}
