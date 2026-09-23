# Fix HTTPS warning — pivot the dashboard to `merciraymond.fr`

**Status:** the dashboard is reachable today at
`https://dashboard-commercial.srv1082911.hstgr.cloud` but the browser shows
"Not secure". This file is the step-by-step fix.

**Time needed:** ~15 min of work + ~5–30 min of DNS propagation wait.

---

## 1. Why this is needed (1-paragraph context)

Traefik can't get a Let's Encrypt cert for `*.srv1082911.hstgr.cloud` because
Let's Encrypt enforces a global limit of **50 certs per registered domain per
week**, and `hstgr.cloud` is Hostinger's shared base domain — it's permanently
saturated by all Hostinger customers combined. Our requests hit HTTP 429 every
time and Traefik falls back to its self-signed `TRAEFIK DEFAULT CERT`.

The fix is to move the dashboard onto a subdomain of `merciraymond.fr`
(which we own and which has effectively zero traffic against the LE rate
limit). One new DNS record + one label change in compose. Nothing else moves —
n8n, the existing website, and email all stay where they are.

The rate-limit error in Traefik logs (for reference when re-diagnosing):

```
acme: error: 429 :: too many certificates (50000) already issued for
"hstgr.cloud" in the last 168h0m0s
```

---

## 2. Pre-flight — find out where `merciraymond.fr` DNS lives

Run from anywhere (your laptop is fine):

```bash
dig +short NS merciraymond.fr
```

The answer will look like `ns1.<provider>.com`. Common matches:

- `ns*.ovh.net`             → OVH
- `ns*.hostinger.com`       → Hostinger DNS (logged in to hpanel.hostinger.com)
- `ns*.gandi.net`           → Gandi
- `ns*.bookmyname.com`      → Bookmyname
- `ns*.ionos.fr`            → IONOS
- `*.cloudflare.com`        → Cloudflare (different flow, see appendix)

Log in to that provider's DNS management UI before continuing.

---

## 3. Add ONE A record

In the DNS UI for `merciraymond.fr`, add a single A record:

| Field      | Value                                |
|------------|--------------------------------------|
| Type       | `A`                                  |
| Name / Host| `dashboard-commercial`               |
| Value / IP | `72.61.166.144`                      |
| TTL        | default (or 300 if asked)            |
| Proxy      | OFF (if Cloudflare — see appendix)   |

Do **not** touch any existing record. Do not delete MX, do not touch the root `@` A record, do not touch existing subdomains.

Verify propagation (run every few minutes until it returns the right IP):

```bash
dig +short dashboard-commercial.merciraymond.fr
# expect: 72.61.166.144
```

Also from the VPS to be sure:

```bash
ssh root@srv1082911.hstgr.cloud
dig +short dashboard-commercial.merciraymond.fr @1.1.1.1
```

Don't move on until both return `72.61.166.144`.

---

## 4. Switch the Host label in compose

On the VPS:

```bash
cd /root/Customs/Dashboard_Commercial/deploy
nano docker-compose.yml
```

Change exactly **one line**, the router rule:

```diff
-      - traefik.http.routers.dashboard.rule=Host(`dashboard-commercial.srv1082911.hstgr.cloud`)
+      - traefik.http.routers.dashboard.rule=Host(`dashboard-commercial.merciraymond.fr`)
```

Save and exit.

---

## 5. Apply the change and watch ACME succeed

```bash
cd /root/Customs/Dashboard_Commercial/deploy
docker compose up -d
```

That recreates the dashboard container (~2 s) so Traefik picks up the new label.

Then tail Traefik for our domain:

```bash
docker logs -f root-traefik-1 2>&1 \
  | grep --line-buffered -iE "dashboard|acme|obtained|error|certificate"
```

Within ~30 s you should see something like:

```
... level=info msg="Successfully obtained certificate for ... merciraymond.fr"
```

If you see a `429 / rateLimited` again, **stop and re-read this file from the
top** — something else changed. If you see `unable to find ... A record`, DNS
hasn't propagated to Traefik yet — wait 5 min and run `docker restart
root-traefik-1` to retry.

---

## 6. Verify

From your laptop:

```bash
echo | openssl s_client \
  -connect dashboard-commercial.merciraymond.fr:443 \
  -servername dashboard-commercial.merciraymond.fr 2>/dev/null \
  | openssl x509 -noout -issuer -subject -dates
```

Expected output (the key line is `issuer=` containing `Let's Encrypt`):

```
issuer=C = US, O = Let's Encrypt, CN = R10
subject=CN = dashboard-commercial.merciraymond.fr
notBefore=...
notAfter=...
```

Then open `https://dashboard-commercial.merciraymond.fr` in the browser — the
padlock should be solid, no warning.

---

## 7. Communicate the new URL

The old `dashboard-commercial.srv1082911.hstgr.cloud` URL will keep working
(same Traefik instance, same container) but will keep showing the "Not secure"
warning forever. Tell the team to switch their bookmarks to the new URL.

If you want to actively retire the old URL, remove its router rule by deleting
the old Host label entirely (we already replaced it in step 4, so once the new
one resolves you're done).

---

## 8. Rollback (if something breaks)

Just put the old Host label back and `docker compose up -d`:

```diff
-      - traefik.http.routers.dashboard.rule=Host(`dashboard-commercial.merciraymond.fr`)
+      - traefik.http.routers.dashboard.rule=Host(`dashboard-commercial.srv1082911.hstgr.cloud`)
```

The dashboard returns to the (insecure but functional) hstgr URL within 2 s.
Nothing else is affected.

---

## Appendix — Cloudflare-fronted variant (optional, only if `merciraymond.fr` already uses Cloudflare nameservers)

If `dig NS merciraymond.fr` returns Cloudflare nameservers, you have **two**
choices:

1. **Easy**: in Cloudflare DNS, add the same A record with the orange
   "Proxied" toggle set to **OFF (DNS only)**. Then the steps above work
   unchanged — Let's Encrypt issues the cert, Traefik serves it.

2. **Cleanest long-term**: turn the Proxied toggle **ON** and let Cloudflare
   terminate TLS (free trusted cert from Cloudflare's CA). Then in
   `docker-compose.yml` you can drop the `tls.certresolver` label and just
   serve plain HTTP behind Cloudflare. Skip if you're not already familiar
   with Cloudflare — option 1 is fine.
