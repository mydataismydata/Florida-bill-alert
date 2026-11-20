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
 *
 * One payload is not a digest: a `ready` payload, written by `flba requests
 * answer`, names the requested bills whose analysis is now up. Those go to
 * the people who asked (answer_requests below), not to subscribers.
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

// The confirmation email promises an unused address is removed after seven
// days. This keeps that promise. The link has expired by then (confirm.php),
// so the row can never become a subscriber and is only an address held for
// no reason. It runs before the payload check so it happens on every run.
$gone = db()->prepare("DELETE FROM subscriber WHERE confirmed_at = '' AND confirm_sent < ?");
$gone->execute([gmdate('Y-m-d H:i:s', time() - 7 * 86400)]);
if ($gone->rowCount()) {
    fwrite(STDOUT, sprintf("[%s] removed %d unconfirmed\n", now(), $gone->rowCount()));
}

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
    if (!is_array($doc)) {
        continue;
    }
    // Analysis requests answered. Not a digest: it goes to the people who
    // asked, not to subscribers, and carries no `bills` list, so a sender
    // that predates it skips it rather than mailing it to everyone.
    if (($doc['product'] ?? '') === 'ready') {
        if ($limit && $sent >= $limit) {
            break;
        }
        $sent += answer_requests($pdo, $doc, $limit ? $limit - $sent : 0);
        continue;
    }
    if (empty($doc['bills'])) {
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
    return 'Know Your Legislation — the week in the ' . ($doc['session'] ?? '') . ' session';
}

function body_for(array $doc, array $bills, string $email): string {
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
        $out[] = '  ' . page_url($b);
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

/**
 * The bill's page. The payload names it; one written before payloads did is
 * a 2026 bill, whose pages sit directly under bills/.
 */
function page_url(array $b): string {
    $path = (string) ($b['path'] ?? '');
    if (!preg_match('#^bills/(?:\d{4}[A-Z]?/)?\d{1,6}\.html$#', $path)) {
        $path = 'bills/' . (int) ($b['num'] ?? 0) . '.html';
    }
    return rtrim(cfg()['base_url'], '/') . '/' . $path;
}

/**
 * Write to the people who asked for an analysis that is now up, and close
 * their requests. The payload names the bills whose published page now
 * carries an analysis, and the requests for bills that do not exist. Who
 * asked never left this server; it is matched here.
 *
 * Each entry closes only requests made up to the `upto` it carries -- the
 * newest one the box saw. Payloads stay in the folder and are read on every
 * run, so without that bound an old one would close a request made long
 * after it, for a bill it knew nothing about.
 *
 * A request is claimed before it is answered, the address cleared with the
 * claim: the address was given for this one message. An address gets one
 * message per payload however many of its bills are in it.
 */
function answer_requests(PDO $pdo, array $doc, int $room): int {
    $close = $pdo->prepare("UPDATE request SET fulfilled = ?, email = ''
                             WHERE session = ? AND num = ? AND fulfilled = '' AND created_at <= ?");
    foreach ((array) ($doc['unknown'] ?? []) as $u) {
        if (is_array($u)) {
            $close->execute([now(), (string) ($u['session'] ?? ''), (int) ($u['num'] ?? 0),
                             (string) ($u['upto'] ?? '')]);
        }
    }

    $ready = [];
    foreach ((array) ($doc['ready'] ?? []) as $b) {
        if (is_array($b) && (int) ($b['num'] ?? 0) > 0) {
            $ready[(string) ($b['session'] ?? '') . '/' . (int) $b['num']] = $b;
        }
    }
    if (!$ready) {
        return 0;
    }

    $claim = $pdo->prepare("UPDATE request SET fulfilled = ?, email = ''
                             WHERE id = ? AND fulfilled = ''");
    $asked = [];                       // address => [bill key => [request ids]]
    foreach ($pdo->query("SELECT id, session, num, email, created_at FROM request
                           WHERE fulfilled = '' ORDER BY id") as $r) {
        $key = $r['session'] . '/' . (int) $r['num'];
        if (!isset($ready[$key]) || (string) $r['created_at'] > (string) ($ready[$key]['upto'] ?? '')) {
            continue;
        }
        if ((string) $r['email'] === '') {
            $claim->execute([now(), $r['id']]);   // nobody to tell; just done
            continue;
        }
        $asked[(string) $r['email']][$key][] = (int) $r['id'];
    }

    $sent = 0;
    foreach ($asked as $email => $keys) {
        if ($room && $sent >= $room) {
            break;                     // the rest stay open for the next run
        }
        $mine = [];
        foreach ($keys as $key => $ids) {
            foreach ($ids as $id) {
                $claim->execute([now(), $id]);
                if ($claim->rowCount() === 1) {
                    $mine[$key] = $ready[$key];
                }
            }
        }
        if (!$mine) {
            continue;                  // another run has them
        }
        $n = count($mine);
        $ok = send_mail(
            $email,
            $n === 1 ? 'The analysis you asked for is ready'
                     : "The $n analyses you asked for are ready",
            ready_body(array_values($mine))
        );
        if (!$ok) {
            fwrite(STDERR, "send failed: request answer to $email\n");
        }
        $sent++;
    }
    return $sent;
}

function ready_body(array $bills): string {
    $out   = [];
    $out[] = count($bills) === 1
        ? 'You asked Know Your Legislation to analyse a bill. The analysis is on its page now.'
        : 'You asked Know Your Legislation to analyse these bills. The analyses are on their pages now.';
    $out[] = '';
    foreach ($bills as $b) {
        $out[] = ($b['label'] ?? '') . ' (' . ($b['session'] ?? '') . ')';
        $out[] = (string) ($b['title'] ?? '');
        if (!empty($b['one_line'])) {
            $out[] = '  ' . $b['one_line'];
        }
        $out[] = '  ' . page_url($b);
        $out[] = '';
    }
    $out[] = str_repeat('-', 56);
    $out[] = 'This is the only message about this request. The address was';
    $out[] = 'kept for it alone, is on no list, and has now been removed.';
    $out[] = '';
    $out[] = 'Every fact outside the marked analysis box is read mechanically';
    $out[] = 'from the official record at flsenate.gov.';
    return implode("\n", $out) . "\n";
}
