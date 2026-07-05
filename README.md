# Ping Identity Platform 8.1 — One-Shot Ansible Deployment

Deploy the complete [Ping Identity Platform 8.1 "Shared identity store" sample](https://docs.pingidentity.com/platform/8/sample-setup/deployment2.html)
on a fresh **Rocky Linux 9** host with a single command — zero interactive steps, zero admin-UI clicking.

```
PingDS  →  PingAM (file-based config)  →  PingIDM  →  PingGateway  →  Platform UIs  →  verification
```

Everything is driven by variables (hosts, ports, versions, credentials), every
destructive step is guarded so re-runs are safe, and a built-in `verify` role
proves each layer works — DS over LDAPS, AM auth, a full OAuth2
issue-and-introspect round trip, IDM with an AM-issued token, gateway routes,
and all three UIs.

## What you get

| Component | How it runs |
|---|---|
| **PingDS 8.1** (directory) | systemd, 4 platform setup profiles, LDAPS with its own CA |
| **PingAM 8.1** (access management) | Tomcat 10.1 / systemd, **file-based configuration passive install** — no configurator UI, config provisioned by Amster + REST |
| **PingIDM 8.1** (identity management) | systemd, shared DS repo, rsFilter (OAuth2) authentication |
| **PingGateway 2026.3** | systemd, TLS on 9443, the only publicly exposed port |
| **Platform UIs** (login / admin / end-user) | podman **quadlet** containers (reboot-safe) |

SELinux stays **enforcing** and firewalld stays **on** — the playbook labels
what it must and opens only the gateway port.

## Prerequisites

1. **Target host**: fresh Rocky Linux 9 (8 GB+ RAM recommended), SSH access,
   passwordless sudo.
2. **Control node**: anywhere `ansible-core` ≥ 2.14 runs (can be the target
   itself), plus:
   ```bash
   ansible-galaxy collection install -r requirements.yml
   ```
3. **Installer zips** — cloning with Git LFS pulls them into `zips/`
   automatically (`git lfs install && git clone ...`). If you supply your own,
   drop these names into `zips/` next to `site.yml`:
   `DS-8.1.0.zip`, `AM-8.1.0.zip`, `Amster-8.1.0.zip`, `IDM-8.1.0.zip`,
   `PingGateway-2026.3.0.zip`, `apache-tomcat-10.1.55.zip`
4. Internet access on the target (packages + the three UI container images).

## Deploy

```bash
git lfs install
git clone https://github.com/Haseeb-1698/PingIdentity-Playbook.git
cd PingIdentity-Playbook

# point the inventory at your host
vim inventory/hosts.ini        # e.g.  192.168.1.10 ansible_user=rockylinux
                               # running ON the target? add ansible_connection=local

# review secrets (documented sample values work out of the box for a lab),
# then encrypt for anything beyond a lab:
#   ansible-vault encrypt group_vars/all/vault.yml

ansible-playbook site.yml      # add --ask-vault-pass if you encrypted the vault
```

~20 minutes later the `verify` role prints the entry points:

```
Landing page:   https://platform.example.com:9443/
End-user UI:    https://platform.example.com:9443/enduser-ui/?realm=/alpha
Admin UI:       https://platform.example.com:9443/platform-ui/
AM console:     https://platform.example.com:9443/am
```

## First login

1. On your workstation, map the sample hostnames to the target's IP in your
   hosts file (`C:\Windows\System32\drivers\etc\hosts` or `/etc/hosts`):
   ```
   <target-ip>  platform.example.com am.example.com openidm.example.com directory.example.com admin.example.com enduser.example.com login.example.com
   ```
2. Import the deployment CA into your browser (or accept the warning):
   `/home/ping/ping/security/ca-cert.pem` on the target.
3. **Admin**: `https://platform.example.com:9443/platform-ui/` → `amadmin` / `password`
4. **End user**: `https://platform.example.com:9443/enduser-ui/?realm=/alpha` →
   register an account, then log in with it.

> The admin (`amadmin`) works only on the admin UI (root realm); registered
> users work only on the end-user UI (`alpha` realm).

## Customizing

Every knob lives in `group_vars/all/`:

- `main.yml` — domain, hostnames, ports, versions, paths, OAuth client
  names, CORS origins, redirect URIs, feature toggles.
- `vault.yml` — every credential, one variable each, referenced everywhere
  it is consumed. **Change these and `ansible-vault encrypt` before real use.**

Notable toggles: `manage_selinux`, `manage_firewalld`, `manage_etc_hosts`,
`ui_containers_as_systemd`.

## Re-running & recovery

The playbook is idempotent — re-running on a working deployment is safe.
Guards: DS setup runs once (`creates:`), the DS deployment ID is generated
once and persisted, Amster steps are protected by marker files
(`.realm-created`, `.amster-imported` under `/home/ping/ping/am-config/`).
To re-apply the AM config payload after changing a template, delete
`.amster-imported` and re-run.

## Design highlights

- **AM without the configurator**: AM boots in FBC *passive install* mode
  (self-initializing; `AM_TEST_MODE=true` supplies the default test
  keystores). The platform payload — realm, the exact OAuth2 client matrix,
  validation/CORS/base-URL services, sample authentication trees retargeted
  to `/alpha` — is applied by `amster import-config`, plus idempotent REST
  calls (`roles/am-config/files/am_platform_tweaks.py`) for the entities the
  Amster CLI cannot import (OAuth2 provider, IDM integration, CORS config,
  self-service trees) and the documented post-import tweaks.
- **OAuth wiring that actually matches the 8.x UIs**: the public UI clients
  ship `authorization_code` + PKCE support and an explicit RS256 id_token
  signing algorithm — the documented implicit-only matrix breaks the current
  platform UIs.
- **Field-tested details baked in**: IDM repo base DN derived from your
  domain, anonymous access rules for the UI theme/locale endpoints, admin UI
  image pinned to a compatible tag, gateway routes that don't shadow each
  other, no-trailing-newline keystore pins, and more.

## Production notes

This reproduces the official **sample** deployment: self-signed TLS from the
deployment ID, `AM_TEST_MODE` test keys, sample passwords, single host. For
anything real: replace the secrets and encrypt the vault, configure real
secret stores for AM (including the `amadmin` password), bring your own TLS,
and split the roles across hosts (role boundaries are already clean).

## License / installers

Ping Identity installers are subject to Ping Identity's license terms —
ensure your use of the zips complies with them. Playbook code is provided
as-is; use at your own risk.
