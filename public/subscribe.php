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
    if (wants_json()) {
        reply(false, 'That address does not look right. Check it and try again.', 400);
    }
    page('Check the address', '<div class="slabel">SUBSCRIBE</div>'
        . '<p>That address does not look right. '
        . '<a href="' . h(rtrim(cfg()['base_url'], '/')) . '/subscribe.html">Try again</a>.</p>', 400);
}

// The form carries a box that people never see. A script filling in every
// field it finds fills this one too, and gets the same answer a person would,
// so nothing tells it the post was dropped.
if (trim((string) ($_POST['contact_by_fax_only'] ?? '')) !== '') {
    answered($email);
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
$row  = $pdo->prepare('SELECT id, confirmed_at, confirm_token, confirm_sent
                         FROM subscriber WHERE email = ?');
$row->execute([strtolower($email)]);
$existing = $row->fetch();

$token   = bin2hex(random_bytes(32));
$day_ago = gmdate('Y-m-d H:i:s', time() - 86400);
$base    = rtrim(cfg()['base_url'], '/');

if ($existing && $existing['confirmed_at']) {
    // Already on the list. Typing an address into a form proves nothing about
    // owning it, so the post changes nothing here: not the topics, and not an
    // unsubscribe, which it used to quietly undo. The owner is sent the signed
    // settings link instead and makes any change from there. The answer is the
    // same one a new address gets, so the form still tells a stranger nothing.
    //
    // On a confirmed row confirm_sent dates that settings mail, which holds it
    // to one a day for the same reason the confirmation is.
    if ((string) $existing['confirm_sent'] < $day_ago) {
        $pdo->prepare('UPDATE subscriber SET confirm_sent=? WHERE id=?')
            ->execute([now(), $existing['id']]);
        send_mail($email, 'Your Know Your Legislation subscription',
            "Someone asked to subscribe this address to Know Your Legislation. "
          . "It is already on the list, so nothing was changed.\n\n"
          . "To change your topics, or to turn mail back on if you unsubscribed, "
          . "use this link:\n\n" . link_for($email, 'manage', 'manage.php') . "\n\n"
          . "If you did not ask for this, ignore this message. Your settings stay "
          . "as they were.\n\n$base\n");
    }
    answered($email);
}

if ($existing) {
    // Changing preferences before confirming. Nothing is sent to an
    // unconfirmed address but the link, so these are free to change.
    //
    // An unconfirmed address gets one confirmation a day, however often it is
    // submitted. Without that, the form can be pointed at a stranger's inbox
    // and fired repeatedly, and an address that does not exist returns every
    // attempt to us as a bounce. Inside the day the first link stays good.
    $needs_confirm = (string) $existing['confirm_sent'] < $day_ago;
    $pdo->prepare(
        'UPDATE subscriber SET name=?, role=?, county=?, areas=?, daily=?, weekly=?,
                confirm_token=?, confirm_sent=?, unsubscribed=\'\' WHERE id=?'
    )->execute([$name, $role, $county, $areas, $daily, $weekly,
                $needs_confirm ? $token : $existing['confirm_token'],
                $needs_confirm ? now() : $existing['confirm_sent'],
                $existing['id']]);
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
    $link = $base . '/confirm.php?e=' . rawurlencode($email) . '&t=' . $token;
    send_mail($email, 'Confirm your Know Your Legislation subscription',
        "Confirm your subscription to Know Your Legislation:\n\n$link\n\n"
      . "The link works once. If you did not ask for this, ignore this message "
      . "-- nothing is sent until it is used, and the address is removed after "
      . "seven days.\n\n$base\n");
}

answered($email);

/**
 * The one answer to every post that got this far, sent or not. A second try
 * inside the day sends nothing, so the answer says so for everyone rather
 * than leaving a person waiting for a link that is not coming.
 */
function answered(string $email): void {
    if (wants_json()) {
        reply(true, 'A confirmation link is on its way to ' . $email . '. Nothing is sent '
            . 'until you use it. If it does not arrive within a few minutes, check the spam '
            . 'folder. One link is sent a day, so trying again today will not send another.');
    }
    page('Check your email', '<div class="slabel">SUBSCRIBE</div>'
        . '<p>A confirmation link is on its way to <strong>' . h($email) . '</strong>. '
        . 'Nothing is sent until you use it.</p>'
        . '<p>If it does not arrive within a few minutes, check the spam folder. '
        . 'One link is sent a day, so trying again today will not send another.</p>');
}
