#!/usr/bin/env python3
"""Diagnostic snapshot of Top Level realm auth/identity-repo state (stdlib only).

Called at two checkpoints around the Amster import (see roles/am-config/tasks/
main.yml) to capture, without guessing, whether the platform payload import
changes root-realm authentication — the open question in gotcha G-28. Never
raises: an authentication failure IS the data point, so the failure detail
(HTTP status + body) is written to the snapshot file instead of aborting the
play.

Reuses am-tweaks.json (am_url/admin_user/admin_passwords) so no credential
ever appears on the command line / in Ansible's task log.

Usage: am_diag_dump.py <am-tweaks.json path> <output_file> <label>
"""

import json
import sys
import urllib.error
import urllib.request


def authenticate(base, user, pwd):
    headers = {
        "X-OpenAM-Username": user,
        "X-OpenAM-Password": pwd,
        "Content-Type": "application/json",
        "Accept-API-Version": "resource=2.0, protocol=1.0",
    }
    req = urllib.request.Request(
        base + "/json/realms/root/authenticate",
        data=b"{}", headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def get(base, path, cookie_name, token):
    headers = {"Cookie": "%s=%s" % (cookie_name, token)} if token else {}
    req = urllib.request.Request(base + path, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def main():
    cfg_path, out_file, label = sys.argv[1], sys.argv[2], sys.argv[3]
    with open(cfg_path) as fh:
        cfg = json.load(fh)
    base = cfg["am_url"].rstrip("/")
    passwords = cfg["admin_passwords"]
    snapshot = {"label": label}

    try:
        info = get(base, "/json/serverinfo/*", None, None)
        cookie_name = info["cookieName"]
    except Exception as err:  # noqa: BLE001 - diagnostic capture, never abort
        snapshot["serverinfo_error"] = repr(err)
        write(out_file, snapshot)
        return

    token = None
    auth_errors = []
    for pwd in passwords:
        try:
            token = authenticate(base, cfg["admin_user"], pwd)["tokenId"]
            break
        except urllib.error.HTTPError as err:
            body = err.read().decode(errors="replace")
            auth_errors.append({"status": err.code, "body": body[:4000]})
        except Exception as err:  # noqa: BLE001
            auth_errors.append({"error": repr(err)})

    if token is None:
        snapshot["authenticate_failed"] = True
        snapshot["auth_attempts"] = auth_errors
        write(out_file, snapshot)
        return

    snapshot["authenticate_ok"] = True
    for name, path in (
        ("root_authentication_config",
         "/json/realms/root/realm-config/authentication"),
        ("root_id_repositories",
         "/json/realms/root/realm-config/services/id-repositories?_queryFilter=true"),
    ):
        try:
            snapshot[name] = get(base, path, cookie_name, token)
        except urllib.error.HTTPError as err:
            snapshot[name + "_error"] = {
                "status": err.code,
                "body": err.read().decode(errors="replace")[:4000],
            }

    write(out_file, snapshot)


def write(out_file, snapshot):
    with open(out_file, "w") as fh:
        json.dump(snapshot, fh, indent=2)
    print("wrote %s" % out_file)


if __name__ == "__main__":
    main()
