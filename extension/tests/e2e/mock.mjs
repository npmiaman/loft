// Minimal imitation of the Reddit DOM shapes the content script relies on. This tests MECHANICS only.
const shell = (body, extra = '') => `<!doctype html><html><head><title>mock</title><style>
body{font:15px sans-serif;margin:0} shreddit-post,shreddit-comment{display:block;border-bottom:1px solid #ddd;padding:12px;min-height:200px}
a{color:blue} .sp{height:600px} button{padding:8px 12px;margin:4px}
[contenteditable]{border:1px solid #888;min-height:40px;padding:6px;margin:6px 0}
</style></head><body><header><a href="/">Home</a><button>Messages</button></header>${body}
<script>
window.__log={upvotes:0,comments:[],dms:[]};
for (const el of document.querySelectorAll('shreddit-post,shreddit-comment')) {
  const sr=el.attachShadow({mode:'open'});
  sr.innerHTML='<button upvote aria-pressed="false">▲ Upvote</button><slot></slot>';
  sr.querySelector('button').addEventListener('click',e=>{e.currentTarget.setAttribute('aria-pressed','true');window.__log.upvotes++;});
}
${extra}
</script></body></html>`;

const post = (n, author, title, body) => `<shreddit-post permalink="/r/saas/comments/p${n}/t${n}/" post-title="${title}" author="${author}"
 subreddit-prefixed-name="r/saas" score="${10 * n}" comment-count="${n}"><a href="/r/saas/comments/p${n}/t${n}/">${title}</a>
 <div slot="text-body">${body}</div></shreddit-post>`;

export const pages = {
  listing: () => shell(
    post(1, 'founder_amy', 'How do I find my first 10 customers?', 'I have built a product and nobody knows it exists. Where do people find early users without ad budget? Any advice appreciated, I am stuck.') +
    '<div class="sp"></div>' +
    post(2, 'AutoModerator', 'Weekly thread', 'bot post that should be ignored by the reader') +
    '<div class="sp"></div>' +
    post(3, 'dev_bob', 'Alternative to Mailchimp for tiny lists?', 'Mailchimp pricing is killing me for 800 subscribers. Looking for something simpler and cheaper to use.') +
    '<div class="sp"></div>' + post(4, 'sam_k', 'Show: my weekend project', 'Just sharing what I built this weekend with the community, feedback welcome.')),
  postPage: () => shell(
    post(1, 'founder_amy', 'How do I find my first 10 customers?', 'I have built a product and nobody knows it exists. Where do people find early users?') +
    `<faceplate-textarea-input placeholder="Join the conversation" id="trigger" style="display:block;border:1px solid #888;padding:10px;margin:10px">Join the conversation</faceplate-textarea-input>
     <div id="composer" style="display:none"><div contenteditable="true" id="ed"></div><button id="submit">Comment</button></div>` +
    ['c1|reader_one|0|I had the same problem, tried cold DMs and communities.', 'c2|reader_two|1|Honestly look at where your buyers already complain about alternatives.', 'c3|AutoModerator|0|Please read the rules.']
      .map((s) => { const [id, a, d, t] = s.split('|'); return `<shreddit-comment permalink="/r/saas/comments/p1/t1/${id}/" author="${a}" depth="${d}" score="3"><div slot="comment">${t}</div></shreddit-comment>`; }).join(''),
    `document.getElementById('trigger').addEventListener('click',()=>{document.getElementById('composer').style.display='block'});
     document.getElementById('submit').addEventListener('click',()=>{const ed=document.getElementById('ed');window.__log.comments.push(ed.innerText.trim());ed.innerHTML='';});`),
  profile: (name) => shell(
    `<h1>u/${name}</h1><button id="chat">Chat</button>
     <div id="chatbox" style="display:none"><div role="textbox" contenteditable="true" aria-label="Message" id="msg"></div><button aria-label="Send message" id="send">Send</button><div id="thread"></div><div id="err"></div></div>`,
    `document.getElementById('chat').addEventListener('click',()=>{document.getElementById('chatbox').style.display='block'});
     document.getElementById('send').addEventListener('click',()=>{const m=document.getElementById('msg');const t=m.innerText.trim();
       if(${name === 'ratelimited' ? 'true' : 'false'}){document.getElementById('err').textContent="You're doing that too much. Try again in 9 minutes.";return;}
       window.__log.dms.push(t);document.getElementById('thread').textContent=t;m.innerHTML='';});`),
};
