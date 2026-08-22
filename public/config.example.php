<?php
/**
 * Copy to config.php on the server and fill in. config.php is never committed
 * and never deployed -- it is uploaded once, by hand, and left alone.
 */
return [
    // PDO DSN. IONOS shared hosting provides MySQL; SQLite works too and needs
    // no provisioning, which is enough for a list of this size.
    'dsn'  => 'mysql:host=localhost;dbname=sessionwatch;charset=utf8mb4',
    'user' => 'dbuser',
    'pass' => 'dbpass',

    // Signs the manage/unsubscribe links. Any long random string; rotating it
    // invalidates every link already sent, which is the intended emergency stop.
    'secret' => 'change-me-to-64-random-characters',

    // Envelope. Use a dedicated sending subdomain so a reputation problem here
    // cannot reach your ordinary mail.
    'from_name'  => 'Session Watch',
    'from_email' => 'alerts@mail.example.org',
    'reply_to'   => 'hello@example.org',

    // Where the site is served from, for building links in emails.
    'base_url' => 'https://billtrack.example.org',

    // Where `flba digest` drops payloads, relative to this directory.
    'mail_dir' => __DIR__ . '/mail',
];
