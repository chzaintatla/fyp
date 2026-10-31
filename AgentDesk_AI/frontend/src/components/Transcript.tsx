interface TranscriptProps {
  heard: string;
  lastCommand?: string;
  reply?: string | null;
  name: string;
}

/** Chat bubbles: you on the right (live words while you speak), the assistant on the left. */
export function Transcript({ heard, lastCommand, reply, name }: TranscriptProps) {
  const mine = heard || lastCommand;
  return (
    <div className="flex w-full min-w-0 flex-col gap-2 rounded-xl border border-line bg-raised p-3 text-sm" data-testid="transcript">
      <p className="text-xs font-semibold uppercase tracking-wide text-ink2">Conversation</p>
      <p aria-live="polite" data-testid="transcript-heard"
        className={`max-w-[85%] self-end rounded-2xl rounded-br-sm px-3 py-2 ${mine ? 'bg-accent text-[var(--on-accent)]' : 'text-ink2'}`}>
        {heard ? <>“{heard}”</> : lastCommand ? <>You: “{lastCommand}”</> : <span>Say a command…</span>}
      </p>
      {reply && (
        <p className="max-w-[85%] self-start rounded-2xl rounded-bl-sm border border-line bg-surface px-3 py-2 text-ink" data-testid="transcript-reply">
          <span className="font-medium">{name}:</span> {reply}
        </p>
      )}
    </div>
  );
}
