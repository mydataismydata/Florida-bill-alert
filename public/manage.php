<?php
/**
 * The pre-authenticated preferences page. The signed link in every email is
 * the credential -- there are no accounts and no passwords to lose.
 */
declare(strict_types=1);
require __DIR__ . '/lib.php';

$email = strtolower(trim((string) ($_GET['e'] ?? '')));
$token = (string) ($_GET['t'] ?? '');
$base  = rtrim(cfg()['base_url'], '/');

if (!valid_email($email) || !signed_ok($email, 'manage', $token)) {
    page('Link not valid', '<div class="slabel">SETTINGS</div>'
        . '<p>That link is not valid. Use the one at the foot of any email we sent you.</p>', 400);
}

$st = db()->prepare('SELECT * FROM subscriber WHERE email = ?');
$st->execute([$email]);
$sub = $st->fetch();
if (!$sub) {
    page('Not subscribed', '<div class="slabel">SETTINGS</div>'
        . '<p>That address is not on the list. '
        . '<a href="' . h($base) . '/subscribe.html">Subscribe</a>.</p>', 404);
}

$mine  = explode(',', (string) $sub['areas']);
$names = [
    'agriculture' => 'Agriculture', 'ai-technology' => 'AI & Technology',
    'criminal-justice' => 'Criminal Justice', 'development-land-use' => 'Development & Land Use',
    'education' => 'Education', 'elections' => 'Elections',
    'environment-water' => 'Environment & Water', 'healthcare' => 'Healthcare',
    'housing' => 'Housing', 'insurance' => 'Insurance',
    'local-government' => 'Local Government', 'taxes-budget' => 'Taxes & Budget',
    'transportation' => 'Transportation',
];

$boxes = '';
foreach (AREAS as $slug) {
    $on = in_array($slug, $mine, true) ? ' checked' : '';
    $boxes .= '<label><input type="checkbox" name="areas[]" value="' . h($slug) . '"'
            . $on . '><span class="box">&#10003;</span>' . h($names[$slug]) . '</label>';
}
$d = $sub['daily']  ? ' checked' : '';
$w = $sub['weekly'] ? ' checked' : '';
$gone = $sub['unsubscribed']
      ? '<p class="unverified">This address is currently unsubscribed. Saving '
      . 'settings below will start it again.</p>' : '';

page('Your settings', <<<HTML
<div class="slabel">SETTINGS FOR {$email}</div>
{$gone}
<form method="post" action="{$base}/preferences.php">
  <input type="hidden" name="e" value="{$email}">
  <input type="hidden" name="t" value="{$token}">
  <p><label><input type="checkbox" name="daily" value="1"{$d}><span class="box">&#10003;</span>Daily alerts in session</label></p>
  <p><label><input type="checkbox" name="weekly" value="1"{$w}><span class="box">&#10003;</span>Weekly digest</label></p>
  <div class="slabel">AREAS OF INTEREST</div>
  <div class="grid3">{$boxes}</div>
  <p style="margin-top:22px">
    <button class="btn" type="submit">Save</button>
    <a class="btn ghost" href="{$base}/unsubscribe.php?e={$email}&t={$token}">Unsubscribe</a>
  </p>
</form>
HTML);
