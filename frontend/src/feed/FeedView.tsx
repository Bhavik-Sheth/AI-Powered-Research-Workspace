import { useEffect, useState } from "react";
import {
  actOnFeedItemApiProjectsProjectIdFeedPost,
  listFeedApiProjectsProjectIdFeedGet,
  type FeedItem,
} from "@research-os/api-client";

import { ErrorCard } from "../design/ErrorCard";
import "./FeedView.css";

function whyRelevantText(item: FeedItem): string {
  const keywords = item.why_relevant.matched_keywords ?? [];
  const categories = item.why_relevant.matched_categories ?? [];
  const similarity = item.why_relevant.similarity ?? 0;
  const parts: string[] = [];
  if (keywords.length > 0) parts.push(`keywords: ${keywords.join(", ")}`);
  if (categories.length > 0) parts.push(`categories: ${categories.join(", ")}`);
  if (similarity > 0) parts.push(`similarity ${similarity.toFixed(2)}`);
  return parts.length > 0 ? parts.join(" · ") : "surfaced this poll";
}

function metadataString(item: FeedItem, key: string): string | null {
  const value = item.metadata[key];
  return typeof value === "string" && value.length > 0 ? value : null;
}

/** Feed View (MODULES.md Research Feed, D28) — new items only, best score
 * first, each stating why it surfaced; save adds it to the library and
 * shifts the corpus centroid, dismiss removes it and never resurfaces. */
export function FeedView({ projectId }: { projectId: string }) {
  const [items, setItems] = useState<FeedItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);

  async function load() {
    setError(null);
    try {
      const { data } = await listFeedApiProjectsProjectIdFeedGet({ path: { project_id: projectId }, throwOnError: true });
      setItems(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the feed");
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  async function act(itemId: string, action: "save" | "dismiss") {
    setPendingId(itemId);
    setError(null);
    try {
      const { data } = await actOnFeedItemApiProjectsProjectIdFeedPost({
        path: { project_id: projectId },
        body: { action, item_id: itemId },
        throwOnError: true,
      });
      setItems(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : `Could not ${action} this item`);
    } finally {
      setPendingId(null);
    }
  }

  if (error && items === null) return <ErrorCard title="Could not load the feed" message={error} onRetry={load} />;
  if (items === null) return <p>Loading…</p>;

  return (
    <div className="feed">
      {items.length === 0 ? (
        <p className="feed__empty">
          Nothing new yet — the feed fills in on the next catch-up poll, matched against your interest profile in
          Settings.
        </p>
      ) : (
        items.map((item) => {
          const sourceUrl = metadataString(item, "source_url");
          const abstract = metadataString(item, "abstract");
          return (
          <div className="feed__card" key={item.id}>
            <h3 className="feed__title">
              {sourceUrl ? (
                <a href={sourceUrl} target="_blank" rel="noreferrer">
                  {item.title}
                </a>
              ) : (
                item.title
              )}
            </h3>
            {abstract && <p className="feed__abstract">{abstract}</p>}
            <p className="feed__why">{whyRelevantText(item)}</p>
            <div className="feed__actions">
              <button type="button" className="feed__save" disabled={pendingId === item.id} onClick={() => void act(item.id, "save")}>
                Add to library
              </button>
              <button type="button" className="feed__dismiss" disabled={pendingId === item.id} onClick={() => void act(item.id, "dismiss")}>
                Dismiss
              </button>
            </div>
          </div>
          );
        })
      )}
      {error && <ErrorCard title="Could not update this item" message={error} onRetry={load} />}
    </div>
  );
}
