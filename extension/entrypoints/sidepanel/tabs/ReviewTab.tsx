import { useState } from 'react';
import { useLeads, useStore } from '../../../lib/useStore';
import CommentsTab, { PostIdeas } from './CommentsTab';
import LeadsTab from './LeadsTab';
import { WarmupDrafts } from './WarmupTab';
import { EmptyState } from '../ui';

type Filter = 'waiting' | 'dms' | 'comments' | 'posts';

/** One inbox for everything Pigeon wrote that needs a yes from you: DMs, comment drafts, post drafts. */
export default function ReviewTab({ goto }: { goto: (t: string) => void }) {
  const leads = useLeads();
  const [opps] = useStore('opportunities', []);
  const [items] = useStore('warmupItems', []);
  const [f, setF] = useState<Filter>('waiting');

  const dmDrafts = leads.filter((l) => l.status === 'draft').length;
  const comments = opps.filter((o) => o.status === 'pending' || o.status === 'failed').length + items.filter((i) => i.kind === 'comment' && (i.status === 'pending' || i.status === 'failed')).length;
  const posts = items.filter((i) => i.kind === 'post' && (i.status === 'pending' || i.status === 'failed')).length;
  const waiting = dmDrafts + comments + posts;

  const tabs: [Filter, string, number][] = [['waiting', 'Waiting', waiting], ['dms', 'DMs', leads.length], ['comments', 'Comments', comments], ['posts', 'Posts', posts]];

  return (
    <>
      <div className="seg filter" role="group" aria-label="Show">
        {tabs.map(([id, label, n]) => (
          <button key={id} className={f === id ? 'on' : ''} aria-pressed={f === id} onClick={() => setF(id)}>{label} ({n})</button>
        ))}
      </div>

      {f === 'waiting' && (
        <>
          {waiting === 0 && (
            <div className="card">
              <EmptyState title="Nothing waiting for you" action={<button className="btn pri" onClick={() => goto('home')}>Go to Home</button>}>
                Run the agent and the DMs, comments and posts it writes show up here for your approval.
              </EmptyState>
            </div>
          )}
          {dmDrafts > 0 && <LeadsTab only="draft" />}
          {comments > 0 && <CommentsTab compact />}
          {comments > 0 && <WarmupDrafts kind="comment" />}
          {posts > 0 && <WarmupDrafts kind="post" />}
        </>
      )}
      {f === 'dms' && <LeadsTab />}
      {f === 'comments' && (
        <>
          <CommentsTab />
          <WarmupDrafts kind="comment" />
        </>
      )}
      {f === 'posts' && (
        <>
          <WarmupDrafts kind="post" />
          <PostIdeas />
        </>
      )}
    </>
  );
}
