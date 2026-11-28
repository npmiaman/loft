// The desktop app's own API. The X-Pigeon header is what stops other websites from calling it.
export async function api<T = any>(method: string, path: string, body?: unknown): Promise<T> {
  let r: Response;
  try {
    r = await fetch("/api" + path, {
      method,
      headers: { "X-Pigeon": "1", "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError("Can't reach the Pigeon app. Is it still running?", 0);
  }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new ApiError(friendly(typeof j.detail === "string" ? j.detail : readable(j.detail) || r.statusText), r.status);
  return j as T;
}

/** For endpoints that return a file (team backup, account export): the bytes and the server's file name. */
export async function apiFile(method: string, path: string, body?: unknown): Promise<{ blob: Blob; name: string | null }> {
  let r: Response;
  try {
    r = await fetch("/api" + path, { method, headers: { "X-Pigeon": "1", "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError("Can't reach the Pigeon app. Is it still running?", 0);
  }
  if (!r.ok) { const j = await r.json().catch(() => ({})); throw new ApiError(friendly(typeof j.detail === "string" ? j.detail : readable(j.detail) || r.statusText), r.status); }
  const m = (r.headers.get("Content-Disposition") || "").match(/filename="(.+?)"/);
  return { blob: await r.blob(), name: m ? m[1] : null };
}

export function saveFile(blob: Blob, name: string) {
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

export class ApiError extends Error {
  constructor(msg: string, public status: number) { super(msg); }
}

/** The server's validation errors arrive as a list of {loc, msg, type, ctx}; turn them into a sentence a person can act on. */
function readable(detail: unknown): string {
  if (!Array.isArray(detail)) return detail ? JSON.stringify(detail) : "";
  return detail.map((d: { loc?: unknown[]; msg?: string; type?: string; ctx?: { min_length?: number; max_length?: number } }) => {
    const raw = String(d.loc?.[d.loc.length - 1] ?? "");
    const field = raw ? raw.charAt(0).toUpperCase() + raw.slice(1).replace(/_/g, " ") : "This";
    if (d.type === "missing") return `${field} is required.`;
    if (d.type === "string_too_short") return d.ctx?.min_length === 1 ? `${field} is required.` : `${field} needs at least ${d.ctx?.min_length} characters.`;
    if (d.type === "string_too_long") return `${field} can be at most ${d.ctx?.max_length} characters.`;
    return `${field}: ${d.msg ?? "is not valid"}.`;
  }).join(" ");
}

/** Messages a person sees when the AI (DeepSeek) key is missing. The UI turns these into an "Add AI key" button. */
export const NO_AI_KEY = "No AI key is set on this computer yet. Add it in the agent's Settings.";
export const isNoAiKey = (m: string) => m === NO_AI_KEY || /deepseek key|DEEPSEEK_API_KEY/i.test(m);

/**
 * Backend strings written for the command line, mapped to plain sentences for the dashboard. Anything not listed passes
 * through unchanged (most server errors are already sentences). Keep the patterns loose: the wording upstream drifts.
 */
const MAP: [RegExp, string | ((m: RegExpMatchArray) => string)][] = [
  [/no deepseek key|DEEPSEEK_API_KEY/i, NO_AI_KEY],
  [/DeepSeek rejected the API key/i, "The AI key was rejected. Check it in the agent's Settings."],
  [/DeepSeek account has insufficient balance/i, "The AI account is out of credit. Top it up with your AI provider, then try again."],
  [/set up the product first|no product yet/i, "Set up the product first, on the Product tab."],
  [/^not signed in$/i, "You're signed out. Sign in again."],
  [/^no team$/i, "You're not in a team yet."],
  [/already running on this machine/i, "That's already open on this computer."],
  [/not running on this machine/i, "That isn't open on this computer."],
  [/stop the profile first/i, "Close the account's browser first."],
  [/no cloud runners in this team/i, "This team has no cloud runners yet. Add one on the Team page."],
  [/The agent is already (\w+) \((\w+)\)/i, "The agent is busy. Wait for it to finish, or stop it first."],
  [/^unknown setting '(\w+)'/i, (m) => `"${m[1]}" isn't a setting this version knows.`],
  [/^invalid literal for int\(\) with base 10: '(.*)'$/i, (m) => `"${m[1]}" isn't a whole number.`],
  [/^could not convert string to float: '(.*)'$/i, (m) => `"${m[1]}" isn't a number.`],
  [/^bad API key/i, "That API key isn't valid."],
  [/^missing X-Pigeon header$/i, "The app refused a request from outside it."],
  [/^wrong encryption passphrase$/i, "That password doesn't unlock your encryption key."],
  [/only an owner can grant or remove owner/i, "Only an owner can make someone an owner, or change an owner's role."],
  [/team needs at least one owner/i, "The team needs at least one owner. Make someone else an owner first."],
  [/^wrong email or password$/i, "That email and password don't match."],
  [/^signup needs an invite$/i, "You need an invite code to join. Ask a teammate for one."],
  [/^email already registered$/i, "That email already has an account."],
  [/^Internal Server Error$/i, "Something went wrong in the Pigeon app. Try again; if it keeps happening, restart the app."],
  [/^\s*<(!doctype|html|head|body)/i, "Got an unexpected reply instead of an answer from Pigeon. Try again."],
  [/invite invalid or expired/i, "That invite code is used or expired. Ask for a new one."],
  [/already a member/i, "You're already in that team."],
  [/the browser isn't installed yet/i, "The browser isn't installed yet. Use Install browser first."],
];

export function friendly(msg: string): string {
  for (const [re, to] of MAP) {
    const m = msg.match(re);
    if (m) return typeof to === "string" ? to : to(m);
  }
  return msg;
}
