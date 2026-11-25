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
EVERY message uses the user-approved format below (user's own sample, 2026-11-25). Whatever the importance,
all fields are mandatory; when a field does not apply, still write it (e.g. «سیگنال : ندارد — اثر مستقیم بر بازار ندارد»).
Each line starts with ⚠️. Date/time line: run `python3 scripts/jdate.py <published UTC>` and paste its output
(Jalali date + Tehran time, UTC+3:30). Digits in the date/time and importance lines are Persian (۰-۹).

```
⚠️ تاریخ خبر: <خروجی jdate.py> ({{age:<published UTC>}})

⚠️ لاین خبری <کریپتو | فارکس | بورس آمریکا | کلان | کالا>

⚠️ تیتر خبر : <تیتر فارسی> (<دارایی‌ها/تیکرها>)
⚠️ درجه اهمیت : <n> از ۱۰۰ ( <برچسب> )

⚠️ متن خبر : <خلاصه ۲–۳ جمله با کلمات خودمان، با ذکر منبع اصلی> (<تک‌منبعی | چندمنبعی | تأییدشده | تأییدنشده؛ نظر تحلیلگر | گزارش | رسمی>)

⚠️ توضیح ساده بات : <توضیح ساده: چرا مهم است و چه اثری روی بازار دارد>

⚠️ سیگنال : <دارایی/شاخص + جهت: صعودی | نزولی | خنثی — دلیل کوتاه — اطمینان: کم|متوسط|زیاد>
⚠️ مهلت ورود : <تا چه رویداد/ساعتی (UTC) اعتبار دارد؛ یا «سیگنال معاملاتی ندارد»>

⚠️ قیمت هنگام خبر : <دارایی> {{price:SYMBOL@<published UTC>}}


منابع : [[<نام منبع۱>|<لینک۱>]] | [[<نام منبع۲>|<لینک۲>]]
```
Importance scale (0–100) and label: ۰–۲۰ کم · ۲۱–۴۰ معمولی · ۴۱–۶۰ مهم · ۶۱–۸۰ بسیار مهم · ۸۱–۱۰۰ حیاتی
(100 ≈ Fed rate decision / major hack / ETF approval; 30–40 ≈ a strategist opinion or mid-size company news).
Event key words (single source, analyst opinion, report, official) go in the parenthesis at the end of «متن خبر».
The 💲-equivalent line «قیمت هنگام خبر» always names the asset of the signal (for non-tradable news: the closest market,
e.g. SPY/S&P 500 or BTC); never put prices elsewhere. Sources line lists every source that carried the item as [[name|url]] — the sender turns each name into a clickable link (no bare URLs in the message).
Updates start with a first line `🔄 به‌روزرسانی :` before the same format.
`دسته` ∈ کریپتو، فارکس، بورس آمریکا، کلان، کالا.
`{{price:SYMBOL}}` uses Yahoo symbols (filled in at send time): BTC-USD, ETH-USD, XRP-USD, SOL-USD,
EURUSD=X, GBPUSD=X, USDJPY=X, DX-Y.NYB, GC=F (gold), CL=F (oil), ^GSPC, ^IXIC, or a stock ticker.
Summaries are written in our own words — never copy article text.

## Trading-safety rules (the user trades on these messages — avoid causing losses)
1. **Freshness.** In the date line, after the Tehran time, add ` ({{age:<published UTC>}})` — the sender
   replaces it with «ارسال n دقیقه بعد از انتشار» (and «⚠️ خبر کهنه است» if > 30 min).
   News older than ~20 min when analysed: say in «مهلت ورود» that it is probably already priced in.
2. **Price line.** Always `{{price:SYMBOL@<published UTC>}}` — the sender prints the price at news time,
   the price now and the % move since the news, and warns when most of the move has already happened.
3. **Signal discipline.** The signal line ends with `— اطمینان: کم | متوسط | زیاد`.
   * single-source, unconfirmed, rumour or analyst opinion → never «زیاد»; prefer «خنثی/احتیاط».
   * official/confirmed (Fed, SEC, company filing, ≥ 2 tier-3 sources) → may be «زیاد».
   * never write buy/sell instructions, leverage, entry/stop levels — only direction, reason, confidence.
   * conflicting news in the last hour (e.g. selloff then rebound) → mention it and lower confidence.
4. **Market hours.** Crypto 24/7. Forex: closed Fri 22:00 → Sun 22:00 UTC. US stocks: 14:30–21:00 UTC
   (pre-market from 09:00, after-hours to 01:00). US holidays: market CLOSED Thu 26 Nov 2026 (Thanksgiving),
   early close Fri 27 Nov 2026 at 18:00 UTC; closed 25 Dec 2026, 1 Jan 2027. When the market is closed,
   «مهلت ورود» must say from when the signal can act (e.g. «بازار سهام تا جمعه ۱۴:۳۰ UTC بسته است»).
5. **Scheduled data.** If a high-impact release (CPI, NFP, FOMC, PCE, GDP) is due within 60 min,
   say so in «مهلت ورود» — it can reverse the signal.
6. **Accuracy first, every card.** Use the item's summary (collector adds the article's own description
   when the feed has none). If `headline_only` is true, write «فقط تیتر در دسترس است» and keep the signal
   «خنثی» with «اطمینان: کم» unless the headline itself is an official, unambiguous fact (e.g. a rate decision).
   Cross-check against other items and events of the last hours; mention contradictions. Never shorten
   or simplify the analysis to save time — quality is fixed regardless of volume.
7. **No fabrication.** If only a headline is available, say «فقط تیتر در دسترس است» in «متن خبر»;
   never invent figures, quotes or reasons.

## Round report (each round, in chat)
`python3 scripts/round_stats.py <previous round time UTC> <analysed> <sent>` → one Persian line:
sources checked · items received · items in Library after filtering · analysed & sent.
Store this round's time in `cursor.json` as `last_round_at`.

## Message object
```json
{"id": "m-<seq>", "event_id": "e-<seq>", "reply_to_event": null,
 "created": "2026-11-25T18:40:00Z", "seqs": [123, 124], "importance": 3, "text": "..."}
```
