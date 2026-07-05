#!/usr/bin/env python3
"""Post-import PingAM platform tweaks over REST (stdlib only).

Applies the GET-modify-PUT changes that a blind `amster import-config`
cannot express safely (docs/03 + gotchas G-16/G-19):

  * alpha identity store: search scope SCOPE_ONE, users search attribute
    fr-idm-uuid, auth naming attribute uid; delete the Top Level realm's
    LDAP identity store
  * insert a Success URL node before Success in the Platform* trees
  * PlatformUpdatePassword: Patch Object node -> patchAsObject = false
  * External Login Page URL per realm

Every change inspects current state first, so re-running is a no-op.
Usage: am_platform_tweaks.py /path/to/am-tweaks.json
"""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

# AM's static authentication-tree exit nodes (TreeConstants).
SUCCESS_NODE_ID = "70e691a5-1e33-4ac3-a356-e7b6d60d92e0"
FAILURE_NODE_ID = "e301438c-0bd0-429c-ab0c-66126501069a"


class AmClient:
    def __init__(self, base_url, user, passwords):
        self.base = base_url.rstrip("/")
        self.cookie_name = None
        self.token = None
        info = self.request("GET", "/json/serverinfo/*")
        self.cookie_name = info["cookieName"]
        for pwd in passwords:
            try:
                self.token = self._authenticate(user, pwd)
                break
            except urllib.error.HTTPError as err:
                if err.code != 401:
                    raise
        if self.token is None:
            raise SystemExit(
                "FATAL: could not authenticate as %s with any candidate password" % user)

    def _authenticate(self, user, pwd):
        headers = {
            "X-OpenAM-Username": user,
            "X-OpenAM-Password": pwd,
            "Content-Type": "application/json",
            "Accept-API-Version": "resource=2.0, protocol=1.0",
        }
        req = urllib.request.Request(
            self.base + "/json/realms/root/authenticate",
            data=b"{}", headers=headers, method="POST")
        with urllib.request.urlopen(req) as resp:
            return json.load(resp)["tokenId"]

    def request(self, method, path, body=None, create=False, api_version=None):
        # X-Requested-With defeats AM's CsrfFilter, which 403s any non-GET
        # /json request that carries neither it nor an API-version header.
        headers = {"Content-Type": "application/json",
                   "X-Requested-With": "XMLHttpRequest"}
        if api_version:
            # Some endpoints (tree nodes) route to an incompatible older
            # resource version unless pinned ("Missing node id" otherwise).
            headers["Accept-API-Version"] = api_version
        if self.token:
            headers["Cookie"] = "%s=%s" % (self.cookie_name, self.token)
        if method == "PUT":
            headers["If-None-Match" if create else "If-Match"] = "*"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data,
                                     headers=headers, method=method)
        with urllib.request.urlopen(req) as resp:
            payload = resp.read()
        return json.loads(payload) if payload else {}

    def get(self, path):
        return self.request("GET", path)

    def put(self, path, body):
        return self.request("PUT", path, body)

    def put_new(self, path, body):
        """Create-via-PUT (If-None-Match: *) — how tree nodes are created."""
        return self.request("PUT", path, body, create=True,
                            api_version="protocol=2.0,resource=1.0")

    def post(self, path, body=None):
        return self.request("POST", path, body if body is not None else {})

    def delete(self, path):
        return self.request("DELETE", path)


def dig_set(data, dotted_key, value):
    """Set a dotted-path key (e.g. 'consent.clientsCanSkipConsent').
    Parent objects must already exist. Returns (found, changed)."""
    obj = data
    parts = dotted_key.split(".")
    for part in parts[:-1]:
        if not isinstance(obj, dict) or part not in obj:
            return False, False
        obj = obj[part]
    if not isinstance(obj, dict):
        return False, False
    leaf = parts[-1]
    if obj.get(leaf) == value:
        return True, False
    obj[leaf] = value
    return True, True


def ensure_service(am, spec):
    """Create-if-missing + settings enforcement for an AM service resource.

    The Amster 8.1 CLI does not recognise several documented entity types
    (IdmIntegrationService, CorsService/CorsServiceConfiguration,
    SelfServiceTrees) and rejects the OAuth2Provider payload shape, so these
    are driven over plain REST instead — field names verified against the
    live ?_action=schema output.
    """
    path = spec["path"]
    try:
        data = am.get(path)
    except urllib.error.HTTPError as err:
        if err.code != 404 or not spec.get("create"):
            raise
        am.post(spec.get("create_path", path) + "?_action=create",
                spec.get("create_body", {}))
        print("CHANGED: created %s" % path)
        data = am.get(path)
    changed = False
    for key, value in spec.get("set", {}).items():
        found, ch = dig_set(data, key, value)
        if not found:
            print("WARNING: %s has no attribute path %s" % (path, key))
        changed |= ch
    if changed:
        am.put(path, putable(data))
        print("CHANGED: updated %s" % path)


def realm_path(realm):
    """'/json/realms/root[/realms/<child>...]' for a realm like None or 'alpha'."""
    path = "/json/realms/root"
    for part in (realm or "").strip("/").split("/"):
        if part:
            path += "/realms/" + urllib.parse.quote(part)
    return path


def putable(obj):
    """Strip CREST metadata (_id, _rev, _type, ...) before a PUT."""
    return {k: v for k, v in obj.items() if not k.startswith("_")}


def set_nested(obj, key, value):
    """Set key to value wherever it appears in a nested dict.
    Returns (found, changed)."""
    found = changed = False
    if isinstance(obj, dict):
        if key in obj:
            found = True
            if obj[key] != value:
                obj[key] = value
                changed = True
        for child in obj.values():
            if isinstance(child, dict):
                f, c = set_nested(child, key, value)
                found |= f
                changed |= c
    return found, changed


def list_id_repositories(am, rp):
    # AM 8.1 rejects _queryFilter=true here ("Query not supported"); listing
    # service sub-instances is done with the nextdescendents action.
    return am.post(rp + "/realm-config/services/id-repositories?_action=nextdescendents")


def tweak_identity_stores(am, realm, settings):
    rp = realm_path(realm)
    listing = list_id_repositories(am, rp)
    for inst in listing.get("result", []):
        type_id = (inst.get("_type") or {}).get("_id", "")
        if "LDAP" not in type_id.upper():
            continue
        path = "%s/realm-config/services/id-repositories/%s/%s" % (
            rp, urllib.parse.quote(type_id), urllib.parse.quote(inst["_id"]))
        data = am.get(path)
        changed = False
        for key, value in settings.items():
            found, ch = set_nested(data, key, value)
            if not found:
                print("WARNING: identity store %s has no attribute %s" % (inst["_id"], key))
            changed |= ch
        if changed:
            am.put(path, putable(data))
            print("CHANGED: updated identity store %s in realm /%s" % (inst["_id"], realm or ""))


def delete_root_identity_stores(am):
    listing = list_id_repositories(am, "/json/realms/root")
    for inst in listing.get("result", []):
        type_id = (inst.get("_type") or {}).get("_id", "")
        if "LDAP" not in type_id.upper():
            continue
        am.delete("/json/realms/root/realm-config/services/id-repositories/%s/%s" % (
            urllib.parse.quote(type_id), urllib.parse.quote(inst["_id"])))
        print("CHANGED: deleted Top Level identity store %s" % inst["_id"])


def tree_base(rp):
    return rp + "/realm-config/authentication/authenticationtrees"


def get_tree(am, rp, tree_name):
    try:
        return am.get(tree_base(rp) + "/trees/" + urllib.parse.quote(tree_name))
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return None
        raise


def insert_success_url(am, realm, tree_name, url):
    rp = realm_path(realm)
    tree = get_tree(am, rp, tree_name)
    if tree is None:
        print("WARNING: tree %s not found in realm /%s — skipped" % (tree_name, realm))
        return
    for node in tree.get("nodes", {}).values():
        if node.get("nodeType") == "SetSuccessUrlNode":
            print("tree %s already has a Success URL node" % tree_name)
            return
    # AM 8.1 rejects _action=create here ("Missing node id"): tree nodes are
    # created by PUT to a client-generated UUID.
    new_id = str(uuid.uuid4())
    # ... and the _id must ALSO be repeated in the body, or the same
    # "Missing node id" comes back (matches the sample node JSON shape).
    am.put_new(tree_base(rp) + "/nodes/SetSuccessUrlNode/" + new_id,
               {"_id": new_id, "successUrl": url})
    rewired = 0
    for node in tree.get("nodes", {}).values():
        connections = node.get("connections", {})
        for outcome, target in list(connections.items()):
            if target == SUCCESS_NODE_ID:
                connections[outcome] = new_id
                rewired += 1
    if tree.get("entryNodeId") == SUCCESS_NODE_ID:
        tree["entryNodeId"] = new_id
        rewired += 1
    if not rewired:
        print("WARNING: tree %s has no edge into Success — node not inserted" % tree_name)
        return
    tree.setdefault("nodes", {})[new_id] = {
        "displayName": "Success URL",
        "nodeType": "SetSuccessUrlNode",
        "connections": {"outcome": SUCCESS_NODE_ID},
    }
    am.put(tree_base(rp) + "/trees/" + urllib.parse.quote(tree_name), putable(tree))
    print("CHANGED: tree %s: Success URL node inserted (%d edges rewired)" % (tree_name, rewired))


def disable_patch_as_object(am, realm, tree_name):
    rp = realm_path(realm)
    tree = get_tree(am, rp, tree_name)
    if tree is None:
        print("WARNING: tree %s not found in realm /%s — skipped" % (tree_name, realm))
        return
    node_api = "protocol=2.0,resource=1.0"   # same version pin as put_new
    for node_id, node in tree.get("nodes", {}).items():
        if node.get("nodeType") != "PatchObjectNode":
            continue
        npath = tree_base(rp) + "/nodes/PatchObjectNode/" + urllib.parse.quote(node_id)
        cfg = am.request("GET", npath, api_version=node_api)
        if cfg.get("patchAsObject"):
            cfg["patchAsObject"] = False
            am.request("PUT", npath, putable(cfg), api_version=node_api)
            print("CHANGED: tree %s: Patch As Object disabled on node %s" % (tree_name, node_id))
        else:
            print("tree %s: Patch As Object already disabled" % tree_name)


def set_external_login_url(am, realm, url):
    rp = realm_path(realm)
    path = rp + "/realm-config/authentication"
    data = am.get(path)
    # The attribute lives at general.externalLoginPageUrl (per ?_action=schema)
    # but is OMITTED from GET responses while unset — so set it explicitly
    # rather than searching for an existing key.
    section = data.setdefault("general", {})
    if section.get("externalLoginPageUrl") == url:
        print("External Login Page URL already set for realm /%s" % (realm or ""))
        return
    section["externalLoginPageUrl"] = url
    am.put(path, putable(data))
    print("CHANGED: set External Login Page URL for realm /%s" % (realm or ""))


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: am_platform_tweaks.py /path/to/am-tweaks.json")
    with open(sys.argv[1]) as fh:
        cfg = json.load(fh)

    am = AmClient(cfg["am_url"], cfg["admin_user"], cfg["admin_passwords"])

    for spec in cfg.get("services", []):
        ensure_service(am, spec)
    tweak_identity_stores(am, cfg["realm"], cfg["idrepo_settings"])
    if cfg.get("delete_root_idrepo"):
        delete_root_identity_stores(am)
    for tree in cfg["success_url_trees"]:
        insert_success_url(am, cfg["realm"], tree, cfg["success_url"])
    disable_patch_as_object(am, cfg["realm"], cfg["patch_object_tree"])
    set_external_login_url(am, None, cfg["external_login_url_root"])
    set_external_login_url(am, cfg["realm"], cfg["external_login_url_alpha"])
    print("AM platform tweaks complete")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as err:
        body = err.read().decode(errors="replace")
        raise SystemExit("HTTP %s on %s: %s" % (err.code, err.url, body[:2000]))
