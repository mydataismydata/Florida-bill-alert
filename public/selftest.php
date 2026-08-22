<?php
/**
 * Checks the parts that are easy to get quietly wrong: the token scheme, the
 * area enum, and the guarantee that nobody receives the same digest twice.
 * Run it on the server once after setup, against a scratch database.
 *
 *   php selftest.php
 *
 * It writes only to the database named in config.php, so point that at a
 * throwaway file the first time.
 */
declare(strict_types=1);
require __DIR__ . '/lib.php';

if (PHP_SAPI !== 'cli') { http_response_code(404); exit; }

$fail = 0;
function check(string $what, bool $ok): void {
    global $fail;
    if (!$ok) { $fail++; }
    printf("  %-34s %s\n", $what, $ok ? 'ok' : 'FAIL');
}

$pdo = db();
$sql = preg_replace('/^\s*--.*$/m', '', (string) file_get_contents(__DIR__ . '/schema.sql'));
foreach (explode(';', $sql) as $stmt) {
    if (trim($stmt) !== '') { $pdo->exec($stmt); }
}
check('schema applies', true);

$e = 'Reader@Example.test';
$t = sign($e, 'manage');
check('signed link verifies',        signed_ok($e, 'manage', $t));
check('a different purpose fails',   !signed_ok($e, 'confirm', $t));
check('a tampered token fails',      !signed_ok($e, 'manage', substr($t, 0, -1) . '0'));
check('address case does not matter', signed_ok(strtolower($e), 'manage', $t));

check('unknown areas are dropped',
      clean_areas(['education', 'not-an-area', 'housing', '<script>']) === ['education', 'housing']);
check('newline in address rejected', !valid_email("a@b.test\nBcc: victim@x.test"));
check('overlong address rejected',   !valid_email(str_repeat('a', 250) . '@b.test'));

$pdo->prepare('INSERT INTO subscriber (email, areas, daily, weekly, confirmed_at, created_at)
               VALUES (?,?,?,?,?,?)')
    ->execute(['selftest@example.test', 'education', 1, 1, now(), now()]);
$id = (int) $pdo->lastInsertId();

$ins = $pdo->prepare('INSERT INTO sent (subscriber_id, payload, sent_at) VALUES (?,?,?)');
$ins->execute([$id, 'selftest.json', now()]);
$twice = false;
try { $ins->execute([$id, 'selftest.json', now()]); }
catch (PDOException $ex) { $twice = true; }
check('the same digest cannot go twice', $twice);

$pdo->prepare('UPDATE subscriber SET unsubscribed = ? WHERE id = ?')->execute([now(), $id]);
$n = $pdo->query("SELECT COUNT(*) c FROM subscriber
                   WHERE confirmed_at <> '' AND unsubscribed = ''")->fetch();
check('unsubscribed are excluded', (int) $n['c'] === 0);

$pdo->prepare('DELETE FROM sent WHERE subscriber_id = ?')->execute([$id]);
$pdo->prepare('DELETE FROM subscriber WHERE id = ?')->execute([$id]);

echo $fail ? "\n$fail check(s) failed\n" : "\nall checks passed\n";
exit($fail ? 1 : 0);
