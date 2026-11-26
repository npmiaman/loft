import { useState } from 'react';
import { EMPTY_AGENT } from '../../lib/storage';
import { useStore } from '../../lib/useStore';
import CommentsTab from './tabs/CommentsTab';
import LeadsTab from './tabs/LeadsTab';
import MessagesTab from './tabs/MessagesTab';
import RunTab from './tabs/RunTab';
import SettingsTab from './tabs/SettingsTab';
import SetupTab from './tabs/SetupTab';

const TABS = [
  ['run', 'Run'],
  ['setup', 'Setup'],
  ['leads', 'Leads'],
  ['messages', 'Messages'],
  ['comments', 'Comments'],
  ['settings', 'Settings'],
] as const;

export default function App() {
  const [tab, setTab] = useState<string>('run');
  const [agent] = useStore('agent', EMPTY_AGENT);

  return (
    <div className="app">
      <header className="top">
        <span className="logo">🕊</span>
        <h1>Pigeon</h1>
        <span className={`tag ${agent.running ? 'live' : ''}`}>{agent.running ? `● ${agent.phase}` : 'idle'}</span>
      </header>
      <nav className="tabs">
        {TABS.map(([id, label]) => (
          <button key={id} className={tab === id ? 'on' : ''} onClick={() => setTab(id)}>{label}</button>
        ))}
      </nav>
      <main>
        {tab === 'run' && <RunTab goto={setTab} />}
        {tab === 'setup' && <SetupTab />}
        {tab === 'leads' && <LeadsTab />}
        {tab === 'messages' && <MessagesTab />}
        {tab === 'comments' && <CommentsTab />}
        {tab === 'settings' && <SettingsTab />}
      </main>
    </div>
  );
}
