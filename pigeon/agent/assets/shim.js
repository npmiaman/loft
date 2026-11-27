// A tiny stand-in for the bits of chrome.* that Pigeon's content script touches, so the unmodified
// extension script runs inside a Camoufox page. Python talks to it through window.__pigeon.send().
(function (settings) {
  var listeners = [];
  var api = {
    stopped: false,
    send: function (msg) {
      return new Promise(function (resolve) {
        for (var i = 0; i < listeners.length; i++) {
          if (listeners[i](msg, {}, resolve) === true) return; // handler will call resolve later
        }
        resolve(undefined);
      });
    },
  };
  Object.defineProperty(window, '__pigeon', { value: api, enumerable: false, configurable: true });
  window.chrome = {
    runtime: {
      onMessage: { addListener: function (f) { listeners.push(f); } },
      sendMessage: function (m) {
        if (m && m.type === 'user-stop') api.stopped = true; // the on-page Stop button
        return Promise.resolve({ ok: true });
      },
    },
    storage: {
      local: { get: function () { return Promise.resolve({ settings: settings }); } },
      onChanged: { addListener: function () {} },
    },
  };
})
