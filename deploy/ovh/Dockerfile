# PHP-FPM for the handful of endpoints the public site needs.
#
# The static site is not in this image and is not served by it. nginx on the
# host reads the built tree straight off disk; this container exists only so
# that six PHP files have an interpreter. That split is deliberate: 8,000
# static files should not travel through a second web server to reach a reader.
#
# The base image already carries pdo_sqlite, sqlite3 and mbstring, which is
# everything the endpoints use. Only a way to send mail has to be added.
FROM php:8.3-fpm

# msmtp-mta installs /usr/sbin/sendmail, which is the path PHP's mail() calls.
# The code therefore needs no change: mail() keeps working exactly as it did on
# shared hosting, and the relay is configuration rather than a rewrite.
#
# libfcgi-bin provides cgi-fcgi, which the healthcheck uses to ask FPM itself
# whether it is alive. Testing the CLI binary instead would report a healthy
# container while the pool was dead.
RUN apt-get update \
 && apt-get install -y --no-install-recommends msmtp msmtp-mta libfcgi-bin ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY php.ini   /usr/local/etc/php/conf.d/sessionwatch.ini
COPY pool.conf /usr/local/etc/php-fpm.d/zz-sessionwatch.conf

# The database lives outside the web root, so nginx can never serve it as a
# file. www-data has to be able to write both the file and its directory --
# SQLite writes a journal beside the database, not inside it.
RUN mkdir -p /srv/sessionwatch/data && chown www-data:www-data /srv/sessionwatch/data

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD SCRIPT_NAME=/ping SCRIPT_FILENAME=/ping REQUEST_METHOD=GET \
      cgi-fcgi -bind -connect 127.0.0.1:9000 | grep -q pong
