<?php
/**
 * "Analyse this bill" from a bill page. Writes a row and stops. The analysis
 * box reads the open requests over SSH on its nightly run (requests.php) --
 * nothing here can reach inward, which is the whole point of the arrangement.
 *
 * Once the bill's page carries its analysis, the box ships a payload saying
 * so, and send.php writes to the address given here and closes the request.
 */
declare(strict_types=1);
require __DIR__ . '/lib.php';

if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
    http_response_code(405);
    exit('POST only.');
}
$session = strtoupper(trim((string) ($_POST['session'] ?? '')));
$num     = (int) ($_POST['num'] ?? 0);
$email   = strtolower(trim((string) ($_POST['email'] ?? '')));

// A session is a year, and a special session a year and a letter: 2026,
// 2026A. Anything else is not a page this site has.
if (!preg_match('/^\d{4}[A-Z]?$/', $session) || $num <= 0 || $num > 999999) {
    page('Not a bill', '<div class="slabel">REQUEST</div><p>That is not a bill number.</p>', 400);
}
if ($email !== '' && !valid_email($email)) {
    $email = '';
}

$pdo = db();

// An address typed into a form proves nothing about owning it. send.php
// writes to an address at most once a night, however many of its bills come
// up, and an address with this many requests already open is not taken for
// more. The request itself still counts, without the address, and the page
// below makes no promise to write.
const OPEN_PER_ADDRESS = 20;
if ($email !== '') {
    $n = $pdo->prepare("SELECT COUNT(*) FROM request WHERE email = ? AND fulfilled = ''");
    $n->execute([$email]);
    if ((int) $n->fetchColumn() >= OPEN_PER_ADDRESS) {
        $email = '';
    }
}

// One open request per bill per address. Keyed on the bill alone, as it once
// was, a second person asking for the same bill was told they would hear
// back and their address was never stored.
$st = $pdo->prepare("SELECT id FROM request WHERE session = ? AND num = ? AND email = ? AND fulfilled = ''");
$st->execute([$session, $num, $email]);
if (!$st->fetch()) {
    $pdo->prepare('INSERT INTO request (session, num, email, created_at) VALUES (?,?,?,?)')
        ->execute([$session, $num, $email, now()]);
}

page('Requested', '<div class="slabel">REQUEST</div>'
    . '<p>Noted. This bill goes into the queue for the next analysis run, which is overnight.</p>'
    . ($email ? '<p>We will write to <strong>' . h($email) . '</strong> once, when it is ready, '
              . 'and not again.</p>' : ''));
