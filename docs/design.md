# Design notes

This document records how each part of `route-intent` works and why. It grows with each
milestone. [SPEC.md](../SPEC.md) remains the source of truth for scope and behavior.

## Intent (M1)

The intent file is a YAML mapping describing what the network should look like. It is loaded by
`route_intent.intent.load_intent(path)`, which returns a validated `Intent` or raises `IntentError`.
A complete example for the lab is in [examples/intent.yaml](../examples/intent.yaml).

### Schema

Only `devices` is required. The other sections default to empty lists.

| Section | Shape | Fields | Meaning |
|---|---|---|---|
| `devices` | mapping of name → device | `asn`, `loopback` | A router, keyed by its name. `asn` is an integer from 1 to 4294967295; `loopback` is an IPv4 address. At least one device. |
| `isis_adjacencies` | list | `devices: [a, b]` | An IS-IS adjacency is expected between the two named devices. |
| `bgp_sessions` | list | `device`, `peer`, `type`, optional `min_prefixes_received` | `device` has a BGP session to `peer`. `type` is `ibgp` or `ebgp`. `min_prefixes_received` is an integer ≥ 0. |
| `routes` | list | `device`, `prefix`, `protocol` | `device` has a route to `prefix` learned by `protocol` (`bgp`, `isis` or `connected`). |
| `path_preferences` | list | `device`, `prefix`, `exit_via` | `device` reaches `prefix` by leaving the AS through `exit_via`. |

```yaml
devices:
  r1:
    asn: 65001
    loopback: 10.0.0.1
  r3:
    asn: 65001
    loopback: 10.0.0.3
isis_adjacencies:
  - devices: [r1, r3]
bgp_sessions:
  - device: r1
    peer: r3
    type: ibgp
routes:
  - device: r3
    prefix: 192.0.2.0/24
    protocol: bgp
path_preferences:
  - device: r3
    prefix: 192.0.2.0/24
    exit_via: r1
```

In Python, `Intent.devices` is a `dict[str, Device]`, and each `Device` also carries its `name`,
filled in from the key. A device entry may repeat its name as `name:`, but it must match the key.

### Validation rules

The numbers match SPEC.md §4. "Reported at" is the location carried by the issue.

| # | Rule | Reported at |
|---|---|---|
| 1 | Every device named in an adjacency, session, route or path preference is listed in `devices`. | The field holding the name, e.g. `bgp_sessions[2].peer`, `isis_adjacencies[0].devices[1]` |
| 2 | An `ibgp` session needs both devices in the same ASN; an `ebgp` session needs different ASNs. | `bgp_sessions[i].type` |
| 3 | No self-references: an adjacency, a session, or an `exit_via` pointing at the device itself. | `isis_adjacencies[i].devices[1]`, `bgp_sessions[i].peer`, `path_preferences[i].exit_via` |
| 4 | No duplicates (see the table below). | The later entry, e.g. `isis_adjacencies[1]` |
| 5 | ASN is an integer from 1 to 4294967295. | `devices.<name>.asn` |
| 6 | Prefixes are valid IPv4 networks written as `address/length`, with no host bits set. | `routes[i].prefix`, `path_preferences[i].prefix` |
| 7 | Unknown keys are rejected at every level. | The unknown key, e.g. `devices.r1.role` |
| 8 | The loader raises `IntentError` for a missing file, an unreadable file, invalid YAML, duplicate YAML keys, an empty file, a top level that is not a mapping, and any validation failure. | `<file>` for file and YAML problems, the key's path for duplicate keys, `<root>` for a non-mapping top level |

What rule 4 treats as the same entry:

| Section | Same entry when | How it is caught |
|---|---|---|
| `devices` | Same name | Duplicate YAML key, reported at `devices.<name>` |
| `devices` | Same `loopback` | Model validator, reported at `devices.<name>.loopback` |
| `isis_adjacencies` | Same two devices, in either order | Model validator |
| `bgp_sessions` | Same `device` and same `peer` (direction matters) | Model validator |
| `routes` | Same `device` and `prefix` | Model validator |
| `path_preferences` | Same `device` and `prefix` | Model validator |

Types are strict: values are never converted. `asn: "65001"`, `asn: true` and `asn: 65001.0` are
rejected, as are a non-string device name, an integer loopback and a string `min_prefixes_received`.

### Errors

`IntentError.issues` is a list of `IntentIssue(location, message)`. `str(error)` prints one issue
per line as `location: message`:

```
devices.r1.role: unknown key
routes[0].prefix: '192.0.2.1/24' has host bits set; the network address is 192.0.2.0/24
bgp_sessions[0].peer: unknown device 'r9'; it is not listed in devices
```

One run reports everything it can find, in three stages:

1. **YAML.** The whole document is scanned for duplicate keys before anything is built, and every
   duplicate is reported. Nothing else is checked when there are duplicates, because there is no
   way to know which of the two definitions the operator meant.
2. **Single values** (rules 5–7, and the types of every field) are checked by Pydantic, which
   reports every invalid field at once.
3. **Rules across entries** (rules 1–4) run in a model validator on `Intent`. Pydantic skips that
   validator when any single value is invalid, so in that case the loader runs the same checks over
   the entries that were valid on their own. A device that exists but is itself invalid still
   counts as known, so one bad device does not also flag every reference to it.

### Decisions

| Decision | Reason |
|---|---|
| Prefixes with host bits set are rejected, not normalized. | `192.0.2.1/24` is more likely a typo than a deliberate way to write `192.0.2.0/24`, and silently checking a different prefix than the one written would hide it. |
| Any ASN from 1 to 4294967295 is allowed, with no special handling of public or private ranges. | The tool verifies routing state; which ASNs an operator may use is the operator's policy. |
| A session declared from both ends is allowed. | Each entry is checked from its own `device`, so the two entries are two different checks, not a duplicate. |
| Adjacency duplicates are detected regardless of order. | An IS-IS adjacency has no direction, so `[r1, r2]` and `[r2, r1]` are one adjacency written twice. |
| Duplicate YAML keys are rejected, using a `SafeLoader` subclass. | Plain `yaml.safe_load` keeps the last value silently, so a device or a whole section defined twice would quietly lose one definition. |
| `devices` is a mapping keyed by device name. | Names are identifiers that every other section refers to; a mapping makes them unique by construction (with duplicate keys rejected) and gives locations like `devices.r6`. |
| All models use `extra="forbid"`. | A misspelled key would otherwise be dropped silently, and the check the operator thought they declared would never run. |
| All models use strict types. | Converting `"65001"`, `true` or `65001.0` into an ASN could turn a mistake into a different value than the operator intended, and the tool would then verify the wrong thing. |
| A prefix must include `/length`; a bare address or a netmask (`/255.255.255.0`) is rejected. | A bare address would silently become a `/32`, which is the same kind of rewrite as normalizing host bits. |
| A loopback must be an IPv4 address written as a string. | Python's `ipaddress` also accepts integers, so `loopback: 1` would load as `0.0.0.1`. |
| Two devices cannot share a loopback. | iBGP sessions run between loopbacks, so a shared loopback makes a peer ambiguous. |
| Two route expectations, or two path preferences, for the same device and prefix are duplicates even if `protocol` or `exit_via` differ. | A device has one selected route per prefix, so two different expectations for it cannot both hold. |
| A duplicate is reported on the later entry and names the earlier one. | The operator sees both places without searching the file. |
| YAML is parsed by a `SafeLoader` subclass, never `yaml.load` with an unsafe loader. | The unsafe loaders can construct arbitrary Python objects from tags in the file, which means running code from an input file. |
| Merge keys (`<<: *base`) may override the keys they merge in. | Overriding merged values is what `<<` is for, so it is not treated as a duplicate. |
| Models are frozen. | An intent is a statement of what was declared; nothing downstream should change it. |

### Known limits

- A section that is present but empty (`routes:` with nothing under it) is YAML `null` and is
  rejected. Write `routes: []` or leave the section out.
