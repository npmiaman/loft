import { useState } from 'react';
import { EMPTY_AGENT } from '../../lib/storage';
import { useLeads, useStore } from '../../lib/useStore';
import ReviewTab from './tabs/ReviewTab';
import RunTab from './tabs/RunTab';
import SettingsTab from './tabs/SettingsTab';
import SetupTab from './tabs/SetupTab';
import WarmupTab from './tabs/WarmupTab';
import { ConfirmProvider } from './ui';

export type TabId = 'home' | 'review' | 'setup' | 'warmup' | 'settings';
const TABS: [TabId, string][] = [
  ['home', 'Home'],
  ['review', 'Review'],
  ['setup', 'Setup'],
  ['warmup', 'Warm-up'],
];
const KEY = 'pigeon.tab';
const OLD: Record<string, TabId> = { run: 'home', leads: 'review', comments: 'review', messages: 'setup' };
const readTab = (): TabId => {
  try {
    const v = localStorage.getItem(KEY) ?? 'home';
    return (OLD[v] ?? (['home', 'review', 'setup', 'warmup', 'settings'].includes(v) ? v : 'home')) as TabId;
  } catch {
    return 'home';
  }
};

const Logo = () => (
  <svg className="logo" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 13c3 0 5-2 6-5 1.5 3 4 4 7 4l5-3-2 5c-1 3-4 5-8 5H7l-3 3v-4c-1-1-1-3-1-5z" />
    <circle cx="16" cy="9" r=".6" fill="currentColor" />
  </svg>
);
const Gear = () => (
  <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
  </svg>
);

export default function App() {
  const [tab, setTabState] = useState<TabId>(readTab);
  const [agent] = useStore('agent', EMPTY_AGENT);
  const [opps] = useStore('opportunities', []);
  const [items] = useStore('warmupItems', []);
  const leads = useLeads();
  const waiting = leads.filter((l) => l.status === 'draft').length + opps.filter((o) => o.status === 'pending').length + items.filter((i) => i.status === 'pending').length;

  const setTab = (t: string) => {
    const id = (OLD[t] ?? t) as TabId;
    setTabState(id);
    try { localStorage.setItem(KEY, id); } catch { /* private window: just don't remember */ }
  };

  return (
    <ConfirmProvider>
      <div className="app">
        <header className="top">
          <Logo />
          <h1>Pigeon</h1>
          <span className={`tag ${agent.running ? 'live' : ''}`}>{agent.running ? `Running: ${agent.phase}` : 'Idle'}</span>
          <button className={`gear ${tab === 'settings' ? 'on' : ''}`} aria-label="Settings" aria-pressed={tab === 'settings'} title="Settings" onClick={() => setTab(tab === 'settings' ? 'home' : 'settings')}><Gear /></button>
        </header>
        <nav className="tabs" aria-label="Sections">
          {TABS.map(([id, label]) => (
            <button key={id} className={tab === id ? 'on' : ''} aria-current={tab === id ? 'page' : undefined} onClick={() => setTab(id)}>
              {label}{id === 'review' && waiting > 0 && <span className="count" aria-label={`${waiting} waiting`}>{waiting}</span>}
            </button>
          ))}
        </nav>
        <main>
          {tab === 'home' && <RunTab goto={setTab} />}
          {tab === 'review' && <ReviewTab goto={setTab} />}
          {tab === 'setup' && <SetupTab />}
          {tab === 'warmup' && <WarmupTab goto={setTab} />}
          {tab === 'settings' && <SettingsTab />}
        </main>
      </div>
    </ConfirmProvider>
  );
}
