import type { CompletionContext, CompletionResult } from "@codemirror/autocomplete";
import { listReferencesApiProjectsProjectIdReferencesGet } from "@research-os/api-client";

const CITE_PREFIX = /\\cite\{[^}]*/;

/** `\cite{` autocomplete (MODULES.md, D34) — pulls from the project's own
 * references via `autocomplete_citations`; never a client-side ranking of
 * its own (Rules.md: the frontend renders what the backend returns). */
export function citeCompletionSource(projectId: string) {
  return async function citeCompletions(context: CompletionContext): Promise<CompletionResult | null> {
    const match = context.matchBefore(CITE_PREFIX);
    if (!match) return null;
    const braceIndex = match.text.indexOf("{");
    const prefix = match.text.slice(braceIndex + 1);
    const from = match.from + braceIndex + 1;

    const { data } = await listReferencesApiProjectsProjectIdReferencesGet({
      path: { project_id: projectId },
      query: { prefix },
      throwOnError: true,
    });

    return {
      from,
      options: data.map((ref) => ({
        label: ref.cite_key,
        detail: ref.title,
        apply: ref.cite_key,
      })),
    };
  };
}
