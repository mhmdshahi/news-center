<?php
// Shared by api/ingest.php (push route) and cron/pull.php (pull route). Idempotent: upsert by msg_id.
declare(strict_types=1);

function meta_set(string $k, ?string $v): void {
    $pdo = db();
    $st = $pdo->prepare('UPDATE meta SET v = ? WHERE k = ?');
    $st->execute([$v, $k]);
    if ($st->rowCount() === 0) {
        $chk = $pdo->prepare('SELECT 1 FROM meta WHERE k = ?');
        $chk->execute([$k]);
        if (!$chk->fetchColumn()) $pdo->prepare('INSERT INTO meta (k, v) VALUES (?, ?)')->execute([$k, $v]);
    }
}

function meta_get(string $k): ?string {
    $st = db()->prepare('SELECT v FROM meta WHERE k = ?');
    $st->execute([$k]);
    $v = $st->fetchColumn();
    return $v === false ? null : $v;
}

function _s($v, int $max): ?string { return $v === null ? null : mb_substr((string)$v, 0, $max); }

function _utc(?string $iso): string {
    $t = $iso ? strtotime($iso) : false;
    return gmdate('Y-m-d H:i:s', $t ?: time());
}

/** Save items (array of arrays from the VPS). Returns number saved. Throws on DB error. */
function save_items(array $items): int {
    $pdo = db();
    $find = $pdo->prepare('SELECT id FROM news WHERE msg_id = ?');
    $ins = $pdo->prepare('INSERT INTO news (msg_id, rseq, kind, code, event_id, reply_to_event, importance, line, title,
        sig_dir, sig_label, sig_symbol, sig_conf, html, created_at, received_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)');
    $upd = $pdo->prepare('UPDATE news SET rseq = COALESCE(rseq, ?) WHERE msg_id = ?');
    $saved = 0;
    $pdo->beginTransaction();
    try {
        foreach ($items as $it) {
            if (!is_array($it) || empty($it['msg_id']) || !isset($it['html'])) continue;
            $msg = _s($it['msg_id'], 64);
            $rseq = isset($it['rseq']) ? (int)$it['rseq'] : null;
            $find->execute([$msg]);
            if ($find->fetchColumn()) {           // already have it (other route) — content is identical
                $upd->execute([$rseq, $msg]);
                continue;
            }
            $kind = in_array($it['kind'] ?? 'card', ['card', 'review'], true) ? $it['kind'] : 'card';
            $ins->execute([$msg, $rseq, $kind, isset($it['code']) ? (int)$it['code'] : null,
                _s($it['event_id'] ?? null, 64), _s($it['reply_to_event'] ?? null, 64),
                isset($it['importance']) ? (int)$it['importance'] : null, _s($it['line'] ?? null, 64),
                _s($it['title'] ?? null, 500), _s($it['sig_dir'] ?? null, 10), _s($it['sig_label'] ?? null, 64),
                _s($it['sig_symbol'] ?? null, 32), _s($it['sig_conf'] ?? null, 16),
                clean_card_html((string)$it['html']), _utc($it['created'] ?? null), utc_now()]);
            $saved++;
        }
        $pdo->commit();
    } catch (Throwable $ex) {
        $pdo->rollBack();
        throw $ex;
    }
    return $saved;
}

/** Link status for the feed banner: ok | late | down, plus timestamps. */
function link_status(): array {
    $last = max(strtotime((meta_get('last_heartbeat') ?? '1970-01-01') . ' UTC'),
                strtotime((meta_get('last_pull_ok') ?? '1970-01-01') . ' UTC'));
    $age = time() - $last;
    $st = db()->query("SELECT MAX(received_at) FROM news WHERE kind = 'card'")->fetchColumn();
    return ['state' => $age <= 180 ? 'ok' : ($age <= 600 ? 'late' : 'down'),
            'last_contact_ts' => $last > 0 ? $last : null,
            'last_item_ts' => $st ? strtotime($st . ' UTC') : null];
}
