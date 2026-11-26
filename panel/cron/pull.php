<?php
// Pull route (backup for the VPS push). Run from cPanel → Cron Jobs every minute:
//   php /home/CPANELUSER/public_html/panel/cron/pull.php >/dev/null 2>&1
// Asks the VPS relay for queued items newer than the highest rseq we already have, saves them
// (idempotent), which also acknowledges everything up to that rseq on the VPS side.
declare(strict_types=1);
if (PHP_SAPI !== 'cli') { http_response_code(404); exit; }
require __DIR__ . '/../inc/bootstrap.php';
require __DIR__ . '/../inc/ingest_lib.php';

$relay  = rtrim((string)($CFG['relay_url'] ?? ''), '/');
$secret = (string)($CFG['ingest_secret'] ?? '');
if ($relay === '' || strlen($secret) < 32) { fwrite(STDERR, "relay_url / ingest_secret not set\n"); exit(0); }
if (!function_exists('curl_init')) { fwrite(STDERR, "php curl extension missing\n"); exit(1); }

// one run at a time
$lock = fopen(sys_get_temp_dir() . '/radarban_pull.lock', 'c');
if (!$lock || !flock($lock, LOCK_EX | LOCK_NB)) exit(0);

$deadline = time() + 50;   // stay inside the 1-minute cron slot
$rounds = 0;
do {
    $after = (int)db()->query('SELECT COALESCE(MAX(rseq), 0) FROM news')->fetchColumn();
    $query = 'after=' . $after . '&limit=100';
    $ts = (string)time();
    $ch = curl_init($relay . '/relay/items?' . $query);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true, CURLOPT_TIMEOUT => 25, CURLOPT_CONNECTTIMEOUT => 10,
        CURLOPT_HTTPHEADER => ['X-Radar-Ts: ' . $ts, 'X-Radar-Sig: ' . hash_hmac('sha256', $ts . '.' . $query, $secret),
                               'User-Agent: RadarbanPanelPull/1.0'],
    ]);
    $resp = curl_exec($ch);
    $code = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $err = curl_error($ch);
    curl_close($ch);
    $data = is_string($resp) ? json_decode($resp, true) : null;
    if ($code !== 200 || !is_array($data) || empty($data['ok'])) {
        meta_set('last_pull_error', utc_now() . ' ' . ($err ?: "HTTP $code"));
        exit(0);   // push route may still work; nothing is lost on the VPS
    }
    meta_set('last_pull_ok', utc_now());
    $items = $data['items'] ?? [];
    if ($items) save_items($items);
    $rounds++;
} while (count($items) >= 100 && time() < $deadline);
