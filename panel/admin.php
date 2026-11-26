<?php
declare(strict_types=1);
require __DIR__ . '/inc/bootstrap.php';
$u = require_login();
if ((int)$u['is_admin'] !== 1) { http_response_code(403); exit('دسترسی ندارید.'); }
$pdo = db();
$msg = '';

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    csrf_check();
    $id = (int)($_POST['id'] ?? 0);
    $act = (string)($_POST['act'] ?? '');
    if ($id > 0) {
        if (preg_match('/^add(\d{1,3})$/', $act, $m)) {
            $days = (int)$m[1];
            $st = $pdo->prepare('SELECT sub_until FROM users WHERE id = ?');
            $st->execute([$id]);
            $cur = $st->fetchColumn();
            $base = ($cur && $cur > utc_now()) ? strtotime($cur . ' UTC') : time();
            $pdo->prepare('UPDATE users SET sub_until = ? WHERE id = ?')->execute([gmdate('Y-m-d H:i:s', $base + $days * 86400), $id]);
            $msg = fa_digits("$days روز به اشتراک اضافه شد.");
        } elseif ($act === 'revoke') {
            $pdo->prepare('UPDATE users SET sub_until = NULL WHERE id = ?')->execute([$id]);
            $msg = 'اشتراک لغو شد.';
        } elseif ($act === 'admin' && $id !== (int)$u['id']) {
            $pdo->prepare('UPDATE users SET is_admin = 1 - is_admin WHERE id = ?')->execute([$id]);
            $msg = 'نقش مدیر تغییر کرد.';
        }
    }
}

$users = $pdo->query('SELECT id, email, name, is_admin, sub_until, created_at FROM users ORDER BY id DESC LIMIT 500')->fetchAll();
$stats = $pdo->query("SELECT COUNT(*) AS n, MAX(received_at) AS last FROM news WHERE kind = 'card'")->fetch();
$active = 0;
foreach ($users as $x) if ($x['sub_until'] && $x['sub_until'] > utc_now()) $active++;
page_head('مدیریت');
?>
<header class="top">
  <div class="brand"><?= e($CFG['site_name'] ?? 'راداربان') ?> — مدیریت</div>
  <nav><a href="index.php">اخبار</a><a href="logout.php">خروج</a></nav>
</header>
<main class="admin">
  <?php if ($msg): ?><p class="ok"><?= e($msg) ?></p><?php endif; ?>
  <div class="stats">
    <div><b><?= e(fa_digits((string)count($users))) ?></b> کاربر</div>
    <div><b><?= e(fa_digits((string)$active)) ?></b> اشتراک فعال</div>
    <div><b><?= e(fa_digits((string)$stats['n'])) ?></b> خبر در پنل</div>
    <div>آخرین خبر: <b><?= e(jdate_teh($stats['last'], true)) ?></b></div>
  </div>
  <table>
    <thead><tr><th>کاربر</th><th>عضویت</th><th>اشتراک تا</th><th>عملیات</th></tr></thead>
    <tbody>
    <?php foreach ($users as $x): $on = $x['sub_until'] && $x['sub_until'] > utc_now(); ?>
      <tr>
        <td><?= e($x['name']) ?><br><span class="muted" dir="ltr"><?= e($x['email']) ?></span><?= (int)$x['is_admin'] ? ' <span class="tag">مدیر</span>' : '' ?></td>
        <td><?= e(jdate_teh($x['created_at'])) ?></td>
        <td class="<?= $on ? 'on' : 'offc' ?>"><?= $x['sub_until'] ? e(jdate_teh($x['sub_until'])) : '—' ?></td>
        <td>
          <form method="post" class="inline">
            <?= csrf_field() ?><input type="hidden" name="id" value="<?= (int)$x['id'] ?>">
            <button name="act" value="add30">+۳۰ روز</button>
            <button name="act" value="add90">+۹۰ روز</button>
            <button name="act" value="add365">+۳۶۵ روز</button>
            <button name="act" value="revoke" class="danger">لغو</button>
            <?php if ((int)$x['id'] !== (int)$u['id']): ?><button name="act" value="admin" class="ghost"><?= (int)$x['is_admin'] ? 'حذف مدیر' : 'مدیر کن' ?></button><?php endif; ?>
          </form>
        </td>
      </tr>
    <?php endforeach; ?>
    </tbody>
  </table>
</main>
<?php page_foot();
