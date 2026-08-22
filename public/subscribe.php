<?php
/**
 * POST from the subscribe form. Always answers the same way, whether or not
 * the address was already on the list -- otherwise the form tells a stranger
 * who is subscribed.
 */
declare(strict_types=1);
require __DIR__ . '/lib.php';

if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
    header('Location: ' . rtrim(cfg()['base_url'], '/') . '/subscribe.html');
    exit;
}

$email = trim((string) ($_POST['email'] ?? ''));
if (!valid_email($email)) {
    page('Check the address', '<div class="slabel">SUBSCRIBE</div>'
        . '<p>That address does not look right. '
        . '<a href="' . h(rtrim(cfg()['base_url'], '/')) . '/subscribe.html">Try again</a>.</p>', 400);
}

$name   = mb_substr(trim((string) ($_POST['name'] ?? '')), 0, 120);
$county = mb_substr(trim((string) ($_POST['county'] ?? '')), 0, 60);
$role   = (string) ($_POST['role'] ?? '');
$role   = in_array($role, ['legislator_staff', 'citizen'], true) ? $role : '';
$areas  = implode(',', clean_areas((array) ($_POST['areas'] ?? [])));
$daily  = empty($_POST['daily'])  ? 0 : 1;
$weekly = empty($_POST['weekly']) ? 0 : 1;
if (!$daily && !$weekly) {
    $weekly = 1;                     // asking for neither is asking for nothing
}

$pdo  = db();
$row  = $pdo->prepare('SELECT id, confirmed_at FROM subscriber WHERE email = ?');
$row->execute([strtolower($email)]);
$existing = $row->fetch();

$token = bin2hex(random_bytes(32));

if ($existing) {
    // Re-subscribing, or changing preferences before confirming. Never reveal
    // which of those it was.
    $pdo->prepare(
        'UPDATE subscriber SET name=?, role=?, county=?, areas=?, daily=?, weekly=?,
                confirm_token=?, confirm_sent=?, unsubscribed=\'\' WHERE id=?'
    )->execute([$name, $role, $county, $areas, $daily, $weekly,
                $existing['confirmed_at'] ? '' : $token, now(), $existing['id']]);
    $needs_confirm = !$existing['confirmed_at'];
} else {
    $pdo->prepare(
        'INSERT INTO subscriber (email, name, role, county, areas, daily, weekly,
                                 confirm_token, confirm_sent, created_at)
         VALUES (?,?,?,?,?,?,?,?,?,?)'
    )->execute([strtolower($email), $name, $role, $county, $areas, $daily, $weekly,
                $token, now(), now()]);
    $needs_confirm = true;
}

if ($needs_confirm) {
    $base = rtrim(cfg()['base_url'], '/');
    $link = $base . '/confirm.php?e=' . rawurlencode($email) . '&t=' . $token;
    send_mail($email, 'Confirm your Session Watch subscription',
        "Confirm your subscription to Session Watch:\n\n$link\n\n"
      . "The link works once. If you did not ask for this, ignore this message "
      . "-- nothing is sent until it is used, and the address is removed after "
      . "seven days.\n\n$base\n");
}

page('Check your email', '<div class="slabel">SUBSCRIBE</div>'
    . '<p>A confirmation link is on its way to <strong>' . h($email) . '</strong>. '
    . 'Nothing is sent until you use it.</p>'
    . '<p>If it does not arrive within a few minutes, check the spam folder.</p>');
