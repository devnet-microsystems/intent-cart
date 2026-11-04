import { useState } from "react";
import { AgGridReact } from "ag-grid-react";
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-alpine.css";

export default function App() {
  const [rows, setRows] = useState([]);
  const [selected, setSelected] = useState(null);

  const columns = [
    { headerName: "Product", field: "product.name", flex: 2 },
    { headerName: "Retailer", field: "product.retailer", flex: 1 },
    { headerName: "Price", field: "product.offer.price", flex: 1 },
    { headerName: "Condition", field: "product.condition", flex: 1 },
    { headerName: "Availability", field: "product.availability", flex: 1 },
    { headerName: "Gate", field: "gate.decision", flex: 1,
      cellRenderer: (p) => <span style={{ color: p.value === "APPROVED" ? "green" : "red", fontWeight: "bold" }}>{p.value}</span> },
    { headerName: "Rejection Reasons", field: "gate.rules", flex: 2,
      valueFormatter: (p) => (p.value || []).filter(r=>!r.passed).map(r=>r.rule).join(", ") },
  ];

  const approved = rows.filter(r => r.gate?.decision === "APPROVED").length;
  const blocked = rows.filter(r => r.gate?.decision === "REJECTED").length;

  return (
    <div style={{ padding: 16 }}>
      <h1>INTENT CART</h1>
      <p>Your intent. Your money. Your rules.</p>
      <div style={{ display: "flex", gap: 16, marginBottom: 16 }}>
        <div>Found: {rows.length}</div>
        <div>Approved: {approved}</div>
        <div>Rejected: {blocked}</div>
      </div>
      <div className="ag-theme-alpine" style={{ height: 400, width: "100%" }}>
        <AgGridReact
          rowData={rows}
          columnDefs={columns}
          onSelectionChanged={(e) => setSelected(e.api.getSelectedRows()[0])}
          rowSelection="single"
        />
      </div>
      {selected && (
        <div style={{ marginTop: 16, border: "1px solid #ccc", padding: 12 }}>
          <h3>Evidence: {selected.product?.name}</h3>
          <pre>{JSON.stringify(selected.gate, null, 2)}</pre>
        </div>
      )}
    </div>
  );
}
