# Delft-FEWS Config Guide

Astro/Starlight static documentation site for Delft-FEWS configuration.

## Deployment

Live at **https://df-docs.streamflows.org** — static site, no gunicorn/systemd service.

- CloudPanel static site, site user `fewsdocs`
- Deploy clone lives at `/home/fewsdocs/repo` (origin = this repo on GitHub)
- nginx docroot `/home/fewsdocs/htdocs/df-docs.streamflows.org` is a **symlink** to
  `/home/fewsdocs/repo/dist` — do not replace it with a real directory
- SSL via Let's Encrypt (`clpctl lets-encrypt:install:certificate --domainName=df-docs.streamflows.org`)
- `fewsdocs` has a read-only deploy key (`/home/fewsdocs/.ssh/id_ed25519.pub`) registered
  on this GitHub repo for pulls

### Redeploy after content changes

```bash
sudo -u fewsdocs git -C /home/fewsdocs/repo pull
sudo -u fewsdocs bash -c 'cd /home/fewsdocs/repo && npm ci && npm run build'
```

No restart needed — nginx serves whatever is in `dist/`.

### nginx vhost caveat

The vhost (`/etc/nginx/sites-enabled/df-docs.streamflows.org.conf`) has two hand-added lines:

```nginx
error_page 403 =404 /404.html;
error_page 404 /404.html;
```

These make section URLs without an index page (e.g. `/tasks/`) and unknown URLs serve the
site's styled 404 page. CloudPanel regenerates this file if the site's vhost is saved in the
UI — if that happens, re-add these lines (via the CloudPanel vhost editor so they persist).
