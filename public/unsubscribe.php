<?php
/**
 * One click, and one-click POST for mail clients that honour
 * List-Unsubscribe-Post. Both stop the mail immediately; the row stays so a
 * later re-subscribe is not a new confirmation.
 */
declare(strict_types=1);
require __DIR__ . '/lib.php';

$src   = ($_SERVER['REQUEST_METHOD'] ?? '') === 'POST' ? $_POST : $_GET;
$email = strtolower(trim((string) ($src['e'] ?? '')));
$token = (string) ($src['t'] ?? '');

if (!valid_email($email) || !signed_ok($email, 'manage', $token)) {
    page('Link not valid', '<div class="slabel">UNSUBSCRIBE</div>'
        . '<p>That link is not valid.</p>', 400);
}

db()->prepare('UPDATE subscriber SET unsubscribed=? WHERE email=?')->execute([now(), $email]);

if (($_SERVER['REQUEST_METHOD'] ?? '') === 'POST') {
    http_response_code(200);          // one-click: the client wants no page
    exit;
}
page('Unsubscribed', '<div class="slabel">UNSUBSCRIBE</div>'
    . '<p>Done — nothing further will be sent to <strong>' . h($email) . '</strong>.</p>'
    . '<p>If that was a mistake, '
    . '<a href="' . h(link_for($email, 'manage', 'manage.php')) . '">turn it back on</a>.</p>');
