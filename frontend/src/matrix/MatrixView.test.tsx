import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { MatrixView } from "./MatrixView";

const listMatrices = vi.fn();
const listProjectPapers = vi.fn();
const listExperiments = vi.fn();
const getMatrixView = vi.fn();
const putMatrix = vi.fn();

vi.mock("@research-os/api-client", () => ({
  listMatricesApiProjectsProjectIdMatrixGet: (...args: unknown[]) => listMatrices(...args),
  listProjectPapersApiProjectsProjectIdPapersGet: (...args: unknown[]) => listProjectPapers(...args),
  listExperimentsApiProjectsProjectIdExperimentsGet: (...args: unknown[]) => listExperiments(...args),
  getMatrixViewApiProjectsProjectIdMatrixMatrixIdGet: (...args: unknown[]) => getMatrixView(...args),
  createMatrixApiProjectsProjectIdMatrixPost: vi.fn(),
  putMatrixApiProjectsProjectIdMatrixMatrixIdPut: (...args: unknown[]) => putMatrix(...args),
  updateCellApiProjectsProjectIdMatrixMatrixIdCellsPatch: vi.fn(),
}));

const PAPER_1 = { paper: { id: "paper-1", title: "Paper One" }, relevance: "unset" };
const PAPER_2 = { paper: { id: "paper-2", title: "Paper Two" }, relevance: "unset" };

const BASE_MATRIX = {
  id: "matrix-1",
  project_id: "proj-1",
  name: "Matrix 1",
  selected_paper_ids: [] as string[],
  selected_experiment_ids: [] as string[],
  column_defs: [],
};

function baseView() {
  return { matrix: { ...BASE_MATRIX }, rows: [], cells: [] };
}

describe("MatrixView (Phase 3.3 — rapid edits no longer overwrite each other)", () => {
  beforeEach(() => {
    listMatrices.mockReset().mockResolvedValue({ data: [BASE_MATRIX] });
    listProjectPapers.mockReset().mockResolvedValue({ data: [PAPER_1, PAPER_2] });
    listExperiments.mockReset().mockResolvedValue({ data: [] });
    getMatrixView.mockReset().mockResolvedValue({ data: baseView() });
    // Never resolves within the test — the assertion is about what each PUT
    // is called with and what the checkboxes show *before* any round trip
    // completes, mirroring the original bug's "looks stuck/inconsistent
    // mid-round-trip" symptom.
    putMatrix.mockReset().mockReturnValue(new Promise(() => {}));
  });

  it("selecting two rows in quick succession sends a PUT for each with both selections composed, and the UI shows both checked immediately", async () => {
    render(<MatrixView projectId="proj-1" onOpenPaper={vi.fn()} />);

    fireEvent.click(await screen.findByRole("button", { name: "Edit rows & columns" }));

    const paperOneCheckbox = (await screen.findByText("Paper One")).closest("label")!.querySelector("input")!;
    const paperTwoCheckbox = screen.getByText("Paper Two").closest("label")!.querySelector("input")!;

    // Two back-to-back clicks, well before either PUT resolves (it never
    // does in this test) — the second click's optimistic state must build
    // on the first's, not on the pre-click snapshot from before either
    // toggle (Frontend Improvement Plan Phase 3.3).
    fireEvent.click(paperOneCheckbox);
    fireEvent.click(paperTwoCheckbox);

    await waitFor(() => expect(putMatrix).toHaveBeenCalledTimes(2));

    // Neither click's PUT ever "lost" the other's selection.
    expect(putMatrix.mock.calls[0][0].body.selected_paper_ids).toEqual(["paper-1"]);
    expect(putMatrix.mock.calls[1][0].body.selected_paper_ids.sort()).toEqual(["paper-1", "paper-2"]);

    // The optimistic update is applied to the UI immediately, not only sent
    // to the server — both checkboxes read as checked despite neither PUT
    // having resolved (the InterestProfileForm half of this same finding:
    // an edit must not "look dead" for the whole round-trip).
    expect(paperOneCheckbox).toBeChecked();
    expect(paperTwoCheckbox).toBeChecked();
  });
});
