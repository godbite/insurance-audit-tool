import React, { useState, useRef } from "react";
import { Icons } from "./Icons";

export default function ClaimSubmitModal({ isOpen, onClose, onSubmitSuccess, backendUrl }) {
  const [submitLoading, setSubmitLoading] = useState(false);
  const [formFields, setFormFields] = useState({
    memberId: "EMP005",
    policyId: "PLUM_GHI_2024",
    claimCategory: "ALTERNATIVE_MEDICINE",
    treatmentDate: "2024-10-28",
    claimedAmount: "4000.00",
    hospitalName: "Ayur Wellness Centre",
    preAuthObtained: false
  });
  const [uploadedFiles, setUploadedFiles] = useState([]);
  const fileInputRef = useRef(null);

  if (!isOpen) return null;

  // Drag and Drop handlers
  const handleDragOver = (e) => {
    e.preventDefault();
  };

  const handleDrop = (e) => {
    e.preventDefault();
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      const filesArray = Array.from(e.dataTransfer.files);
      setUploadedFiles(prev => [...prev, ...filesArray]);
    }
  };

  const handleFileChange = (e) => {
    if (e.target.files && e.target.files.length > 0) {
      const filesArray = Array.from(e.target.files);
      setUploadedFiles(prev => [...prev, ...filesArray]);
    }
  };

  const removeFile = (idx) => {
    setUploadedFiles(prev => prev.filter((_, i) => i !== idx));
  };

  // Preset triggers
  const autofillPreset = (type) => {
    if (type === "vikram") {
      setFormFields({
        memberId: "EMP005",
        policyId: "PLUM_GHI_2024",
        claimCategory: "ALTERNATIVE_MEDICINE",
        treatmentDate: "2024-10-28",
        claimedAmount: "4000.00",
        hospitalName: "Ayur Wellness Centre",
        preAuthObtained: false
      });
    } else if (type === "mismatch") {
      setFormFields({
        memberId: "EMP004",
        policyId: "PLUM_GHI_2024",
        claimCategory: "CONSULTATION",
        treatmentDate: "2024-10-25",
        claimedAmount: "4000.00",
        hospitalName: "Apollo Clinics",
        preAuthObtained: false
      });
    }
  };

  const handleFormSubmit = async (e) => {
    e.preventDefault();
    if (uploadedFiles.length === 0) {
      alert("Please upload at least one document (Prescription / Bill) to proceed.");
      return;
    }

    try {
      setSubmitLoading(true);
      
      const formData = new FormData();
      formData.append("member_id", formFields.memberId);
      formData.append("policy_id", formFields.policyId);
      formData.append("claim_category", formFields.claimCategory);
      formData.append("treatment_date", formFields.treatmentDate);
      formData.append("claimed_amount", formFields.claimedAmount);
      if (formFields.hospitalName) formData.append("hospital_name", formFields.hospitalName);
      formData.append("pre_auth_obtained", formFields.preAuthObtained ? "true" : "false");

      uploadedFiles.forEach(file => {
        formData.append("files", file);
      });

      const response = await fetch(`${backendUrl}/claims`, {
        method: "POST",
        body: formData,
      });

      if (response.ok) {
        const result = await response.json();
        onClose();
        setUploadedFiles([]);
        onSubmitSuccess(result.claim_id);
      } else {
        const errorData = await response.json();
        alert(`Failed to submit claim: ${errorData.detail || "Server error"}`);
      }
    } catch (err) {
      console.error("Error submitting claim", err);
      alert("Failed to submit claim. Make sure backend is running.");
    } finally {
      setSubmitLoading(false);
    }
  };

  return (
    <div className="modal-overlay animate-fade-in">
      <div className="glass-panel modal-content animate-scale-up" onDragOver={handleDragOver} onDrop={handleDrop}>
        <div className="modal-header">
          <h2>New Insurance Claim Audit Request</h2>
          <button onClick={() => { onClose(); setUploadedFiles([]); }} className="close-btn">&times;</button>
        </div>

        {/* Presets */}
        <div className="autofill-presets">
          <span className="presets-label">Testing Presets:</span>
          <button type="button" onClick={() => autofillPreset("vikram")} className="btn-preset">
            Vikram Joshi (Alternative Medicine)
          </button>
          <button type="button" onClick={() => autofillPreset("mismatch")} className="btn-preset">
            Sneha/Shaina (Patient Mismatch)
          </button>
        </div>

        <form onSubmit={handleFormSubmit} className="modal-form">
          <div className="form-grid">
            <div className="form-group">
              <label htmlFor="memberId">Member ID</label>
              <input 
                type="text" 
                id="memberId" 
                value={formFields.memberId}
                onChange={(e) => setFormFields(prev => ({ ...prev, memberId: e.target.value }))}
                required
              />
            </div>

            <div className="form-group">
              <label htmlFor="policyId">Policy ID</label>
              <input 
                type="text" 
                id="policyId" 
                value={formFields.policyId}
                onChange={(e) => setFormFields(prev => ({ ...prev, policyId: e.target.value }))}
                required
              />
            </div>

            <div className="form-group">
              <label htmlFor="claimCategory">Claim Category</label>
              <select 
                id="claimCategory" 
                value={formFields.claimCategory}
                onChange={(e) => setFormFields(prev => ({ ...prev, claimCategory: e.target.value }))}
              >
                <option value="CONSULTATION">Consultation</option>
                <option value="DENTAL">Dental</option>
                <option value="VISION">Vision</option>
                <option value="ALTERNATIVE_MEDICINE">Alternative Medicine</option>
              </select>
            </div>

            <div className="form-group">
              <label htmlFor="treatmentDate">Treatment Date</label>
              <input 
                type="date" 
                id="treatmentDate" 
                value={formFields.treatmentDate}
                onChange={(e) => setFormFields(prev => ({ ...prev, treatmentDate: e.target.value }))}
                required
              />
            </div>

            <div className="form-group">
              <label htmlFor="claimedAmount">Claimed Amount (INR)</label>
              <input 
                type="number" 
                step="0.01"
                id="claimedAmount" 
                value={formFields.claimedAmount}
                onChange={(e) => setFormFields(prev => ({ ...prev, claimedAmount: e.target.value }))}
                required
              />
            </div>

            <div className="form-group">
              <label htmlFor="hospitalName">Hospital / Clinic Name</label>
              <input 
                type="text" 
                id="hospitalName" 
                value={formFields.hospitalName}
                onChange={(e) => setFormFields(prev => ({ ...prev, hospitalName: e.target.value }))}
              />
            </div>

            <div className="form-group checkbox-group">
              <input 
                type="checkbox" 
                id="preAuthObtained" 
                checked={formFields.preAuthObtained}
                onChange={(e) => setFormFields(prev => ({ ...prev, preAuthObtained: e.target.checked }))}
              />
              <label htmlFor="preAuthObtained">Pre-Authorization Obtained</label>
            </div>
          </div>

          {/* Drag & Drop files */}
          <div className="form-group file-upload-group">
            <label>Claim Documents (Prescriptions / Bill Images / PDFs)</label>
            <div className="drag-drop-zone" onClick={() => fileInputRef.current.click()}>
              <Icons.FileText />
              <p>Drag & drop documents here, or <span>browse files</span></p>
              <span className="text-xs text-secondary">Supports PNG, JPEG, PDF</span>
              <input 
                type="file" 
                ref={fileInputRef}
                onChange={handleFileChange}
                style={{ display: "none" }} 
                multiple
              />
            </div>

            {uploadedFiles.length > 0 && (
              <div className="uploaded-files-list">
                {uploadedFiles.map((file, idx) => (
                  <div key={idx} className="file-item-chip animate-fade-in">
                    <span className="file-name">{file.name}</span>
                    <span className="file-size">({(file.size / 1024).toFixed(1)} KB)</span>
                    <button type="button" onClick={() => removeFile(idx)} className="remove-file-btn">
                      &times;
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="form-actions">
            <button type="button" onClick={() => { onClose(); setUploadedFiles([]); }} className="btn-secondary">
              Cancel
            </button>
            <button type="submit" disabled={submitLoading} className="btn-primary">
              {submitLoading ? (
                <>
                  <Icons.Refresh /> Submitting Claim...
                </>
              ) : (
                "Submit to Agent Pipeline"
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
