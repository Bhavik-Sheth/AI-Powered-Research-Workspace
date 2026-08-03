import { useEffect, useState } from "react";
import {
  createMatrixApiProjectsProjectIdMatrixPost,
  getMatrixViewApiProjectsProjectIdMatrixMatrixIdGet,
  listExperimentsApiProjectsProjectIdExperimentsGet,
  listMatricesApiProjectsProjectIdMatrixGet,
  listProjectPapersApiProjectsProjectIdPapersGet,
  putMatrixApiProjectsProjectIdMatrixMatrixIdPut,
  updateCellApiProjectsProjectIdMatrixMatrixIdCellsPatch,
  type ColumnDef,
  type Experiment,
  type LibraryEntry,
  type Matrix,
  type MatrixCell,
  type MatrixView as MatrixViewData,
} from "@research-os/api-client";

import "./MatrixView.css";

const STANDARD_COLUMNS: { column_key: string; label: string }[] = [
  { column_key: "problem", label: "Problem" },
  { column_key: "method", label: "Method" },
  { column_key: "datasets", label: "Datasets" },
  { column_key: "results", label: "Results" },
  { column_key: "limitations", label: "Limitations" },
];

function cellKey(rowId: string, columnKey: string): string {
  return `${rowId}::${columnKey}`;
}

/** Matrix View (MODULES.md) — the literature matrix grid: paper/experiment
 * rows × standard/custom/user columns. Standard and custom cells carry the
 * quote treatment and open the source paper; user cells render as plain
 * body type and are edited in place (US10). */
export function MatrixView({ projectId, onOpenPaper }: { projectId: string; onOpenPaper: (paperId: string, title: string) => void }) {
  const [matrices, setMatrices] = useState<Matrix[]>([]);
  const [activeMatrixId, setActiveMatrixId] = useState<string | null>(null);
  const [view, setView] = useState<MatrixViewData | null>(null);
  const [papers, setPapers] = useState<LibraryEntry[]>([]);
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [showPicker, setShowPicker] = useState(false);
  const [newColumnLabel, setNewColumnLabel] = useState("");
  const [newColumnQuery, setNewColumnQuery] = useState("");

  async function refreshMatrices(selectId?: string) {
    const { data } = await listMatricesApiProjectsProjectIdMatrixGet({ path: { project_id: projectId }, throwOnError: true });
    setMatrices(data);
    const next = selectId ?? data[0]?.id ?? null;
    setActiveMatrixId(next);
  }

  useEffect(() => {
    void refreshMatrices();
    void (async () => {
      const [papersRes, experimentsRes] = await Promise.all([
        listProjectPapersApiProjectsProjectIdPapersGet({ path: { project_id: projectId }, throwOnError: true }),
        listExperimentsApiProjectsProjectIdExperimentsGet({ path: { project_id: projectId }, throwOnError: true }),
      ]);
      setPapers(papersRes.data);
      setExperiments(experimentsRes.data);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  async function refreshView(matrixId: string) {
    const { data } = await getMatrixViewApiProjectsProjectIdMatrixMatrixIdGet({
      path: { project_id: projectId, matrix_id: matrixId },
      throwOnError: true,
    });
    setView(data);
  }

  useEffect(() => {
    if (activeMatrixId) void refreshView(activeMatrixId);
    else setView(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeMatrixId]);

  async function createNewMatrix() {
    const { data } = await createMatrixApiProjectsProjectIdMatrixPost({
      path: { project_id: projectId },
      body: { name: `Matrix ${matrices.length + 1}`, column_defs: STANDARD_COLUMNS.map((c) => ({ ...c, kind: "standard" as const })) },
      throwOnError: true,
    });
    await refreshMatrices(data.id);
  }

  async function putMatrix(patch: Partial<Pick<Matrix, "selected_paper_ids" | "selected_experiment_ids" | "column_defs">>) {
    if (!view) return;
    await putMatrixApiProjectsProjectIdMatrixMatrixIdPut({
      path: { project_id: projectId, matrix_id: view.matrix.id },
      body: {
        selected_paper_ids: patch.selected_paper_ids ?? view.matrix.selected_paper_ids,
        selected_experiment_ids: patch.selected_experiment_ids ?? view.matrix.selected_experiment_ids,
        column_defs: patch.column_defs ?? view.matrix.column_defs,
      },
      throwOnError: true,
    });
    await refreshView(view.matrix.id);
  }

  function toggleRow(kind: "paper" | "experiment", id: string) {
    if (!view) return;
    if (kind === "paper") {
      const selected = view.matrix.selected_paper_ids.includes(id)
        ? view.matrix.selected_paper_ids.filter((p) => p !== id)
        : [...view.matrix.selected_paper_ids, id];
      void putMatrix({ selected_paper_ids: selected });
    } else {
      const selected = view.matrix.selected_experiment_ids.includes(id)
        ? view.matrix.selected_experiment_ids.filter((e) => e !== id)
        : [...view.matrix.selected_experiment_ids, id];
      void putMatrix({ selected_experiment_ids: selected });
    }
  }

  function addStandardColumn(column: (typeof STANDARD_COLUMNS)[number]) {
    if (!view) return;
    void putMatrix({ column_defs: [...view.matrix.column_defs, { ...column, kind: "standard" }] });
  }

  function addCustomColumn() {
    if (!view || !newColumnLabel.trim() || !newColumnQuery.trim()) return;
    const columnKey = `custom_${Date.now()}`;
    const column: ColumnDef = { column_key: columnKey, label: newColumnLabel.trim(), kind: "custom", query: newColumnQuery.trim() };
    void putMatrix({ column_defs: [...view.matrix.column_defs, column] });
    setNewColumnLabel("");
    setNewColumnQuery("");
  }

  function addUserColumn(label: string) {
    if (!view || !label.trim()) return;
    const column: ColumnDef = { column_key: `user_${Date.now()}`, label: label.trim(), kind: "user" };
    void putMatrix({ column_defs: [...view.matrix.column_defs, column] });
  }

  async function editCell(rowId: string, columnKey: string, value: string) {
    if (!view) return;
    await updateCellApiProjectsProjectIdMatrixMatrixIdCellsPatch({
      path: { project_id: projectId, matrix_id: view.matrix.id },
      body: { row_id: rowId, column_key: columnKey, value },
      throwOnError: true,
    });
    await refreshView(view.matrix.id);
  }

  if (matrices.length === 0) {
    return (
      <div className="matrix matrix--empty">
        <p className="matrix__empty-title">No matrix yet in this project.</p>
        <button type="button" onClick={() => void createNewMatrix()}>
          + New matrix
        </button>
      </div>
    );
  }

  if (!view) return <p>Loading…</p>;

  const cellByKey = new Map<string, MatrixCell>(view.cells.map((cell) => [cellKey(cell.row_id, cell.column_key), cell]));
  const availableStandardColumns = STANDARD_COLUMNS.filter(
    (column) => !view.matrix.column_defs.some((c) => c.column_key === column.column_key),
  );

  return (
    <div className="matrix">
      <div className="matrix__toolbar">
        <select value={activeMatrixId ?? ""} onChange={(event) => setActiveMatrixId(event.target.value)}>
          {matrices.map((m) => (
            <option key={m.id} value={m.id}>
              {m.name}
            </option>
          ))}
        </select>
        <button type="button" onClick={() => void createNewMatrix()}>
          + New matrix
        </button>
        <button type="button" onClick={() => setShowPicker((prev) => !prev)}>
          {showPicker ? "Hide row/column editor" : "Edit rows & columns"}
        </button>
      </div>

      {showPicker && (
        <div className="matrix__picker">
          <div className="matrix__picker-section">
            <h4>Papers</h4>
            {papers.map(({ paper }) => (
              <label key={paper.id} className="matrix__picker-row">
                <input
                  type="checkbox"
                  checked={view.matrix.selected_paper_ids.includes(paper.id)}
                  onChange={() => toggleRow("paper", paper.id)}
                />
                {paper.title}
              </label>
            ))}
          </div>
          <div className="matrix__picker-section">
            <h4>Experiments</h4>
            {experiments.map((experiment) => (
              <label key={experiment.id} className="matrix__picker-row">
                <input
                  type="checkbox"
                  checked={view.matrix.selected_experiment_ids.includes(experiment.id)}
                  onChange={() => toggleRow("experiment", experiment.id)}
                />
                {experiment.title}
              </label>
            ))}
          </div>
          <div className="matrix__picker-section">
            <h4>Columns</h4>
            {availableStandardColumns.map((column) => (
              <button key={column.column_key} type="button" onClick={() => addStandardColumn(column)}>
                + {column.label}
              </button>
            ))}
            <div className="matrix__new-column">
              <input placeholder="Custom column label" value={newColumnLabel} onChange={(event) => setNewColumnLabel(event.target.value)} />
              <input
                placeholder="Scoped question, e.g. What baseline did they compare against?"
                value={newColumnQuery}
                onChange={(event) => setNewColumnQuery(event.target.value)}
              />
              <button type="button" onClick={addCustomColumn}>
                + Custom column
              </button>
            </div>
            <UserColumnAdder onAdd={addUserColumn} />
          </div>
        </div>
      )}

      {view.rows.length === 0 ? (
        <p className="matrix__not-stated">No rows selected yet — use "Edit rows & columns" to add papers or experiments.</p>
      ) : (
        <div className="matrix__grid-scroll">
          <table className="matrix__grid">
            <thead>
              <tr>
                <th>Row</th>
                {view.matrix.column_defs.map((column) => (
                  <th key={column.column_key}>{column.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {view.rows.map((row) => (
                <tr key={row.row_id}>
                  <td className="matrix__row-label">
                    {row.row_kind === "paper" ? (
                      <button type="button" className="matrix__row-link" onClick={() => onOpenPaper(row.row_id, row.label)}>
                        {row.label}
                      </button>
                    ) : (
                      row.label
                    )}
                  </td>
                  {view.matrix.column_defs.map((column) => {
                    const cell = cellByKey.get(cellKey(row.row_id, column.column_key));
                    return (
                      <td key={column.column_key}>
                        <MatrixCellView
                          cell={cell}
                          onOpenPaper={row.row_kind === "paper" ? () => onOpenPaper(row.row_id, row.label) : undefined}
                          onEdit={(value) => void editCell(row.row_id, column.column_key, value)}
                        />
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function UserColumnAdder({ onAdd }: { onAdd: (label: string) => void }) {
  const [label, setLabel] = useState("");
  return (
    <div className="matrix__new-column">
      <input placeholder="User column label" value={label} onChange={(event) => setLabel(event.target.value)} />
      <button
        type="button"
        onClick={() => {
          onAdd(label);
          setLabel("");
        }}
      >
        + User column
      </button>
    </div>
  );
}

/** A single cell: an extracted value carries the quote treatment and opens
 * the paper it was drawn from; a user override or a blank user column is
 * plain, editable body text. A cell absent after a completed query renders
 * `not stated`, never a stuck spinner (Schema.md). */
function MatrixCellView({
  cell,
  onOpenPaper,
  onEdit,
}: {
  cell: MatrixCell | undefined;
  onOpenPaper: (() => void) | undefined;
  onEdit: (value: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(cell?.value ?? "");

  if (editing) {
    return (
      <div className="matrix__cell-edit">
        <textarea value={draft} onChange={(event) => setDraft(event.target.value)} autoFocus />
        <div className="matrix__cell-edit-actions">
          <button
            type="button"
            onClick={() => {
              onEdit(draft);
              setEditing(false);
            }}
          >
            Save
          </button>
          <button type="button" onClick={() => setEditing(false)}>
            Cancel
          </button>
        </div>
      </div>
    );
  }

  if (!cell) {
    return (
      <button
        type="button"
        className="matrix__cell matrix__cell--not-stated"
        onClick={() => {
          setDraft("");
          setEditing(true);
        }}
      >
        not stated
      </button>
    );
  }

  if (cell.source === "extracted") {
    return (
      <button
        type="button"
        className="matrix__cell matrix__cell--quote"
        onClick={onOpenPaper}
        title="Open the source paper"
      >
        {cell.value}
      </button>
    );
  }

  return (
    <button
      type="button"
      className="matrix__cell matrix__cell--user"
      onClick={() => {
        setDraft(cell.value);
        setEditing(true);
      }}
    >
      {cell.value}
    </button>
  );
}
