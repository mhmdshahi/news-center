<?php
declare(strict_types=1);
require __DIR__ . '/inc/bootstrap.php';
start_session();
if (current_user()) { header('Location: index.php'); exit; }

$err = '';
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    csrf_check();
    $pdo = db();
    $ip = client_ip();
    $pdo->prepare('DELETE FROM login_attempts WHERE at < ?')->execute([gmdate('Y-m-d H:i:s', time() - 86400)]);
    $st = $pdo->prepare('SELECT COUNT(*) FROM login_attempts WHERE ip = ? AND at > ?');
    $st->execute([$ip, gmdate('Y-m-d H:i:s', time() - 900)]);
    if ((int)$st->fetchColumn() >= 10) {
        $err = 'تلاش‌های ناموفق زیاد بود؛ ۱۵ دقیقهٔ دیگر دوباره امتحان کنید.';
    } else {
        $email = mb_strtolower(trim((string)($_POST['email'] ?? '')));
        $pass = (string)($_POST['password'] ?? '');
        $st = $pdo->prepare('SELECT id, pass_hash FROM users WHERE email = ?');
        $st->execute([$email]);
        $row = $st->fetch();
        if ($row && password_verify($pass, $row['pass_hash'])) {
            session_regenerate_id(true);
            $_SESSION['uid'] = (int)$row['id'];
            header('Location: index.php');
            exit;
        }
        $pdo->prepare('INSERT INTO login_attempts (ip, at) VALUES (?, ?)')->execute([$ip, utc_now()]);
        usleep(400000);
        $err = 'ایمیل یا رمز عبور اشتباه است.';
    }
}
page_head('ورود');
?>
<main class="auth">
  <h1><?= e($CFG['site_name'] ?? 'راداربان') ?></h1>
  <form method="post" class="box">
    <?= csrf_field() ?>
    <h2>ورود به پنل</h2>
    <?php if ($err): ?><p class="err"><?= e($err) ?></p><?php endif; ?>
    <label>ایمیل <input type="email" name="email" required autocomplete="username" dir="ltr"></label>
    <label>رمز عبور <input type="password" name="password" required autocomplete="current-password" dir="ltr"></label>
    <button type="submit">ورود</button>
    <?php if (!empty($CFG['allow_register'])): ?><p class="muted">حساب ندارید؟ <a href="register.php">ثبت‌نام</a></p><?php endif; ?>
  </form>
</main>
<?php page_foot();
