import { defineConfig } from 'wxt';

export default defineConfig({
  modules: ['@wxt-dev/module-react'],
  manifest: {
    name: 'Pigeon — Reddit Outreach Agent',
    short_name: 'Pigeon',
    description:
      'A cursor that browses Reddit like a human, finds people who fit your ICP, upvotes, drafts comments and sends DMs.',
    permissions: ['storage', 'sidePanel', 'alarms', 'tabs', 'contextMenus', 'windows'],
    host_permissions: [
      'https://www.reddit.com/*',
      'https://old.reddit.com/*',
      'https://chat.reddit.com/*',
      'https://api.deepseek.com/*',
    ],
    // Needed only to read the user's product website during onboarding; requested at click time.
    optional_host_permissions: ['https://*/*', 'http://*/*'],
    action: { default_title: 'Open Pigeon' },
    side_panel: { default_path: 'sidepanel.html' },
  },
});
