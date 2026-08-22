<?php
/** Saving from the manage page. Same signed link is the credential. */
declare(strict_types=1);
require __DIR__ . '/lib.php';

if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
    http_response_code(405);
    exit('POST only.');
}
$email = strtolower(trim((string) ($_POST['e'] ?? '')));
$token = (string) ($_POST['t'] ?? '');
if (!valid_email($email) || !signed_ok($email, 'manage', $token)) {
    page('Link not valid', '<div class="slabel">SETTINGS</div><p>That link is not valid.</p>', 400);
}

$areas  = implode(',', clean_areas((array) ($_POST['areas'] ?? [])));
$daily  = empty($_POST['daily'])  ? 0 : 1;
$weekly = empty($_POST['weekly']) ? 0 : 1;

if (!$daily && !$weekly) {
    // Wanting neither product is unsubscribing, so treat it as one rather than
    // leaving a confirmed row that can never be sent to.
    db()->prepare('UPDATE subscriber SET daily=0, weekly=0, unsubscribed=? WHERE email=?')
        ->execute([now(), $email]);
    page('Unsubscribed', '<div class="slabel">SETTINGS</div>'
        . '<p>Both products are off, so nothing further will be sent.</p>');
}

db()->prepare('UPDATE subscriber SET areas=?, daily=?, weekly=?, unsubscribed=\'\' WHERE email=?')
    ->execute([$areas, $daily, $weekly, $email]);

page('Saved', '<div class="slabel">SETTINGS</div><p>Saved.</p>'
    . '<p><a href="' . h(link_for($email, 'manage', 'manage.php')) . '">Back to your settings</a>.</p>');
