from ipaddress import IPv4Address, IPv4Network
from pathlib import Path

import pytest
from pydantic import ValidationError

from route_intent.intent import (
    FILE_LOCATION,
    ROOT_LOCATION,
    Device,
    Intent,
    IntentError,
    IntentIssue,
    IsisAdjacency,
    load_intent,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = REPO_ROOT / "examples" / "intent.yaml"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "intent"

ONE_DEVICE = "devices:\n  r1: {asn: 65001, loopback: 10.0.0.1}\n"


def load_fixture(name: str) -> Intent:
    return load_intent(FIXTURES / name)


def issues_for(name: str) -> list[IntentIssue]:
    with pytest.raises(IntentError) as excinfo:
        load_fixture(name)
    return excinfo.value.issues


def write_intent(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "intent.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def issue_locations(path: Path) -> list[str]:
    with pytest.raises(IntentError) as excinfo:
        load_intent(path)
    return [issue.location for issue in excinfo.value.issues]


# --- valid intents ---------------------------------------------------------------------------


def test_example_matches_the_lab_topology():
    intent = load_intent(EXAMPLE)

    assert len(intent.devices) == 5
    assert len(intent.isis_adjacencies) == 3
    assert len(intent.bgp_sessions) == 5
    assert len(intent.routes) == 2
    assert len(intent.path_preferences) == 1

    assert {(d.name, d.asn, str(d.loopback)) for d in intent.devices.values()} == {
        ("r1", 65001, "10.0.0.1"),
        ("r2", 65001, "10.0.0.2"),
        ("r3", 65001, "10.0.0.3"),
        ("r4", 65002, "10.0.0.4"),
        ("r5", 65003, "10.0.0.5"),
    }
    assert {a.key for a in intent.isis_adjacencies} == {("r1", "r2"), ("r2", "r3"), ("r1", "r3")}
    assert {(s.device, s.peer, s.type) for s in intent.bgp_sessions} == {
        ("r1", "r2", "ibgp"),
        ("r1", "r3", "ibgp"),
        ("r1", "r4", "ebgp"),
        ("r2", "r4", "ebgp"),
        ("r3", "r5", "ebgp"),
    }
    assert {(r.device, str(r.prefix), r.protocol) for r in intent.routes} == {
        ("r3", "192.0.2.0/24", "bgp"),
        ("r3", "198.51.100.0/24", "bgp"),
    }

    preference = intent.path_preferences[0]
    assert (preference.device, str(preference.prefix), preference.exit_via) == (
        "r3",
        "192.0.2.0/24",
        "r1",
    )


def test_valid_fixture_loads_with_typed_values():
    intent = load_fixture("valid.yaml")

    assert intent.devices["r1"] == Device(name="r1", asn=65001, loopback=IPv4Address("10.0.0.1"))
    assert intent.routes[0].prefix == IPv4Network("192.0.2.0/24")
    assert {r.protocol for r in intent.routes} == {"bgp", "isis", "connected"}
    assert intent.bgp_sessions[0].min_prefixes_received is None
    assert intent.bgp_sessions[2].min_prefixes_received == 1
    assert intent.bgp_sessions[3].min_prefixes_received == 0


def test_session_declared_from_both_ends_is_allowed():
    intent = load_fixture("valid.yaml")

    ends = [(s.device, s.peer) for s in intent.bgp_sessions]
    assert ("r1", "r2") in ends
    assert ("r2", "r1") in ends


@pytest.mark.parametrize("asn", [1, 64512, 4200000000, 4294967295])
def test_any_asn_in_range_is_accepted(tmp_path, asn):
    path = write_intent(tmp_path, f"devices:\n  r1: {{asn: {asn}, loopback: 10.0.0.1}}\n")

    assert load_intent(path).devices["r1"].asn == asn


def test_only_devices_is_required(tmp_path):
    intent = load_intent(write_intent(tmp_path, ONE_DEVICE))

    assert intent.isis_adjacencies == []
    assert intent.bgp_sessions == []
    assert intent.routes == []
    assert intent.path_preferences == []


def test_device_may_repeat_its_own_name(tmp_path):
    path = write_intent(tmp_path, "devices:\n  r1: {name: r1, asn: 65001, loopback: 10.0.0.1}\n")

    assert load_intent(path).devices["r1"].name == "r1"


def test_adjacency_compares_regardless_of_order():
    forward = IsisAdjacency(devices=("r1", "r2"))
    reverse = IsisAdjacency(devices=("r2", "r1"))

    assert forward == reverse
    assert hash(forward) == hash(reverse)
    assert forward != IsisAdjacency(devices=("r1", "r3"))
    assert len({forward, reverse}) == 1


# --- one failing case per rule, asserting where the issue is reported -------------------------

# Each fixture breaks exactly one rule, so the whole issue list must be that single location.
SINGLE_ISSUE_CASES = [
    # Rule 1: every device reference exists.
    (
        "rule1_unknown_device_adjacency.yaml",
        "isis_adjacencies[0].devices[1]",
        "unknown device 'r9'",
    ),
    ("rule1_unknown_device_session_device.yaml", "bgp_sessions[0].device", "unknown device 'r9'"),
    ("rule1_unknown_device_session_peer.yaml", "bgp_sessions[2].peer", "unknown device 'r9'"),
    ("rule1_unknown_device_route.yaml", "routes[0].device", "unknown device 'r9'"),
    ("rule1_unknown_device_path_device.yaml", "path_preferences[0].device", "unknown device 'r9'"),
    ("rule1_unknown_device_exit_via.yaml", "path_preferences[0].exit_via", "unknown device 'r9'"),
    # Rule 2: ibgp needs equal ASNs, ebgp needs different ASNs.
    ("rule2_ibgp_different_asn.yaml", "bgp_sessions[0].type", "ibgp requires equal ASNs"),
    ("rule2_ebgp_same_asn.yaml", "bgp_sessions[0].type", "ebgp requires different ASNs"),
    # Rule 3: no self-references.
    ("rule3_self_adjacency.yaml", "isis_adjacencies[0].devices[1]", "to itself"),
    ("rule3_self_session.yaml", "bgp_sessions[0].peer", "to itself"),
    ("rule3_self_exit_via.yaml", "path_preferences[0].exit_via", "cannot exit via itself"),
    # Rule 4: no duplicates. Duplicate device names are duplicate YAML keys, tested below.
    ("rule4_duplicate_loopback.yaml", "devices.r2.loopback", "duplicate loopback 10.0.0.1"),
    ("rule4_duplicate_adjacency_same_order.yaml", "isis_adjacencies[1]", "duplicate adjacency"),
    ("rule4_duplicate_adjacency_reversed.yaml", "isis_adjacencies[1]", "duplicate adjacency"),
    ("rule4_duplicate_session.yaml", "bgp_sessions[1]", "duplicate BGP session"),
    ("rule4_duplicate_route.yaml", "routes[1]", "duplicate route expectation"),
    ("rule4_duplicate_path_preference.yaml", "path_preferences[1]", "duplicate path preference"),
    # Rule 5: ASN is an integer in 1-4294967295, with no conversion from other types.
    ("rule5_asn_zero.yaml", "devices.r1.asn", "greater than or equal to 1"),
    ("rule5_asn_too_large.yaml", "devices.r1.asn", "less than or equal to 4294967295"),
    ("rule5_asn_string.yaml", "devices.r1.asn", "valid integer (got '65001')"),
    ("rule5_asn_bool.yaml", "devices.r1.asn", "valid integer (got True)"),
    ("rule5_asn_float.yaml", "devices.r1.asn", "valid integer (got 65001.0)"),
    # Rule 6: valid IPv4 networks; host bits set are rejected, not normalized.
    ("rule6_prefix_host_bits_route.yaml", "routes[0].prefix", "host bits set"),
    ("rule6_prefix_host_bits_path_preference.yaml", "path_preferences[0].prefix", "host bits set"),
    ("rule6_prefix_bad_length.yaml", "routes[0].prefix", "not a valid IPv4 prefix"),
    ("rule6_prefix_no_length.yaml", "routes[0].prefix", "address/length"),
    ("rule6_prefix_ipv6.yaml", "routes[0].prefix", "not a valid IPv4 prefix"),
    # Rule 7: unknown keys are rejected, at the top level and in every model.
    ("rule7_unknown_key_top_level.yaml", "bgp_session", "unknown key"),
    ("rule7_unknown_key_device.yaml", "devices.r1.role", "unknown key"),
    ("rule7_unknown_key_adjacency.yaml", "isis_adjacencies[0].metric", "unknown key"),
    ("rule7_unknown_key_session.yaml", "bgp_sessions[0].remote_as", "unknown key"),
    ("rule7_unknown_key_route.yaml", "routes[0].metric", "unknown key"),
    ("rule7_unknown_key_path_preference.yaml", "path_preferences[0].weight", "unknown key"),
    # Constraints that come from the field definitions.
    ("field_loopback_ipv6.yaml", "devices.r1.loopback", "not a valid IPv4 address"),
    ("field_bad_loopback.yaml", "devices.r1.loopback", "not a valid IPv4 address"),
    ("field_missing_asn.yaml", "devices.r1.asn", "required key is missing"),
    ("field_device_name_mismatch.yaml", "devices.r1.name", "does not match its key 'r1'"),
    ("field_devices_as_list.yaml", "devices", "valid dictionary"),
    ("field_no_devices.yaml", "devices", "at least 1 item"),
    ("field_bad_session_type.yaml", "bgp_sessions[0].type", "'ibgp' or 'ebgp'"),
    ("field_negative_min_prefixes.yaml", "bgp_sessions[0].min_prefixes_received", "greater than"),
    ("field_bad_protocol.yaml", "routes[0].protocol", "'bgp', 'isis' or 'connected'"),
    ("field_adjacency_three_devices.yaml", "isis_adjacencies[0].devices", "exactly two"),
    ("field_adjacency_one_device.yaml", "isis_adjacencies[0].devices", "exactly two"),
    # Duplicate YAML keys.
    ("dupkey_device.yaml", "devices.r1", "duplicate key 'r1' (first on line 2, again on line 4)"),
    ("dupkey_section.yaml", "routes", "duplicate key 'routes'"),
    ("dupkey_nested_field.yaml", "bgp_sessions[0].peer", "duplicate key 'peer'"),
]


@pytest.mark.parametrize(
    ("fixture", "location", "message"), SINGLE_ISSUE_CASES, ids=[c[0] for c in SINGLE_ISSUE_CASES]
)
def test_rule_violation_is_reported_at_its_location(fixture, location, message):
    issues = issues_for(fixture)

    assert [issue.location for issue in issues] == [location]
    assert message in issues[0].message


def test_every_single_issue_fixture_is_covered_by_a_test():
    covered = {case[0] for case in SINGLE_ISSUE_CASES}
    on_disk = {
        p.name for pattern in ("rule*", "field_*", "dupkey_*") for p in FIXTURES.glob(pattern)
    }

    assert on_disk - {"dupkey_several.yaml"} == covered


def test_duplicate_adjacency_names_the_first_declaration():
    (issue,) = issues_for("rule4_duplicate_adjacency_reversed.yaml")

    assert "isis_adjacencies[0]" in issue.message
    assert "order does not matter" in issue.message


def test_host_bits_message_names_the_network_and_does_not_normalize():
    (issue,) = issues_for("rule6_prefix_host_bits_route.yaml")

    assert "'192.0.2.1/24'" in issue.message
    assert "192.0.2.0/24" in issue.message


# --- strict types: nothing is silently converted ----------------------------------------------


@pytest.mark.parametrize("value", ['"65001"', "true", "65001.0", "1.5", "null", "AS65001"])
def test_asn_must_be_a_yaml_integer(tmp_path, value):
    path = write_intent(tmp_path, f"devices:\n  r1: {{asn: {value}, loopback: 10.0.0.1}}\n")

    assert issue_locations(path) == ["devices.r1.asn"]


@pytest.mark.parametrize("value", ['"1"', "true", "1.0", "-1"])
def test_min_prefixes_received_must_be_a_non_negative_integer(tmp_path, value):
    path = write_intent(
        tmp_path,
        ONE_DEVICE
        + "  r2: {asn: 65002, loopback: 10.0.0.2}\n"
        + "bgp_sessions:\n"
        + f"  - {{device: r1, peer: r2, type: ebgp, min_prefixes_received: {value}}}\n",
    )

    assert issue_locations(path) == ["bgp_sessions[0].min_prefixes_received"]


@pytest.mark.parametrize(
    "value", ['"2001:db8::1"', "167772161", "true", "10.0.0.1/32", "[10, 0, 0, 1]"]
)
def test_loopback_must_be_a_dotted_ipv4_address(tmp_path, value):
    path = write_intent(tmp_path, f"devices:\n  r1: {{asn: 65001, loopback: {value}}}\n")

    assert issue_locations(path) == ["devices.r1.loopback"]


@pytest.mark.parametrize(
    "prefix",
    ["192.0.2.0/255.255.255.0", "192.0.2.0/", "/24", "192.0.2/24", "192.0.2.0/24 ", "0", "24"],
)
def test_prefix_must_be_address_slash_length(tmp_path, prefix):
    path = write_intent(
        tmp_path, ONE_DEVICE + f'routes:\n  - {{device: r1, prefix: "{prefix}", protocol: bgp}}\n'
    )

    assert issue_locations(path) == ["routes[0].prefix"]


@pytest.mark.parametrize("name", ["1", "true", "null"])
def test_device_references_must_be_strings(tmp_path, name):
    path = write_intent(
        tmp_path, ONE_DEVICE + f"bgp_sessions:\n  - {{device: r1, peer: {name}, type: ebgp}}\n"
    )

    assert issue_locations(path) == ["bgp_sessions[0].peer"]


# --- duplicate YAML keys ----------------------------------------------------------------------


def test_every_duplicate_key_is_reported():
    issues = issues_for("dupkey_several.yaml")

    assert [issue.location for issue in issues] == [
        "devices.r1.asn",
        "devices.r1",
        "isis_adjacencies",
    ]


def test_merge_keys_may_override_merged_values(tmp_path):
    # "<<" deliberately lets a mapping override keys it merges in; that is not a duplicate.
    path = write_intent(
        tmp_path,
        "base: &base {asn: 65001, loopback: 10.0.0.1}\n"
        "devices:\n  r1:\n    <<: *base\n    loopback: 10.0.0.2\n",
    )

    # The merge itself is accepted; "base" is then rejected only as an unknown top-level key.
    assert issue_locations(path) == ["base"]


# --- several errors at once -------------------------------------------------------------------


def test_three_different_errors_are_all_reported():
    issues = issues_for("multiple_errors.yaml")

    assert sorted(issue.location for issue in issues) == [
        "bgp_sessions[0].peer",
        "devices.r1.role",
        "routes[0].prefix",
    ]


def test_field_errors_and_rule_violations_are_all_reported():
    issues = issues_for("many_errors.yaml")

    assert sorted(issue.location for issue in issues) == sorted(
        [
            "devices.r1.asn",
            "routes[0].prefix",
            "routes[1].protocol",
            "color",
            "isis_adjacencies[1]",
            "bgp_sessions[0].type",
            "bgp_sessions[1].peer",
            "path_preferences[0].exit_via",
        ]
    )


def test_several_rule_violations_are_all_reported():
    issues = issues_for("multiple_rule_errors.yaml")

    assert [issue.location for issue in issues] == [
        "isis_adjacencies[0].devices[1]",
        "bgp_sessions[0].type",
        "bgp_sessions[1].peer",
        "routes[0].device",
    ]


def test_error_text_lists_every_issue_one_per_line():
    with pytest.raises(IntentError) as excinfo:
        load_fixture("multiple_rule_errors.yaml")

    lines = str(excinfo.value).splitlines()

    assert len(lines) == len(excinfo.value.issues) == 4
    assert lines[0] == "isis_adjacencies[0].devices[1]: adjacency from 'r1' to itself"
    assert lines[2] == "bgp_sessions[1].peer: unknown device 'r9'; it is not listed in devices"


# --- loader failures (rule 8) -----------------------------------------------------------------


def test_missing_file(tmp_path):
    path = tmp_path / "nope.yaml"

    with pytest.raises(IntentError) as excinfo:
        load_intent(path)

    (issue,) = excinfo.value.issues
    assert issue.location == FILE_LOCATION
    assert "file not found" in issue.message
    assert "nope.yaml" in issue.message
    assert excinfo.value.path == path


def test_unreadable_path(tmp_path):
    # A directory cannot be read as a file on any platform.
    with pytest.raises(IntentError) as excinfo:
        load_intent(tmp_path)

    (issue,) = excinfo.value.issues
    assert issue.location == FILE_LOCATION
    assert "cannot read" in issue.message


def test_file_that_is_not_utf8(tmp_path):
    path = tmp_path / "intent.yaml"
    path.write_bytes(b"devices: \xff\xfe\n")

    with pytest.raises(IntentError) as excinfo:
        load_intent(path)

    (issue,) = excinfo.value.issues
    assert issue.location == FILE_LOCATION
    assert "cannot read" in issue.message


def test_invalid_yaml_reports_the_position():
    (issue,) = issues_for("loader_invalid_yaml.yaml")

    assert issue.location == FILE_LOCATION
    assert "invalid YAML at line 3" in issue.message


@pytest.mark.parametrize("fixture", ["loader_empty.yaml", "loader_comments_only.yaml"])
def test_empty_file(fixture):
    (issue,) = issues_for(fixture)

    assert issue.location == FILE_LOCATION
    assert "file is empty" in issue.message


@pytest.mark.parametrize(
    ("fixture", "found"), [("loader_list_root.yaml", "list"), ("loader_scalar_root.yaml", "str")]
)
def test_top_level_must_be_a_mapping(fixture, found):
    (issue,) = issues_for(fixture)

    assert issue.location == ROOT_LOCATION
    assert f"top level must be a mapping of sections, got {found}" in issue.message


def test_python_object_tags_are_refused_not_executed():
    # An unsafe loader would call os.getcwd() here; the safe loader must refuse the tag.
    (issue,) = issues_for("loader_python_tag.yaml")

    assert issue.location == FILE_LOCATION
    assert "invalid YAML" in issue.message
    assert "python/object/apply" in issue.message


def test_accepts_str_and_pathlike_paths():
    path = FIXTURES / "valid.yaml"

    assert load_intent(str(path)) == load_intent(path)


# --- the models enforce the rules without the loader -------------------------------------------


def test_model_validation_enforces_cross_entry_rules():
    data = {
        "devices": {"r1": {"asn": 65001, "loopback": "10.0.0.1"}},
        "bgp_sessions": [{"device": "r1", "peer": "r2", "type": "ibgp"}],
    }

    with pytest.raises(ValidationError) as excinfo:
        Intent.model_validate(data)

    assert [error["loc"] for error in excinfo.value.errors()] == [("bgp_sessions", 0, "peer")]
