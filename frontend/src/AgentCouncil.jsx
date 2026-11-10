import "./AgentCouncil.css";

const STATUSES = {
  NO_CONCERN_FOUND: "NO_CONCERN_FOUND",
  CONCERN: "CONCERN",
  INSUFFICIENT_DATA: "INSUFFICIENT_DATA",
  UNAVAILABLE: "UNAVAILABLE",
  RUNNING: "RUNNING",
};

function normalizeStatus(status) {
  return String(status ?? "").toUpperCase().trim();
}

function statusLabel(status) {
  switch (normalizeStatus(status)) {
    case STATUSES.NO_CONCERN_FOUND:
      return "No concern found";
    case STATUSES.CONCERN:
      return "Concern";
    case STATUSES.INSUFFICIENT_DATA:
      return "Insufficient data";
    case STATUSES.UNAVAILABLE:
      return "Unavailable";
    case STATUSES.RUNNING:
      return "Running";
    default:
      return "Unknown";
  }
}

function cardClass(status) {
  switch (normalizeStatus(status)) {
    case STATUSES.NO_CONCERN_FOUND:
      return "agent-card--ok";
    case STATUSES.CONCERN:
      return "agent-card--concern";
    case STATUSES.INSUFFICIENT_DATA:
      return "agent-card--insufficient";
    case STATUSES.UNAVAILABLE:
      return "agent-card--unavailable";
    case STATUSES.RUNNING:
      return "agent-card--running";
    default:
      return "agent-card--running";
  }
}

function badgeClass(status) {
  const key = normalizeStatus(status);
  switch (key) {
    case STATUSES.NO_CONCERN_FOUND:
      return "agent-badge--ok";
    case STATUSES.CONCERN:
      return "agent-badge--concern";
    case STATUSES.INSUFFICIENT_DATA:
      return "agent-badge--insufficient";
    case STATUSES.UNAVAILABLE:
      return "agent-badge--unavailable";
    default:
      return "agent-badge--running";
  }
}

function severityClass(severity) {
  switch (String(severity ?? "").toLowerCase()) {
    case "critical":
    case "high":
      return "agent-severity--high";
    case "medium":
      return "agent-severity--medium";
    case "low":
      return "agent-severity--low";
    case "info":
      return "agent-severity--info";
    default:
      return "agent-severity--medium";
  }
}

function gateLabel(decision) {
  switch (String(decision ?? "").toUpperCase()) {
    case "APPROVED":
      return "Approved";
    case "REJECTED":
      return "Rejected";
    case "NEEDS_CONFIRMATION":
      return "Needs confirmation";
    default:
      return "Not determined";
  }
}

function gateClass(decision) {
  switch (String(decision ?? "").toUpperCase()) {
    case "APPROVED":
      return "agent-gate--approved";
    case "REJECTED":
      return "agent-gate--rejected";
    case "NEEDS_CONFIRMATION":
      return "agent-gate--confirm";
    default:
      return "agent-gate--undetermined";
  }
}

function AgentCard({ agent, running }) {
  const status = running ? STATUSES.RUNNING : agent.status;
  const findings = agent.findings || [];

  return (
    <article className={`agent-card ${cardClass(status)}`}>
      <div className="agent-card__head">
        <span className="agent-card__name">{agent.name}</span>
        <span className={`agent-badge ${badgeClass(status)}`}>{statusLabel(status)}</span>
      </div>
      <p className="agent-card__summary">{running ? "Evaluating evidence…" : agent.summary || "No summary available."}</p>
      <div className="agent-card__findings">
        {running ? (
          <span className="agent-spinner" aria-hidden="true" />
        ) : findings.length === 0 ? (
          <p className="agent-card__empty">No findings recorded.</p>
        ) : (
          findings.map((finding, index) => (
            <div key={index} className="agent-finding">
              <span className={`agent-severity ${severityClass(finding.severity)}`}>
                {String(finding.severity ?? "").charAt(0).toUpperCase() + String(finding.severity ?? "").slice(1)}
              </span>
              <span className="agent-finding__message">{finding.message}</span>
            </div>
          ))
        )}
      </div>
    </article>
  );
}

export default function AgentCouncil({
  executionId,
  offerId,
  policyGateDecision,
  agents = [],
  isLoading = false,
  onRun,
}) {
  const decision = gateLabel(policyGateDecision);
  const decisionCls = gateClass(policyGateDecision);

  return (
    <section className="agent-council" aria-label="Agent Council">
      <header className="agent-council__header">
        <div className="agent-council__titles">
          <h2 className="agent-council__title">Agent Council</h2>
          <p className="agent-council__subtitle">AI agent review of purchase evidence</p>
        </div>
        {(executionId || offerId) && (
          <div className="agent-council__context">
            {executionId && <span className="agent-context__chip">Execution {executionId}</span>}
            {offerId && <span className="agent-context__chip">Offer {offerId}</span>}
          </div>
        )}
      </header>

      <div className="agent-council__warning" role="status" aria-live="polite">
        <strong>AI agent findings are advisory.</strong> Only the deterministic Policy Gate can authorize checkout.
      </div>

      <div className="agent-council__toolbar">
        <button className="agent-button" disabled={isLoading} onClick={onRun} aria-busy={isLoading}>
          {isLoading ? "Running…" : "Run council"}
        </button>
        <span className="agent-council__divider" aria-hidden="true" />
        <span className="agent-council__gate-kind">Policy Gate &middot; deterministic authorization</span>
      </div>

      <div className="agent-council__cards" aria-busy={isLoading}>
        {agents.map((agent) => (
          <AgentCard key={agent.id || agent.name} agent={agent} running={isLoading} />
        ))}
      </div>

      <section className="agent-council__gate">
        <h3 className="agent-council__gate-title">Policy Gate &mdash; deterministic authorization</h3>
        <p className="agent-council__gate-note">
          Server-side policy evaluation. AI findings above are advisory and cannot authorize checkout.
        </p>
        <div className="agent-council__decision">
          <span className={`agent-decision-badge ${decisionCls}`}>{decision}</span>
        </div>
      </section>
    </section>
  );
}
