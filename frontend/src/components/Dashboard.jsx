import React, { useState } from "react";
import { Icons } from "./Icons";

export default function Dashboard({ claims, loading, onInspect, onNewClaim }) {
  const [searchQuery, setSearchQuery] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("ALL");
  const [statusFilter, setStatusFilter] = useState("ALL");

  // Filtering claims list
  const filteredClaims = claims.filter(claim => {
    const matchesSearch = claim.member_id.toLowerCase().includes(searchQuery.toLowerCase()) || 
                          claim.claim_id.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesCategory = categoryFilter === "ALL" || claim.category === categoryFilter;
    const matchesStatus = statusFilter === "ALL" || claim.status === statusFilter;
    return matchesSearch && matchesCategory && matchesStatus;
  });

  return (
    <div className="dashboard-view animate-fade-in">
      {/* Controls: Filter and Search */}
      <div className="glass-panel controls-panel">
        <div className="search-box">
          <Icons.Search />
          <input 
            type="text" 
            placeholder="Search by Member ID or Claim ID..." 
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>

        <div className="filters-row">
          <div className="select-container">
            <label>Category</label>
            <select value={categoryFilter} onChange={(e) => setCategoryFilter(e.target.value)}>
              <option value="ALL">All Categories</option>
              <option value="CONSULTATION">Consultation</option>
              <option value="DENTAL">Dental Care</option>
              <option value="VISION">Vision Care</option>
              <option value="ALTERNATIVE_MEDICINE">Alternative Medicine</option>
            </select>
          </div>

          <div className="select-container">
            <label>Status</label>
            <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="ALL">All Statuses</option>
              <option value="QUEUED">Queued</option>
              <option value="IN_PROGRESS">In Progress</option>
              <option value="COMPLETE">Complete</option>
              <option value="VERIFICATION_FAILED">Verification Failed</option>
            </select>
          </div>

          <button onClick={onNewClaim} className="btn-primary">
            <Icons.Plus /> New Claim
          </button>
        </div>
      </div>

      {/* Claims List Table */}
      <div className="glass-panel claims-table-panel">
        <h2>Historical Claims Trace List ({filteredClaims.length})</h2>
        {loading ? (
          <div className="table-loader">
            <Icons.Refresh /> Loading claims database...
          </div>
        ) : filteredClaims.length === 0 ? (
          <div className="empty-state">
            <Icons.FileText />
            <p>No claims match the active filters or search parameters.</p>
          </div>
        ) : (
          <div className="table-wrapper">
            <table className="claims-table">
              <thead>
                <tr>
                  <th>Claim ID</th>
                  <th>Member ID</th>
                  <th>Category</th>
                  <th>Submitted At</th>
                  <th>Claimed</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredClaims.map((claim) => (
                  <tr 
                    key={claim.claim_id} 
                    onClick={() => onInspect(claim.claim_id)}
                    className="clickable-row"
                  >
                    <td className="claim-id-cell">
                      <code>{claim.claim_id.substring(0, 8)}...</code>
                    </td>
                    <td className="font-semibold">{claim.member_id}</td>
                    <td>
                      <span className="category-tag">{claim.category.replace("_", " ")}</span>
                    </td>
                    <td className="text-secondary text-sm">
                      {new Date(claim.submitted_at).toLocaleString()}
                    </td>
                    <td className="font-mono font-semibold">₹{claim.claimed_amount.toFixed(2)}</td>
                    <td>
                      <span className={`status-pill ${claim.status.toLowerCase()}`}>
                        {claim.status.replace("_", " ")}
                      </span>
                    </td>
                    <td>
                      <button className="btn-table">
                        Inspect <Icons.ChevronRight />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
