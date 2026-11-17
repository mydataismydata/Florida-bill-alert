# Email

Two products: a **daily alert** filtered to the areas a subscriber chose, and a
**weekly digest** of everything. Double opt-in, no accounts, no passwords.

## Email route

The public server addresses and delivers. It never composes.

```
  analysis box                          public server
  ------------                          -------------
  flba digest   ──►  site/mail/*.json ──►  send.php  ──►  subscribers
                     (in the bundle)       subscriber table
                                           subscribe / confirm / manage
```

Everything a subscriber reads is written on the analysis (local) box, from the same
database the site is built from, and travels in the same bundle as the HTML.
`send.php` only picks recipients and posts the message. A payload carries bill
label, title, area and the one-line summary. Nothing else, so a payload cannot put markup into an email.

## Setting it up, once

1. **Database.** Create one, then load `public/schema.sql`.

2. **Config.** Copy `public/config.example.php` to `config.php` **on the
   server**, next to the other PHP files, and fill it in. It is gitignored and
   the build never copies it — it is uploaded by hand and left alone. Set
   `secret` to something long and random: it signs every manage and unsubscribe
   link, and changing it invalidates all of them at once, which is an
   emergency stop.

3. **Check it.** `php selftest.php` — the token scheme, the area enum, and the
   guarantee that nobody gets the same digest twice. Point `config.php` at a
   scratch database the first time.

4. **Cron.** Every five minutes. On the OVH box PHP lives in a container, so
   the line enters it rather than calling a binary on the host, and the guard
   makes a missing config file a no-op instead of five minutes of noise:

   ```
   */5 * * * * [ -f /srv/sessionwatch/site/config.php ] && /usr/bin/docker exec sessionwatch-php php /srv/sessionwatch/site/send.php >> /var/log/sessionwatch-send.log 2>&1
   ```

   Pass a number to cap a run while testing: `php send.php 5`.

5. **Relay.** `mail()` needs something behind `/usr/sbin/sendmail`. Shared
   hosting supplied both that and its own sending reputation; a plain server
   supplies neither. Mail sent straight from a machine whose reverse DNS does
   not match the sending domain, and whose address no SPF record names, is
   junked or refused. That failure is invisible from this end -- a confirmation
   link that never arrives looks exactly like a reader who never clicked. See
   [deploy/ovh/README.md](../deploy/ovh/README.md).

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

## Confirmations

An unconfirmed address gets one confirmation email a day, however often the
form is submitted with it. Without that limit the form could be pointed at a
stranger's inbox and fired repeatedly, and an address that does not exist
would return every attempt as a bounce. The form also carries a hidden field
people never see. A post with it filled in gets the usual answer and nothing
else.

The form never changes a confirmed subscriber. Typing an address proves
nothing about owning it, so a post for one already on the list leaves the
topics alone and does not undo an unsubscribe. The owner is emailed the signed
settings link instead, once a day at most, and changes things from there. The
form gives the same answer either way.

`send.php` deletes an unconfirmed address seven days after its last link went
out, which is what the confirmation email promises. The link has expired by
then, so the row could never become a subscriber.

## Ad-hoc requests

The **Request AI analysis** button on an un-analysed bill writes a row and
stops. The analysis box pulls those on its next run; nothing on the public side
can reach inward. That is the one place where a public action leads to work on
the private machine, and it is deliberately a queue rather than a call.
