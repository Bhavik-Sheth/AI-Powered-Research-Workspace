import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createNoteApiProjectsProjectIdNotesPost,
  listNotesApiProjectsProjectIdNotesGet,
  updateNoteApiProjectsProjectIdNotesPatch,
  type Note,
} from "@research-os/api-client";

import { ErrorCard } from "../design/ErrorCard";
import "./NotesView.css";

async function fetchNotes(projectId: string): Promise<Note[]> {
  const { data } = await listNotesApiProjectsProjectIdNotesGet({
    path: { project_id: projectId },
    throwOnError: true,
  });
  return data;
}

/**
 * Notes Editor (MODULES.md) — note list + markdown editor for one project.
 *
 * Every note renders `Unlinked` (UI_DESIGN.md §4.4/§3.4): linking a note to
 * the paper that inspired it is `highlights.note_id`, which does not exist
 * until the phase that builds highlights. This is the real, current state,
 * not a placeholder — `Unlinked` is drawn deliberately rather than guessed.
 */
export function NotesView({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const notesQuery = useQuery({ queryKey: ["notes", projectId], queryFn: () => fetchNotes(projectId) });

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const notesList = notesQuery.data ?? [];
  const selected = notesList.find((note) => note.id === selectedId) ?? null;

  const didAutoSelect = useRef(false);
  useEffect(() => {
    if (!didAutoSelect.current && notesList.length > 0) {
      didAutoSelect.current = true;
      selectNote(notesList[0]);
    }
  }, [notesList]);

  function selectNote(note: Note | null) {
    setSelectedId(note?.id ?? null);
    setTitle(note?.title ?? "");
    setBody(note?.body ?? "");
    setError(null);
  }

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      const { data } = selected
        ? await updateNoteApiProjectsProjectIdNotesPatch({
            path: { project_id: projectId },
            body: { frontmatter_id: selected.id, title, body },
            throwOnError: true,
          })
        : await createNoteApiProjectsProjectIdNotesPost({
            path: { project_id: projectId },
            body: { title, body },
            throwOnError: true,
          });
      await queryClient.invalidateQueries({ queryKey: ["notes", projectId] });
      setSelectedId(data.id);
    } catch (err) {
      // The draft in the editor is left exactly as typed — a save failure
      // never discards user text (MODULES.md Notes Editor: Errors).
      setError(err instanceof Error ? err.message : "Could not save the note");
    } finally {
      setSaving(false);
    }
  }

  if (notesQuery.isPending) {
    return null;
  }

  if (notesQuery.isError) {
    return (
      <ErrorCard
        title="Could not load notes"
        message={notesQuery.error instanceof Error ? notesQuery.error.message : "Unknown error"}
        onRetry={() => notesQuery.refetch()}
      />
    );
  }

  return (
    <div className="notes-view">
      <div className="notes-view__list">
        <div className="notes-view__list-header">
          <span>All Notes</span>
          <button type="button" className="notes-view__new" onClick={() => selectNote(null)}>
            + New
          </button>
        </div>
        {notesList.length === 0 ? (
          <p className="notes-view__empty">No notes yet — write one to get started.</p>
        ) : (
          <ul className="notes-view__rows">
            {notesList.map((note) => (
              <li key={note.id}>
                <button
                  type="button"
                  className={
                    note.id === selectedId ? "notes-view__row notes-view__row--active" : "notes-view__row"
                  }
                  onClick={() => selectNote(note)}
                >
                  <span className="notes-view__row-title">{note.title}</span>
                  <span className="notes-view__unlinked">Unlinked</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="notes-view__editor">
        <div className="notes-view__editor-header">
          <input
            className="notes-view__title-input"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="Untitled note"
          />
          <span className="notes-view__unlinked">Unlinked</span>
        </div>
        <textarea
          className="notes-view__body"
          value={body}
          onChange={(event) => setBody(event.target.value)}
          placeholder="Write in markdown…"
        />
        {error && <ErrorCard title="Could not save the note" message={error} onRetry={handleSave} />}
        <div className="notes-view__actions">
          <button type="button" className="notes-view__save" onClick={handleSave} disabled={saving || !title}>
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
