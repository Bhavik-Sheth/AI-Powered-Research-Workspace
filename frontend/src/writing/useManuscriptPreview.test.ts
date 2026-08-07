import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useManuscriptPreview } from "./useManuscriptPreview";

const updateDocument = vi.fn();

vi.mock("@research-os/api-client", () => ({
  updateDocumentApiProjectsProjectIdDocumentsDocumentIdPut: (...args: unknown[]) => updateDocument(...args),
}));
vi.mock("../state/bridge", () => ({
  fetchBinary: vi.fn().mockResolvedValue(new ArrayBuffer(0)),
}));

const NON_EMPTY_TEX = "\\begin{document}hello\\end{document}";

describe("useManuscriptPreview (Phase 3.2 — switching drafts flushes, not drops, an edit)", () => {
  beforeEach(() => {
    updateDocument.mockReset();
    updateDocument.mockResolvedValue({ data: { success: true, log: "" } });
    vi.useFakeTimers();
  });

  it("sends the pending save for the OLD document when documentId changes mid-debounce", async () => {
    const { rerender } = renderHook(
      ({ tex, documentId }) => useManuscriptPreview(tex, "proj-1", documentId, "Draft"),
      { initialProps: { tex: NON_EMPTY_TEX, documentId: "doc-1" } },
    );

    // Well inside the 1500ms debounce window — the edit hasn't autosaved yet
    // on its own, matching the original bug's exact failure window.
    await act(async () => {
      vi.advanceTimersByTime(500);
    });
    expect(updateDocument).not.toHaveBeenCalled();

    // Switch documents before the debounce would ever have fired — this is
    // the exact "switching drafts within ~1.5s silently drops the edit" bug
    // (Frontend Improvement Plan Phase 3.2). The unmounting effect's cleanup
    // must flush the pending save rather than only cancelling it.
    await act(async () => {
      rerender({ tex: NON_EMPTY_TEX, documentId: "doc-2" });
      await Promise.resolve();
    });

    expect(updateDocument).toHaveBeenCalledTimes(1);
    expect(updateDocument.mock.calls[0][0]).toMatchObject({
      path: { project_id: "proj-1", document_id: "doc-1" },
      body: { tex: NON_EMPTY_TEX },
    });
  });

  it("does not send a save at all for an empty document body (Phase 4.1's empty-state fix)", async () => {
    renderHook(({ tex, documentId }) => useManuscriptPreview(tex, "proj-1", documentId, "Draft"), {
      initialProps: { tex: "\\begin{document}\\end{document}", documentId: "doc-1" },
    });

    await act(async () => {
      vi.advanceTimersByTime(DEBOUNCE_MS_PLUS_MARGIN);
    });

    expect(updateDocument).not.toHaveBeenCalled();
  });
});

const DEBOUNCE_MS_PLUS_MARGIN = 2000;
