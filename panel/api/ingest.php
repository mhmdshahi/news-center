<?php
// Receives cards / reviews from the VPS pusher. Auth: HMAC-SHA256 over "<ts>.<body>" with ingest_secret.
declare(strict_types=1);
require __DIR__ . '/../inc/bootstrap.php';

if ($_SERVER['REQUEST_METHOD'] !== 'POST') json_out(['ok' => false, 'error' => 'POST only'], 405);

$secret = (string)($CFG['ingest_secret'] ?? '');
if (strlen($secret) < 32 || strpos($secret, 'CHANGE_ME') === 0) json_out(['ok' => false, 'error' => 'ingest_secret not configured'], 500);

$body = file_get_contents('php://input') ?: '';
$ts   = (string)($_SERVER['HTTP_X_RADAR_TS'] ?? '');
$sig  = (string)($_SERVER['HTTP_X_RADAR_SIG'] ?? '');
if (!ctype_digit($ts) || abs(time() - (int)$ts) > 300) json_out(['ok' => false, 'error' => 'bad or old timestamp'], 401);
$want = hash_hmac('sha256', $ts . '.' . $body, $secret);
if (!hash_equals($want, $sig)) json_out(['ok' => false, 'error' => 'bad signature'], 401);
if (strlen($body) > 4 * 1024 * 1024) json_out(['ok' => false, 'error' => 'too large'], 400);

$data = json_decode($body, true);
if (!is_array($data) || !isset($data['items']) || !is_array($data['items'])) json_out(['ok' => false, 'error' => 'bad json'], 400);

function s(?string $v, int $max): ?string { return $v === null ? null : mb_substr($v, 0, $max); }
function utc(?string $iso): string {
    $t = $iso ? strtotime($iso) : false;
    return gmdate('Y-m-d H:i:s', $t ?: time());
}

$pdo = db();
$find = $pdo->prepare('SELECT id FROM news WHERE msg_id = ?');
$ins = $pdo->prepare('INSERT INTO news (msg_id, kind, code, event_id, reply_to_event, importance, line, title,
    sig_dir, sig_label, sig_symbol, sig_conf, html, created_at, received_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)');
$upd = $pdo->prepare('UPDATE news SET html = ?, importance = ?, title = ?, line = ? WHERE msg_id = ?');

$saved = 0;
$pdo->beginTransaction();
try {
    foreach ($data['items'] as $it) {
        if (!is_array($it) || empty($it['msg_id']) || !isset($it['html'])) continue;
        $msg  = s((string)$it['msg_id'], 64);
        $kind = in_array($it['kind'] ?? 'card', ['card', 'review'], true) ? $it['kind'] : 'card';
        $html = clean_card_html((string)$it['html']);
        $imp  = isset($it['importance']) ? (int)$it['importance'] : null;
        $title = s($it['title'] ?? null, 500);
        $line  = s($it['line'] ?? null, 64);
        $find->execute([$msg]);
        if ($find->fetchColumn()) {
            $upd->execute([$html, $imp, $title, $line, $msg]);
        } else {
            $ins->execute([$msg, $kind, isset($it['code']) ? (int)$it['code'] : null,
                s($it['event_id'] ?? null, 64), s($it['reply_to_event'] ?? null, 64), $imp, $line, $title,
                s($it['sig_dir'] ?? null, 10), s($it['sig_label'] ?? null, 64), s($it['sig_symbol'] ?? null, 32),
                s($it['sig_conf'] ?? null, 16), $html, utc($it['created'] ?? null), utc_now()]);
        }
        $saved++;
    }
    $pdo->commit();
} catch (Throwable $ex) {
    $pdo->rollBack();
    error_log('radarban ingest: ' . $ex->getMessage());
    json_out(['ok' => false, 'error' => 'db error'], 500);
}
json_out(['ok' => true, 'saved' => $saved]);
