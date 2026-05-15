"""Tests for the OpenAI-compatibility shim ``_unwrap_stringified_nested``.

Some non-canonical OpenAI-compatible servers (observed on Xiaomi MiMo
mimo-v2.5-pro, 2026-05-15) return tool-call arguments in which nested
object and array values are JSON-encoded strings rather than native
nested objects. The shim parses such strings back into structured data
while leaving spec-compliant providers' output untouched.
"""

from __future__ import annotations

from spec2testbench.extract import _unwrap_stringified_nested


def test_native_dict_passes_through_unchanged() -> None:
    src = {"meta": {"id": "x", "n": 42}, "dut": {"name": "amp"}}
    assert _unwrap_stringified_nested(src) == src


def test_top_level_stringified_dict_is_parsed() -> None:
    src = {"meta": '{"id": "x", "n": 42}', "dut": {"name": "amp"}}
    out = _unwrap_stringified_nested(src)
    assert out == {"meta": {"id": "x", "n": 42}, "dut": {"name": "amp"}}


def test_top_level_stringified_array_is_parsed() -> None:
    src = {"analyses": '[{"id": "a"}, {"id": "b"}]'}
    out = _unwrap_stringified_nested(src)
    assert out == {"analyses": [{"id": "a"}, {"id": "b"}]}


def test_recursively_stringified_nested_dict_parses_fully() -> None:
    """If both an outer field and an inner field are stringified, both unwrap."""
    src = {"outer": '{"inner": "{\\"deep\\": 1}", "flat": 2}'}
    out = _unwrap_stringified_nested(src)
    assert out == {"outer": {"inner": {"deep": 1}, "flat": 2}}


def test_free_form_string_starting_with_brace_but_not_json_is_unchanged() -> None:
    """A natural-language string that happens to begin with `{` must not be misparsed."""
    src = {"nl_spec": "{this is not JSON, just a chat-style note}"}
    out = _unwrap_stringified_nested(src)
    assert out == src


def test_mixed_native_and_stringified_in_list() -> None:
    """A list whose elements are a mix of native dicts and stringified dicts."""
    src = {"items": ['{"id": "a"}', {"id": "b"}, '{"id": "c"}']}
    out = _unwrap_stringified_nested(src)
    assert out == {"items": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}


def test_deeply_nested_legitimate_structure_untouched() -> None:
    """A complex but spec-compliant payload must pass through unchanged."""
    src = {
        "analyses": [
            {"id": "ac1", "type": "AC", "f_start": 1.0, "f_stop": 1e9},
        ],
        "measurements": [
            {
                "id": "g",
                "primitive": "ac_low_freq_asymptote",
                "trigger_event": None,
            },
            {
                "id": "ts",
                "primitive": "tran_settling_time",
                "trigger_event": {"stimulus_id": "p", "edge": "rising"},
            },
        ],
        "scalar": 3.14,
        "literal_string": "ngspice fallback",
        "null_field": None,
        "bool_field": True,
    }
    assert _unwrap_stringified_nested(src) == src


def test_non_dict_non_list_root_passed_through() -> None:
    """The shim is called with the args dict; check it also handles weird roots."""
    assert _unwrap_stringified_nested("plain string") == "plain string"
    assert _unwrap_stringified_nested(42) == 42
    assert _unwrap_stringified_nested(None) is None


def test_real_mimo_smoke_payload_shape() -> None:
    """Recreate the actual payload shape observed on mimo-v2.5-pro 2026-05-15:
    top-level dict where meta and dut arrived as escaped JSON strings,
    while the unordered list fields arrived as native lists."""
    src = {
        "meta": '{"id": "a1_diff_pair_gain_ugb", "nl_spec": "...spec text..."}',
        "dut": '{"name": "diff_pair_ota_5t", "subckt_ports": [{"name": "vinp", "role": "inp"}]}',
        "analyses": [
            {"id": "ac_1", "type": "AC", "f_start": 1.0, "f_stop": 1e9},
        ],
        "measurements": [
            {"id": "m_dc_gain", "primitive": "ac_low_freq_asymptote", "output_unit": "dB"},
        ],
    }
    out = _unwrap_stringified_nested(src)
    assert isinstance(out["meta"], dict)
    assert out["meta"]["id"] == "a1_diff_pair_gain_ugb"
    assert isinstance(out["dut"], dict)
    assert out["dut"]["subckt_ports"] == [{"name": "vinp", "role": "inp"}]
    # List fields were already native; preserved as-is
    assert out["analyses"][0]["id"] == "ac_1"
