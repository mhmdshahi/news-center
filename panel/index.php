<?php
declare(strict_types=1);
require __DIR__ . '/inc/bootstrap.php';
$u = require_login();
$ok = has_access($u);
page_head('اخبار');
?>
<header class="top">
  <div class="brand"><?= e($CFG['site_name'] ?? 'راداربان') ?> <span class="live" id="live" title="به‌روزرسانی خودکار">●</span></div>
  <nav>
    <?php if ((int)$u['is_admin'] === 1): ?><a href="admin.php">مدیریت</a><?php endif; ?>
    <span class="muted"><?= e($u['name'] ?: $u['email']) ?></span>
    <a href="logout.php">خروج</a>
  </nav>
</header>
<?php if (!$ok): ?>
<main class="auth"><div class="box">
  <h2>اشتراک فعال ندارید</h2>
  <p><?= e($CFG['contact_text'] ?? '') ?></p>
  <?php if ($u['sub_until']): ?><p class="muted">اشتراک قبلی شما تمام شده است.</p><?php endif; ?>
</div></main>
<?php else: ?>
<?php if ((int)$u['is_admin'] !== 1 && $u['sub_until']): ?>
  <p class="subline muted">اشتراک تا <?= e(jdate_teh($u['sub_until'])) ?></p>
<?php endif; ?>
<div class="tabs">
  <button class="tab on" data-kind="card">اخبار</button>
  <button class="tab" data-kind="review">برایند ۶ ساعته</button>
</div>
<div class="filters" id="filters">
  <input type="search" id="q" placeholder="جستجو: کد خبر، نماد یا متن…">
  <select id="line"><option value="">همهٔ لاین‌ها</option></select>
  <select id="sig">
    <option value="">همهٔ سیگنال‌ها</option>
    <option value="dir">فقط صعودی/نزولی</option>
    <option value="up">صعودی</option>
    <option value="down">نزولی</option>
  </select>
  <select id="imp">
    <option value="0">هر اهمیتی</option>
    <option value="41">مهم به بالا (۴۱+)</option>
    <option value="61">بسیار مهم به بالا (۶۱+)</option>
  </select>
  <label class="chk"><input type="checkbox" id="sound"> صدای خبر جدید</label>
</div>
<p class="disclaimer">محتوای این پنل تحلیل خودکار خبر است و توصیهٔ سرمایه‌گذاری نیست. مسئولیت هر معامله با خود شماست.</p>
<main id="feed" class="feed" aria-live="polite"></main>
<div class="more"><button id="older">خبرهای قدیمی‌تر</button></div>
<script src="assets/app.js?v=1"></script>
<?php endif; ?>
<?php page_foot();
