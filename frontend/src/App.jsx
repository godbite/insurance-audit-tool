import { useState, useEffect, useRef } from "react";
import { BACKEND_URL, WS_URL } from "./config";
import { Icons } from "./components/Icons";
import Dashboard from "./components/Dashboard";
import LiveAudit from "./components/LiveAudit";
import ClaimSubmitModal from "./components/ClaimSubmitModal";
import "./App.css";

const STAGES_CONFIG = [
  { id: "DOC_CLASSIFICATION", name: "Doc Classification" },
  { id: "DOC_VERIFICATION", name: "Doc Verification" },
  { id: "EXTRACTION", name: "Data Extraction" },
  { id: "CONSISTENCY_CHECK", name: "Consistency Check" },
  { id: "DECISIONING", name: "Rules & AI Decisioning" }
];

function App() {
  // Navigation / Tabs
  const [activeTab, setActiveTab] = useState("dashboard"); // "dashboard" | "audit"

  // Dashboard state
  const [claims, setClaims] = useState([]);
  const [loading, setLoading] = useState(true);

  // Backend Health
  const [healthStatus, setHealthStatus] = useState("checking"); // "checking" | "ok" | "error"

  // Active/Selected Claim
  const [selectedClaimId, setSelectedClaimId] = useState(null);
  const [claimDetails, setClaimDetails] = useState(null);
  const [traceDetails, setTraceDetails] = useState(null);

  // WebSocket live pipeline
  const [wsStages, setWsStages] = useState({});
  const [wsCompleteInfo, setWsCompleteInfo] = useState(null);
  const wsRef = useRef(null);

  // Submit Modal
  const [showModal, setShowModal] = useState(false);

  // Fetch claims list
  const fetchClaims = async () => {
    try {
      setLoading(true);
      const res = await fetch(`${BACKEND_URL}/claims`);
      if (res.ok) {
        const data = await res.json();
        setClaims(data);
      }
    } catch (err) {
      console.error("Failed to load claims list", err);
    } finally {
      setLoading(false);
    }
  };

  // Check Backend Health
  const checkHealth = async () => {
    try {
      const res = await fetch(`${BACKEND_URL}/health`);
      if (res.ok) {
        setHealthStatus("ok");
      } else {
        setHealthStatus("error");
      }
    } catch {
      setHealthStatus("error");
    }
  };

  // Load claims on mount, set health check interval, & listen to hash changes for routing
  useEffect(() => {
    fetchClaims();
    checkHealth();
    const interval = setInterval(checkHealth, 15000);

    const handleHashChange = () => {
      const hash = window.location.hash;
      if (hash.startsWith("#/claims/")) {
        const claimId = hash.replace("#/claims/", "");
        setSelectedClaimId((prevClaimId) => {
          if (prevClaimId !== claimId) {
            setWsStages({});
            setWsCompleteInfo(null);
            loadClaimDetails(claimId);
          }
          return claimId;
        });
        setActiveTab("audit");
      } else {
        setActiveTab("dashboard");
      }
    };

    window.addEventListener("hashchange", handleHashChange);
    handleHashChange(); // Run on mount

    return () => {
      clearInterval(interval);
      window.removeEventListener("hashchange", handleHashChange);
    };
  }, []);


  // Fetch full details & trace for a completed/failed claim
  const loadClaimDetails = async (claimId) => {
    try {
      // 1. Get current claim state
      const claimRes = await fetch(`${BACKEND_URL}/claims/${claimId}`);
      if (claimRes.ok) {
        const data = await claimRes.json();
        setClaimDetails(data);

        // If the claim is not in a terminal state, start live tracking via WebSocket
        const terminalStates = ["COMPLETE", "FAILED", "VERIFICATION_FAILED"];
        if (!terminalStates.includes(data.status)) {
          startLiveTracking(claimId);
        }
      }
      // 2. Get trace logs
      const traceRes = await fetch(`${BACKEND_URL}/claims/${claimId}/trace`);
      if (traceRes.ok) {
        const data = await traceRes.json();
        setTraceDetails(data);
      }
    } catch (err) {
      console.error("Failed to load claim details", err);
    }
  };

  // WebSocket Live pipeline tracking setup
  const startLiveTracking = (claimId) => {
    // Reset pipeline visualizer
    const initialStages = {};
    STAGES_CONFIG.forEach(s => {
      initialStages[s.id] = { status: "PENDING", ts: null };
    });
    setWsStages(initialStages);
    setWsCompleteInfo(null);
    setTraceDetails(null);
    setSelectedClaimId(claimId);
    setActiveTab("audit");

    // Close existing ws if any
    if (wsRef.current) {
      wsRef.current.close();
    }

    const ws = new WebSocket(`${WS_URL}/ws/claims/${claimId}`);
    wsRef.current = ws;

    ws.onopen = () => {
      console.log(`WebSocket connected for claim: ${claimId}`);
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        console.log("WS Pipeline Event:", msg);

        if (msg.stage) {
          const currentStage = msg.stage;
          const status = msg.status;

          if (currentStage === "COMPLETE") {
            setWsCompleteInfo(msg);
            // Wait slightly for database transaction to persist and then load complete trace/details
            setTimeout(() => {
              loadClaimDetails(claimId);
              fetchClaims();
            }, 1000);
          } else {
            // Update individual stage status
            setWsStages(prev => ({
              ...prev,
              [currentStage]: {
                status: status,
                detail: msg.detail || msg.message || null,
                code: msg.code || null,
                ts: msg.ts
              }
            }));
          }
        }
      } catch (err) {
        console.error("Failed parsing WS message", err);
      }
    };

    ws.onclose = () => {
      console.log("WebSocket connection closed");
    };

    ws.onerror = (err) => {
      console.error("WebSocket error:", err);
    };
  };

  return (
    <div className="app-container">
      {/* Dynamic Background Effects */}
      <div className="bg-glow purple"></div>
      <div className="bg-glow blue"></div>

      {/* Aesthetic Header */}
      <header className="glass-panel main-header">
        <div className="logo-container">
          <div className="logo-pulse"></div>
          <h1>Plum Claims Audit Portal</h1>
          <span className="badge version-badge">AI Multi-Agent v2.0</span>
        </div>

        <div className="header-actions">
          {/* Health Status Indicator */}
          <div className={`health-indicator ${healthStatus}`}>
            <span className="indicator-dot"></span>
            <span className="indicator-text">
              {healthStatus === "checking" && "Checking connection..."}
              {healthStatus === "ok" && "Backend Connected"}
              {healthStatus === "error" && "Backend Offline"}
            </span>
          </div>

          <button onClick={fetchClaims} className="btn-secondary" title="Sync dashboard data">
            <Icons.Refresh /> Refresh
          </button>
        </div>
      </header>

      {/* Main Tab Navigation */}
      <nav className="tab-navigation">
        <button
          onClick={() => { window.location.hash = "#/dashboard"; }}
          className={`tab-btn ${activeTab === "dashboard" ? "active" : ""}`}
        >
          Claims Dashboard
        </button>
        {selectedClaimId && (
          <button
            onClick={() => { window.location.hash = `#/claims/${selectedClaimId}`; }}
            className={`tab-btn ${activeTab === "audit" ? "active" : ""}`}
          >
            Claim Live Audit
          </button>
        )}
      </nav>

      {/* Content views */}
      <main className="main-content">
        {activeTab === "dashboard" ? (
          <Dashboard
            claims={claims}
            loading={loading}
            onInspect={(claimId) => {
              window.location.hash = `#/claims/${claimId}`;
            }}
            onNewClaim={() => setShowModal(true)}
          />
        ) : (
          <LiveAudit
            claimId={selectedClaimId}
            wsStages={wsStages}
            wsCompleteInfo={wsCompleteInfo}
            claimDetails={claimDetails}
            traceDetails={traceDetails}
          />
        )}
      </main>

      {/* Claim Submission Dialog / Modal */}
      <ClaimSubmitModal
        isOpen={showModal}
        onClose={() => setShowModal(false)}
        onSubmitSuccess={(claimId) => {
          window.location.hash = `#/claims/${claimId}`;
        }}
        backendUrl={BACKEND_URL}
      />
    </div>
  );
}

export default App;
