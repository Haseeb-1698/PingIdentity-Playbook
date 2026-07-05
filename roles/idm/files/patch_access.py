#!/usr/bin/env python3
"""Insert the platform sample's access rules into IDM's conf/access.json.

Official sample (idm-setup-2.html step 9): the end-user/admin UIs read the
realm theme anonymously from config/ui/themerealm, but the stock IDM 8.1
access.json has no rule for it, so the SPAs get 403 and log console errors.
Idempotent: prints CHANGED only when a rule was actually added.

Usage: patch_access.py /path/to/conf/access.json
"""

import json
import sys

RULES = [
    {
        "pattern": "config/ui/themerealm",
        "roles": "*",
        "methods": "read",
        "actions": "*",
    },
    # the login/enduser UIs fetch locale bundles anonymously on every page load
    {
        "pattern": "config/uilocale/*",
        "roles": "*",
        "methods": "read",
        "actions": "*",
    },
]


def main():
    path = sys.argv[1]
    with open(path) as fh:
        doc = json.load(fh)
    configs = doc["configs"]
    existing = {rule.get("pattern") for rule in configs}
    changed = False
    for rule in RULES:
        if rule["pattern"] in existing:
            print("access rule for %s already present" % rule["pattern"])
            continue
        # insert before the catch-all '*' rules so it is evaluated first
        insert_at = next((i for i, r in enumerate(configs)
                          if r.get("pattern") == "*"), len(configs))
        configs.insert(insert_at, rule)
        changed = True
        print("CHANGED: added access rule for %s" % rule["pattern"])
    if changed:
        with open(path, "w") as fh:
            json.dump(doc, fh, indent=4)
            fh.write("\n")


if __name__ == "__main__":
    main()
