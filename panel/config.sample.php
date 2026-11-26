<?php
// Copy this file to inc/config.php on the host and fill in the values.
// inc/ is blocked from the web by inc/.htaccess. Never commit the real config.php.
return [
    'db_dsn'        => 'mysql:host=localhost;dbname=CPANELUSER_radarban;charset=utf8mb4',
    'db_user'       => 'CPANELUSER_radarban',
    'db_pass'       => 'CHANGE_ME',
    // Long random string (≥ 32 chars). The same value goes into PANEL_SECRET on the VPS.
    'ingest_secret' => 'CHANGE_ME_TO_A_LONG_RANDOM_STRING',
    'site_name'     => 'راداربان',
    // Shown to users without an active subscription (how to buy).
    'contact_text'  => 'برای خرید اشتراک با پشتیبانی تماس بگیرید.',
    // Allow new sign-ups (the first account becomes admin).
    'allow_register' => true,
];
