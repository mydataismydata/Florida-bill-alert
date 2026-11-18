# Running the public site on the OVH box

Know Your Legislation's public half is a static tree, six PHP endpoints, and a
subscriber database. This directory holds everything the server needs to run
that, and nothing else.

The pipeline is not here and does not belong here. `src/` never reaches this
machine: what arrives is the built site and the container definition below.
That is what lets the README say the public host has no model, no inference
endpoint and no pipeline code — it is a property of what gets copied, not a
promise about how the code behaves.

## What runs where

```
  nginx (host, shared with PAC Tracker)
    static      /srv/sessionwatch/site        read straight off disk
    six .php    127.0.0.1:9010                sessionwatch-php container
                     |
                     +-- SQLite  /srv/sessionwatch/data/sessionwatch.sqlite
                     +-- msmtp   relay, for mail() to keep working unchanged
```

8,000 HTML files are served by nginx directly. Only the endpoints reach PHP,
and only six of them: nginx refuses every other `.php` by name rather than by
pattern, so a file added to the tree cannot become executable by accident.

## Names this application has taken

Copy these into the box's handover note before bringing another application to
it. Ports and names are first-come and nothing enforces them, so that note is
the only thing standing between the next application and a collision.

| Kind | Know Your Legislation uses |
| --- | --- |
| Host port (loopback) | `9010` |
| Container | `sessionwatch-php` |
| Compose project | `session-watch` |
| Docker network | `session-watch_default` |
| nginx site | `sessionwatch` |
| nginx snippet | `snippets/sessionwatch-body.conf` |
| limit_req zone | `sessionwatch_php` |
| Paths | `/srv/sessionwatch/{site,data,stack}` |

`limit_req_status` is set by PAC Tracker and is not set here. It takes one
value for the whole `http` block, so a second declaration is a duplicate
directive error, not an override. nginx refuses to start, which takes PAC
Tracker down as well.

## Layout on the server

```
/srv/sessionwatch/
  stack/    this directory, rsynced. Plus two files made here and never committed:
              msmtprc                     relay credentials, 0600, owned by uid 33
              docker-compose.override.yml server-specific settings
  site/     the built site. Replaced by every deploy.
  data/     the SQLite database. Outside the web root, owned by uid 33.
```

`data/` is outside `site/` on purpose. nginx serves `site/` and nothing above
it, so no request can name the database however it is spelled.

## First install

1. **Directories.** `data/` is written by www-data inside the container, which
   is uid 33 on the host. SQLite writes its journal beside the database, so the
   directory needs to be writable and not just the file.

   ```bash
   sudo mkdir -p /srv/sessionwatch/{site,data,stack} /var/www/certbot
   sudo chown -R ubuntu:ubuntu /srv/sessionwatch
   sudo chown -R 33:33 /srv/sessionwatch/data
   sudo chown -R www-data:www-data /var/www/certbot
   ```

2. **Stack.** From the analysis box. The schema travels with it, from
   `public/` rather than a second copy kept here, so the two cannot drift.

   ```bash
   rsync -az --delete --filter 'protect schema.sql' --filter 'protect msmtprc' --filter 'protect docker-compose.override.yml' deploy/ovh/ user@203.0.113.10:/srv/sessionwatch/stack/
   ```

   ```bash
   rsync -az public/schema.sql user@203.0.113.10:/srv/sessionwatch/stack/
   ```

   The protect filters matter. `--delete` mirrors this directory, and three of
   the files in the destination are not in the source: the schema that arrives
   in the second command, and the two made on the server in the next steps.
   Docker creates a *directory* where a bind mount's source is missing, so a
   deleted `msmtprc` does not fail loudly — it comes back as a directory msmtp
   cannot read, and mail stops.

3. **Relay credentials.** msmtp refuses a config file others can read, and a
   bind-mounted file keeps the host's ownership, so both have to be set here.

   ```bash
   cd /srv/sessionwatch/stack && cp msmtprc.example msmtprc
   sudo chown 33:33 msmtprc && sudo chmod 600 msmtprc
   ```

4. **Check the port bindings before starting anything.** Docker writes its
   rules ahead of ufw's, so a port published without the loopback prefix is
   open to the internet whatever `ufw status` reports.

   ```bash
   cd /srv/sessionwatch/stack && docker compose config | grep -A4 published
   ```

   Every `host_ip` must read `127.0.0.1`.

5. **Container.**

   ```bash
   cd /srv/sessionwatch/stack && docker compose build && docker compose up -d
   ```

6. **nginx.** Install the rate limit zone, the shared body, and the bootstrap
   server block. The bootstrap serves the whole site over plain HTTP, so
   everything can be tested before a certificate exists and before DNS moves.

   ```bash
   cd /srv/sessionwatch/stack/nginx
   sudo cp sessionwatch-limits.conf /etc/nginx/conf.d/
   sudo mkdir -p /etc/nginx/snippets && sudo cp sessionwatch-body.conf sessionwatch-headers.conf /etc/nginx/snippets/
   sudo cp sessionwatch-bootstrap.conf /etc/nginx/sites-available/sessionwatch
   sudo ln -sfn /etc/nginx/sites-available/sessionwatch /etc/nginx/sites-enabled/sessionwatch
   ```

   ```bash
   sudo nginx -t
   ```

   Only after that passes:

   ```bash
   sudo systemctl reload nginx
   ```

7. **Config.** Copy `public/config.example.php` to
   `/srv/sessionwatch/site/config.php` and fill it in. It is uploaded by hand,
   never committed, and never overwritten by a deploy — `scripts/deploy.sh`
   protects it from `--delete` by name.

   ```php
   'dsn'      => 'sqlite:/srv/sessionwatch/data/sessionwatch.sqlite',
   'user'     => null,
   'pass'     => null,
   'base_url' => 'https://kyl.sjcrlc.org',
   'mail_dir' => __DIR__ . '/mail',
   ```

   `secret` has to be carried over from the old server unchanged. It signs
   every manage and unsubscribe link ever emailed, and a new value silently
   breaks all of them.

8. **Database.** The file has to exist and be writable by uid 33 before the
   first request. There is no `sqlite3` binary on this box or in the image, and
   there is no reason to add one — the container's PHP already knows how to
   apply the schema, and running it as uid 33 is what makes the resulting file
   writable by the pool later.

   ```bash
   docker exec -u 33 sessionwatch-php php -r '$p = new PDO("sqlite:/srv/sessionwatch/data/sessionwatch.sqlite"); $sql = preg_replace("/^\\s*--.*$/m", "", file_get_contents(getenv("FLBA_SCHEMA"))); foreach (explode(";", $sql) as $s) { if (trim($s) !== "") { $p->exec($s); } } echo "schema applied\\n";'
   ```

   Then check the whole chain — token scheme, area list, and the guarantee that
   nobody receives the same digest twice. Point `config.php` at a scratch
   database the first time; it writes a row and deletes it again.

   ```bash
   docker exec -u 33 sessionwatch-php php /srv/sessionwatch/site/selftest.php
   ```

   Ten checks, all reading `ok`. `schema.sql` is not in the site tree, where a
   web server would hand it out as a plain file, so the container mounts it
   separately and `FLBA_SCHEMA` says where.

## Certificate

The site is served at `kyl.sjcrlc.org`. `sessionwatch.sjcrlc.org` is its old
name and only redirects there, but it still needs a certificate of its own to
do that over HTTPS.

Both names have to point at the server's address before an HTTP-01 challenge can
succeed, which is the one step that cannot be done ahead of the move. Neither
name may have an AAAA record. Let's Encrypt tries IPv6 first, so an AAAA
record left pointing at the DNS host's parking page fails the challenge.

```bash
sudo certbot certonly --webroot -w /var/www/certbot -d kyl.sjcrlc.org
```

```bash
sudo certbot certonly --webroot -w /var/www/certbot -d sessionwatch.sjcrlc.org
```

`certonly` rather than `--nginx` on purpose: `--nginx` rewrites the site file
in place, and the file in this directory would stop being the one that is
running. Then swap the bootstrap block for the TLS one:

```bash
sudo cp /srv/sessionwatch/stack/nginx/sessionwatch.conf /etc/nginx/sites-available/sessionwatch && sudo nginx -t
```

```bash
sudo systemctl reload nginx
```

`certbot.timer` renews every certificate on this box, PAC Tracker's included.
Never disable it. To retire one domain, delete that lineage alone:

```bash
sudo certbot delete --cert-name olddomain.example.com
```

## Sending

The cron the old host ran, adjusted to enter the container. The guard means a
missing config file is a no-op rather than five minutes of noise:

```
*/5 * * * * [ -f /srv/sessionwatch/site/config.php ] && /usr/bin/docker exec sessionwatch-php php /srv/sessionwatch/site/send.php >> /var/log/sessionwatch-send.log 2>&1
```

Mail leaves through the relay in `msmtprc`, not from this box directly. This
machine's reverse DNS is `ns1002847.ip-51-81-49.us` and the sending domain
publishes no SPF record naming its address, so mail sent straight from here is
junked or refused. That failure is invisible from this end: a confirmation
link that never arrives looks exactly like a reader who never clicked.

## Changing the relay credentials

```bash
sudo -e /srv/sessionwatch/stack/msmtprc
```

```bash
cd /srv/sessionwatch/stack && docker compose up -d --force-recreate php
```

The second command is not optional. Docker binds a single file by its inode,
and `sudoedit` saves by writing a new file and renaming it over the old one.
The running container goes on reading the inode that is no longer there, so
without a recreate the edit appears to have done nothing and mail keeps
failing the way it failed before.

## Deploying afterwards

From the analysis box, unchanged from any other day:

```bash
scripts/deploy.sh --go
```

`--delete` keeps the tree an exact mirror. Two things in the destination are
not in the source and are protected from it by name: `config.php`, and `mail/`
where unsent digest payloads wait for the next cron run.
