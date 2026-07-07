// /review — one-tap grading of raw item ↔ extraction pairs into raw_items.review.
// One item at a time, newest first; verdicts land immediately, no local state to lose.

let queue = [];

const el = (id) => document.getElementById(id);

async function loadStats() {
  const stats = await (await fetch("/api/review/stats")).json();
  el("stats").textContent = JSON.stringify(stats, null, 1);
}

async function loadQueue() {
  queue = await (await fetch("/api/review/items?limit=200")).json();
  render();
}

function render() {
  const item = queue[0];
  el("item").hidden = !item;
  el("done").hidden = !!item;
  el("progress").textContent = item ? `${queue.length} unreviewed in queue — item #${item.id} · ${item.source_type}:${item.source_id} · fetched ${item.fetched_at}` : "";
  if (!item) return;
  el("payload").textContent = JSON.stringify(item.payload, null, 1);
  if (item.extraction === null) {
    el("extraction").textContent = "(no event extracted — grade the *decision to ignore*)";
    el("extraction").className = "no-event";
  } else {
    el("extraction").textContent = JSON.stringify(item.extraction, null, 1);
    el("extraction").className = "";
  }
}

async function grade(verdict) {
  const item = queue[0];
  if (!item) return;
  const res = await fetch(`/api/review/${item.id}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ verdict }),
  });
  if (!res.ok) {
    el("progress").textContent = `verdict failed (${res.status}) — item stays in queue`;
    return;
  }
  queue.shift();
  render();
  loadStats(); // accuracy updates as grading proceeds
}

document.querySelectorAll("#verdicts button").forEach((b) =>
  b.addEventListener("click", () => grade(b.dataset.verdict))
);

const KEYS = { 1: "correct", 2: "wrong_event", 3: "wrong_location", 4: "wrong_summary" };
document.addEventListener("keydown", (e) => {
  if (KEYS[e.key]) grade(KEYS[e.key]);
});

loadStats();
loadQueue();
