import { defineBackground } from 'wxt/utils/define-background';
import { checkReplies, postOpportunity, scanActivePage, scheduledTick, sendLeadNow, startAgent, stopAgent } from '../lib/agent';
import * as store from '../lib/storage';

export default defineBackground(() => {
  chrome.sidePanel?.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});

  chrome.runtime.onInstalled.addListener(() => {
    chrome.contextMenus.removeAll(() => {
      chrome.contextMenus.create({ id: 'scan-page', title: 'Pigeon: queue everyone on this page', contexts: ['page'], documentUrlPatterns: ['*://*.reddit.com/*'] });
      chrome.contextMenus.create({ id: 'open-panel', title: 'Pigeon: open panel', contexts: ['page'] });
    });
    chrome.alarms.create('tick', { periodInMinutes: 15 });
  });

  chrome.contextMenus.onClicked.addListener(async (info, tab) => {
    if (tab?.windowId != null) chrome.sidePanel.open({ windowId: tab.windowId }).catch(() => {});
    if (info.menuItemId === 'scan-page') {
      try {
        await scanActivePage(true);
      } catch (e) {
        store.log('error', (e as Error).message);
      }
    }
  });

  chrome.alarms.onAlarm.addListener((a) => {
    if (a.name === 'tick') scheduledTick().catch((e) => store.log('error', String(e)));
  });

  chrome.runtime.onMessage.addListener((msg, _sender, send) => {
    if (msg?.target === 'page') return; // handled by the content script
    const reply = (p: Promise<unknown>) => {
      p.then((r) => send({ ok: true, ...(r as object | undefined) })).catch((e) => send({ ok: false, error: String(e?.message ?? e) }));
      return true;
    };
    switch (msg?.type) {
      case 'agent:start': return reply(startAgent(msg.mode));
      case 'agent:stop':
      case 'user-stop': stopAgent(); send({ ok: true }); return;
      case 'lead:send': return reply(sendLeadNow(msg.username));
      case 'comment:post': return reply(postOpportunity(msg.id));
      case 'replies:check': return reply(checkReplies().then((n) => ({ replies: n })));
      case 'page:scan': return reply(scanActivePage(!!msg.skipScoring));
      default: return;
    }
  });
});
