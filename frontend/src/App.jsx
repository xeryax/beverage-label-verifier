import React, { useState } from "react";

const SAMPLE = {
  beverageType: "spirits",
  brandName: "OLD TOM DISTILLERY",
  classType: "Kentucky Straight Bourbon Whiskey",
  alcoholContent: "45% Alc./Vol. (90 Proof)",
  netContents: "750 mL",
  producer: "Old Tom Distillery, Louisville, KY",
  countryOfOrigin: "United States",
};

const FIELD_ORDER = [
  "Brand Name",
  "Class/Type",
  "Alcohol Content",
  "Net Contents",
  "Producer",
  "Country of Origin",
  "Government Warning",
];

function Badge({ status }) {
  const label = { pass: "Matches", review: "Needs review", fail: "Does not match" }[status] || status;
  return <span className={`badge ${status}`}>{label}</span>;
}

export default function App() {
  const [form, setForm] = useState({ ...SAMPLE });
  const [files, setFiles] = useState([]);
  const [csvFile, setCsvFile] = useState(null);
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState([]);
  const [progress, setProgress] = useState("");

  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }));

  const onDrop = (e) => {
    e.preventDefault();
    const list = [...e.dataTransfer.files].filter((f) => f.type.startsWith("image/"));
    setFiles((prev) => [...prev, ...list]);
  };

  const runVerify = async () => {
    if (!files.length) return;
    setLoading(true);
    setResults([]);
    const out = [];

    if (csvFile) {
      const body = new FormData();
      files.forEach((f) => body.append("images", f));
      body.append("manifest", csvFile);
      body.append("application", JSON.stringify(form));
      setProgress("Processing batch…");
      const res = await fetch("/api/batch", { method: "POST", body });
      const data = await res.json();
      setResults(data.results || []);
      setProgress(`Done — ${data.summary?.pass || 0} pass, ${data.summary?.review || 0} review, ${data.summary?.fail || 0} fail`);
    } else {
      for (let i = 0; i < files.length; i++) {
        const f = files[i];
        setProgress(`Processing ${i + 1} of ${files.length}: ${f.name}`);
        const body = new FormData();
        body.append("image", f);
        body.append("application", JSON.stringify(form));
        const res = await fetch("/api/verify", { method: "POST", body });
        const data = await res.json();
        out.push({ ...data, filename: f.name });
      }
      const order = { fail: 0, review: 1, pass: 2 };
      out.sort((a, b) => (order[a.overall] ?? 0) - (order[b.overall] ?? 0));
      setResults(out);
      setProgress(`Done — ${out.length} label(s)`);
    }
    setLoading(false);
  };

  const exportCsv = () => {
    const rows = [["filename", "overall", "field", "status", "detected", "expected"]];
    results.forEach((r) => {
      Object.entries(r.fields || {}).forEach(([field, info]) => {
        rows.push([r.filename, r.overall, field, info.status, info.detected || "", info.expected || ""]);
      });
    });
    const csv = rows.map((row) => row.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(",")).join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "ttb-verification-results.csv";
    a.click();
  };

  return (
    <div className="app">
      <header>
        <h1>TTB Label Verifier</h1>
        <p>Compare label images against COLA application data — failures shown first.</p>
      </header>

      <div className="layout">
        <aside className="card">
          <h2>Application data</h2>
          <label>Beverage type</label>
          <select value={form.beverageType} onChange={(e) => set("beverageType", e.target.value)}>
            <option value="spirits">Distilled spirits</option>
            <option value="wine">Wine</option>
            <option value="beer">Beer / malt beverage</option>
          </select>
          <label>Brand name</label>
          <input value={form.brandName} onChange={(e) => set("brandName", e.target.value)} />
          <label>Class / type</label>
          <input value={form.classType} onChange={(e) => set("classType", e.target.value)} />
          <label>Alcohol content</label>
          <input value={form.alcoholContent} onChange={(e) => set("alcoholContent", e.target.value)} />
          <label>Net contents</label>
          <input value={form.netContents} onChange={(e) => set("netContents", e.target.value)} />
          <label>Producer / bottler</label>
          <input value={form.producer} onChange={(e) => set("producer", e.target.value)} />
          <label>Country of origin</label>
          <input value={form.countryOfOrigin} onChange={(e) => set("countryOfOrigin", e.target.value)} />
          <button type="button" className="btn-primary" style={{ marginBottom: "0.75rem", background: "#5c6b7a" }} onClick={() => setForm({ ...SAMPLE })}>
            Load sample data
          </button>
        </aside>

        <main>
          <div className="card">
            <h2>Upload labels</h2>
            <div
              className="dropzone"
              onDragOver={(e) => e.preventDefault()}
              onDrop={onDrop}
              onClick={() => document.getElementById("file-input").click()}
            >
              Drag & drop label images here, or click to browse
              <br />
              <span className="muted">PNG or JPG — flat COLA artwork works best; bottle photos may need manual review</span>
            </div>
            <input
              id="file-input"
              type="file"
              accept="image/*"
              multiple
              hidden
              onChange={(e) => setFiles([...files, ...e.target.files])}
            />
            {files.length > 0 && (
              <p className="muted">{files.length} file(s) selected: {files.map((f) => f.name).join(", ")}</p>
            )}
            <label>Optional batch CSV (columns: itemId, imageFile, brandName, …)</label>
            <input type="file" accept=".csv" onChange={(e) => setCsvFile(e.target.files[0] || null)} />
            <button className="btn-primary" disabled={loading || !files.length} onClick={runVerify}>
              {loading ? "Verifying…" : "Verify labels"}
            </button>
            {progress && <p className="progress">{progress}</p>}
          </div>

          {results.length > 0 && (
            <div className="card" style={{ marginTop: "1.25rem" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <h2>Results</h2>
                <button type="button" onClick={exportCsv} style={{ background: "#e8edf2", color: "#1a2332" }}>
                  Export CSV
                </button>
              </div>
              {results.map((r) => (
                <div key={r.filename || r.itemId} style={{ marginBottom: "1.5rem", borderBottom: "1px solid var(--border)", paddingBottom: "1rem" }}>
                  <div style={{ display: "flex", gap: "0.75rem", alignItems: "center", flexWrap: "wrap" }}>
                    <strong>{r.filename || r.itemId}</strong>
                    <Badge status={r.overall} />
                    <span className="muted">{r.processingTimeMs}ms · OCR {Math.round((r.ocrConfidence || 0) * 100)}%</span>
                  </div>
                  {r.imageQualityNote && (
                    <p className="muted" style={{ marginTop: "0.5rem", color: "var(--review)" }}>
                      {r.imageQualityNote}
                    </p>
                  )}
                  <div className="field-grid">
                    {FIELD_ORDER.filter((f) => r.fields?.[f]).map((field) => {
                      const info = r.fields[field];
                      return (
                        <div key={field} className="field-row">
                          <span>{field}</span>
                          <span>
                            <Badge status={info.status} />{" "}
                            <span className="muted">{info.detected || "(not detected)"}</span>
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
