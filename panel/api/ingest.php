<?php
// Push route: receives cards / reviews / heartbeats from the VPS.
// Auth: HMAC-SHA256 over "<ts>.<body>" with ingest_secret; timestamp must be within 5 minutes.
declare(strict_types=1);
require __DIR__ . '/../inc/bootstrap.php';
require __DIR__ . '/../inc/ingest_lib.php';

if ($_SERVER['REQUEST_METHOD'] !== 'POST') json_out(['ok' => false, 'error' => 'POST only'], 405);

$secret = (string)($CFG['ingest_secret'] ?? '');
if (strlen($secret) < 32 || strpos($secret, 'CHANGE_ME') === 0) json_out(['ok' => false, 'error' => 'ingest_secret not configured'], 500);

$body = file_get_contents('php://input') ?: '';
if (strlen($body) > 4 * 1024 * 1024) json_out(['ok' => false, 'error' => 'too large'], 400);
$ts  = (string)($_SERVER['HTTP_X_RADAR_TS'] ?? '');
$sig = (string)($_SERVER['HTTP_X_RADAR_SIG'] ?? '');
if (!ctype_digit($ts) || abs(time() - (int)$ts) > 300) json_out(['ok' => false, 'error' => 'bad or old timestamp'], 401);
if (!hash_equals(hash_hmac('sha256', $ts . '.' . $body, $secret), $sig)) json_out(['ok' => false, 'error' => 'bad signature'], 401);

$data = json_decode($body, true);
if (!is_array($data) || !isset($data['items']) || !is_array($data['items'])) json_out(['ok' => false, 'error' => 'bad json'], 400);

try {
    $saved = save_items($data['items']);
    meta_set('last_heartbeat', utc_now());
    if (isset($data['heartbeat']['queue'])) meta_set('vps_queue', (string)(int)$data['heartbeat']['queue']);
} catch (Throwable $ex) {
    error_log('radarban ingest: ' . $ex->getMessage());
    json_out(['ok' => false, 'error' => 'db error'], 500);
}
json_out(['ok' => true, 'saved' => $saved]);
