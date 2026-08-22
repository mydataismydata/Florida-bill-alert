<?php
/**
 * "Analyse this bill" from a bill page. Writes a row and stops. The analysis
 * box pulls these on its next run -- nothing here can reach inward, which is
 * the whole point of the arrangement.
 */
declare(strict_types=1);
require __DIR__ . '/lib.php';

if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
    http_response_code(405);
    exit('POST only.');
}
$session = preg_replace('/[^0-9A-Z]/', '', strtoupper((string) ($_POST['session'] ?? '')));
$num     = (int) ($_POST['num'] ?? 0);
$email   = strtolower(trim((string) ($_POST['email'] ?? '')));

if ($session === '' || $num <= 0 || $num > 999999) {
    page('Not a bill', '<div class="slabel">REQUEST</div><p>That is not a bill number.</p>', 400);
}
if ($email !== '' && !valid_email($email)) {
    $email = '';
}

$st = db()->prepare('SELECT id FROM request WHERE session=? AND num=? AND fulfilled=\'\'');
$st->execute([$session, $num]);
if (!$st->fetch()) {
    db()->prepare('INSERT INTO request (session, num, email, created_at) VALUES (?,?,?,?)')
        ->execute([$session, $num, $email, now()]);
}

page('Requested', '<div class="slabel">REQUEST</div>'
    . '<p>Noted. This bill goes into the queue for the next analysis run.</p>'
    . ($email ? '<p>We will write to <strong>' . h($email) . '</strong> when it is ready.</p>' : ''));
