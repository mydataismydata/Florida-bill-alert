<?php
/**
 * The open analysis requests, for the analysis box to read. Command line
 * only: the box runs it over SSH each night, before it analyses anything.
 *
 *   docker exec -u 33 sessionwatch-php php /srv/sessionwatch/site/requests.php
 *
 * One line per bill: which bill, when it was first and last asked for, and
 * by how many requests. Nothing about who asked. The addresses stay on this
 * server; send.php writes to them once the analysis is up, so the box never
 * needs them.
 *
 * Read-only. A request is closed by send.php, when a payload from the box
 * says the bill's page now carries its analysis.
 */
declare(strict_types=1);
require __DIR__ . '/lib.php';

if (PHP_SAPI !== 'cli') {          // never reachable over HTTP
    http_response_code(404);
    exit;
}

$rows = db()->query(
    "SELECT session, num, MIN(created_at) AS first, MAX(created_at) AS last,
            COUNT(*) AS n
       FROM request
      WHERE fulfilled = ''
      GROUP BY session, num
      ORDER BY first
      LIMIT 2000"
)->fetchAll();

$out = [];
foreach ($rows as $r) {
    $out[] = [
        'session' => (string) $r['session'],
        'num'     => (int) $r['num'],
        'first'   => (string) $r['first'],
        'last'    => (string) $r['last'],
        'n'       => (int) $r['n'],
    ];
}
echo json_encode($out, JSON_UNESCAPED_SLASHES), "\n";
