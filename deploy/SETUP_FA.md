# راه‌اندازی راداربان (فاز آزمایشی)

ساختار:
سرور آلمان (جمع‌آوری خبر + سرویس MCP + ارسال به پنل) ⇄ Claude (کانکتور سفارشی) → هاست cPanel (radarban.com/panel)

> هیچ رمز، کلید یا آدرس مخفی را در چت نفرستید. همه را خودتان روی سرور / هاست وارد می‌کنید.

## ۱) پنل روی هاست cPanel
1. در cPanel → **MySQL Databases**: یک دیتابیس و یک کاربر بسازید، کاربر را با «ALL PRIVILEGES» به دیتابیس وصل کنید.
2. در **phpMyAdmin** دیتابیس را انتخاب کنید → Import → فایل `panel/install/schema.sql`.
3. محتوای پوشهٔ `panel/` را در `public_html/panel/` آپلود کنید (File Manager → Upload → فایل zip → Extract).
4. فایل `config.sample.php` را کپی کنید به `public_html/panel/inc/config.php` و پر کنید:
   * `db_dsn` / `db_user` / `db_pass` (نام‌ها در cPanel معمولاً با پیشوند نام کاربری هاست هستند)
   * `ingest_secret`: یک رشتهٔ تصادفی بلند (حداقل ۳۲ کاراکتر، فقط حروف و عدد). همین را بعداً روی سرور هم می‌گذارید.
   * `relay_url`: `https://mcp.radarban.com` (مسیر پشتیبان دریافت خبر)
5. نسخهٔ PHP هاست را از **Select PHP Version** روی ۸ یا بالاتر بگذارید (افزونهٔ pdo_mysql روشن باشد).
6. SSL دامنه (AutoSSL / Let's Encrypt) فعال باشد.
7. `https://radarban.com/panel/register.php` → اولین حسابی که می‌سازید **مدیر** می‌شود.
8. cPanel → **Cron Jobs** → هر دقیقه (`* * * * *`):
   `php /home/نام‌کاربری‌هاست/public_html/panel/cron/pull.php >/dev/null 2>&1`
   (مسیر پشتیبان: اگر ارسال از سرور به هاست مختل شد، خود هاست خبرها را از سرور می‌گیرد.)

## ۲) DNS
در cPanel → **Zone Editor** برای radarban.com یک رکورد A بسازید:
`mcp` → IP سرور آلمان  (نتیجه: mcp.radarban.com)

## ۳) سرور آلمان (Ubuntu 22.04 یا 24.04)
با SSH وارد شوید و:
```
curl -fsSL https://raw.githubusercontent.com/mhmdshahi/news-center/main/deploy/install.sh -o install.sh
sudo bash install.sh mcp.radarban.com
sudo nano /etc/radarban.env      # PANEL_SECRET = همان ingest_secret پنل
sudo systemctl start radar-push.service
```
آخر خروجی نصب، **آدرس کانکتور** را نشان می‌دهد (شامل رمز مخفی). آن را فقط در مرحلهٔ ۴ استفاده کنید.

بررسی وضعیت: `systemctl list-timers 'radar-*'` و `journalctl -u radar-mcp -n 50`

## ۴) وصل کردن به Claude
claude.ai → Settings → Connectors → **Add custom connector** → نام: Radarban، آدرس: همان آدرس کانکتور.
بعد در چت بگویید «کانکتور وصل شد» تا تست و راندها را شروع کنم.

## تحویل خبر به پنل (بدون از دست رفتن)
* هر کارت یک‌بار روی سرور آماده و در **صف** گذاشته می‌شود و از دو مسیر تحویل داده می‌شود:
  ارسال سرور → هاست (هر ۳۰ ثانیه) و دریافت هاست ← سرور (کرون هر دقیقه). هر کدام زودتر کار کند.
* اگر هر دو مسیر قطع شوند (مثلاً اینترنت ملی)، صف روی سرور می‌ماند و بعد از وصل شدن به ترتیب تحویل می‌شود.
  تحلیل و جمع‌آوری هیچ‌وقت منتظر این بخش نمی‌مانند.
* پنل وضعیت ارتباط را به کاربران نشان می‌دهد؛ اگر بیش از ۱۰ دقیقه قطع باشد و تلگرام تنظیم شده باشد، به شما هشدار می‌رسد.
* جبران اشتراک بعد از قطعی: صفحهٔ مدیریت → «جبران قطعی».

## بعد از راه‌اندازی
* جمع‌آوری هر ۵ دقیقه، ارسال به پنل هر دقیقه، برایند ۶ ساعته ساعت‌های ۰۰:۰۳ / ۰۶:۰۳ / ۱۲:۰۳ / ۱۸:۰۳ UTC روی خود سرور.
* اگر بعداً تلگرام را هم خواستید: `TELEGRAM_BOT_TOKEN` و `TELEGRAM_CHAT_ID` در `/etc/radarban.env`.
* به‌روزرسانی کد: دوباره `sudo bash install.sh mcp.radarban.com` (داده‌ها و رمزها حفظ می‌شوند).
