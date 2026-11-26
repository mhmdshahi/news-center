<?php
declare(strict_types=1);
require __DIR__ . '/inc/bootstrap.php';
start_session();
if (current_user()) { header('Location: index.php'); exit; }
if (empty($CFG['allow_register'])) { http_response_code(403); exit('ثبت‌نام بسته است.'); }

$err = '';
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    csrf_check();
    $email = mb_strtolower(trim((string)($_POST['email'] ?? '')));
    $name = mb_substr(trim((string)($_POST['name'] ?? '')), 0, 100);
    $pass = (string)($_POST['password'] ?? '');
    if (!filter_var($email, FILTER_VALIDATE_EMAIL)) {
        $err = 'ایمیل معتبر نیست.';
    } elseif (mb_strlen($pass) < 8) {
        $err = 'رمز عبور باید حداقل ۸ کاراکتر باشد.';
    } elseif ($pass !== (string)($_POST['password2'] ?? '')) {
        $err = 'تکرار رمز عبور یکسان نیست.';
    } else {
        $pdo = db();
        $st = $pdo->prepare('SELECT 1 FROM users WHERE email = ?');
        $st->execute([$email]);
        if ($st->fetchColumn()) {
            $err = 'این ایمیل قبلاً ثبت شده است.';
        } else {
            $first = (int)$pdo->query('SELECT COUNT(*) FROM users')->fetchColumn() === 0;  // first account = admin
            $pdo->prepare('INSERT INTO users (email, name, pass_hash, is_admin, sub_until, created_at) VALUES (?,?,?,?,NULL,?)')
                ->execute([$email, $name, password_hash($pass, PASSWORD_DEFAULT), $first ? 1 : 0, utc_now()]);
            session_regenerate_id(true);
            $_SESSION['uid'] = (int)$pdo->lastInsertId();
            header('Location: index.php');
            exit;
        }
    }
}
page_head('ثبت‌نام');
?>
<main class="auth">
  <h1><?= e($CFG['site_name'] ?? 'راداربان') ?></h1>
  <form method="post" class="box">
    <?= csrf_field() ?>
    <h2>ثبت‌نام</h2>
    <?php if ($err): ?><p class="err"><?= e($err) ?></p><?php endif; ?>
    <label>نام <input type="text" name="name" maxlength="100" value="<?= e($_POST['name'] ?? '') ?>"></label>
    <label>ایمیل <input type="email" name="email" required dir="ltr" value="<?= e($_POST['email'] ?? '') ?>"></label>
    <label>رمز عبور (حداقل ۸ کاراکتر) <input type="password" name="password" required minlength="8" dir="ltr" autocomplete="new-password"></label>
    <label>تکرار رمز عبور <input type="password" name="password2" required minlength="8" dir="ltr" autocomplete="new-password"></label>
    <button type="submit">ساخت حساب</button>
    <p class="muted">حساب دارید؟ <a href="login.php">ورود</a></p>
  </form>
</main>
<?php page_foot();
