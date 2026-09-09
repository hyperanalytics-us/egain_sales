<?php
/**
 * ESP host preflight check.
 *
 * Upload this one file to javasri.com/esp/preflight.php and open it in a browser.
 * It reports whether the host can run ESP, and what has to be tuned.
 * Delete it once you are done - it discloses server configuration.
 */
declare(strict_types=1);
header('Content-Type: text/html; charset=utf-8');

$checks = [];
function check(string $group, string $name, $ok, string $value, string $note = ''): void {
    global $checks;
    $checks[$group][] = ['name' => $name, 'ok' => $ok, 'value' => $value, 'note' => $note];
}
function bytes(string $val): int {
    $val = trim($val);
    if ($val === '' || $val === '-1') return -1;
    $unit = strtolower($val[strlen($val) - 1]);
    $n = (int)$val;
    return match ($unit) { 'g' => $n * 1073741824, 'm' => $n * 1048576, 'k' => $n * 1024, default => $n };
}
function human(int $b): string { return $b < 0 ? 'unlimited' : round($b / 1048576, 1) . ' MB'; }

/* ---------------------------------------------------------------- PHP ---- */
$php = PHP_VERSION;
check('PHP', 'Version', version_compare($php, '8.0', '>=') ? true : (version_compare($php, '7.4', '>=') ? 'warn' : false),
      $php, version_compare($php, '8.0', '>=') ? '' : 'ESP targets PHP 8.0+. Switch version in cPanel > MultiPHP Manager.');
check('PHP', 'SAPI', true, PHP_SAPI);
check('PHP', '64-bit integers', PHP_INT_SIZE >= 8, PHP_INT_SIZE * 8 . '-bit');

/* --------------------------------------------------------- extensions ---- */
$required = [
    'pdo_sqlite' => 'Stores the analysed dataset. Without it ESP cannot run.',
    'zip'        => 'Reads .xlsx uploads (an .xlsx is a zip archive).',
    'xmlreader'  => 'Streams the worksheet XML without loading it all into memory.',
    'json'       => 'API payloads.',
    'mbstring'   => 'Safe handling of non-ASCII URLs and user agents.',
    'curl'       => 'Calls the Claude API for ASK AI.',
];
foreach ($required as $ext => $why) {
    check('Required extensions', $ext, extension_loaded($ext), extension_loaded($ext) ? 'loaded' : 'MISSING', $why);
}
foreach (['sqlite3' => 'Alternative SQLite driver.', 'opcache' => 'Speeds up repeat requests.',
          'zlib' => 'Response compression.'] as $ext => $why) {
    check('Optional extensions', $ext, extension_loaded($ext) ? true : 'warn',
          extension_loaded($ext) ? 'loaded' : 'not loaded', $why);
}

/* ------------------------------------------------------------- limits ---- */
$mem  = bytes((string)ini_get('memory_limit'));
$time = (int)ini_get('max_execution_time');
$up   = bytes((string)ini_get('upload_max_filesize'));
$post = bytes((string)ini_get('post_max_size'));
$inp  = (int)ini_get('max_input_time');

check('Limits', 'memory_limit', $mem < 0 || $mem >= 256 * 1048576 ? true : 'warn', human($mem),
      'ESP streams rows to SQLite and rolls up in SQL, so 256 MB is enough. Under 128 MB is risky.');
check('Limits', 'max_execution_time', $time === 0 || $time >= 120 ? true : 'warn', $time === 0 ? 'unlimited' : $time . 's',
      'A 530k-row log is ingested in resumable chunks, so a 30s cap still works - it just takes more round trips.');
check('Limits', 'upload_max_filesize', $up >= 50 * 1048576 ? true : 'warn', human($up),
      'ESP accepts weblogs up to 50 MB. Raise via cPanel > MultiPHP INI Editor if lower.');
check('Limits', 'post_max_size', $post >= 52 * 1048576 ? true : 'warn', human($post),
      'Must be a little larger than upload_max_filesize.');
check('Limits', 'max_input_time', $inp === -1 || $inp >= 120 ? true : 'warn', $inp === -1 ? 'unlimited' : $inp . 's',
      'Time allowed to receive a 22 MB+ upload.');
$disabled = array_filter(array_map('trim', explode(',', (string)ini_get('disable_functions'))));
$blockers = array_values(array_intersect($disabled, ['fopen', 'file_get_contents', 'curl_exec', 'set_time_limit', 'ignore_user_abort']));
check('Limits', 'disable_functions', empty($blockers) ? true : 'warn',
      $blockers ? implode(', ', $blockers) : 'nothing ESP needs is disabled',
      $blockers ? 'These are disabled and ESP uses them.' : '');

/* --------------------------------------------------------------- disk ---- */
$dir = __DIR__ . '/_esp_preflight_tmp';
$writable = @mkdir($dir) || is_dir($dir);
check('Disk', 'Directory is writable', $writable, $writable ? 'yes' : 'NO - cannot create data/',
      'ESP needs to write uploads and dataset databases beside the app.');

$sqliteOk = false; $sqliteErr = '';
if ($writable && extension_loaded('pdo_sqlite')) {
    try {
        $f = $dir . '/probe.db';
        $db = new PDO('sqlite:' . $f);
        $db->exec('CREATE TABLE t (a INTEGER, b TEXT)');
        $st = $db->prepare('INSERT INTO t VALUES (?, ?)');
        $t0 = microtime(true);
        $db->beginTransaction();
        for ($i = 0; $i < 20000; $i++) { $st->execute([$i, 'row-' . $i]); }
        $db->commit();
        $secs = microtime(true) - $t0;
        $n = (int)$db->query('SELECT COUNT(*) FROM t')->fetchColumn();
        $ver = $db->query('SELECT sqlite_version()')->fetchColumn();
        $sqliteOk = $n === 20000;
        $rate = (int)round(20000 / max(0.001, $secs));
        check('Disk', 'SQLite write test', $sqliteOk, "20,000 rows in " . round($secs, 2) . "s ({$rate}/s), SQLite $ver",
              'A 530k-row log needs roughly ' . max(1, (int)round(530000 / max(1, $rate))) . 's of insert time at this rate.');
        $db = null; @unlink($f);
    } catch (Throwable $e) { $sqliteErr = $e->getMessage(); check('Disk', 'SQLite write test', false, 'failed', $sqliteErr); }
}
$free = @disk_free_space(__DIR__);
check('Disk', 'Free space', $free === false ? 'warn' : ($free > 1073741824 ? true : 'warn'),
      $free === false ? 'unknown' : round($free / 1073741824, 1) . ' GB',
      'Each analysed 530k-row weblog stores about 170 MB.');

/* ------------------------------------------------------- xlsx reading ---- */
if (extension_loaded('zip') && extension_loaded('xmlreader') && $writable) {
    $ok = false; $msg = '';
    try {
        $xlsx = $dir . '/probe.xlsx';
        $zip = new ZipArchive();
        $zip->open($xlsx, ZipArchive::CREATE | ZipArchive::OVERWRITE);
        $zip->addFromString('xl/worksheets/sheet1.xml',
            '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            . '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>IP</t></is></c></row></sheetData></worksheet>');
        $zip->close();
        $z = new ZipArchive();
        $z->open($xlsx);
        $xml = $z->getFromName('xl/worksheets/sheet1.xml');
        $r = new XMLReader();
        $r->XML($xml);
        while ($r->read()) { if ($r->nodeType === XMLReader::ELEMENT && $r->localName === 't') { $ok = ($r->readString() === 'IP'); break; } }
        $r->close(); $z->close(); @unlink($xlsx);
    } catch (Throwable $e) { $msg = $e->getMessage(); }
    check('XLSX', 'Streamed zip + XML read', $ok, $ok ? 'works' : ('failed ' . $msg),
          'This is exactly how ESP reads uploaded .xlsx weblogs.');
}
@rmdir($dir);

/* ---------------------------------------------------- outbound HTTPS ----- */
if (extension_loaded('curl')) {
    $ch = curl_init('https://api.anthropic.com/v1/models');
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true, CURLOPT_TIMEOUT => 12, CURLOPT_NOBODY => false,
        CURLOPT_HTTPHEADER => ['anthropic-version: 2023-06-01', 'x-api-key: preflight-no-key'],
    ]);
    $body = curl_exec($ch);
    $code = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $err  = curl_error($ch);
    curl_close($ch);
    // 401 is the good answer: we reached Anthropic and it rejected our fake key.
    $reachable = $code === 401 || $code === 403 || $code === 200;
    check('Outbound network', 'api.anthropic.com reachable', $reachable ? true : false,
          $reachable ? "HTTP $code (reached Anthropic)" : ($err ?: "HTTP $code"),
          $reachable ? 'ASK AI will work once you add the API key.'
                     : 'Shared hosts sometimes block outbound HTTPS. Without this, ASK AI cannot run - ask support to allow api.anthropic.com.');
}

/* ------------------------------------------------------------ server ----- */
check('Server', 'Software', true, $_SERVER['SERVER_SOFTWARE'] ?? 'unknown');
check('Server', 'Document root', true, $_SERVER['DOCUMENT_ROOT'] ?? 'unknown');
check('Server', 'This script path', true, __DIR__);
check('Server', 'URL path', true, $_SERVER['SCRIPT_NAME'] ?? 'unknown',
      'ESP derives its base path from this, so /esp/ works without configuration.');
check('Server', 'HTTPS', !empty($_SERVER['HTTPS']) ? true : 'warn', !empty($_SERVER['HTTPS']) ? 'on' : 'off',
      'Serve ESP over HTTPS - it carries prospect data and a login.');
$ht = function_exists('apache_get_modules') ? apache_get_modules() : null;
check('Server', 'mod_rewrite', $ht === null ? 'warn' : in_array('mod_rewrite', $ht, true),
      $ht === null ? 'cannot detect (not mod_php)' : (in_array('mod_rewrite', $ht, true) ? 'available' : 'missing'),
      'Used to route API calls and to protect the data directory.');

/* ------------------------------------------------------------ verdict --- */
$fail = 0; $warn = 0;
foreach ($checks as $rows) foreach ($rows as $r) { if ($r['ok'] === false) $fail++; elseif ($r['ok'] === 'warn') $warn++; }
$verdict = $fail === 0 ? ($warn === 0 ? 'ready' : 'ready-with-tuning') : 'blocked';
?>
<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ESP host preflight</title>
<style>
 body{font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
      margin:0;background:#f4f6fa;color:#12203a}
 .wrap{max-width:920px;margin:0 auto;padding:28px 20px 60px}
 h1{font-size:21px;margin:0 0 4px} .sub{color:#8592a8;margin-bottom:20px}
 .verdict{border-radius:12px;padding:14px 18px;margin-bottom:22px;font-weight:600;border:1px solid}
 .ready{background:#e8f5ee;border-color:#1b7f4d;color:#0f5c37}
 .tune{background:#fff6e5;border-color:#ef6c00;color:#8a4700}
 .blocked{background:#fdecec;border-color:#c62828;color:#8e1c1c}
 h2{font-size:12px;text-transform:uppercase;letter-spacing:.07em;color:#8592a8;margin:22px 0 8px}
 table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e2e7f0;border-radius:10px;overflow:hidden}
 td{padding:9px 13px;border-bottom:1px solid #eef1f6;vertical-align:top}
 tr:last-child td{border-bottom:0}
 td.s{width:26px;text-align:center;font-weight:700}
 td.n{width:210px;font-weight:600} td.v{width:270px;font-family:ui-monospace,Menlo,monospace;font-size:12.5px}
 td.note{color:#55627a;font-size:12.5px}
 .ok{color:#1b7f4d}.wa{color:#ef6c00}.no{color:#c62828}
 code{background:#eef1f6;padding:1px 5px;border-radius:4px;font-size:12px}
 footer{margin-top:26px;color:#8592a8;font-size:12.5px}
</style>
<div class="wrap">
<h1>ESP host preflight</h1>
<div class="sub">Checks whether this server can run eGain Sales Prospects.</div>
<?php if ($verdict === 'ready'): ?>
  <div class="verdict ready">Ready. Everything ESP needs is present.</div>
<?php elseif ($verdict === 'ready-with-tuning'): ?>
  <div class="verdict tune">Ready, with <?= $warn ?> setting<?= $warn === 1 ? '' : 's' ?> to tune. See the orange rows.</div>
<?php else: ?>
  <div class="verdict blocked"><?= $fail ?> blocking problem<?= $fail === 1 ? '' : 's' ?>. See the red rows - ESP cannot run until they are resolved.</div>
<?php endif; ?>
<?php foreach ($checks as $group => $rows): ?>
  <h2><?= htmlspecialchars($group) ?></h2>
  <table>
  <?php foreach ($rows as $r):
      $cls = $r['ok'] === true ? 'ok' : ($r['ok'] === 'warn' ? 'wa' : 'no');
      $sym = $r['ok'] === true ? '✓' : ($r['ok'] === 'warn' ? '!' : '✕'); ?>
    <tr>
      <td class="s <?= $cls ?>"><?= $sym ?></td>
      <td class="n"><?= htmlspecialchars($r['name']) ?></td>
      <td class="v"><?= htmlspecialchars($r['value']) ?></td>
      <td class="note"><?= $r['ok'] === true && $r['note'] === '' ? '' : htmlspecialchars($r['note']) ?></td>
    </tr>
  <?php endforeach; ?>
  </table>
<?php endforeach; ?>
<footer>Delete <code>preflight.php</code> when you are finished - it discloses server configuration.</footer>
</div>
