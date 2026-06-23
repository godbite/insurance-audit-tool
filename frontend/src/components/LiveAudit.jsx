import React, { useState } from "react";
import { Icons } from "./Icons";

const STAGES_CONFIG = [
  { id: "DOC_CLASSIFICATION", name: "Doc Classification", desc: "Identify document types via local text/visual signals" },
  { id: "DOC_VERIFICATION", name: "Doc Verification", desc: "Check presence of critical files and metadata validation" },
  { id: "EXTRACTION", name: "Data Extraction", desc: "Local PaddleOCR parsing + Groq Llama-3 extraction engine" },
  { id: "CONSISTENCY_CHECK", name: "Consistency Check", desc: "Validate cross-document fields (e.g. Patient Name matching)" },
  { id: "DECISIONING", name: "Rules & AI Decisioning", desc: "Compute policy mathematics & parallel LLM verification" }
];

export default function LiveAudit({ claimId, wsStages = {}, wsCompleteInfo, claimDetails, traceDetails }) {
  const [isTraceExpanded, setIsTraceExpanded] = useState(false);

  // Helper to get stage status from either live wsStages or historical traceDetails
  const getStageInfo = (stageId) => {
    if (wsStages && wsStages[stageId] && wsStages[stageId].status !== "PENDING") {
      return wsStages[stageId];
    }
    if (traceDetails && traceDetails.stages) {
      const historicalStage = traceDetails.stages.find(s => s.name === stageId);
      if (historicalStage) {
        let stageDetail = historicalStage.error || 
                          historicalStage.outputs_summary?.message || 
                          historicalStage.outputs_summary?.detail || 
                          null;
        
        if (!stageDetail && historicalStage.outputs_summary?.mismatches) {
          const mismatches = historicalStage.outputs_summary.mismatches;
          if (Array.isArray(mismatches) && mismatches.length > 0) {
            stageDetail = mismatches.join(", ");
          } else if (typeof mismatches === "string") {
            stageDetail = mismatches;
          }
        }

        return {
          status: historicalStage.status === "PASSED" ? "COMPLETE" : historicalStage.status,
          detail: stageDetail,
          ts: historicalStage.completed_at || historicalStage.started_at
        };
      }
    }
    return wsStages?.[stageId] || { status: "PENDING", detail: null, ts: null };
  };

  // Helper to get checks to display (falls back to stage checks if no final decision exists)
  const getDisplayChecks = () => {
    if (claimDetails?.decision?.checks && claimDetails.decision.checks.length > 0) {
      return claimDetails.decision.checks;
    }
    if (traceDetails && traceDetails.stages) {
      const harvestedChecks = [];
      traceDetails.stages.forEach(stage => {
        if (stage.checks && Array.isArray(stage.checks)) {
          stage.checks.forEach(check => {
            if (check.passed === false) {
              harvestedChecks.push(check);
            }
          });
        }
      });
      return harvestedChecks;
    }
    return [];
  };

  const displayChecks = getDisplayChecks();

  return (
    <div className="audit-view animate-fade-in">
      <div className="layout-grid">
        
        {/* Left Column: Stage Tracker */}
        <div className="column-left">
          <div className="glass-panel pipeline-panel">
            <h2>Multi-Agent Pipeline Live Tracking</h2>
            <p className="text-secondary text-sm">Active audit process logs from backend Celery state-machine</p>

            <div className="pipeline-flow">
              {STAGES_CONFIG.map((stage, idx) => {
                const liveStage = getStageInfo(stage.id);
                const statusClass = liveStage?.status?.toLowerCase() || "pending";
                
                return (
                  <div key={stage.id} className={`pipeline-step ${statusClass}`}>
                    {idx > 0 && <div className="connector-line"></div>}
                    <div className="step-marker">
                      {liveStage?.status === "COMPLETE" || liveStage?.status === "PASSED" ? (
                        <Icons.Check />
                      ) : liveStage?.status === "FAILED" ? (
                        <Icons.XCircle />
                      ) : liveStage?.status === "DEGRADED" ? (
                        <Icons.Alert />
                      ) : liveStage?.status === "IN_PROGRESS" ? (
                        <span className="spinner-mini"></span>
                      ) : (
                        <span className="dot-mini"></span>
                      )}
                    </div>
                    <div className="step-content">
                      <h3>{stage.name}</h3>
                      <p className="text-secondary">{stage.desc}</p>
                      
                      {/* Render stage errors/degradation details */}
                      {liveStage?.detail && (
                        <div className={`step-detail ${liveStage.status.toLowerCase()}`}>
                          <span className="detail-icon">⚠️</span>
                          <span className="detail-text">{liveStage.detail}</span>
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* Right Column: Decisions, breakdown, trace checks */}
        <div className="column-right">
          
          {/* Visual Overall Decision banner */}
          <div className="glass-panel summary-panel">
            <h2>Decision Resolution</h2>
            {claimDetails ? (
              <div className="decision-summary-content">
                {/* Decision Badges */}
                <div className="decision-header-row">
                  <span className={`decision-badge ${claimDetails.decision?.decision?.toLowerCase() || claimDetails.status.toLowerCase()}`}>
                    {(claimDetails.decision?.decision || claimDetails.status).replace("_", " ")}
                  </span>
                  
                  <div className="amounts-comparison">
                    <div className="amount-metric">
                      <span className="text-secondary label-xs">CLAIMED</span>
                      <span className="metric-val">
                        ₹{Number(claimDetails.claimed_amount ?? claimDetails.decision?.claimed_amount ?? claimDetails.trace?.claimed_amount ?? 0).toFixed(2)}
                      </span>
                    </div>
                    <div className="metric-divider">/</div>
                    <div className="amount-metric">
                      <span className="text-secondary label-xs">APPROVED</span>
                      <span className="metric-val text-emerald">
                        ₹{Number(claimDetails.decision?.approved_amount ?? 0).toFixed(2)}
                      </span>
                    </div>
                  </div>
                </div>

                {/* Display warning or patient mismatch */}
                {claimDetails.status === "VERIFICATION_FAILED" && claimDetails.verification_error && (
                  <div className="alert-banner error">
                    <div className="alert-header">
                      <Icons.XCircle />
                      <h4>Verification Mismatch: {claimDetails.verification_error.code}</h4>
                    </div>
                    <p>{claimDetails.verification_error.message}</p>
                  </div>
                )}

                {/* Decision mismatch alerts */}
                {claimDetails.decision?.reasons?.includes("DECISION_MISMATCH") && (
                  <div className="alert-banner warning">
                    <div className="alert-header">
                      <Icons.Alert />
                      <h4>Rules & AI Engine Mismatch Detected</h4>
                    </div>
                    <p>
                      {claimDetails.decision?.degradation_notes?.find(note => note.includes("mismatch")) || 
                       "Rules Engine and AI decision model generated conflicting resolutions. Claim routed to Manual Review."}
                    </p>
                  </div>
                )}

                {/* Reasons block */}
                {claimDetails.decision?.reasons?.length > 0 && (
                  <div className="reasons-block">
                    <h5>Decision Exclusions / Rules Tripped:</h5>
                    <ul className="reasons-list">
                      {claimDetails.decision?.reasons?.map((reason, idx) => (
                        <li key={idx} className="reason-item">{reason.replace("_", " ")}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            ) : (
              <div className="summary-loader">
                <span className="spinner"></span>
                <p>Pipeline executing. Waiting for complete event...</p>
              </div>
            )}
          </div>

          {/* Line Item Breakdown list */}
          {claimDetails?.decision?.line_item_breakdown && (
            <div className="glass-panel breakdown-panel animate-fade-in">
              <h2>Line Item Audit Breakdown</h2>
              <div className="table-wrapper">
                <table className="breakdown-table">
                  <thead>
                    <tr>
                      <th>Description</th>
                      <th>Claimed</th>
                      <th>Approved</th>
                      <th>Resolution</th>
                      <th>Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {claimDetails.decision?.line_item_breakdown?.map((item, idx) => (
                      <tr key={idx}>
                        <td className="font-semibold">{item.description || "Unlabeled item"}</td>
                        <td className="font-mono">₹{Number(item.claimed_amount ?? 0).toFixed(2)}</td>
                        <td className="font-mono text-emerald">₹{Number(item.approved_amount ?? 0).toFixed(2)}</td>
                        <td>
                          <span className={`status-badge-mini ${item.status.toLowerCase()}`}>
                            {item.status}
                          </span>
                        </td>
                        <td className="text-secondary text-sm">{item.reason || "Eligible under terms"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Policy Rules Checks */}
          {displayChecks.length > 0 && (
            <div className="glass-panel checks-panel animate-fade-in">
              <h2>Policy Rules Execution Checks</h2>
              <div className="checks-grid">
                {displayChecks.map((check, idx) => (
                  <div key={idx} className={`check-card ${check.passed ? "passed" : "failed"}`}>
                    <div className="check-card-header">
                      <span className="check-indicator"></span>
                      <h4>{check.check_name}</h4>
                      <span className="badge-xs">{check.policy_reference}</span>
                    </div>
                    <p>{check.detail}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* JSON Trace collapse tree viewer */}
          {traceDetails && (
            <div className="glass-panel trace-panel animate-fade-in">
              <div className="trace-header" onClick={() => setIsTraceExpanded(!isTraceExpanded)}>
                <h2>Claim Observability Trace Audit Logs</h2>
                <button className="btn-text">
                  {isTraceExpanded ? "Collapse" : "Expand"}
                </button>
              </div>
              {isTraceExpanded && (
                <div className="trace-content animate-slide-down">
                  <pre className="json-pre">
                    <code>{JSON.stringify(traceDetails, null, 2)}</code>
                  </pre>
                </div>
              )}
            </div>
          )}

        </div>
      </div>
    </div>
  );
}
