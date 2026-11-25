# Analyst run procedure (Claude, every ~5 minutes)

The analyst is the AI layer between the collector and Telegram.
Goal: **every new, relevant item goes to Telegram exactly once** — importance is a label, not a filter.
Nothing old, nothing duplicated.

## Files
| file | written by | meaning |
|---|---|---|
| `data/inbox.jsonl` | collector | new items that passed layer-1/2 (each has increasing `seq`) |
| `data/cursor.json` | analyst | `last_seq` = highest inbox seq already processed |
| `data/events.json` | analyst | memory of published events (72 h) for semantic dedup |
| `data/outbox.json` | analyst | messages to send; pushing it triggers the `send` workflow |
| `data/sent.json` | sender | ids already sent + Telegram message ids of events |
| `data/dropped.jsonl`, `data/sources_status.json` | collector | audit log + per-source health |

## Steps
1. `git pull --rebase`. Trigger a fresh collect: `gh workflow run collect.yml -R mhmdshahi/news-center`,
   wait for it (≤ 90 s, `gh run list -w collect.yml -L 1`), then pull again.
2. Read inbox items with `seq > cursor.last_seq`. If none: stop (no commit).
3. For each item decide, against `events.json` (last 72 h) and against the other new items:
   * **irrelevant** — not about crypto, forex/macro, US stocks, gold/oil, or policy that directly moves them → skip.
   * **duplicate** — same event already published (same entity + same action + same figure),
     even if worded differently or in another source → skip, but append the source to that event.
   * **update** — same event with genuinely new information (new figure, official confirmation,
     reversal, "UPDATE") → send as a reply (`reply_to_event` = that event's id).
   * **new** — send; create a new event `{id, key, title_fa, ts, sources, seq}`.
   Merge several new items about the same event into one message (list all sources).
4. Verification label: ✅ if from a tier-3 source or ≥ 2 independent sources, else «⚠️ تأییدنشده».
5. Write messages (Persian) into `outbox.json` `messages` (keep only last 72 h), set
   `cursor.last_seq`, update `events.json` (drop > 72 h), commit and push
   (`data/outbox.json data/cursor.json data/events.json`). Push triggers the sender.

## Message format
Full format for importance ≥ 3:
```
📅 HH:MM UTC | HH:MM نیویورک
📰 <دسته> | <تیتر فارسی>
⚠️ اهمیت: n/5 · ✅ تأییدشده | ⚠️ تأییدنشده
📝 <خلاصهٔ خبر در ۲–۳ جمله>
💡 <توضیح ساده: چرا مهم است>
📈/📉 سیگنال: <دارایی> <صعودی/نزولی/خنثی> — <دلیل کوتاه>
⏳ مهلت ورود: <کوتاه/میان‌مدت، یا «سیگنال معاملاتی ندارد»>
💲 قیمت: <دارایی> {{price:SYMBOL}}
🔗 <نام منبع(ها)>: <لینک>
```
Compact format for importance 1–2:
```
📅 HH:MM UTC · <دسته> · اهمیت n/5 · ✅|⚠️
📰 <تیتر فارسی>
💡 <یک جمله توضیح/اثر بر بازار>
🔗 <منبع>: <لینک>
```
Updates start with `🔄 به‌روزرسانی:`.
`دسته` ∈ کریپتو، فارکس، بورس آمریکا، کلان، کالا.
`{{price:SYMBOL}}` uses Yahoo symbols (filled in at send time): BTC-USD, ETH-USD, XRP-USD, SOL-USD,
EURUSD=X, GBPUSD=X, USDJPY=X, DX-Y.NYB, GC=F (gold), CL=F (oil), ^GSPC, ^IXIC, or a stock ticker.
Summaries are written in our own words — never copy article text.

## Message object
```json
{"id": "m-<seq>", "event_id": "e-<seq>", "reply_to_event": null,
 "created": "2026-11-25T18:40:00Z", "seqs": [123, 124], "importance": 3, "text": "..."}
```
