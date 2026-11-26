<?php
// News feed for logged-in subscribers. GET ?after=<id> → newer items; ?before=<id> → older page.
declare(strict_types=1);
require __DIR__ . '/../inc/bootstrap.php';

$u = current_user();
if (!$u) json_out(['ok' => false, 'error' => 'login'], 401);
if (!has_access($u)) json_out(['ok' => false, 'error' => 'subscription'], 403);
session_write_close(); // don't block parallel requests

$kind   = ($_GET['kind'] ?? 'card') === 'review' ? 'review' : 'card';
$after  = max(0, (int)($_GET['after'] ?? 0));
$before = max(0, (int)($_GET['before'] ?? 0));
$limit  = min(100, max(1, (int)($_GET['limit'] ?? 40)));

$cols = 'id, msg_id, kind, code, event_id, reply_to_event, importance, line, title, sig_dir, sig_label, html, created_at';
if ($after > 0) {
    $st = db()->prepare("SELECT $cols FROM news WHERE kind = ? AND id > ? ORDER BY id ASC LIMIT $limit");
    $st->execute([$kind, $after]);
} elseif ($before > 0) {
    $st = db()->prepare("SELECT $cols FROM news WHERE kind = ? AND id < ? ORDER BY id DESC LIMIT $limit");
    $st->execute([$kind, $before]);
} else {
    $st = db()->prepare("SELECT $cols FROM news WHERE kind = ? ORDER BY id DESC LIMIT $limit");
    $st->execute([$kind]);
}
$rows = $st->fetchAll();
foreach ($rows as &$r) {
    $r['id'] = (int)$r['id'];
    $r['code'] = $r['code'] === null ? null : (int)$r['code'];
    $r['importance'] = $r['importance'] === null ? null : (int)$r['importance'];
    $r['created_ts'] = strtotime($r['created_at'] . ' UTC');
}
json_out(['ok' => true, 'items' => $rows, 'server_ts' => time()]);
