<?php
declare(strict_types=1);
/* ═══════════════════════════════════════════════════════════════════
   MedAlert — لوحة الصحة والسلامة
   مشروع طلابي · PHP 8.0+ · تخزين في ملف JSON · بدون قاعدة بيانات
   ═══════════════════════════════════════════════════════════════════ */

const DATA_FILE  = __DIR__ . '/data.json';
const UPLOAD_DIR = __DIR__ . '/uploads';
const MAX_UPLOAD = 4194304;          // 4 ميجابايت
const MAX_TRIES  = 6;                // محاولات دخول فاشلة قبل التهدئة
const LOCK_SECS  = 120;              // مدة التهدئة بالثواني

/* ── وضع الموجِّه لـ php -S ────────────────────────────────────────
   شغّله هكذا:  php -S localhost:8000 index.php
   عندها تمرّ كل الطلبات من هنا، فنمنع تنزيل data.json مباشرةً.
   لا نستخدم `return false` إلا لملفات uploads بأسماء آمنة — لأن
   إرجاع false لملف .php يجعل السيرفر يعرض شيفرته المصدرية كنص. */
if (PHP_SAPI === 'cli-server') {
    $reqPath = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH) ?: '/';
    if (preg_match('~^/uploads/[A-Za-z0-9._-]{1,64}$~', $reqPath) && is_file(__DIR__ . $reqPath)) {
        return false;                       // صورة مرفوعة — دع السيرفر يخدمها
    }
    if ($reqPath !== '/' && $reqPath !== '/index.php') {
        http_response_code(404);
        exit('Not found');                  // أي شيء آخر (data.json، README…) ممنوع
    }
}

session_start([
    'cookie_httponly' => true,
    'cookie_samesite' => 'Lax',
    'use_strict_mode' => true,
]);

/* ═══════════ 1. التخزين ═══════════ */

function db_load(): array {
    if (!is_file(DATA_FILE)) {
        http_response_code(500);
        exit('data.json غير موجود بجانب index.php');
    }
    $raw = file_get_contents(DATA_FILE);
    $db  = json_decode((string)$raw, true);
    if (!is_array($db)) {
        http_response_code(500);
        exit('data.json تالف — تعذّر تحليله كـ JSON');
    }
    return db_seed($db);
}

function db_save(array $db): void {
    $json = json_encode($db, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    if ($json === false) return;
    // كتابة ذرّية: ملف مؤقت ثم إعادة تسمية، حتى لا يتلف الملف عند التزامن
    $tmp = DATA_FILE . '.tmp' . getmypid();
    if (file_put_contents($tmp, $json, LOCK_EX) !== false) {
        @rename($tmp, DATA_FILE);
    }
}

/** أول تشغيل: يشفّر كلمات المرور ويحوّل الفروق الزمنية إلى تواريخ حقيقية. */
function db_seed(array $db): array {
    if (!empty($db['seeded'])) return $db;

    foreach ($db['users'] as &$u) {
        if (isset($u['pass_plain'])) {
            $u['pass'] = password_hash((string)$u['pass_plain'], PASSWORD_DEFAULT);
            unset($u['pass_plain']);
        }
    }
    unset($u);

    $now = time();
    foreach ($db['alerts'] as &$a) {
        if (isset($a['mins_ago'])) { $a['at'] = $now - ((int)$a['mins_ago'] * 60); unset($a['mins_ago']); }
    }
    unset($a);

    foreach ($db['kits'] as &$k) {
        if (isset($k['exp_days'])) { $k['exp'] = $now + ((int)$k['exp_days'] * 86400); unset($k['exp_days']); }
    }
    unset($k);

    $db['seeded'] = true;
    db_save($db);
    return $db;
}

/* ═══════════ 2. المصادقة والحماية ═══════════ */

function csrf(): string {
    if (empty($_SESSION['csrf'])) $_SESSION['csrf'] = bin2hex(random_bytes(32));
    return $_SESSION['csrf'];
}
function csrf_ok(): bool {
    return isset($_POST['csrf'], $_SESSION['csrf'])
        && hash_equals($_SESSION['csrf'], (string)$_POST['csrf']);
}
function me(): ?array   { return $_SESSION['user'] ?? null; }
function is_admin(): bool { return (me()['role'] ?? '') === 'admin'; }

function flash(string $type, string $title, string $body = ''): void {
    $_SESSION['flash'] = ['type' => $type, 'title' => $title, 'body' => $body];
}
function take_flash(): ?array {
    $f = $_SESSION['flash'] ?? null;
    unset($_SESSION['flash']);
    return $f;
}
/* `void` وليس `never` — حتى يبقى الحد الأدنى PHP 8.0 لا 8.1 */
function redirect(string $to): void {
    header('Location: ' . $to, true, 303);
    exit;
}

/* ═══════════ 3. أدوات العرض ═══════════ */

function h(?string $s): string {
    return htmlspecialchars((string)$s, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function rel_time(int $ts): string {
    $m = max(0, (int)round((time() - $ts) / 60));
    if ($m < 1)  return 'الآن';
    if ($m < 60) return match (true) {
        $m === 1 => 'قبل دقيقة',
        $m === 2 => 'قبل دقيقتين',
        $m < 11  => "قبل $m دقائق",
        default  => "قبل $m دقيقة",
    };
    $hr = (int)round($m / 60);
    if ($hr < 24) return match (true) {
        $hr === 1 => 'قبل ساعة',
        $hr === 2 => 'قبل ساعتين',
        $hr < 11  => "قبل $hr ساعات",
        default   => "قبل $hr ساعة",
    };
    $d = (int)round($hr / 24);
    return match (true) {
        $d === 1 => 'أمس',
        $d === 2 => 'قبل يومين',
        $d < 11  => "قبل $d أيام",
        default  => "قبل $d يومًا",
    };
}

function exp_days(int $ts): int   { return (int)round(($ts - time()) / 86400); }
function exp_state(int $ts): string {
    $d = exp_days($ts);
    return $d < 0 ? 'crit' : ($d <= 60 ? 'warn' : 'ok');
}
function exp_text(int $ts): string {
    $d = exp_days($ts);
    return match (true) {
        $d < 0   => 'منتهية منذ ' . abs($d) . ' يومًا',
        $d === 0 => 'تنتهي اليوم',
        $d === 1 => 'تنتهي غدًا',
        $d === 2 => 'تنتهي بعد يومين',
        $d < 11  => "تنتهي بعد $d أيام",
        default  => "تنتهي بعد $d يومًا",
    };
}

const TYPES = [
    'injury'   => ['إصابة',        'i-drop'],
    'fainting' => ['إغماء',        'i-user'],
    'supplies' => ['نقص مستلزمات', 'i-box'],
    'heat'     => ['إجهاد حراري',  'i-heat'],
    'other'    => ['أخرى',         'i-dots'],
];
const SEVS = [
    'critical' => ['حرجة',   'i-alert'],
    'high'     => ['عالية',  'i-warn'],
    'medium'   => ['متوسطة', 'i-dots'],
    'low'      => ['منخفضة', 'i-check'],
];
const STATES = [
    'open'     => ['مفتوح',        'i-alert'],
    'progress' => ['قيد المعالجة', 'i-clock'],
    'closed'   => ['مغلق',         'i-check'],
];

function ico(string $n): string { return '<svg><use href="#' . h($n) . '"/></svg>'; }
function chip(string $cls, string $icon, string $label): string {
    return '<span class="chip ' . h($cls) . '">' . ico($icon) . h($label) . '</span>';
}
function sev_chip(string $s): string  { return chip("s-$s",  SEVS[$s][1]   ?? 'i-dots', SEVS[$s][0]   ?? $s); }
function st_chip(string $s): string   { return chip("t-$s",  STATES[$s][1] ?? 'i-dots', STATES[$s][0] ?? $s); }
function ty_chip(string $t): string   { return chip('chip-plain', TYPES[$t][1] ?? 'i-dots', TYPES[$t][0] ?? $t); }

/* ═══════════ 4. معالجة الطلبات ═══════════ */

$db     = db_load();
$action = $_POST['action'] ?? '';

/* ---- تسجيل الدخول ---- */
if ($action === 'login') {
    if (!csrf_ok()) { flash('err', 'انتهت صلاحية الجلسة', 'أعد المحاولة.'); redirect('?'); }

    $tries = $_SESSION['tries'] ?? 0;
    $until = $_SESSION['locked_until'] ?? 0;
    if ($tries >= MAX_TRIES && time() < $until) {
        flash('err', 'محاولات كثيرة', 'انتظر ' . ($until - time()) . ' ثانية قبل إعادة المحاولة.');
        redirect('?');
    }

    $user = trim((string)($_POST['username'] ?? ''));
    $pass = (string)($_POST['password'] ?? '');
    $found = null;
    foreach ($db['users'] as $u) {
        if (strcasecmp($u['username'], $user) === 0) { $found = $u; break; }
    }

    // نتحقق دائمًا من هاش ما، حتى لا يكشف فرق التوقيت وجود المستخدم
    $hash = $found['pass'] ?? '$2y$12$invalidinvalidinvalidinvalidinvalidinvalidinvalidinvalidinv';
    if ($found && password_verify($pass, $hash)) {
        session_regenerate_id(true);
        $_SESSION['user'] = ['id' => $found['id'], 'username' => $found['username'],
                             'name' => $found['name'], 'role' => $found['role']];
        unset($_SESSION['tries'], $_SESSION['locked_until']);
        flash('ok', 'أهلًا ' . $found['name'], 'تم تسجيل الدخول بنجاح.');
        redirect('?p=home');
    }

    $_SESSION['tries'] = $tries + 1;
    if ($_SESSION['tries'] >= MAX_TRIES) $_SESSION['locked_until'] = time() + LOCK_SECS;
    flash('err', 'بيانات الدخول غير صحيحة', 'تحقق من اسم المستخدم وكلمة المرور.');
    redirect('?');
}

/* ---- تسجيل الخروج ---- */
if ($action === 'logout') {
    if (csrf_ok()) {
        $_SESSION = [];
        session_destroy();
        session_start();
        flash('ok', 'تم تسجيل الخروج', 'إلى اللقاء.');
    }
    redirect('?');
}

/* ---- كل ما بعده يتطلب تسجيل دخول ---- */
$ME = me();

if ($ME && $action === 'create_alert') {
    if (!csrf_ok()) { flash('err', 'انتهت صلاحية الجلسة', 'أعد إرسال البلاغ.'); redirect('?p=new'); }

    $title = trim((string)($_POST['title'] ?? ''));
    $loc   = trim((string)($_POST['loc'] ?? ''));
    if ($title === '' || $loc === '') {
        flash('err', 'حقول ناقصة', 'العنوان والموقع إلزاميان.');
        redirect('?p=new');
    }

    /* رفع الصورة — التحقق من المحتوى الفعلي لا من الامتداد */
    $photo = '';
    if (!empty($_FILES['photo']['tmp_name']) && ($_FILES['photo']['error'] ?? 1) === UPLOAD_ERR_OK) {
        $f = $_FILES['photo'];
        $allowed = [IMAGETYPE_JPEG => 'jpg', IMAGETYPE_PNG => 'png',
                    IMAGETYPE_GIF  => 'gif', IMAGETYPE_WEBP => 'webp'];
        $info = @getimagesize($f['tmp_name']);
        if ($f['size'] > MAX_UPLOAD) {
            flash('err', 'الصورة كبيرة جدًا', 'الحد الأقصى ٤ ميجابايت.');
            redirect('?p=new');
        }
        if ($info && isset($allowed[$info[2]])) {
            if (!is_dir(UPLOAD_DIR)) @mkdir(UPLOAD_DIR, 0775, true);
            $name = bin2hex(random_bytes(12)) . '.' . $allowed[$info[2]];
            if (move_uploaded_file($f['tmp_name'], UPLOAD_DIR . '/' . $name)) {
                $photo = 'uploads/' . $name;
            }
        } else {
            flash('err', 'ملف غير مدعوم', 'اختر صورة JPG أو PNG أو GIF أو WEBP.');
            redirect('?p=new');
        }
    }

    $ty = (string)($_POST['type']   ?? 'other');
    $sv = (string)($_POST['sev']    ?? 'medium');
    $st = (string)($_POST['status'] ?? 'open');
    $id = 'AL-' . (int)($db['seq'] ?? 1000);
    $db['seq'] = (int)($db['seq'] ?? 1000) + 1;

    array_unshift($db['alerts'], [
        'id' => $id,
        't'  => mb_substr($title, 0, 160),
        'lc' => mb_substr($loc, 0, 160),
        'ty' => isset(TYPES[$ty])  ? $ty : 'other',
        'sv' => isset(SEVS[$sv])   ? $sv : 'medium',
        'st' => isset(STATES[$st]) ? $st : 'open',
        'at' => time(),
        'by' => trim((string)($_POST['by'] ?? '')) ?: $ME['name'],
        'nt' => mb_substr(trim((string)($_POST['note'] ?? '')), 0, 1200),
        'ph' => $photo,
    ]);
    db_save($db);
    flash('ok', "تم تسجيل البلاغ $id", 'يظهر الآن في السجل وفي لوحة الرئيسية.');
    redirect('?p=alerts');
}

if ($ME && $action === 'close_alert') {
    if (!csrf_ok())  { redirect('?p=alerts'); }
    if (!is_admin()) { flash('err', 'صلاحية غير كافية', 'إغلاق البلاغات متاح لمنسّق السلامة فقط.'); redirect('?p=alerts'); }

    $id = (string)($_POST['id'] ?? '');
    foreach ($db['alerts'] as &$a) {
        if ($a['id'] === $id) {
            $a['st'] = 'closed';
            $a['closed_by'] = $ME['name'];
            $a['closed_at'] = time();
            break;
        }
    }
    unset($a);
    db_save($db);
    flash('ok', "أُغلق البلاغ $id", 'يبقى محفوظًا في السجل للمراجعة.');
    redirect('?p=alerts' . (isset($_POST['q_st']) ? '&st=' . urlencode((string)$_POST['q_st']) : ''));
}

if ($ME && $action === 'restock') {
    if (!csrf_ok())  { redirect('?p=kits'); }
    if (!is_admin()) { flash('err', 'صلاحية غير كافية', 'تحديث المخزون متاح لمنسّق السلامة فقط.'); redirect('?p=kits'); }

    $kid = (string)($_POST['kit'] ?? '');
    $idx = (int)($_POST['idx'] ?? -1);
    $qty = max(1, min(999, (int)($_POST['qty'] ?? 1)));
    foreach ($db['kits'] as &$k) {
        if ($k['id'] === $kid && isset($k['items'][$idx])) {
            $k['items'][$idx]['q']  = $qty;
            $k['items'][$idx]['ok'] = true;
            $name = $k['items'][$idx]['n'];
            db_save($db);
            flash('ok', 'تم تحديث المخزون', "أُعيدت تعبئة «$name» في الحقيبة $kid.");
            break;
        }
    }
    unset($k);
    redirect('?p=kits');
}

/* ═══════════ 5. تجهيز بيانات العرض ═══════════ */

$flash = take_flash();
$page  = (string)($_GET['p'] ?? 'home');
if (!in_array($page, ['home', 'new', 'alerts', 'kits', 'emergency'], true)) $page = 'home';

$alerts = $db['alerts'];
usort($alerts, fn(array $a, array $b): int => ($b['at'] ?? 0) <=> ($a['at'] ?? 0));

$kOpen = count(array_filter($alerts, fn($a) => ($a['st'] ?? '') === 'open'));
$kCrit = count(array_filter($alerts, fn($a) => ($a['sv'] ?? '') === 'critical' && ($a['st'] ?? '') !== 'closed'));
$kSoon = count(array_filter($db['kits'], fn($k) => exp_state((int)$k['exp']) !== 'ok'));
$kMiss = array_sum(array_map(fn($k) => count(array_filter($k['items'], fn($i) => empty($i['ok']))), $db['kits']));

/* مرشّحات السجل */
$fSt = (string)($_GET['st'] ?? '');
$fSv = (string)($_GET['sv'] ?? '');
$fQ  = trim((string)($_GET['q'] ?? ''));
$rows = array_values(array_filter($alerts, function ($a) use ($fSt, $fSv, $fQ) {
    if ($fSt !== '' && ($a['st'] ?? '') !== $fSt) return false;
    if ($fSv !== '' && ($a['sv'] ?? '') !== $fSv) return false;
    if ($fQ !== '') {
        $hay = mb_strtolower(($a['t'] ?? '') . ' ' . ($a['lc'] ?? '') . ' ' . ($a['id'] ?? ''));
        if (!str_contains($hay, mb_strtolower($fQ))) return false;
    }
    return true;
}));

$CSRF = csrf();
?>
<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MedAlert — لوحة الصحة والسلامة</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@300;400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
/* ══════════ TOKENS ══════════ */
:root{
  color-scheme: light;
  --ground:#F2F6F5; --surface:#FFFFFF; --surface-2:#EAF0EF; --surface-3:#DFE8E6;
  --line:#D3DEDB; --line-soft:#E4EBEA;
  --ink:#0C1917; --ink-2:#2D3E3A; --muted:#566863; --faint:#7A8C87;
  --teal:#0B7268; --teal-hi:#095E56; --teal-tint:#E0F0EE; --on-teal:#FFFFFF;
  --crit:#B62F22; --crit-bg:#FBE8E5; --crit-line:#EEC5BE;
  --high:#B4590A; --high-bg:#FCEEDF; --high-line:#EFD2B2;
  --warn:#8E6A05; --warn-bg:#FAF1D6; --warn-line:#E8D79E;
  --good:#1C6E45; --good-bg:#E2F1E9; --good-line:#BCDDCB;
  --info:#175C88; --info-bg:#E2EEF6; --info-line:#BDD7E8;
  --sh-1:0 1px 2px rgba(12,25,23,.07);
  --sh-2:0 1px 2px rgba(12,25,23,.06), 0 10px 28px -16px rgba(12,25,23,.28);
  --r-s:6px; --r-m:10px; --r-l:16px; --shell:1120px; --navh:66px;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    color-scheme: dark;
    --ground:#0A1211; --surface:#121D1B; --surface-2:#1A2624; --surface-3:#22302D;
    --line:#293835; --line-soft:#1E2C29;
    --ink:#E4EDEA; --ink-2:#C3D2CE; --muted:#93A7A1; --faint:#71857F;
    --teal:#46BFAF; --teal-hi:#66D2C3; --teal-tint:#0F302C; --on-teal:#04201C;
    --crit:#F28A7C; --crit-bg:#331816; --crit-line:#5C2A24;
    --high:#EDA155; --high-bg:#31210E; --high-line:#573C1B;
    --warn:#E0C04A; --warn-bg:#2C2710; --warn-line:#4E451C;
    --good:#5FC78C; --good-bg:#0E2D1E; --good-line:#204830;
    --info:#78B9E4; --info-bg:#0D2536; --info-line:#1D4157;
    --sh-1:0 1px 2px rgba(0,0,0,.5);
    --sh-2:0 1px 2px rgba(0,0,0,.45), 0 10px 28px -16px rgba(0,0,0,.8);
  }
}
*,*::before,*::after{box-sizing:border-box}
body{background:var(--ground);color:var(--ink);
  font-family:"IBM Plex Sans Arabic",system-ui,-apple-system,"Segoe UI",sans-serif;
  font-size:15px;line-height:1.7;margin:0;-webkit-font-smoothing:antialiased;
  padding-block-end:calc(var(--navh) + 20px);}
h1,h2,h3,h4{margin:0;line-height:1.35;font-weight:600;text-wrap:balance}
p{margin:0} ul,ol{margin:0;padding-inline-start:1.3em} li{margin-block:2px}
a{color:var(--teal)} img{max-width:100%}
button,input,select,textarea{font:inherit;color:inherit}
:focus-visible{outline:2px solid var(--teal);outline-offset:2px;border-radius:4px}
svg{display:block}
[hidden]{display:none!important}
.mono{font-family:"IBM Plex Mono",ui-monospace,monospace;font-variant-numeric:tabular-nums}
.shell{max-width:var(--shell);margin-inline:auto;padding-inline:16px}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}
.eyebrow{font-size:10.5px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:var(--faint);font-family:"IBM Plex Mono",monospace}
@media (prefers-reduced-motion:reduce){*{animation-duration:.01ms!important;transition-duration:.01ms!important}}

/* ══════════ LOGIN ══════════ */
.login-wrap{min-height:100vh;display:grid;place-items:center;padding:24px 16px}
.login{width:100%;max-width:400px}
.login-brand{display:flex;flex-direction:column;align-items:center;gap:11px;margin-block-end:22px;text-align:center}
.login-brand .mark{width:52px;height:52px;border-radius:14px;background:var(--teal);color:var(--on-teal);display:grid;place-items:center}
.login-brand .mark svg{width:28px;height:28px}
.login-brand b{font-size:24px;font-weight:700;letter-spacing:-.02em;font-family:"IBM Plex Mono",monospace;display:block}
.login-brand small{font-size:12.5px;color:var(--muted)}
.login-card{background:var(--surface);border:1px solid var(--line);border-radius:var(--r-l);padding:22px;box-shadow:var(--sh-2)}
.login-card h1{font-size:17px;margin-block-end:16px}
.demo-box{margin-block-start:16px;padding:12px 14px;background:var(--surface-2);border:1px dashed var(--line);border-radius:var(--r-m);font-size:12.5px;color:var(--muted)}
.demo-box b{display:block;color:var(--ink);font-size:12.5px;margin-block-end:6px}
.demo-box table{width:100%;border-collapse:collapse;font-size:12px}
.demo-box td{padding:3px 0}
.demo-box td:last-child{text-align:end}
.demo-box code{font-family:"IBM Plex Mono",monospace;background:var(--surface);padding:1px 6px;border-radius:4px;border:1px solid var(--line);font-size:11.5px}

/* ══════════ HEADER ══════════ */
.top{position:sticky;top:0;z-index:60;background:var(--surface);border-block-end:1px solid var(--line)}
.top-in{display:flex;align-items:center;gap:12px;min-height:60px;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:10px;margin-inline-end:auto;min-width:0}
.mark{width:36px;height:36px;flex:0 0 36px;border-radius:9px;background:var(--teal);color:var(--on-teal);display:grid;place-items:center}
.mark svg{width:20px;height:20px}
.brand b{display:block;font-size:16px;font-weight:700;letter-spacing:-.02em;line-height:1.15;font-family:"IBM Plex Mono",monospace}
.brand small{display:block;font-size:11px;color:var(--faint);line-height:1.3}
.who{display:flex;align-items:center;gap:9px}
.who .av{width:32px;height:32px;flex:0 0 32px;border-radius:50%;background:var(--teal-tint);color:var(--teal);display:grid;place-items:center;font-weight:700;font-size:13px}
.who .t{line-height:1.25;min-width:0}
.who .t b{display:block;font-size:12.5px;font-weight:600;white-space:nowrap}
.who .t span{font-size:10.5px;color:var(--faint)}
.role{font-size:10px;font-weight:600;letter-spacing:.05em;padding:2px 7px;border-radius:99px;border:1px solid}
.role-admin{background:var(--teal-tint);color:var(--teal);border-color:var(--teal)}
.role-staff{background:var(--surface-2);color:var(--muted);border-color:var(--line)}
.desknav{display:none;gap:2px}
.desknav a{display:flex;align-items:center;gap:7px;padding:9px 13px;border-radius:var(--r-s);
  font-size:13.5px;font-weight:500;color:var(--muted);text-decoration:none;transition:background .15s,color .15s}
.desknav a svg{width:16px;height:16px;flex:0 0 16px}
.desknav a:hover{background:var(--surface-2);color:var(--ink)}
.desknav a[aria-current="page"]{background:var(--teal-tint);color:var(--teal);font-weight:600}
.botnav{position:fixed;inset-inline:0;bottom:0;z-index:60;height:var(--navh);background:var(--surface);
  border-block-start:1px solid var(--line);display:grid;grid-template-columns:repeat(5,1fr);
  padding-block-end:env(safe-area-inset-bottom,0)}
.botnav a{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;
  text-decoration:none;color:var(--faint);font-size:10.5px;font-weight:500;position:relative}
.botnav a svg{width:21px;height:21px}
.botnav a[aria-current="page"]{color:var(--teal);font-weight:600}
.botnav a[aria-current="page"]::before{content:"";position:absolute;top:0;inset-inline:22%;height:2.5px;background:var(--teal);border-radius:0 0 3px 3px}

/* ══════════ LAYOUT ══════════ */
main{padding-block:22px}
.phead{margin-block-end:18px}
.phead h1{font-size:22px;letter-spacing:-.02em}
.phead p{font-size:13.5px;color:var(--muted);margin-block-start:4px;max-width:62ch}
.sec{margin-block-end:26px}
.sec-t{display:flex;align-items:baseline;gap:10px;margin-block-end:11px;flex-wrap:wrap}
.sec-t h2{font-size:16px}
.kpis{display:grid;grid-template-columns:repeat(2,1fr);gap:11px}
.kpi{background:var(--surface);border:1px solid var(--line);border-radius:var(--r-m);padding:14px 15px;
  display:flex;flex-direction:column;gap:2px;position:relative;overflow:hidden}
.kpi::before{content:"";position:absolute;inset-block:0;inset-inline-start:0;width:3px;background:var(--kc,var(--line))}
.kpi .lab{display:flex;align-items:center;gap:6px;font-size:12.5px;color:var(--muted);font-weight:500}
.kpi .lab svg{width:14px;height:14px;flex:0 0 14px;color:var(--kc,var(--muted))}
.kpi .val{font-size:34px;font-weight:700;line-height:1.1;letter-spacing:-.03em;font-variant-numeric:tabular-nums}
.kpi .sub{font-size:11px;color:var(--faint);font-family:"IBM Plex Mono",monospace;letter-spacing:.05em;text-transform:uppercase}
.k-crit{--kc:var(--crit)} .k-warn{--kc:var(--high)} .k-good{--kc:var(--good)} .k-teal{--kc:var(--teal)}

/* ══════════ CHIPS ══════════ */
.chip{display:inline-flex;align-items:center;gap:5px;padding:3px 9px;border-radius:99px;
  font-size:11.5px;font-weight:600;border:1px solid;white-space:nowrap;line-height:1.5}
.chip svg{width:12px;height:12px;flex:0 0 12px}
.s-critical{background:var(--crit-bg);color:var(--crit);border-color:var(--crit-line)}
.s-high{background:var(--high-bg);color:var(--high);border-color:var(--high-line)}
.s-medium{background:var(--warn-bg);color:var(--warn);border-color:var(--warn-line)}
.s-low{background:var(--good-bg);color:var(--good);border-color:var(--good-line)}
.t-open{background:var(--crit-bg);color:var(--crit);border-color:var(--crit-line)}
.t-progress{background:var(--info-bg);color:var(--info);border-color:var(--info-line)}
.t-closed{background:var(--surface-2);color:var(--muted);border-color:var(--line)}
.chip-plain{background:var(--surface-2);color:var(--muted);border-color:var(--line);font-weight:500}

/* ══════════ BUTTONS ══════════ */
.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;min-height:44px;padding:10px 18px;
  border-radius:var(--r-s);cursor:pointer;border:1px solid var(--line);background:var(--surface);color:var(--ink);
  font-size:14px;font-weight:600;transition:background .15s,border-color .15s,transform .06s;text-decoration:none}
.btn:hover{background:var(--surface-2)} .btn:active{transform:translateY(1px)}
.btn svg{width:17px;height:17px;flex:0 0 17px}
.btn-primary{background:var(--teal);border-color:var(--teal);color:var(--on-teal)}
.btn-primary:hover{background:var(--teal-hi);border-color:var(--teal-hi)}
.btn-danger{background:var(--crit);border-color:var(--crit);color:#fff}
.btn-danger:hover{filter:brightness(1.07);background:var(--crit)}
.btn-lg{min-height:56px;font-size:16px;padding:14px 26px;border-radius:var(--r-m);width:100%}
.btn-sm{min-height:34px;padding:5px 11px;font-size:12.5px;font-weight:500}
.btn-sm svg{width:14px;height:14px;flex:0 0 14px}
.btn:disabled{opacity:.45;cursor:not-allowed} .btn:disabled:hover{background:var(--surface)}

/* ══════════ ALERT CARDS ══════════ */
.alist{display:grid;gap:10px}
.al{background:var(--surface);border:1px solid var(--line);border-radius:var(--r-m);padding:13px 15px;
  display:flex;gap:13px;align-items:flex-start;border-inline-start:4px solid var(--ac,var(--line))}
.sev-critical{--ac:var(--crit)} .sev-high{--ac:var(--high)} .sev-medium{--ac:var(--warn)} .sev-low{--ac:var(--good)}
.al-ic{width:34px;height:34px;flex:0 0 34px;border-radius:9px;display:grid;place-items:center;background:var(--surface-2);color:var(--muted)}
.al-ic svg{width:18px;height:18px}
.al-b{min-width:0;flex:1}
.al-b h3{font-size:14.5px;font-weight:600;line-height:1.45}
.al-meta{display:flex;flex-wrap:wrap;gap:5px 12px;margin-block-start:6px;font-size:12px;color:var(--muted)}
.al-meta span{display:inline-flex;align-items:center;gap:4px}
.al-meta svg{width:12px;height:12px;flex:0 0 12px;color:var(--faint)}
.al-chips{display:flex;flex-wrap:wrap;gap:6px;margin-block-start:9px}
.al-note{font-size:12.5px;color:var(--ink-2);margin-block-start:8px;padding-block-start:8px;border-block-start:1px dashed var(--line)}
.al-photo{margin-block-start:9px}
.al-photo img{max-height:150px;border-radius:var(--r-s);border:1px solid var(--line)}

/* ══════════ FORM ══════════ */
.form{display:grid;gap:15px;max-width:640px}
.f{display:grid;gap:6px}
.f>label{font-size:13px;font-weight:600;display:flex;align-items:center;gap:5px}
.f .hint{font-size:11.5px;color:var(--faint);line-height:1.5}
.req{color:var(--crit);font-weight:700}
input[type=text],input[type=password],input[type=number],select,textarea{
  width:100%;background:var(--surface);border:1px solid var(--line);border-radius:var(--r-s);
  padding:11px 12px;min-height:46px;font-size:14.5px;transition:border-color .15s,box-shadow .15s}
textarea{min-height:96px;resize:vertical;line-height:1.65}
input:focus,select:focus,textarea:focus{border-color:var(--teal);box-shadow:0 0 0 3px var(--teal-tint);outline:none}
select{appearance:none;
  background-image:linear-gradient(45deg,transparent 49%,var(--muted) 50%),linear-gradient(-45deg,transparent 49%,var(--muted) 50%);
  background-size:6px 6px,6px 6px;background-repeat:no-repeat;
  background-position:left 22px top 21px,left 16px top 21px;padding-left:38px;padding-right:12px}
input[type=file]{width:100%;padding:10px;background:var(--surface);border:1.5px dashed var(--line);border-radius:var(--r-m);font-size:13px}
input[type=file]:hover{border-color:var(--teal)}
.f2{display:grid;gap:15px}
.segs{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}
.seg{position:relative}
.seg input{position:absolute;opacity:0;pointer-events:none}
.seg span{display:flex;align-items:center;justify-content:center;gap:7px;min-height:48px;padding:8px 10px;
  border:1.5px solid var(--line);border-radius:var(--r-s);cursor:pointer;font-size:13.5px;font-weight:600;
  background:var(--surface);transition:.15s;text-align:center}
.seg span svg{width:14px;height:14px;flex:0 0 14px}
.seg input:focus-visible+span{outline:2px solid var(--teal);outline-offset:2px}
.seg input:checked+span.sv-critical{background:var(--crit-bg);border-color:var(--crit);color:var(--crit)}
.seg input:checked+span.sv-high{background:var(--high-bg);border-color:var(--high);color:var(--high)}
.seg input:checked+span.sv-medium{background:var(--warn-bg);border-color:var(--warn);color:var(--warn)}
.seg input:checked+span.sv-low{background:var(--good-bg);border-color:var(--good);color:var(--good)}

/* ══════════ TABLE ══════════ */
.tools{display:flex;gap:9px;flex-wrap:wrap;align-items:flex-end;margin-block-end:13px}
.tool{display:grid;gap:4px;flex:1 1 150px;min-width:0}
.tool label{font-size:11px;font-weight:600;color:var(--muted)}
.tool select,.tool input{min-height:42px;padding-block:8px;font-size:13.5px}
.tool select{background-position:left 20px top 19px,left 14px top 19px;padding-left:34px;padding-right:12px}
.twrap{overflow-x:auto;border:1px solid var(--line);border-radius:var(--r-m);background:var(--surface)}
table{width:100%;border-collapse:collapse;font-size:13.5px}
thead th{text-align:start;font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;
  color:var(--faint);padding:11px 13px;background:var(--surface-2);border-block-end:1px solid var(--line);white-space:nowrap}
tbody td{padding:11px 13px;border-block-start:1px solid var(--line-soft);vertical-align:middle}
tbody tr:hover{background:var(--surface-2)}
td .ttl{font-weight:600;font-size:13.5px;line-height:1.45}
td .loc{font-size:11.5px;color:var(--faint);margin-block-start:2px}
.empty{padding:44px 20px;text-align:center;color:var(--muted)}
.empty svg{width:34px;height:34px;margin-inline:auto;color:var(--faint);margin-block-end:10px}
.empty b{display:block;font-size:15px;color:var(--ink);margin-block-end:4px}
.empty p{font-size:13px;max-width:38ch;margin-inline:auto}

/* ══════════ KITS ══════════ */
.kits{display:grid;gap:13px}
.kit{background:var(--surface);border:1px solid var(--line);border-radius:var(--r-m);overflow:hidden}
.kit-h{display:flex;align-items:flex-start;gap:11px;padding:14px 15px;border-block-end:1px solid var(--line-soft)}
.kit-h .ic{width:36px;height:36px;flex:0 0 36px;border-radius:9px;display:grid;place-items:center;background:var(--teal-tint);color:var(--teal)}
.kit-h .ic svg{width:19px;height:19px}
.kit-h h3{font-size:15px}
.kit-h .sub{font-size:12px;color:var(--muted);margin-block-start:2px}
.kit-body{padding:13px 15px;display:grid;gap:13px}
.meter{display:grid;gap:5px}
.meter-top{display:flex;justify-content:space-between;align-items:baseline;font-size:12px}
.meter-top b{font-weight:600} .meter-top span{color:var(--muted);font-variant-numeric:tabular-nums}
.bar{height:7px;background:var(--surface-3);border-radius:99px;overflow:hidden}
.bar i{display:block;height:100%;border-radius:99px;background:var(--good)}
.bar.warn i{background:var(--high)} .bar.crit i{background:var(--crit)}
.items{display:flex;flex-wrap:wrap;gap:6px}
.item{display:inline-flex;align-items:center;gap:5px;padding:4px 9px;border-radius:var(--r-s);
  font-size:12px;border:1px solid var(--line);background:var(--surface-2);color:var(--ink-2)}
.item svg{width:12px;height:12px;flex:0 0 12px;color:var(--good)}
.item .q{font-family:"IBM Plex Mono",monospace;font-size:11px;color:var(--faint)}
.miss-row{display:flex;align-items:center;gap:7px;flex-wrap:wrap;padding:7px 9px;border-radius:var(--r-s);
  background:var(--crit-bg);border:1px solid var(--crit-line)}
.miss-row .nm{font-size:12.5px;font-weight:600;color:var(--crit);flex:1;min-width:90px;display:flex;align-items:center;gap:5px}
.miss-row .nm svg{width:13px;height:13px;flex:0 0 13px}
.miss-row input[type=number]{width:66px;min-height:34px;padding:4px 8px;font-size:13px;text-align:center}

/* ══════════ EMERGENCY ══════════ */
.banner{display:flex;gap:11px;align-items:flex-start;padding:13px 15px;border-radius:var(--r-m);
  background:var(--warn-bg);border:1px solid var(--warn-line);color:var(--warn)}
.banner svg{width:19px;height:19px;flex:0 0 19px;margin-block-start:2px}
.banner b{display:block;font-size:13.5px}
.banner p{font-size:12.5px;line-height:1.6;margin-block-start:2px;opacity:.92}
.calls{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}
.call{display:flex;align-items:center;gap:12px;padding:14px;border-radius:var(--r-m);background:var(--surface);
  border:1px solid var(--line);text-decoration:none;color:var(--ink);min-height:76px;transition:.15s}
.call:hover{border-color:var(--crit);background:var(--crit-bg)}
.call .ic{width:40px;height:40px;flex:0 0 40px;border-radius:10px;display:grid;place-items:center;background:var(--crit-bg);color:var(--crit)}
.call .ic svg{width:20px;height:20px}
.call .t b{display:block;font-size:13px;font-weight:600;line-height:1.35}
.call .t .n{font-family:"IBM Plex Mono",monospace;font-size:21px;font-weight:600;color:var(--crit);line-height:1.25}
.info-grid{display:grid;gap:11px}
.info{display:flex;gap:12px;align-items:flex-start;padding:14px;background:var(--surface);border:1px solid var(--line);border-radius:var(--r-m)}
.info .ic{width:36px;height:36px;flex:0 0 36px;border-radius:9px;display:grid;place-items:center;background:var(--teal-tint);color:var(--teal)}
.info .ic svg{width:18px;height:18px}
.info b{font-size:13.5px;display:block}
.info p{font-size:12.5px;color:var(--muted);margin-block-start:2px;line-height:1.6}
.fa{display:grid;gap:9px}
.fa details{background:var(--surface);border:1px solid var(--line);border-radius:var(--r-m);overflow:hidden}
.fa summary{padding:13px 15px;cursor:pointer;font-size:14px;font-weight:600;list-style:none;
  display:flex;align-items:center;gap:10px;min-height:50px}
.fa summary::-webkit-details-marker{display:none}
.fa summary::after{content:"+";margin-inline-start:auto;font-size:19px;font-weight:400;color:var(--faint);font-family:"IBM Plex Mono",monospace}
.fa details[open] summary::after{content:"−"}
.fa summary .ic{width:30px;height:30px;flex:0 0 30px;border-radius:8px;display:grid;place-items:center;background:var(--teal-tint);color:var(--teal)}
.fa summary .ic svg{width:16px;height:16px}
.fa .fb{padding:0 15px 15px;border-block-start:1px solid var(--line-soft);padding-block-start:13px}
.fa ol{font-size:13.5px;line-height:1.75;padding-inline-start:1.4em}
.fa .redline{margin-block-start:11px;padding:9px 12px;background:var(--crit-bg);border:1px solid var(--crit-line);
  border-radius:var(--r-s);font-size:12.5px;color:var(--crit);font-weight:600;display:flex;gap:8px;align-items:flex-start}
.fa .redline svg{width:15px;height:15px;flex:0 0 15px;margin-block-start:3px}

/* ══════════ FLASH ══════════ */
.flash{display:flex;gap:11px;align-items:flex-start;padding:13px 15px;border-radius:var(--r-m);margin-block-end:16px}
.flash svg{width:19px;height:19px;flex:0 0 19px;margin-block-start:1px}
.flash b{display:block;font-size:13.5px} .flash p{font-size:12.5px;opacity:.9;margin-block-start:1px}
.flash-ok{background:var(--good-bg);border:1px solid var(--good-line);color:var(--good)}
.flash-err{background:var(--crit-bg);border:1px solid var(--crit-line);color:var(--crit)}
footer{border-block-start:1px solid var(--line);margin-block-start:10px;padding-block:20px;font-size:12px;color:var(--faint);line-height:1.7}
footer b{color:var(--muted)}

/* ══════════ RESPONSIVE ══════════ */
@media (min-width:560px){
  .kpis{grid-template-columns:repeat(4,1fr)} .segs{grid-template-columns:repeat(4,1fr)}
  .f2{grid-template-columns:1fr 1fr} .calls{grid-template-columns:repeat(3,1fr)}
  .info-grid{grid-template-columns:repeat(3,1fr)}
}
@media (min-width:860px){
  body{padding-block-end:24px} .botnav{display:none} .desknav{display:flex}
  .shell{padding-inline:24px} main{padding-block:28px}
  .kits{grid-template-columns:repeat(2,1fr)} .phead h1{font-size:26px}
}
@media (max-width:719px){
  .twrap{border:0;background:transparent;overflow:visible}
  table,tbody,tr,td{display:block;width:100%}
  thead{display:none}
  tbody tr{background:var(--surface);border:1px solid var(--line);border-radius:var(--r-m);margin-block-end:10px;padding:4px 0}
  tbody tr:hover{background:var(--surface)}
  tbody td{border:0;display:flex;gap:12px;align-items:center;justify-content:space-between;padding:7px 14px}
  tbody td::before{content:attr(data-lab);font-size:11px;font-weight:600;color:var(--faint);text-transform:uppercase;letter-spacing:.05em;flex:0 0 auto}
  tbody td:first-child{border-block-end:1px solid var(--line-soft);padding-block:11px;display:block}
  tbody td:first-child::before{display:none}
  tbody td:last-child{padding-block-end:11px}
}
</style>
</head>
<body>

<svg class="sr" aria-hidden="true"><defs>
<symbol id="i-shield" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2 4 5.5v6c0 5 3.4 9.2 8 10.5 4.6-1.3 8-5.5 8-10.5v-6L12 2Z"/><path d="M12 8.5v6M9 11.5h6"/></symbol>
<symbol id="i-home" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 10.5 12 3l9 7.5"/><path d="M5.5 9.5V20h13V9.5"/><path d="M9.5 20v-6h5v6"/></symbol>
<symbol id="i-plus" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 8v8M8 12h8"/></symbol>
<symbol id="i-list" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01"/></symbol>
<symbol id="i-kit" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2.5" y="7" width="19" height="13" rx="2.5"/><path d="M8.5 7V5a1.5 1.5 0 0 1 1.5-1.5h4A1.5 1.5 0 0 1 15.5 5v2"/><path d="M12 11v5M9.5 13.5h5"/></symbol>
<symbol id="i-phone" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6.5 3.5h-2a2 2 0 0 0-2 2.2 17.5 17.5 0 0 0 15.6 15.6 2 2 0 0 0 2.2-2v-2a2 2 0 0 0-1.7-2l-2.4-.3a2 2 0 0 0-1.8.8l-.9 1.2a13.5 13.5 0 0 1-6.4-6.4l1.2-.9a2 2 0 0 0 .8-1.8l-.3-2.4a2 2 0 0 0-2-1.7Z"/></symbol>
<symbol id="i-warn" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.3 3.9 2.4 17.5A2 2 0 0 0 4.1 20.5h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4.5M12 17h.01"/></symbol>
<symbol id="i-alert" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7.5V13M12 16.5h.01"/></symbol>
<symbol id="i-check" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="m8 12.3 2.7 2.7L16 9.5"/></symbol>
<symbol id="i-clock" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5.3l3.2 1.9"/></symbol>
<symbol id="i-pin" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 21s7-5.6 7-11a7 7 0 1 0-14 0c0 5.4 7 11 7 11Z"/><circle cx="12" cy="10" r="2.6"/></symbol>
<symbol id="i-user" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="3.6"/><path d="M4.5 20a7.5 7.5 0 0 1 15 0"/></symbol>
<symbol id="i-drop" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3.2s6 6.3 6 10.2a6 6 0 0 1-12 0c0-3.9 6-10.2 6-10.2Z"/></symbol>
<symbol id="i-heat" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M19.1 4.9l-1.8 1.8M6.7 17.3l-1.8 1.8"/></symbol>
<symbol id="i-box" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.5 8.5v7.6a2 2 0 0 1-1 1.7l-6.5 3.7a2 2 0 0 1-2 0l-6.5-3.7a2 2 0 0 1-1-1.7V8.5"/><path d="m3.5 8 8.5 4.8L20.5 8 12 3.2 3.5 8Z"/><path d="M12 12.8V21"/></symbol>
<symbol id="i-dots" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M8.5 12h.01M12 12h.01M15.5 12h.01"/></symbol>
<symbol id="i-x" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 6l12 12M18 6 6 18"/></symbol>
<symbol id="i-cal" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="16" rx="2.5"/><path d="M3 10h18M8 3v4M16 3v4"/></symbol>
<symbol id="i-flag" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 21V4"/><path d="M5 5.5h11l-1.6 3.2L16 12H5"/></symbol>
<symbol id="i-hospital" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 21V8.5L12 3l8 5.5V21"/><path d="M12 9v6M9 12h6M9.5 21v-4h5v4"/></symbol>
<symbol id="i-inbox" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 13.5h4l1.5 3h6l1.5-3h4"/><path d="M5.6 5.2 3.5 13.5v4a2 2 0 0 0 2 2h13a2 2 0 0 0 2-2v-4l-2.1-8.3a2 2 0 0 0-1.9-1.5H7.5a2 2 0 0 0-1.9 1.5Z"/></symbol>
<symbol id="i-fire" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22a7 7 0 0 0 7-7c0-5-4-6-4-10 0 0-3 1.5-3 5 0 1.5-1 2-1.6 1.2C9.8 10.4 9.5 9.5 9.5 9.5S5 12 5 15a7 7 0 0 0 7 7Z"/></symbol>
<symbol id="i-lock" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="10.5" width="16" height="10.5" rx="2.5"/><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/></symbol>
<symbol id="i-out" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 4.5h3.5A1.5 1.5 0 0 1 20 6v12a1.5 1.5 0 0 1-1.5 1.5H15"/><path d="M10 8.5 6.5 12 10 15.5M6.5 12H16"/></symbol>
</defs></svg>

<?php if (!$ME): /* ═══════════ صفحة تسجيل الدخول ═══════════ */ ?>
<div class="login-wrap">
  <div class="login">
    <div class="login-brand">
      <div class="mark"><svg><use href="#i-shield"/></svg></div>
      <div><b>MedAlert</b><small>نظام بلاغات الصحة والسلامة</small></div>
    </div>

    <?php if ($flash): ?>
      <div class="flash flash-<?= h($flash['type']) ?>">
        <svg><use href="#i-<?= $flash['type'] === 'ok' ? 'check' : 'alert' ?>"/></svg>
        <div><b><?= h($flash['title']) ?></b><?php if ($flash['body']): ?><p><?= h($flash['body']) ?></p><?php endif; ?></div>
      </div>
    <?php endif; ?>

    <div class="login-card">
      <h1>تسجيل الدخول</h1>
      <form method="post" class="form" style="gap:13px">
        <input type="hidden" name="csrf" value="<?= h($CSRF) ?>">
        <input type="hidden" name="action" value="login">
        <div class="f">
          <label for="u">اسم المستخدم</label>
          <input type="text" id="u" name="username" required autocomplete="username" autofocus>
        </div>
        <div class="f">
          <label for="p">كلمة المرور</label>
          <input type="password" id="p" name="password" required autocomplete="current-password">
        </div>
        <button type="submit" class="btn btn-primary btn-lg"><svg><use href="#i-lock"/></svg>دخول</button>
      </form>

      <div class="demo-box">
        <b>حسابات تجريبية — للعرض فقط</b>
        <table>
          <tr><td>منسّق السلامة <span class="role role-admin">admin</span></td><td><code>admin</code> / <code>admin123</code></td></tr>
          <tr><td>ممرضة العيادة <span class="role role-admin">admin</span></td><td><code>nurse</code> / <code>nurse123</code></td></tr>
          <tr><td>مشرف مبنى <span class="role role-staff">staff</span></td><td><code>staff</code> / <code>staff123</code></td></tr>
        </table>
      </div>
    </div>
    <p style="text-align:center;font-size:11.5px;color:var(--faint);margin-block-start:16px">
      مشروع طلابي — بيانات تجريبية لا تمثّل حوادث حقيقية.
    </p>
  </div>
</div>

<?php else: /* ═══════════ التطبيق ═══════════ */ ?>

<header class="top">
  <div class="shell top-in">
    <div class="brand">
      <div class="mark"><svg><use href="#i-shield"/></svg></div>
      <div><b>MedAlert</b><small>نظام بلاغات الصحة والسلامة</small></div>
    </div>
    <nav class="desknav" aria-label="التنقل الرئيسي">
      <a href="?p=home"      <?= $page==='home'?'aria-current="page"':'' ?>><svg><use href="#i-home"/></svg>الرئيسية</a>
      <a href="?p=new"       <?= $page==='new'?'aria-current="page"':'' ?>><svg><use href="#i-plus"/></svg>بلاغ جديد</a>
      <a href="?p=alerts"    <?= $page==='alerts'?'aria-current="page"':'' ?>><svg><use href="#i-list"/></svg>السجل</a>
      <a href="?p=kits"      <?= $page==='kits'?'aria-current="page"':'' ?>><svg><use href="#i-kit"/></svg>الحقائب</a>
      <a href="?p=emergency" <?= $page==='emergency'?'aria-current="page"':'' ?>><svg><use href="#i-phone"/></svg>الطوارئ</a>
    </nav>
    <div class="who">
      <div class="av"><?= h(mb_substr($ME['name'], 0, 1)) ?></div>
      <div class="t">
        <b><?= h($ME['name']) ?></b>
        <span class="role role-<?= h($ME['role']) ?>"><?= $ME['role'] === 'admin' ? 'منسّق' : 'موظف' ?></span>
      </div>
      <form method="post" style="margin:0">
        <input type="hidden" name="csrf" value="<?= h($CSRF) ?>">
        <input type="hidden" name="action" value="logout">
        <button class="btn btn-sm" title="تسجيل الخروج"><svg><use href="#i-out"/></svg><span class="sr">خروج</span></button>
      </form>
    </div>
  </div>
</header>

<main class="shell">

<?php if ($flash): ?>
  <div class="flash flash-<?= h($flash['type']) ?>">
    <svg><use href="#i-<?= $flash['type'] === 'ok' ? 'check' : 'alert' ?>"/></svg>
    <div><b><?= h($flash['title']) ?></b><?php if ($flash['body']): ?><p><?= h($flash['body']) ?></p><?php endif; ?></div>
  </div>
<?php endif; ?>

<?php
/* ---------- بطاقة بلاغ ---------- */
function alert_card(array $a): string {
    $t = $a['ty'] ?? 'other';
    $out  = '<article class="al sev-' . h($a['sv'] ?? 'low') . '">';
    $out .= '<div class="al-ic">' . ico(TYPES[$t][1] ?? 'i-dots') . '</div><div class="al-b">';
    $out .= '<h3>' . h($a['t'] ?? '') . '</h3><div class="al-meta">';
    $out .= '<span>' . ico('i-pin') . h($a['lc'] ?? '') . '</span>';
    $out .= '<span>' . ico('i-clock') . h(rel_time((int)($a['at'] ?? time()))) . '</span>';
    if (!empty($a['by'])) $out .= '<span>' . ico('i-user') . h($a['by']) . '</span>';
    $out .= '<span class="mono" style="color:var(--faint)">' . h($a['id'] ?? '') . '</span></div>';
    $out .= '<div class="al-chips">' . sev_chip($a['sv'] ?? 'low') . st_chip($a['st'] ?? 'open') . ty_chip($t) . '</div>';
    if (!empty($a['nt'])) $out .= '<p class="al-note">' . nl2br(h($a['nt'])) . '</p>';
    if (!empty($a['ph'])) $out .= '<div class="al-photo"><img src="' . h($a['ph']) . '" alt="صورة مرفقة بالبلاغ"></div>';
    return $out . '</div></article>';
}
?>

<?php if ($page === 'home'): ?>
  <div class="phead">
    <h1>لوحة السلامة</h1>
    <p>نظرة سريعة على حالة البلاغات ومخزون الإسعاف. البيانات محفوظة في <code class="mono">data.json</code>.</p>
  </div>

  <div class="sec">
    <div class="kpis">
      <div class="kpi k-crit"><span class="lab"><svg><use href="#i-alert"/></svg>بلاغات مفتوحة</span><span class="val"><?= $kOpen ?></span><span class="sub">OPEN ALERTS</span></div>
      <div class="kpi k-crit"><span class="lab"><svg><use href="#i-warn"/></svg>بلاغات حرجة</span><span class="val"><?= $kCrit ?></span><span class="sub">CRITICAL</span></div>
      <div class="kpi k-warn"><span class="lab"><svg><use href="#i-clock"/></svg>حقائب تنتهي قريبًا</span><span class="val"><?= $kSoon ?></span><span class="sub">EXPIRING SOON</span></div>
      <div class="kpi <?= $kMiss ? 'k-warn' : 'k-good' ?>"><span class="lab"><svg><use href="#i-box"/></svg>أصناف ناقصة</span><span class="val"><?= $kMiss ?></span><span class="sub">MISSING ITEMS</span></div>
    </div>
  </div>

  <div class="sec">
    <div class="sec-t">
      <h2>أحدث ٥ بلاغات</h2><span class="eyebrow">Latest alerts</span>
      <a href="?p=alerts" class="btn btn-sm" style="margin-inline-start:auto">عرض السجل كاملًا</a>
    </div>
    <div class="alist">
      <?php foreach (array_slice($alerts, 0, 5) as $a) echo alert_card($a); ?>
    </div>
  </div>

  <div class="sec">
    <a href="?p=new" class="btn btn-primary btn-lg"><svg><use href="#i-plus"/></svg>تسجيل بلاغ جديد</a>
  </div>

<?php elseif ($page === 'new'): ?>
  <div class="phead">
    <h1>بلاغ جديد</h1>
    <p>سجّل الحادثة فور وقوعها. الحقول المعلَّمة بـ <span class="req">*</span> إلزامية.</p>
  </div>

  <form class="form" method="post" enctype="multipart/form-data">
    <input type="hidden" name="csrf" value="<?= h($CSRF) ?>">
    <input type="hidden" name="action" value="create_alert">

    <div class="f">
      <label for="t">عنوان البلاغ <span class="req">*</span></label>
      <input type="text" id="t" name="title" required maxlength="160" placeholder="مثال: إغماء طالبة في قاعة ٢٠٣">
      <span class="hint">جملة قصيرة تصف ما حدث — تظهر في السجل.</span>
    </div>

    <div class="f2">
      <div class="f">
        <label for="l">الموقع <span class="req">*</span></label>
        <input type="text" id="l" name="loc" required maxlength="160" placeholder="مثال: مبنى أ — الطابق ٢">
      </div>
      <div class="f">
        <label for="ty">نوع البلاغ <span class="req">*</span></label>
        <select id="ty" name="type" required>
          <?php foreach (TYPES as $k => $v): ?><option value="<?= h($k) ?>"><?= h($v[0]) ?></option><?php endforeach; ?>
        </select>
      </div>
    </div>

    <div class="f">
      <label>درجة الخطورة <span class="req">*</span></label>
      <div class="segs" role="radiogroup" aria-label="درجة الخطورة">
        <?php foreach (['low', 'medium', 'high', 'critical'] as $s): ?>
          <label class="seg">
            <input type="radio" name="sev" value="<?= h($s) ?>" <?= $s === 'medium' ? 'checked' : '' ?>>
            <span class="sv-<?= h($s) ?>"><svg><use href="#<?= h(SEVS[$s][1]) ?>"/></svg><?= h(SEVS[$s][0]) ?></span>
          </label>
        <?php endforeach; ?>
      </div>
      <span class="hint">اللون وحده لا يكفي — كل درجة تحمل اسمها ورمزها.</span>
    </div>

    <div class="f2">
      <div class="f">
        <label for="st">الحالة</label>
        <select id="st" name="status">
          <?php foreach (STATES as $k => $v): ?><option value="<?= h($k) ?>"><?= h($v[0]) ?></option><?php endforeach; ?>
        </select>
      </div>
      <div class="f">
        <label for="by">اسم المُبلِّغ</label>
        <input type="text" id="by" name="by" maxlength="80" value="<?= h($ME['name']) ?>">
      </div>
    </div>

    <div class="f">
      <label for="nt">ملاحظات وتفاصيل</label>
      <textarea id="nt" name="note" maxlength="1200" placeholder="ماذا حدث؟ ما الإجراء المتخذ؟ هل تم استدعاء أحد؟"></textarea>
    </div>

    <div class="f">
      <label for="ph">صورة مرفقة</label>
      <input type="file" id="ph" name="photo" accept="image/jpeg,image/png,image/gif,image/webp">
      <span class="hint">JPG أو PNG أو GIF أو WEBP — بحد أقصى ٤ ميجابايت. تُحفظ في مجلد <code class="mono">uploads/</code>.</span>
    </div>

    <button type="submit" class="btn btn-primary btn-lg"><svg><use href="#i-plus"/></svg>إرسال البلاغ</button>
  </form>

<?php elseif ($page === 'alerts'): ?>
  <div class="phead">
    <h1>سجل البلاغات</h1>
    <p>جميع البلاغات المحفوظة.<?= is_admin() ? ' استخدم زر «إغلاق» لإنهاء البلاغ.' : ' إغلاق البلاغات متاح لمنسّق السلامة فقط.' ?></p>
  </div>

  <form class="tools" method="get">
    <input type="hidden" name="p" value="alerts">
    <div class="tool">
      <label for="fst">الحالة — Status</label>
      <select id="fst" name="st">
        <option value="">كل الحالات</option>
        <?php foreach (STATES as $k => $v): ?>
          <option value="<?= h($k) ?>" <?= $fSt === $k ? 'selected' : '' ?>><?= h($v[0]) ?></option>
        <?php endforeach; ?>
      </select>
    </div>
    <div class="tool">
      <label for="fsv">الخطورة — Severity</label>
      <select id="fsv" name="sv">
        <option value="">كل الدرجات</option>
        <?php foreach (SEVS as $k => $v): ?>
          <option value="<?= h($k) ?>" <?= $fSv === $k ? 'selected' : '' ?>><?= h($v[0]) ?></option>
        <?php endforeach; ?>
      </select>
    </div>
    <div class="tool">
      <label for="fq">بحث — Search</label>
      <input type="text" id="fq" name="q" value="<?= h($fQ) ?>" placeholder="عنوان أو موقع…">
    </div>
    <button class="btn btn-sm" style="min-height:42px">تطبيق</button>
    <a class="btn btn-sm" href="?p=alerts" style="min-height:42px">إعادة تعيين</a>
  </form>

  <div class="sec-t"><h2><?= count($rows) ?> من <?= count($alerts) ?> بلاغًا</h2></div>

  <div class="twrap">
    <?php if ($rows): ?>
    <table>
      <thead><tr><th>البلاغ</th><th>النوع</th><th>الخطورة</th><th>الحالة</th><th>الوقت</th><th>إجراء</th></tr></thead>
      <tbody>
      <?php foreach ($rows as $a): ?>
        <tr>
          <td data-lab="البلاغ">
            <div class="ttl"><?= h($a['t']) ?></div>
            <div class="loc"><?= h($a['lc']) ?> · <span class="mono"><?= h($a['id']) ?></span></div>
          </td>
          <td data-lab="النوع"><?= ty_chip($a['ty'] ?? 'other') ?></td>
          <td data-lab="الخطورة"><?= sev_chip($a['sv'] ?? 'low') ?></td>
          <td data-lab="الحالة"><?= st_chip($a['st'] ?? 'open') ?></td>
          <td data-lab="الوقت"><span class="mono" style="font-size:12px;white-space:nowrap"><?= h(date('Y-m-d H:i', (int)$a['at'])) ?></span></td>
          <td data-lab="إجراء">
            <?php if (($a['st'] ?? '') === 'closed'): ?>
              <button class="btn btn-sm" disabled><svg><use href="#i-check"/></svg>مغلق</button>
            <?php elseif (is_admin()): ?>
              <form method="post" style="margin:0">
                <input type="hidden" name="csrf" value="<?= h($CSRF) ?>">
                <input type="hidden" name="action" value="close_alert">
                <input type="hidden" name="id" value="<?= h($a['id']) ?>">
                <input type="hidden" name="q_st" value="<?= h($fSt) ?>">
                <button class="btn btn-sm btn-danger"><svg><use href="#i-check"/></svg>إغلاق</button>
              </form>
            <?php else: ?>
              <button class="btn btn-sm" disabled title="متاح لمنسّق السلامة"><svg><use href="#i-lock"/></svg>مقفل</button>
            <?php endif; ?>
          </td>
        </tr>
      <?php endforeach; ?>
      </tbody>
    </table>
    <?php else: ?>
      <div class="empty">
        <svg><use href="#i-inbox"/></svg>
        <b>لا توجد بلاغات مطابقة</b>
        <p>جرّب توسيع المرشّحات أو إعادة تعيينها لعرض كل البلاغات.</p>
      </div>
    <?php endif; ?>
  </div>

<?php elseif ($page === 'kits'): ?>
  <div class="phead">
    <h1>حقائب الإسعاف الأولي</h1>
    <p>حالة الحقائب حسب المبنى.<?= is_admin() ? ' يمكنك إعادة تعبئة الأصناف الناقصة مباشرة.' : ' إعادة التعبئة متاحة لمنسّق السلامة فقط.' ?></p>
  </div>

  <div class="kits">
  <?php foreach ($db['kits'] as $k):
      $items = $k['items'];
      $tot   = count($items);
      $okN   = count(array_filter($items, fn($i) => !empty($i['ok'])));
      $pct   = $tot ? (int)round($okN / $tot * 100) : 0;
      $missN = $tot - $okN;
      $es    = exp_state((int)$k['exp']);
      $expCls = $es === 'crit' ? 's-critical' : ($es === 'warn' ? 's-high' : 's-low');
      $expIcn = $es === 'ok' ? 'i-check' : ($es === 'warn' ? 'i-clock' : 'i-alert');
      $barCls = $missN === 0 ? '' : ($pct < 75 ? 'crit' : 'warn');
  ?>
    <div class="kit">
      <div class="kit-h">
        <div class="ic"><svg><use href="#i-kit"/></svg></div>
        <div>
          <h3><?= h($k['b']) ?></h3>
          <div class="sub"><?= h($k['loc']) ?> · <span class="mono"><?= h($k['id']) ?></span></div>
        </div>
      </div>
      <div class="kit-body">
        <div style="display:flex;gap:6px;flex-wrap:wrap">
          <?= chip($expCls, $expIcn, exp_text((int)$k['exp'])) ?>
          <?= chip($missN ? 's-critical' : 's-low', $missN ? 'i-alert' : 'i-check', $missN ? "ناقص $missN صنف" : 'مكتملة') ?>
          <?= chip('chip-plain', 'i-cal', date('Y-m-d', (int)$k['exp'])) ?>
        </div>

        <div class="meter">
          <div class="meter-top"><b>اكتمال المحتويات</b><span><?= $okN ?> / <?= $tot ?> · <?= $pct ?>%</span></div>
          <div class="bar <?= $barCls ?>"><i style="width:<?= $pct ?>%"></i></div>
        </div>

        <div class="items">
          <?php foreach ($items as $i): if (empty($i['ok'])) continue; ?>
            <span class="item"><svg><use href="#i-check"/></svg><?= h($i['n']) ?><span class="q"><?= (int)$i['q'] ?></span></span>
          <?php endforeach; ?>
        </div>

        <?php if ($missN): ?>
          <div style="display:grid;gap:7px">
          <?php foreach ($items as $idx => $i): if (!empty($i['ok'])) continue; ?>
            <div class="miss-row">
              <span class="nm"><svg><use href="#i-x"/></svg><?= h($i['n']) ?></span>
              <?php if (is_admin()): ?>
                <form method="post" style="display:flex;gap:6px;margin:0">
                  <input type="hidden" name="csrf" value="<?= h($CSRF) ?>">
                  <input type="hidden" name="action" value="restock">
                  <input type="hidden" name="kit" value="<?= h($k['id']) ?>">
                  <input type="hidden" name="idx" value="<?= (int)$idx ?>">
                  <input type="number" name="qty" value="5" min="1" max="999" aria-label="الكمية">
                  <button class="btn btn-sm btn-primary"><svg><use href="#i-check"/></svg>إعادة تعبئة</button>
                </form>
              <?php else: ?>
                <span class="chip s-critical"><?= 'ناقص' ?></span>
              <?php endif; ?>
            </div>
          <?php endforeach; ?>
          </div>
        <?php endif; ?>
      </div>
    </div>
  <?php endforeach; ?>
  </div>

<?php else: /* emergency */ ?>
  <div class="phead">
    <h1>الطوارئ</h1>
    <p>أرقام الاتصال، أقرب عيادة، نقطة التجمع، وخطوات إسعاف أولي مختصرة.</p>
  </div>

  <div class="sec">
    <div class="banner">
      <svg><use href="#i-warn"/></svg>
      <div>
        <b>للتدريب والعرض التوضيحي فقط</b>
        <p>هذه الصفحة جزء من مشروع طلابي ولا تُغني عن التدريب المعتمد على الإسعاف الأولي ولا عن الاتصال بخدمات الطوارئ. في أي حالة حقيقية: اتصل بالطوارئ أولًا.</p>
      </div>
    </div>
  </div>

  <div class="sec">
    <div class="sec-t"><h2>أرقام الطوارئ</h2><span class="eyebrow">Emergency numbers</span></div>
    <div class="calls">
      <a class="call" href="tel:997"><div class="ic"><svg><use href="#i-phone"/></svg></div><div class="t"><b>الهلال الأحمر — إسعاف</b><span class="n">997</span></div></a>
      <a class="call" href="tel:998"><div class="ic"><svg><use href="#i-fire"/></svg></div><div class="t"><b>الدفاع المدني — حريق</b><span class="n">998</span></div></a>
      <a class="call" href="tel:999"><div class="ic"><svg><use href="#i-shield"/></svg></div><div class="t"><b>الشرطة</b><span class="n">999</span></div></a>
      <a class="call" href="tel:911"><div class="ic"><svg><use href="#i-alert"/></svg></div><div class="t"><b>الطوارئ الموحّد</b><span class="n">911</span></div></a>
      <a class="call" href="tel:1200"><div class="ic"><svg><use href="#i-hospital"/></svg></div><div class="t"><b>عيادة الحرم (داخلي)</b><span class="n">1200</span></div></a>
      <a class="call" href="tel:1201"><div class="ic"><svg><use href="#i-user"/></svg></div><div class="t"><b>أمن الحرم (داخلي)</b><span class="n">1201</span></div></a>
    </div>
    <p style="margin-block-start:9px;font-size:12px;color:var(--faint)">الأرقام الوطنية أعلاه سعودية. الأرقام الداخلية (1200 / 1201) قيم تجريبية — استبدلها بأرقام منشأتك.</p>
  </div>

  <div class="sec">
    <div class="sec-t"><h2>مواقع مهمة</h2><span class="eyebrow">Key locations</span></div>
    <div class="info-grid">
      <div class="info"><div class="ic"><svg><use href="#i-hospital"/></svg></div><div><b>أقرب عيادة</b><p>عيادة الحرم — مبنى الخدمات الطلابية، الدور الأرضي. تعمل ٧:٣٠ ص – ٤:٠٠ م.</p></div></div>
      <div class="info"><div class="ic"><svg><use href="#i-flag"/></svg></div><div><b>نقطة التجمع</b><p>الساحة الشمالية أمام البوابة ٣ — تجمّع هناك عند الإخلاء وانتظر النداء بالأسماء.</p></div></div>
      <div class="info"><div class="ic"><svg><use href="#i-kit"/></svg></div><div><b>أقرب جهاز AED</b><p>مدخل الصالة الرياضية، وبجوار مصعد مبنى أ. يُستخدم باتباع التعليمات الصوتية.</p></div></div>
    </div>
  </div>

  <div class="sec">
    <div class="sec-t"><h2>خطوات إسعاف أولي مختصرة</h2><span class="eyebrow">Quick first aid</span></div>
    <div class="fa">
      <details open>
        <summary><span class="ic"><svg><use href="#i-user"/></svg></span>إغماء / فقدان وعي</summary>
        <div class="fb">
          <ol>
            <li>أبعد الزحام وأمّن المكان حول المصاب.</li>
            <li>أضجعه على ظهره وارفع ساقيه نحو ٣٠ سم.</li>
            <li>فُكّ الملابس الضيقة حول الرقبة والصدر.</li>
            <li>لا تُعطه أي شيء بالفم حتى يستعيد وعيه كاملًا.</li>
            <li>ابقَ معه وراقب تنفسه حتى وصول المساعدة.</li>
          </ol>
          <div class="redline"><svg><use href="#i-alert"/></svg><span>لم يستعد وعيه خلال دقيقة، أو توقف تنفسه؟ اتصل بالطوارئ فورًا وابدأ الإنعاش إن كنت مدرَّبًا.</span></div>
        </div>
      </details>
      <details>
        <summary><span class="ic"><svg><use href="#i-drop"/></svg></span>نزيف</summary>
        <div class="fb">
          <ol>
            <li>ارتدِ قفازات إن توفرت — حماية لك وله.</li>
            <li>اضغط مباشرة على الجرح بشاش أو قماش نظيف.</li>
            <li>لا ترفع الضمادة الأولى — أضف فوقها إن تشبّعت.</li>
            <li>ارفع الطرف المصاب فوق مستوى القلب إن أمكن.</li>
          </ol>
          <div class="redline"><svg><use href="#i-alert"/></svg><span>النزيف لا يتوقف بعد ١٠ دقائق ضغط متواصل، أو نزيف غزير ونافر؟ طوارئ فورًا.</span></div>
        </div>
      </details>
      <details>
        <summary><span class="ic"><svg><use href="#i-heat"/></svg></span>إجهاد حراري / ضربة شمس</summary>
        <div class="fb">
          <ol>
            <li>انقله فورًا إلى مكان ظليل أو مكيّف.</li>
            <li>أزل الملابس الزائدة وبرّده بالماء ورذاذ ومروحة.</li>
            <li>ضع كمادات باردة على الرقبة والإبطين والفخذين.</li>
            <li>إن كان واعيًا تمامًا: أعطه ماءً باردًا رشفات صغيرة.</li>
          </ol>
          <div class="redline"><svg><use href="#i-alert"/></svg><span>ارتباك أو تشنّج أو فقدان وعي أو جلد ساخن جاف = ضربة شمس، حالة طوارئ تهدد الحياة. اتصل فورًا واستمر في التبريد.</span></div>
        </div>
      </details>
      <details>
        <summary><span class="ic"><svg><use href="#i-fire"/></svg></span>حروق</summary>
        <div class="fb">
          <ol>
            <li>برّد المنطقة بماء جارٍ فاتر لمدة ٢٠ دقيقة.</li>
            <li>أزل الحلي والملابس غير الملتصقة قبل حدوث التورم.</li>
            <li>غطِّ الحرق بغطاء نظيف غير لاصق.</li>
            <li>لا تضع ثلجًا ولا زيتًا ولا معجون أسنان، ولا تفقأ الفقاعات.</li>
          </ol>
          <div class="redline"><svg><use href="#i-alert"/></svg><span>حروق الوجه أو اليدين أو المفاصل، أو أكبر من كف يد المصاب، أو حروق كهربائية وكيميائية؟ طوارئ فورًا.</span></div>
        </div>
      </details>
    </div>
  </div>
<?php endif; ?>

</main>

<footer class="shell">
  <b>MedAlert</b> — مشروع طلابي للصحة والسلامة. جميع البيانات تجريبية ولا تمثّل حوادث حقيقية.
  <br>التخزين في ملف <span class="mono">data.json</span> · الجلسة باسم <b><?= h($ME['name']) ?></b>.
</footer>

<nav class="botnav" aria-label="التنقل السفلي">
  <a href="?p=home"      <?= $page==='home'?'aria-current="page"':'' ?>><svg><use href="#i-home"/></svg>الرئيسية</a>
  <a href="?p=new"       <?= $page==='new'?'aria-current="page"':'' ?>><svg><use href="#i-plus"/></svg>بلاغ</a>
  <a href="?p=alerts"    <?= $page==='alerts'?'aria-current="page"':'' ?>><svg><use href="#i-list"/></svg>السجل</a>
  <a href="?p=kits"      <?= $page==='kits'?'aria-current="page"':'' ?>><svg><use href="#i-kit"/></svg>الحقائب</a>
  <a href="?p=emergency" <?= $page==='emergency'?'aria-current="page"':'' ?>><svg><use href="#i-phone"/></svg>طوارئ</a>
</nav>

<?php endif; ?>
</body>
</html>
