import { defineConfig } from 'wxt';

export default defineConfig({
  modules: ['@wxt-dev/module-react'],
  manifest: ({ browser }) => ({
    name: 'Pigeon — Reddit Outreach Agent',
    short_name: 'Pigeon',
    description:
      'A cursor that browses Reddit like a human, finds people who fit your ICP, upvotes, drafts comments and sends DMs.',
    // Chrome needs 'windows' and 'sidePanel'. Firefox has neither as a permission: browser.windows needs none, and
    // its sidebar comes from the sidepanel entrypoint.
    permissions: ['storage', 'alarms', 'tabs', 'contextMenus', ...(browser === 'firefox' ? [] : ['windows', 'sidePanel'])],
    host_permissions: [
      'https://www.reddit.com/*',
      'https://old.reddit.com/*',
      'https://chat.reddit.com/*',
      'https://api.deepseek.com/*',
    ],
    // Needed only to read the user's product website during onboarding; requested at click time.
    optional_host_permissions: ['https://*/*', 'http://*/*'],
    // The OCR engine used to read SOP photos/scans is WebAssembly; MV3 blocks wasm unless the page CSP allows it.
    content_security_policy: { extension_pages: "script-src 'self' 'wasm-unsafe-eval'; object-src 'self'" },
    action: { default_title: 'Open Pigeon' },
    ...(browser === 'firefox'
      ? {
          // Firefox needs an add-on id for MV3 + storage. 140 is the first release that understands data_collection_permissions.
          // data_collection_permissions (Firefox 140+, required by AMO for new add-ons): Reddit post/comment text is
          // sent to DeepSeek for scoring and writing. Review this declaration before submitting.
          browser_specific_settings: {
            gecko: {
              id: 'pigeon@trypigeon.in',
              strict_min_version: '140.0',
              data_collection_permissions: { required: ['websiteContent'] },
            },
          },
        }
      : { side_panel: { default_path: 'sidepanel.html' } }),
  }),
});
