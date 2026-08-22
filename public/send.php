<?php
/**
 * The sender. Runs from cron; sends whatever payloads the analysis box has
 * pushed and nobody has been sent yet.
 *
 * Every five minutes, written without the slash-star form so it cannot close
 * this comment:
 *
 *   0,5,10,15,20,25,30,35,40,45,50,55 * * * *  /usr/bin/php /path/to/send.php
 *
 * A payload is a JSON file written by `flba digest` and shipped in the same
 * bundle as the HTML. This script never composes content -- it addresses and
 * sends what the box already wrote, which is what keeps the composing side
 * private.
 *
 * Delivery is recorded per subscriber per payload, with a unique index behind
 * it, so a cron that overlaps itself or a run that dies halfway cannot send
 * the same digest twice.
 */
declare(strict_types=1);
require __DIR__ . '/lib.php';

if (PHP_SAPI !== 'cli') {          // never reachable over HTTP
    http_response_code(404);
    exit;
}

$cfg   = cfg();
$dir   = $cfg['mail_dir'];
$limit = (int) ($argv[1] ?? 0);    // optional cap per run, for a first live test

if (!is_dir($dir)) {
    fwrite(STDERR, "no mail directory at $dir\n");
    exit(1);
}

$payloads = glob(rtrim($dir, '/') . '/*.json') ?: [];
sort($payloads);
if (!$payloads) {
    exit(0);
}

$pdo  = db();
$sent = 0;

foreach ($payloads as $path) {
    $name = basename($path);
    $doc  = json_decode((string) file_get_contents($path), true);
    if (!is_array($doc) || empty($doc['bills'])) {
        continue;
    }
    $product = ($doc['product'] ?? 'weekly') === 'daily' ? 'daily' : 'weekly';

    // The daily alert is filtered by the areas a subscriber chose; the weekly
    // digest goes to everyone confirmed, which is what the two products are.
    $rows = $pdo->query(
        "SELECT * FROM subscriber
          WHERE confirmed_at <> '' AND unsubscribed = '' AND bounces < 5
            AND $product = 1"
    )->fetchAll();

    foreach ($rows as $sub) {
        if ($limit && $sent >= $limit) {
            break 2;
        }
        $bills = $doc['bills'];
        if ($product === 'daily') {
            $mine  = array_filter(explode(',', (string) $sub['areas']));
            $bills = array_values(array_filter($bills, static function ($b) use ($mine) {
                return $mine && in_array($b['area_slug'] ?? '', $mine, true);
            }));
            if (!$bills) {
                continue;             // nothing in their areas today
            }
        }

        // Claim the send before making it. The unique index is what actually
        // prevents a duplicate; the insert failing means someone else has it.
        try {
            $pdo->prepare('INSERT INTO sent (subscriber_id, payload, sent_at) VALUES (?,?,?)')
                ->execute([$sub['id'], $name, now()]);
        } catch (PDOException $e) {
            continue;                 // already sent to this person
        }

        $ok = send_mail(
            $sub['email'],
            subject_for($doc, count($bills)),
            body_for($doc, $bills, $sub['email']),
            unsubscribe_headers($sub['email'])
        );
        if (!$ok) {
            $pdo->prepare('UPDATE subscriber SET bounces = bounces + 1 WHERE id = ?')
                ->execute([$sub['id']]);
            fwrite(STDERR, "send failed: {$sub['email']} $name\n");
        }
        $sent++;
    }
}

fwrite(STDOUT, sprintf("[%s] sent %d\n", now(), $sent));


function subject_for(array $doc, int $n): string {
    if (($doc['product'] ?? '') === 'daily') {
        return sprintf('%d new bill%s in your areas of interest', $n, $n === 1 ? '' : 's');
    }
    return 'Session Watch — the week in the ' . ($doc['session'] ?? '') . ' session';
}

function body_for(array $doc, array $bills, string $email): string {
    $base = rtrim(cfg()['base_url'], '/');
    $out  = [];
    $out[] = ($doc['product'] ?? '') === 'daily'
        ? 'New bills in the areas you follow.'
        : 'What moved in the Legislature this week.';
    $out[] = '';
    foreach ($bills as $b) {
        $out[] = strtoupper((string) ($b['area'] ?? 'GENERAL')) . ' — ' . ($b['label'] ?? '');
        $out[] = (string) ($b['title'] ?? '');
        if (!empty($b['one_line'])) {
            $out[] = '  ' . $b['one_line'];
        }
        $out[] = '  ' . $base . '/bills/' . (int) ($b['num'] ?? 0) . '.html';
        $out[] = '';
    }
    $out[] = str_repeat('-', 56);
    $out[] = 'Every fact outside the marked analysis box is read mechanically';
    $out[] = 'from the official record at flsenate.gov.';
    $out[] = '';
    $out[] = 'Manage topics: ' . link_for($email, 'manage', 'manage.php');
    $out[] = 'Unsubscribe:   ' . link_for($email, 'manage', 'unsubscribe.php');
    return implode("\n", $out) . "\n";
}
