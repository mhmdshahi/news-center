<?php
declare(strict_types=1);

// ── config + database ──────────────────────────────────────────────────
$cfgFile = __DIR__ . '/config.php';
if (!is_file($cfgFile)) {
    http_response_code(500);
    exit('config.php missing — copy config.sample.php to inc/config.php and fill it in.');
}
$CFG = require $cfgFile;

function db(): PDO {
    static $pdo = null;
    global $CFG;
    if ($pdo === null) {
        $pdo = new PDO($CFG['db_dsn'], $CFG['db_user'] ?? null, $CFG['db_pass'] ?? null, [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
            PDO::ATTR_EMULATE_PREPARES => false,
        ]);
    }
    return $pdo;
}

function utc_now(): string { return gmdate('Y-m-d H:i:s'); }

function e(?string $s): string { return htmlspecialchars((string)$s, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8'); }

function fa_digits(string $s): string { return strtr($s, ['0'=>'۰','1'=>'۱','2'=>'۲','3'=>'۳','4'=>'۴','5'=>'۵','6'=>'۶','7'=>'۷','8'=>'۸','9'=>'۹']); }

// ── session ────────────────────────────────────────────────────────────
function start_session(): void {
    if (session_status() === PHP_SESSION_ACTIVE) return;
    $https = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off')
          || (($_SERVER['HTTP_X_FORWARDED_PROTO'] ?? '') === 'https');
    session_name('radarban');
    session_set_cookie_params(['lifetime' => 0, 'path' => '/', 'secure' => $https,
                               'httponly' => true, 'samesite' => 'Lax']);
    session_start();
}

function csrf_token(): string {
    start_session();
    if (empty($_SESSION['csrf'])) $_SESSION['csrf'] = bin2hex(random_bytes(32));
    return $_SESSION['csrf'];
}

function csrf_field(): string { return '<input type="hidden" name="csrf" value="' . e(csrf_token()) . '">'; }

function csrf_check(): void {
    start_session();
    if (!hash_equals($_SESSION['csrf'] ?? '', (string)($_POST['csrf'] ?? ''))) {
        http_response_code(400);
        exit('درخواست نامعتبر (CSRF). صفحه را دوباره باز کنید.');
    }
}

function current_user(): ?array {
    start_session();
    if (empty($_SESSION['uid'])) return null;
    static $u = false;
    if ($u === false) {
        $st = db()->prepare('SELECT id, email, name, is_admin, sub_until FROM users WHERE id = ?');
        $st->execute([$_SESSION['uid']]);
        $u = $st->fetch() ?: null;
    }
    return $u;
}

function require_login(): array {
    $u = current_user();
    if (!$u) { header('Location: login.php'); exit; }
    return $u;
}

function has_access(array $u): bool {
    return (int)$u['is_admin'] === 1 || ($u['sub_until'] !== null && $u['sub_until'] > utc_now());
}

// ── card HTML sanitizer ────────────────────────────────────────────────
// Cards arrive as escaped text with <a href="https://…">name</a> links only. Escape everything,
// then restore just those links (https/http, no quotes/brackets) and keep line breaks.
function clean_card_html(string $h): string {
    $out = htmlspecialchars($h, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8', false);
    $out = preg_replace(
        '~&lt;a href=(?:&quot;|&#039;)(https?://[^\s"\'<>]+?)(?:&quot;|&#039;)&gt;(.*?)&lt;/a&gt;~u',
        '<a href="$1" target="_blank" rel="noopener noreferrer nofollow">$2</a>',
        $out
    );
    return nl2br((string)$out, false);
}

function json_out($data, int $status = 200): void {
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store');
    echo json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

function client_ip(): string { return substr((string)($_SERVER['REMOTE_ADDR'] ?? ''), 0, 45); }

function page_head(string $title): void {
    global $CFG;
    $site = e($CFG['site_name'] ?? 'راداربان');
    echo "<!doctype html><html lang=\"fa\" dir=\"rtl\"><head><meta charset=\"utf-8\">"
       . "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
       . "<meta name=\"robots\" content=\"noindex\">"
       . "<title>" . e($title) . " | {$site}</title>"
       . "<link rel=\"stylesheet\" href=\"assets/style.css?v=1\"></head><body>";
}

function page_foot(): void { echo "</body></html>"; }

// ── Jalali date (Tehran time, UTC+3:30) ────────────────────────────────
function g2j(int $gy, int $gm, int $gd): array {
    $g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334];
    $gy2 = $gm > 2 ? $gy + 1 : $gy;
    $days = 355666 + 365 * $gy + intdiv($gy2 + 3, 4) - intdiv($gy2 + 99, 100) + intdiv($gy2 + 399, 400) + $gd + $g_d_m[$gm - 1];
    $jy = -1595 + 33 * intdiv($days, 12053); $days %= 12053;
    $jy += 4 * intdiv($days, 1461); $days %= 1461;
    if ($days > 365) { $jy += intdiv($days - 1, 365); $days = ($days - 1) % 365; }
    if ($days < 186) { $jm = 1 + intdiv($days, 31); $jd = 1 + $days % 31; }
    else { $jm = 7 + intdiv($days - 186, 30); $jd = 1 + ($days - 186) % 30; }
    return [$jy, $jm, $jd];
}

function jdate_teh(?string $utc, bool $withTime = false): string {
    if (!$utc) return '—';
    $t = strtotime($utc . ' UTC') + 12600; // +3:30
    [$jy, $jm, $jd] = g2j((int)gmdate('Y', $t), (int)gmdate('n', $t), (int)gmdate('j', $t));
    $months = ['فروردین','اردیبهشت','خرداد','تیر','مرداد','شهریور','مهر','آبان','آذر','دی','بهمن','اسفند'];
    $s = "$jd {$months[$jm - 1]} $jy" . ($withTime ? ' ساعت ' . gmdate('H:i', $t) : '');
    return fa_digits($s);
}
