"""Intent models and loader (SPEC.md §4).

``load_intent`` is the supported entry point: every failure, from a missing file to a rule
violation, surfaces as one ``IntentError`` carrying a list of located issues.
"""

import os
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)
from pydantic_core import InitErrorDetails, PydanticCustomError

MIN_ASN = 1
MAX_ASN = 4294967295

# Locations used when an issue is about the file or the document as a whole.
FILE_LOCATION = "<file>"
ROOT_LOCATION = "<root>"

# Error types for our own messages: a single bad value, and a rule that spans entries.
_VALUE_ERROR = "intent_value"
_RULE_ERROR = "intent_rule"

_Loc = tuple[str | int, ...]
_Located = tuple[_Loc, str]


@dataclass(frozen=True)
class IntentIssue:
    """One problem in an intent file, e.g. location ``bgp_sessions[2].peer`` or ``devices.r6``."""

    location: str
    message: str

    def __str__(self) -> str:
        return f"{self.location}: {self.message}"


class IntentError(Exception):
    """The intent could not be loaded. ``issues`` holds every problem found."""

    def __init__(self, issues: Sequence[IntentIssue], path: Path | None = None) -> None:
        super().__init__()
        self.issues = list(issues)
        self.path = path

    def __str__(self) -> str:
        return "\n".join(str(issue) for issue in self.issues)


def _custom_error(error_type: str, message: str) -> PydanticCustomError:
    # The message goes through the context so braces in user input are never read as a template.
    return PydanticCustomError(error_type, "{message}", {"message": message})


def _parse_loopback(value: Any) -> IPv4Address:
    if isinstance(value, IPv4Address):
        return value
    # ipaddress also accepts integers (1 -> 0.0.0.1); an intent file must spell the address out.
    if not isinstance(value, str):
        raise _custom_error(
            _VALUE_ERROR, f"must be an IPv4 address such as 10.0.0.1, got {value!r}"
        )
    try:
        return IPv4Address(value)
    except ValueError:
        raise _custom_error(_VALUE_ERROR, f"{value!r} is not a valid IPv4 address") from None


def _parse_prefix(value: Any) -> IPv4Network:
    if isinstance(value, IPv4Network):
        return value
    form = "must be an IPv4 prefix written as address/length, such as 192.0.2.0/24"
    if not isinstance(value, str):
        raise _custom_error(_VALUE_ERROR, f"{form}, got {value!r}")
    # A bare address would silently become a /32 and a netmask would be rewritten; require /length.
    _, _, length = value.partition("/")
    if not (length.isascii() and length.isdigit()):
        raise _custom_error(_VALUE_ERROR, f"{form}, got {value!r}")
    try:
        return IPv4Network(value)
    except ValueError:
        pass
    try:
        network = IPv4Network(value, strict=False)
    except ValueError:
        raise _custom_error(_VALUE_ERROR, f"{value!r} is not a valid IPv4 prefix") from None
    raise _custom_error(
        _VALUE_ERROR, f"{value!r} has host bits set; the network address is {network}"
    )


def _parse_pair(value: Any) -> Any:
    if isinstance(value, list | tuple) and len(value) == 2:
        return tuple(value)
    raise _custom_error(_VALUE_ERROR, "must be a list of exactly two device names")


DeviceName = Annotated[str, StringConstraints(min_length=1)]
Asn = Annotated[int, Field(ge=MIN_ASN, le=MAX_ASN)]
Loopback = Annotated[IPv4Address, BeforeValidator(_parse_loopback)]
Prefix = Annotated[IPv4Network, BeforeValidator(_parse_prefix)]


class _Model(BaseModel):
    # strict: "65001", true and 65001.0 are not ints, and nothing else is converted either.
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Device(_Model):
    """A router. In the intent file the name is the device's key under ``devices``."""

    name: DeviceName
    asn: Asn
    loopback: Loopback


class IsisAdjacency(_Model):
    """An expected IS-IS adjacency. ``[r1, r2]`` and ``[r2, r1]`` are the same adjacency."""

    devices: Annotated[tuple[DeviceName, DeviceName], BeforeValidator(_parse_pair)]

    @property
    def key(self) -> tuple[str, str]:
        """The pair in a fixed order, so both spellings compare equal."""
        first, second = sorted(self.devices)
        return (first, second)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, IsisAdjacency):
            return NotImplemented
        return self.key == other.key

    def __hash__(self) -> int:
        return hash(self.key)


class BgpSession(_Model):
    """An expected BGP session, checked from ``device``'s side."""

    device: DeviceName
    peer: DeviceName
    type: Literal["ibgp", "ebgp"]
    min_prefixes_received: Annotated[int, Field(ge=0)] | None = None


class RouteExpectation(_Model):
    device: DeviceName
    prefix: Prefix
    protocol: Literal["bgp", "isis", "connected"]


class PathPreference(_Model):
    """``device`` must leave the AS towards ``prefix`` through ``exit_via``."""

    device: DeviceName
    prefix: Prefix
    exit_via: DeviceName


def _with_name(key: Any, spec: Any) -> Any:
    """Give a ``devices`` entry its name from its key, unless it already spells one out."""
    if isinstance(spec, dict) and "name" not in spec:
        return {"name": key, **spec}
    return spec


def _rule_issues(
    devices: Iterable[tuple[str, Device]],
    adjacencies: Iterable[tuple[int, IsisAdjacency]],
    sessions: Iterable[tuple[int, BgpSession]],
    routes: Iterable[tuple[int, RouteExpectation]],
    preferences: Iterable[tuple[int, PathPreference]],
    other_device_names: frozenset[str] = frozenset(),
) -> list[_Located]:
    """Check the rules that span more than one entry (SPEC.md §4 rules 1-4).

    Devices arrive with their key and list entries with their index in the file, so locations
    stay correct when some entries were left out. ``other_device_names`` are devices that exist
    but failed their own validation: they count as known, so one bad device does not also flag
    every reference to it.
    """
    found: list[_Located] = []

    asn_by_name: dict[str, int] = {}
    key_by_loopback: dict[IPv4Address, str] = {}
    for key, device in devices:
        if device.name != key:
            message = f"{device.name!r} does not match its key {key!r}; the key is the name"
            found.append((("devices", key, "name"), message))
        asn_by_name[key] = device.asn
        if device.loopback in key_by_loopback:
            first = key_by_loopback[device.loopback]
            message = f"duplicate loopback {device.loopback} (already used by devices.{first})"
            found.append((("devices", key, "loopback"), message))
        else:
            key_by_loopback[device.loopback] = key

    known = asn_by_name.keys() | other_device_names

    def require_known(loc: _Loc, name: str) -> None:
        if name not in known:
            found.append((loc, f"unknown device {name!r}; it is not listed in devices"))

    seen_adjacencies: dict[tuple[str, str], int] = {}
    for index, adjacency in adjacencies:
        base: _Loc = ("isis_adjacencies", index)
        first_name, second_name = adjacency.devices
        require_known((*base, "devices", 0), first_name)
        require_known((*base, "devices", 1), second_name)
        if first_name == second_name:
            message = f"adjacency from {first_name!r} to itself"
            found.append(((*base, "devices", 1), message))
        elif adjacency.key in seen_adjacencies:
            first = seen_adjacencies[adjacency.key]
            message = (
                f"duplicate adjacency between {first_name!r} and {second_name!r} "
                f"(same pair as isis_adjacencies[{first}]; order does not matter)"
            )
            found.append((base, message))
        else:
            seen_adjacencies[adjacency.key] = index

    seen_sessions: dict[tuple[str, str], int] = {}
    for index, session in sessions:
        base = ("bgp_sessions", index)
        require_known((*base, "device"), session.device)
        require_known((*base, "peer"), session.peer)
        if session.device == session.peer:
            found.append(((*base, "peer"), f"BGP session from {session.device!r} to itself"))
            continue
        if session.device in asn_by_name and session.peer in asn_by_name:
            device_asn = asn_by_name[session.device]
            peer_asn = asn_by_name[session.peer]
            if session.type == "ibgp" and device_asn != peer_asn:
                message = (
                    f"ibgp requires equal ASNs, but {session.device!r} is AS {device_asn} "
                    f"and {session.peer!r} is AS {peer_asn}"
                )
                found.append(((*base, "type"), message))
            elif session.type == "ebgp" and device_asn == peer_asn:
                message = (
                    f"ebgp requires different ASNs, but {session.device!r} and "
                    f"{session.peer!r} are both AS {device_asn}"
                )
                found.append(((*base, "type"), message))
        # Keyed by direction: the same session declared from the other end is allowed.
        session_key = (session.device, session.peer)
        if session_key in seen_sessions:
            first = seen_sessions[session_key]
            message = (
                f"duplicate BGP session from {session.device!r} to {session.peer!r} "
                f"(first declared at bgp_sessions[{first}])"
            )
            found.append((base, message))
        else:
            seen_sessions[session_key] = index

    seen_routes: dict[tuple[str, IPv4Network], int] = {}
    for index, route in routes:
        base = ("routes", index)
        require_known((*base, "device"), route.device)
        route_key = (route.device, route.prefix)
        if route_key in seen_routes:
            first = seen_routes[route_key]
            message = (
                f"duplicate route expectation for {route.prefix} on {route.device!r} "
                f"(first declared at routes[{first}])"
            )
            found.append((base, message))
        else:
            seen_routes[route_key] = index

    seen_preferences: dict[tuple[str, IPv4Network], int] = {}
    for index, preference in preferences:
        base = ("path_preferences", index)
        require_known((*base, "device"), preference.device)
        require_known((*base, "exit_via"), preference.exit_via)
        if preference.device == preference.exit_via:
            message = f"{preference.device!r} cannot exit via itself"
            found.append(((*base, "exit_via"), message))
        preference_key = (preference.device, preference.prefix)
        if preference_key in seen_preferences:
            first = seen_preferences[preference_key]
            message = (
                f"duplicate path preference for {preference.prefix} on {preference.device!r} "
                f"(first declared at path_preferences[{first}])"
            )
            found.append((base, message))
        else:
            seen_preferences[preference_key] = index

    return found


class Intent(_Model):
    """The whole intent. ``devices`` maps each device name to its ``Device``."""

    devices: dict[DeviceName, Device] = Field(min_length=1)
    isis_adjacencies: list[IsisAdjacency] = []
    bgp_sessions: list[BgpSession] = []
    routes: list[RouteExpectation] = []
    path_preferences: list[PathPreference] = []

    @model_validator(mode="before")
    @classmethod
    def _name_devices_from_keys(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("devices"), dict):
            named = {key: _with_name(key, spec) for key, spec in data["devices"].items()}
            return {**data, "devices": named}
        return data

    @model_validator(mode="after")
    def _check_rules_across_entries(self) -> Self:
        found = _rule_issues(
            self.devices.items(),
            enumerate(self.isis_adjacencies),
            enumerate(self.bgp_sessions),
            enumerate(self.routes),
            enumerate(self.path_preferences),
        )
        if found:
            raise ValidationError.from_exception_data(
                type(self).__name__,
                [
                    InitErrorDetails(type=_custom_error(_RULE_ERROR, message), loc=loc, input=None)
                    for loc, message in found
                ],
            )
        return self


def _format_location(loc: _Loc) -> str:
    text = ""
    for part in loc:
        if isinstance(part, int):
            text += f"[{part}]"
        elif part == "[key]":
            # Pydantic's marker for an invalid mapping key; the key itself is the previous part.
            text += " (key)"
        else:
            text += f".{part}" if text else part
    return text or ROOT_LOCATION


def _message(error: Any) -> str:
    kind = error["type"]
    if kind in (_VALUE_ERROR, _RULE_ERROR):
        return error["msg"]
    if kind == "extra_forbidden":
        return "unknown key"
    if kind == "missing":
        return "required key is missing"
    value = error["input"]
    if value is None or isinstance(value, str | int | float):
        return f"{error['msg']} (got {value!r})"
    return error["msg"]


def _rule_issues_for_valid_entries(data: dict[Any, Any]) -> list[_Located]:
    """Run the cross-entry rules over the entries that validated on their own.

    Pydantic skips the model validator when any field is invalid, which would hide rule
    violations behind unrelated mistakes. This keeps one run reporting everything it can.
    """

    def valid_entries(section: str, model: Callable[[Any], Any]) -> list[tuple[int, Any]]:
        raw = data.get(section)
        if not isinstance(raw, list):
            return []
        entries = []
        for index, item in enumerate(raw):
            try:
                entries.append((index, model(item)))
            except ValidationError:
                continue
        return entries

    devices: list[tuple[str, Device]] = []
    other_names: set[str] = set()
    raw_devices = data.get("devices")
    for key, spec in (raw_devices if isinstance(raw_devices, dict) else {}).items():
        if not isinstance(key, str):
            continue
        try:
            devices.append((key, Device.model_validate(_with_name(key, spec))))
        except ValidationError:
            other_names.add(key)

    return _rule_issues(
        devices,
        valid_entries("isis_adjacencies", IsisAdjacency.model_validate),
        valid_entries("bgp_sessions", BgpSession.model_validate),
        valid_entries("routes", RouteExpectation.model_validate),
        valid_entries("path_preferences", PathPreference.model_validate),
        other_device_names=frozenset(other_names),
    )


def _validate(data: dict[Any, Any], path: Path) -> Intent:
    try:
        return Intent.model_validate(data)
    except ValidationError as exc:
        errors = exc.errors(include_url=False)
    issues = [IntentIssue(_format_location(error["loc"]), _message(error)) for error in errors]
    if any(error["type"] != _RULE_ERROR for error in errors):
        issues.extend(
            IntentIssue(_format_location(loc), message)
            for loc, message in _rule_issues_for_valid_entries(data)
        )
    raise IntentError(issues, path)


class _DuplicateKeysError(yaml.YAMLError):
    def __init__(self, issues: list[IntentIssue]) -> None:
        super().__init__()
        self.issues = issues


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """A ``SafeLoader`` that refuses duplicate mapping keys instead of keeping the last one.

    The whole document is scanned before anything is built, so every duplicate is reported.
    """

    def construct_document(self, node: yaml.Node) -> Any:
        issues = self._duplicate_keys(node)
        if issues:
            raise _DuplicateKeysError(issues)
        return super().construct_document(node)

    def _duplicate_keys(self, root: yaml.Node) -> list[IntentIssue]:
        issues: list[IntentIssue] = []
        visited: set[int] = set()

        def walk(node: yaml.Node, loc: _Loc) -> None:
            # Aliases can make the node graph share or even contain itself; visit each node once.
            if id(node) in visited:
                return
            visited.add(id(node))
            if isinstance(node, yaml.SequenceNode):
                for index, child in enumerate(node.value):
                    walk(child, (*loc, index))
            elif isinstance(node, yaml.MappingNode):
                first_line: dict[Any, int] = {}
                for key_node, value_node in node.value:
                    if key_node.tag == "tag:yaml.org,2002:merge":
                        # "<<: *base" deliberately overrides keys from the merged mapping.
                        walk(value_node, loc)
                        continue
                    if not isinstance(key_node, yaml.ScalarNode):
                        walk(value_node, loc)
                        continue
                    key = self.construct_object(key_node)
                    child_loc = (*loc, key if isinstance(key, str) else repr(key))
                    line = key_node.start_mark.line + 1
                    if key in first_line:
                        message = (
                            f"duplicate key {key!r} (first on line {first_line[key]}, again on "
                            f"line {line}); only one definition is allowed"
                        )
                        issues.append(IntentIssue(_format_location(child_loc), message))
                    else:
                        first_line[key] = line
                    walk(value_node, child_loc)

        walk(root, ())
        return issues


def _parse_yaml(text: str) -> Any:
    # Equivalent to yaml.safe_load(text), with duplicate mapping keys rejected.
    loader = _UniqueKeySafeLoader(text)
    try:
        return loader.get_single_data()
    finally:
        loader.dispose()


def load_intent(path: str | os.PathLike[str]) -> Intent:
    """Load and validate an intent file. Raises ``IntentError`` for every kind of failure."""
    path = Path(path)

    def fail(location: str, message: str) -> IntentError:
        return IntentError([IntentIssue(location, message)], path)

    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise fail(FILE_LOCATION, f"file not found: {path}") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise fail(FILE_LOCATION, f"cannot read {path}: {exc}") from exc

    try:
        data = _parse_yaml(text)
    except _DuplicateKeysError as exc:
        raise IntentError(exc.issues, path) from exc
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        detail = getattr(exc, "problem", None) or str(exc)
        raise fail(FILE_LOCATION, f"invalid YAML{where}: {detail}") from exc

    if data is None:
        raise fail(FILE_LOCATION, "file is empty; expected a mapping with a devices section")
    if not isinstance(data, dict):
        found = type(data).__name__
        raise fail(ROOT_LOCATION, f"top level must be a mapping of sections, got {found}")

    return _validate(data, path)
