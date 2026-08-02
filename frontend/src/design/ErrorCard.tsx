import "./ErrorCard.css";

/** UI_DESIGN.md §3.4 — never a dead end; always names what went wrong and offers a retry. */
export function ErrorCard({ title, message, onRetry }: { title: string; message: string; onRetry: () => void }) {
  return (
    <div className="error-card">
      <p className="error-card__title">{title}</p>
      <p className="error-card__body">{message}</p>
      <button type="button" className="error-card__retry" onClick={onRetry}>
        Retry
      </button>
    </div>
  );
}
