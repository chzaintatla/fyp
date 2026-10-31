import type { ReactNode } from 'react';

export type View = 'assistant' | 'characters' | 'memory' | 'settings';

const Icon = ({ children }: { children: ReactNode }) => (
  <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {children}
  </svg>
);

const ITEMS: { id: View; label: string; icon: ReactNode }[] = [
  { id: 'assistant', label: 'Home', icon: <Icon><path d="M3 11l9-8 9 8" /><path d="M5 10v10h14V10" /></Icon> },
  { id: 'characters', label: 'Characters', icon: <Icon><circle cx="12" cy="8" r="4" /><path d="M4 21c1-4 4-6 8-6s7 2 8 6" /></Icon> },
  { id: 'memory', label: 'Memory', icon: <Icon><path d="M9 3h6v3h3v5h-3v3H9v-3H6V6h3z" /><path d="M9 21v-4M15 21v-4" /></Icon> },
  { id: 'settings', label: 'Settings', icon: <Icon><circle cx="12" cy="12" r="3" /><path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2" /></Icon> },
];

interface RailProps {
  view: View;
  onChange: (view: View) => void;
}

/** Left rail on wide windows, bottom bar on narrow ones. Alt+1..4 jump to a page. */
export function Rail({ view, onChange }: RailProps) {
  return (
    <nav aria-label="Pages" data-testid="rail"
      className="fixed inset-x-0 bottom-0 z-20 flex justify-around border-t border-line bg-surface/95 p-1 backdrop-blur md:inset-y-0 md:right-auto md:w-[84px] md:flex-col md:justify-start md:gap-1 md:border-r md:border-t-0 md:p-2">
      {ITEMS.map((item, i) => (
        <button key={item.id} type="button" onClick={() => onChange(item.id)} aria-current={view === item.id ? 'page' : undefined}
          title={`${item.label} (Alt+${i + 1})`}
          className={`flex flex-1 flex-col items-center gap-1 rounded-xl px-2 py-2 text-xs font-medium md:flex-none ${view === item.id ? 'bg-accent text-[var(--on-accent)]' : 'text-ink2 hover:bg-raised hover:text-ink'}`}>
          {item.icon}
          <span>{item.label}</span>
        </button>
      ))}
    </nav>
  );
}
