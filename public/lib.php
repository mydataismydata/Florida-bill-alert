<?php
/**
 * Everything the endpoints share: the database, the token scheme, the mailer,
 * and the one list of areas that must not drift.
 */
declare(strict_types=1);

function cfg(): array {
    static $c = null;
    if ($c === null) {
        $path = __DIR__ . '/config.php';
        if (!is_file($path)) {
            http_response_code(500);
            exit('Not configured.');
        }
        $c = require $path;
    }
    return $c;
}

function db(): PDO {
    static $pdo = null;
    if ($pdo === null) {
        $c = cfg();
        $pdo = new PDO($c['dsn'], $c['user'] ?? null, $c['pass'] ?? null, [
            PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
            PDO::ATTR_EMULATE_PREPARES   => false,
        ]);
    }
    return $pdo;
}

/**
 * The thirteen, and only these. A subscriber's interests are stored as these
 * slugs, so anything not on the list is dropped rather than saved as free text
 * -- otherwise a typo becomes a subscription that can never match a bill.
 */
const AREAS = [
    'agriculture', 'ai-technology', 'criminal-justice', 'development-land-use',
    'education', 'elections', 'environment-water', 'healthcare', 'housing',
    'insurance', 'local-government', 'taxes-budget', 'transportation',
];

function clean_areas(array $given): array {
    return array_values(array_intersect(AREAS, array_map('strval', $given)));
}

/**
 * Manage and unsubscribe links carry this. Derived rather than stored, so a
 * link keeps working without a row to look it up in, and every link ever sent
 * can be revoked at once by changing the secret.
 */
function sign(string $email, string $purpose): string {
    return hash_hmac('sha256', $purpose . "\0" . strtolower($email), cfg()['secret']);
}

function signed_ok(string $email, string $purpose, string $token): bool {
    return hash_equals(sign($email, $purpose), $token);   // timing-safe
}

function link_for(string $email, string $purpose, string $page): string {
    return rtrim(cfg()['base_url'], '/') . '/' . $page
         . '?e=' . rawurlencode($email) . '&t=' . sign($email, $purpose);
}

function valid_email(string $e): bool {
    // Length bound first: filter_var is happy with addresses no MTA will take.
    return strlen($e) <= 254 && filter_var($e, FILTER_VALIDATE_EMAIL) !== false;
}

/**
 * Send one message. Headers are assembled here and nowhere else, and the
 * address is validated before it reaches them -- a newline in a header is how
 * a form becomes an open relay.
 */
function send_mail(string $to, string $subject, string $text, array $extra = []): bool {
    if (!valid_email($to)) {
        return false;
    }
    $c = cfg();
    $subject = str_replace(["\r", "\n"], ' ', $subject);
    $headers = [
        'From'         => sprintf('%s <%s>', $c['from_name'], $c['from_email']),
        'Reply-To'     => $c['reply_to'] ?? $c['from_email'],
        'MIME-Version' => '1.0',
        'Content-Type' => 'text/plain; charset=utf-8',
        'X-Mailer'     => 'session-watch',
    ] + $extra;

    $lines = [];
    foreach ($headers as $k => $v) {
        $lines[] = $k . ': ' . str_replace(["\r", "\n"], ' ', (string) $v);
    }
    return mail($to, $subject, $text, implode("\r\n", $lines),
                '-f' . $c['from_email']);
}

/** Both unsubscribe headers, so a one-click button in a mail client works. */
function unsubscribe_headers(string $email): array {
    $url = link_for($email, 'manage', 'unsubscribe.php');
    return [
        'List-Unsubscribe'      => '<' . $url . '>',
        'List-Unsubscribe-Post' => 'List-Unsubscribe=One-Click',
    ];
}

function h(?string $s): string {
    return htmlspecialchars((string) $s, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function now(): string { return gmdate('Y-m-d H:i:s'); }

/** A plain page in the site's voice, for the handful of times PHP renders one. */
function page(string $title, string $body, int $code = 200): void {
    http_response_code($code);
    header('Content-Type: text/html; charset=utf-8');
    $base = rtrim(cfg()['base_url'], '/');
    echo <<<HTML
<!doctype html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{$title} · Session Watch</title>
<link rel="stylesheet" href="{$base}/style.css">
<script>try{var t=localStorage.getItem('sw-theme');if(t)document.documentElement.setAttribute('data-theme',t)}catch(e){}</script>
</head>
<body>
<header class="masthead compact">
  <div class="wordmark"><a href="{$base}/index.html">SESSION WATCH</a></div>
  <nav><a href="{$base}/index.html">BILLS</a><a href="{$base}/about.html">ABOUT</a></nav>
</header>
<main><div class="prose">{$body}</div></main>
</body>
</html>
HTML;
    exit;
}
