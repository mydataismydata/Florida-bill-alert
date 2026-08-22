<?php
/** The confirmation link. Single use, and expires after a week. */
declare(strict_types=1);
require __DIR__ . '/lib.php';

$email = strtolower(trim((string) ($_GET['e'] ?? '')));
$token = (string) ($_GET['t'] ?? '');
$base  = rtrim(cfg()['base_url'], '/');

$fail = '<div class="slabel">SUBSCRIBE</div><p>That link is not valid any more. '
      . 'It works once, and expires after seven days. '
      . '<a href="' . h($base) . '/subscribe.html">Subscribe again</a>.</p>';

if (!valid_email($email) || strlen($token) !== 64) {
    page('Link not valid', $fail, 400);
}

$st = db()->prepare('SELECT id, confirm_token, confirm_sent, confirmed_at
                       FROM subscriber WHERE email = ?');
$st->execute([$email]);
$sub = $st->fetch();

if ($sub && $sub['confirmed_at'] && !$sub['confirm_token']) {
    page('Already confirmed', '<div class="slabel">SUBSCRIBE</div>'
        . '<p>That subscription is already active. '
        . '<a href="' . h(link_for($email, 'manage', 'manage.php')) . '">Change your settings</a>.</p>');
}
if (!$sub || !$sub['confirm_token'] || !hash_equals($sub['confirm_token'], $token)) {
    page('Link not valid', $fail, 400);
}
if ($sub['confirm_sent'] && strtotime($sub['confirm_sent']) < time() - 7 * 86400) {
    page('Link expired', $fail, 400);
}

db()->prepare('UPDATE subscriber SET confirmed_at=?, confirm_token=\'\', unsubscribed=\'\'
                WHERE id=?')->execute([now(), $sub['id']]);

page('Subscribed', '<div class="slabel">SUBSCRIBE</div>'
    . '<p>Confirmed. You will hear from Session Watch when the Legislature does '
    . 'something in the areas you chose.</p>'
    . '<p><a href="' . h(link_for($email, 'manage', 'manage.php')) . '">Change your settings</a> '
    . 'at any time — every email carries the same link.</p>');
