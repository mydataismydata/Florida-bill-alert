# Email

Two products: a **daily alert** filtered to the areas a subscriber chose, and a
**weekly digest** of everything. Double opt-in, no accounts, no passwords.

## Where the work happens

The public server addresses and delivers. It never composes.

```
  analysis box                          public server (IONOS)
  ------------                          ---------------------
  flba digest   ──►  site/mail/*.json ──►  send.php  ──►  subscribers
                     (in the bundle)       subscriber table
                                           subscribe / confirm / manage
```

Everything a subscriber reads is written on the analysis box, from the same
database the site is built from, and travels in the same bundle as the HTML.
`send.php` only picks recipients and posts the message. A payload carries bill
label, title, area and — when one has passed verification — the one-line
summary. Nothing else, so a payload cannot put markup into an email.

If the public server is fully compromised, the attacker gets a static site and
an address list. Not the model, not the pipeline, not a way in.

## Setting it up, once

1. **Database.** Create one, then load `public/schema.sql`. MySQL is provisioned
   on IONOS shared hosting; SQLite works too and needs nothing.

2. **Config.** Copy `public/config.example.php` to `config.php` **on the
   server**, next to the other PHP files, and fill it in. It is gitignored and
   the build never copies it — it is uploaded by hand and left alone. Set
   `secret` to something long and random: it signs every manage and unsubscribe
   link, and changing it invalidates all of them at once, which is the
   emergency stop.

3. **Check it.** `php selftest.php` — the token scheme, the area enum, and the
   guarantee that nobody gets the same digest twice. Point `config.php` at a
   scratch database the first time.

4. **DNS.** SPF and DKIM are automatic when IONOS is both host and DNS
   provider. **DMARC is the one that is usually missing** and is what decides
   whether a hundred near-identical messages read as a newsletter or as a
   compromised account. Use a dedicated sending subdomain so a reputation
   problem cannot reach your ordinary mail.

5. **Cron.** Every five minutes:

   ```
   0,5,10,15,20,25,30,35,40,45,50,55 * * * * /usr/bin/php /path/to/send.php
   ```

   Pass a number to cap a run while testing: `php send.php 5`.

## Sending

```bash
flba --session 2026 digest                 # both products, into site/mail/
flba --session 2026 digest --product daily
scripts/deploy.sh --go                     # ships payloads with the HTML
```

An empty window writes nothing. A digest that arrives to report that nothing
happened teaches people to ignore the next one.

`send.php` records every delivery against a unique index before making it, so
an overlapping cron or a run that dies halfway cannot send the same digest
twice. An address that fails five times stops being tried.

## Ad-hoc requests

The **Request AI analysis** button on an un-analysed bill writes a row and
stops. The analysis box pulls those on its next run; nothing on the public side
can reach inward. That is the one place where a public action leads to work on
the private machine, and it is deliberately a queue rather than a call.

## What is not built

Bounce processing reads no mailbox — a send that fails at the API level counts
against the address, but a bounce that arrives later by mail does not. At this
list size that is a manual read of the postmaster mailbox.
