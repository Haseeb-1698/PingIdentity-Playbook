#!/usr/bin/env python3
"""Apply the documented shared-identity-store edits to IDM's managed.json.

Per https://docs.pingidentity.com/platform/8/sample-setup/idm-setup-2.html the
default managed/user object is EDITED (never replaced, never extended with new
objects — see gotcha G-17):

  * password: remove "encryption" (DS hashes it), set "userEditable": false
  * add virtual "cn" property (givenName + sn)
  * add "aliasList" property (social IdP subjects)

Prints CHANGED or UNCHANGED. Usage: patch_managed.py /path/to/managed.json
"""

import json
import sys

CN_PROPERTY = {
    "title": "Common Name",
    "description": "Common Name",
    "type": "string",
    "viewable": False,
    "searchable": False,
    "userEditable": False,
    "scope": "private",
    "isPersonal": True,
    "isVirtual": True,
    "onStore": {
        "type": "text/javascript",
        "source": "object.cn || (object.givenName + ' ' + object.sn)"
    }
}

ALIAS_LIST_PROPERTY = {
    "title": "User Alias Names List",
    "description": "List of identity aliases used primarily to record social IdP subjects for this user",
    "type": "array",
    "items": {
        "type": "string",
        "title": "User Alias Names Items"
    },
    "viewable": False,
    "searchable": False,
    "userEditable": True,
    "returnByDefault": False,
    "isVirtual": False
}


def main():
    path = sys.argv[1]
    with open(path) as fh:
        managed = json.load(fh)

    users = [o for o in managed.get("objects", []) if o.get("name") == "user"]
    if not users:
        raise SystemExit("FATAL: no managed 'user' object in %s" % path)
    properties = users[0].setdefault("schema", {}).setdefault("properties", {})

    changed = False

    password = properties.get("password")
    if password is not None:
        if password.pop("encryption", None) is not None:
            changed = True
        if password.get("userEditable") is not False:
            password["userEditable"] = False
            changed = True

    if "cn" not in properties:
        properties["cn"] = CN_PROPERTY
        changed = True
    if "aliasList" not in properties:
        properties["aliasList"] = ALIAS_LIST_PROPERTY
        changed = True

    if changed:
        with open(path, "w") as fh:
            json.dump(managed, fh, indent=4)
            fh.write("\n")
        print("CHANGED")
    else:
        print("UNCHANGED")


if __name__ == "__main__":
    main()
