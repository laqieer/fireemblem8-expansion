"""Focused contract checks for issue #92's local planner bridge."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
PLAYTEST_DIR = TESTS_DIR.parent
for path in (str(PLAYTEST_DIR), str(TESTS_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

import autoplay_planner as planner
import host_mode
from homebrew_fixture import (
    build_planner_transport_backend,
    build_planner_transport_ack_driver,
    build_production_planner_rom,
)

PROVENANCE = {
    "config": "modern-debug",
    "rom": {"sha1": "fixture", "size": 1024},
    "scenario": {"name": "two-chapter", "schema_version": 1},
}
TRANSCRIPT_SESSION = {
    "transport": "fixture", "rom_identity": 0, "config_identity": 0,
    "scenario_identity": 0, "seed_identity": 0,
    "ready_run_id": 0, "run_id": 1,
}
PLANNER_DRIVER_SOURCES = (
    "src/action_semantics.c", "src/bmtarget.c",
    "src/expansion_autoplay_planner.c",
    "tools/gba-playtest/tests/c/expansion_autoplay_planner_driver.c",
)
PLANNER_DRIVER_DEFINES = (
    "-DFE8_EXPANSION_MODERN_BUILD=1", "-DFE8_EXPANSION_DEBUG=1",
    "-DFE8_EXPANSION_AUTOPLAY_PLANNER=1",
    "-DFE8_AUTOPLAY_PLANNER_RUNTIME_TEST=1",
)
INVALID_SCENARIO_IDS = (
    "-1", "0x100000000", "0x1FFFFFFFF", "1.0", "not_a_constant",
)
TRANSCRIPT_RECORDS = {
    "map_cells": {
        "x": 0, "y": 0, "terrain": 1, "unit": 0,
        "availability": "AVAILABLE",
    },
    "units": {
        "slot": 1, "character": 1, "unit_class": 1,
        "position": [0, 0], "hp": [20, 20], "state": 0,
        "inventory_digest": 0, "availability": "AVAILABLE",
    },
    "inventory": {
        "unit": 1, "slot": 0, "item_id": 1, "uses": 30,
        "raw_item": 0x1E01, "availability": "AVAILABLE",
    },
    "resources": {
        "kind": 3, "slot": 0, "value": 0x1E01,
        "item_id": 1, "uses": 30, "availability": "AVAILABLE",
    },
    "flags": {
        "kind": 4, "flag_id": 0, "state": 0,
        "availability": "AVAILABLE",
    },
}

def _transcript_event(document, kind):
    return next(event for event in document["events"] if event["event"] == kind)

def _transcript_target(document, event_kind, path=()):
    target = document if event_kind is None else _transcript_event(document, event_kind)
    for key in path:
        target = target[key]
    return target

def _rechain_transcript(document):
    previous = "0" * 64
    for sequence, event in enumerate(document["events"]):
        event.pop("event_digest", None)
        event["sequence"] = sequence
        event["previous_digest"] = previous
        event["event_digest"] = planner._digest(event)
        previous = event["event_digest"]

def _assert_import_rejected(test, document, message, *, rechain=True):
    if rechain:
        _rechain_transcript(document)
    with test.assertRaisesRegex(planner.PlannerError, message):
        planner.PlannerTranscript.import_bytes(planner._canonical(document))

def _assert_replay_rejected(test, data, message=None):
    factory = mock.Mock(
        side_effect=AssertionError("invalid transcript started transport")
    )
    context = (
        test.assertRaisesRegex(planner.PlannerError, message)
        if message
        else test.assertRaises(planner.PlannerError)
    )
    with context:
        planner.replay_transcript_on_clean_transport(data, factory)
    factory.assert_not_called()

def _sync_settled_observation(settled, observation):
    settled["observation_identity"] = [observation[field] for field in (
        "run_id", "observation_id", "page_index", "page_count",
        "page_kind", "total_action_count")]
    settled["observation_digest"] = planner._digest(observation)
    settled["terminal"] = dict(state=observation["state"],
                               rejection=observation["rejection"])
    settled["rng"] = {
        "state": observation["rng_state"], "lcg": observation["rng_lcg"],
        "consumption": observation["rng_consumption"]}
    settled["telemetry"] = [record["value"] for record in observation["resources"]
                            if record["kind"] == planner.ValueKind.AUTOPLAY_TELEMETRY.value]

def _rejected_response(document, page_kind):
    events = document["events"]
    for index, event in enumerate(events[:-4]):
        if (event["event"] == "command"
            and events[index + 1]["event"] == "acknowledgement"
            and events[index + 1]["result"] == 0
            and events[index + 3]["event"] == "observation_page"
            and events[index + 3]["observation"]["page_kind"] == page_kind.value):
            return events[index + 3]["observation"], events[index + 4]
    raise AssertionError(f"missing rejected {page_kind.value} response")

def _xor_nested(target, path):
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] ^= 1

def _assert_page_mutation_rejected(test, pages, exported, index, changes):
    mutated = list(pages)
    mutated[index] = replace(mutated[index], **changes)
    document = json.loads(exported)
    expected = mutated[index]
    page_event = next(
        event for event in document["events"]
        if event["event"] == "observation_page"
            and event["observation"]["run_id"] == expected.run_id
            and event["observation"]["observation_id"] == expected.observation_id
            and event["observation"]["page_index"] == index
    )
    page_data = asdict(expected)
    page_event["observation"] = page_data
    settled = document["events"][document["events"].index(page_event) + 1]
    _sync_settled_observation(settled, page_data)
    _rechain_transcript(document)
    _assert_replay_rejected(test, planner._canonical(document))
    for implementation in (
        planner.ScriptedPlanner(),
        planner.BoundedSearchPlanner(max_nodes=planner.MAX_ACTIONS),
    ):
        transport = mock.Mock()
        transport.transcript = planner.PlannerTranscript()
        before = transport.transcript.export()
        with mock.patch.object(
            implementation, "choose", wraps=implementation.choose
        ) as choose:
            with test.assertRaises(planner.PlannerError):
                selected = implementation.choose(
                    planner._assemble_observation_pages(mutated)
                )
                transport.exchange(selected)
        choose.assert_not_called()
        transport.exchange.assert_not_called()
        test.assertEqual(transport.transcript.export(), before)

def _set_transcript_value(document, event_kind, path, value):
    target = _transcript_target(document, event_kind, path[:-1])
    target[path[-1]] = value

def _recorded_transcript():
    bridge = planner.PlannerBridge(PROVENANCE)
    run_id = bridge.begin(PROVENANCE)
    observation = bridge.observe(
        1,
        (
            planner.Field(
                "gold",
                "gPlaySt.partyGoldAmount",
                0xFFFFFFFF,
                planner.Availability.AVAILABLE,
                100,
            ),
        ),
        tuple(
            planner.Action("MOVE_WAIT", 1, (index + 1, 0))
            for index in range(23)
        ),
    )
    complete = planner.collect_observation_pages(bridge, observation)
    choice = complete.actions[0]
    bridge.commit(
        planner.Command(
            planner.CommandKind.COMMIT,
            run_id,
            observation.observation_id,
            choice.ordinal,
            choice.token,
        )
    )
    return bridge.transcript.export()

def _single_action_bridge():
    bridge = planner.PlannerBridge(PROVENANCE)
    bridge.begin(PROVENANCE)
    observation = bridge.observe(
        1,
        (),
        (planner.Action("MOVE_WAIT", 1, (1, 1)),),
    )
    complete = planner.collect_observation_pages(bridge, observation)
    command = planner.Command(
        planner.CommandKind.COMMIT,
        observation.run_id,
        observation.observation_id,
        0,
        complete.actions[0].token,
    )
    return bridge, observation, command

def _run_host_c_driver(
    test,
    name,
    sources,
    *,
    defines=(),
    extra_flags=(),
    compilers=("gcc", "cc"),
    environment=None,
):
    compiler = next(
        (path for candidate in compilers if (path := shutil.which(candidate))),
        None,
    )
    if compiler is None:
        test.skipTest("no compatible host C compiler")
    root = TESTS_DIR.parents[2]
    build_root = root / "build"
    build_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=build_root) as temporary:
        executable = Path(temporary) / name
        run_environment = os.environ.copy()
        run_environment["TMPDIR"] = temporary
        if environment:
            run_environment.update(environment)
        command = [
            compiler,
            "-std=gnu89",
            "-Werror=declaration-after-statement",
            "-Werror=implicit-function-declaration",
            "-Werror=implicit-int",
            "-O2",
            "-ffunction-sections",
            "-fdata-sections",
            "-I",
            str(root / "include"),
            "-I",
            str(root / "include" / "generated"),
            *defines,
            *extra_flags,
            *(str(root / source) for source in sources),
            "-Wl,--gc-sections",
            "-o",
            str(executable),
        ]
        compiled = subprocess.run(
            command,
            cwd=root,
            env=run_environment,
            capture_output=True,
            text=True,
        )
        test.assertEqual(
            compiled.returncode,
            0,
            compiled.stdout + compiled.stderr,
        )
        completed = subprocess.run(
            [str(executable)],
            cwd=root,
            env=run_environment,
            capture_output=True,
            text=True,
        )
        test.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )
        return completed.stdout

def _compile_arm_object(
    test,
    compiler,
    source,
    output,
    *,
    planner_enabled,
    debug=True,
    extra_defines=(),
):
    root = TESTS_DIR.parents[2]
    command = [
        compiler,
        "-mcpu=arm7tdmi",
        "-mthumb",
        "-mthumb-interwork",
        "-mabi=aapcs",
        "-std=gnu89",
        "-ffreestanding",
        "-fno-builtin",
        "-O2",
        "-Werror=declaration-after-statement",
        "-Werror=implicit-function-declaration",
        "-Werror=implicit-int",
        "-I",
        str(root / "include"),
        "-I",
        str(root / "include" / "generated"),
        "-DFE8_EXPANSION_MODERN_BUILD=1",
        f"-DFE8_EXPANSION_AUTOPLAY_PLANNER={int(planner_enabled)}",
    ]
    command.append("-DFE8_EXPANSION_DEBUG=1" if debug else "-DNDEBUG")
    command.extend(extra_defines)
    completed = subprocess.run(
        [*command, "-c", str(source), "-o", str(output)],
        cwd=root,
        capture_output=True,
        text=True,
    )
    test.assertEqual(
        completed.returncode,
        0,
        completed.stdout + completed.stderr,
    )
    return output

def _arm_section_sizes(test, size_tool, *objects):
    root = TESTS_DIR.parents[2]
    completed = subprocess.run(
        [size_tool, "-A", *map(str, objects)],
        cwd=root,
        capture_output=True,
        text=True,
    )
    test.assertEqual(
        completed.returncode,
        0,
        completed.stdout + completed.stderr,
    )
    totals = {}
    for section, value in re.findall(
        r"^(\S+)\s+(\d+)\s+\d+$",
        completed.stdout,
        re.MULTILINE,
    ):
        totals[section] = totals.get(section, 0) + int(value)
    return totals

class _PageReplayTransport:
    def __init__(self, pages):
        self.pages = pages
        self.transcript = planner.PlannerTranscript()
        identity = pages[0]
        self.transcript.record_session({
            **TRANSCRIPT_SESSION,
            "rom_identity": identity.actual_rom_identity,
            "config_identity": identity.actual_config_identity,
            "scenario_identity": identity.actual_scenario_identity,
            "seed_identity": identity.actual_seed_identity,
        })
        ready = planner.Observation(
            0, 0, identity.chapter, (), (), page_kind=planner.PageKind.CONTROL,
            state=1, actual_rom_identity=identity.actual_rom_identity,
            actual_config_identity=identity.actual_config_identity,
            actual_scenario_identity=identity.actual_scenario_identity,
            actual_seed_identity=identity.actual_seed_identity,
        )
        self.transcript.record_observation_page(ready)
        self.transcript.record_settled(ready, (0,) * 13, (0,) * 16)
        self.command_id = 1
        self.largest_exchange = 0
    def _respond(self, command, page):
        size_before = len(self.transcript.export())
        kind = planner._COMMAND_KIND_CODES[command["kind"]]
        self.transcript.reserve_exchange()
        self.transcript.record_command(command)
        self.transcript.record_acknowledgement(self.command_id, kind, 1, 0)
        self.transcript.record_completion(self.command_id, kind, 0)
        self.transcript.record_observation_page(page)
        self.transcript.record_settled(page, (0,) * 13, (0,) * 16)
        self.command_id += 1
        self.largest_exchange = max(
            self.largest_exchange, len(self.transcript.export()) - size_before
        )
        return page
    def start(self, *, scenario_identity=None):
        first = self.pages[0]
        identities = (
            first.actual_rom_identity, first.actual_config_identity,
            first.actual_scenario_identity if scenario_identity is None else scenario_identity,
            first.actual_seed_identity,
        )
        return self._respond({
            "kind": planner.CommandKind.START.value,
            "run_id": 0, "observation_id": 0,
            "expected_identities": identities,
        }, first)
    def exchange(self, command):
        if command.kind is not planner.CommandKind.PAGE:
            raise AssertionError("page replay transport accepts only PAGE")
        return self._respond(
            planner._command_payload(command), self.pages[command.page_index]
        )
    def record_complete_observation(self, observation):
        self.transcript.record_complete_and_settled(
            observation, (0,) * 13, (0,) * 16
        )

def _arm_code_size(sections):
    return sum(
        sections.get(name, 0)
        for name in (".text", ".rodata", ".rodata.str1.4")
    )

class PlannerBridgeTests(unittest.TestCase):
    def test_public_validation_errors_name_protocol_v2(self):
        bridge = planner.PlannerBridge(PROVENANCE)
        bridge.begin(PROVENANCE)
        with self.assertRaisesRegex(
            planner.PlannerError,
            r"chapter is outside the v2 range",
        ):
            bridge.observe(0, (), (planner.Action("MOVE_WAIT", 1, (0, 0)),))
        bridge = planner.PlannerBridge(PROVENANCE)
        bridge.begin(PROVENANCE)
        with self.assertRaisesRegex(
            planner.PlannerError,
            r"legal action count exceeds v2 resource limit",
        ):
            bridge.observe(
                1,
                (),
                tuple(
                    planner.Action("MOVE_WAIT", 1, (0, 0))
                    for _ in range(planner.MAX_ACTIONS + 1)
                ),
            )
    def test_transcript_round_trip_tamper_order_and_atomic_limits(self):
        bridge = planner.PlannerBridge(PROVENANCE)
        run_id = bridge.begin(PROVENANCE)
        observation = bridge.observe(
            1,
            (
                planner.Field(
                    "gold",
                    "gPlaySt.partyGoldAmount",
                    0xFFFFFFFF,
                    planner.Availability.AVAILABLE,
                    100,
                ),
            ),
            tuple(
                planner.Action("MOVE_WAIT", 1, (index + 1, 0))
                for index in range(23)
            ),
        )
        complete = planner.collect_observation_pages(bridge, observation)
        selected = complete.actions[0]
        bridge.commit(
            planner.Command(
                planner.CommandKind.COMMIT,
                run_id,
                observation.observation_id,
                selected.ordinal,
                selected.token,
            )
        )
        exported = bridge.transcript.export()
        self.assertLessEqual(len(exported), planner.MAX_TRACE_BYTES)
        imported = planner.PlannerTranscript.import_bytes(exported)
        self.assertEqual(imported.export(), exported)
        self.assertEqual(imported.digest(), bridge.transcript.digest())
        sessionless = json.loads(exported)
        sessionless["events"].pop(0)
        _assert_import_rejected(self, sessionless, "exactly one leading session")
        late_session = json.loads(exported)
        late_session["events"][0], late_session["events"][1] = (
            late_session["events"][1], late_session["events"][0],
        )
        _assert_import_rejected(self, late_session, "exactly one leading session")
        duplicate_session = json.loads(exported)
        duplicate_session["events"].insert(1, dict(duplicate_session["events"][0]))
        _assert_import_rejected(self, duplicate_session, "exactly one leading session")
        token_tampered = json.loads(exported)
        complete_event = next(
            event
            for event in token_tampered["events"]
            if event["event"] == "observation_complete"
        )
        complete_event["observation"]["actions"][0]["token"]["word3"] ^= 1
        complete_event["candidate_set_digest"] = planner._digest(
            complete_event["observation"]["actions"])
        complete_index = token_tampered["events"].index(complete_event)
        token_tampered["events"][complete_index + 1][
            "observation_digest"
        ] = planner._digest(complete_event["observation"])
        _assert_import_rejected(
            self, token_tampered, "accepted transcript token mismatch")
        runtime_tampered = json.loads(exported)
        settled_event = next(
            event
            for event in runtime_tampered["events"]
            if event["event"] == "settled"
        )
        settled_event["terminal"]["state"] ^= 1
        _assert_import_rejected(self, runtime_tampered, "settled runtime state mismatch")
        acknowledgement = next(
            event
            for event in json.loads(exported)["events"]
            if event["event"] == "acknowledgement"
        )
        for result, rejection in (
            (0, 0),
            (1, 4),
        ):
            with self.subTest(
                acknowledgement_result=result,
                acknowledgement_rejection=rejection,
            ):
                invalid_ack = json.loads(exported)
                event = next(
                    item
                    for item in invalid_ack["events"]
                    if item["event"] == "acknowledgement"
                )
                event["result"] = result
                event["rejection"] = rejection
                _assert_import_rejected(
                    self,
                    invalid_ack,
                    "invalid acknowledgement result/rejection pair",
                )
        for field, value, message in (
            ("command_id", acknowledgement["command_id"] + 1,
             "acknowledgement order"),
            ("kind", acknowledgement["kind"] + 1,
             "acknowledgement kind mismatch"),
        ):
            with self.subTest(acknowledgement_field=field):
                invalid_ack = json.loads(exported)
                event = next(
                    item
                    for item in invalid_ack["events"]
                    if item["event"] == "acknowledgement"
                )
                event[field] = value
                _assert_import_rejected(self, invalid_ack, message)
        page_cross_swap = json.loads(exported)
        page_commands = [
            event["command"]
            for event in page_cross_swap["events"]
            if event["event"] == "command"
                and event["command"]["kind"]
                    == planner.CommandKind.PAGE.value
        ]
        page_commands[0]["page_index"], page_commands[1]["page_index"] = (
            page_commands[1]["page_index"],
            page_commands[0]["page_index"],
        )
        _assert_import_rejected(
            self, page_cross_swap, "PAGE response identity mismatch"
        )
        base_events = json.loads(exported)["events"]
        command_index = next(
            index
            for index, event in enumerate(base_events)
            if event["event"] == "command"
                and event["command"]["kind"]
                    == planner.CommandKind.PAGE.value
        )
        ack_index = command_index + 1
        completion_index = command_index + 2
        response_index = command_index + 3
        order_cases = (
            ("swap", ack_index, completion_index, "completion order"),
            (
                "swap",
                completion_index,
                response_index,
                "response observation precedes completion",
            ),
            ("insert", completion_index, ack_index, "acknowledgement order"),
            ("insert", response_index, completion_index, "completion order"),
            (
                "pop",
                response_index,
                0,
                "settled event has no response observation",
            ),
        )
        for operation, target, source, message in order_cases:
            document = json.loads(exported)
            events = document["events"]
            if operation == "swap":
                events[target], events[source] = events[source], events[target]
            elif operation == "insert":
                events.insert(target, dict(events[source]))
            else:
                events.pop(target)
            _assert_import_rejected(self, document, message)
        interleaved_command = json.loads(exported)
        second_command = next(
            event
            for event in interleaved_command["events"][
                command_index + 1 :
            ]
            if event["event"] == "command"
        )
        interleaved_command["events"].insert(
            ack_index,
            dict(second_command),
        )
        _assert_import_rejected(
            self, interleaved_command, "command overlap"
        )
        with self.assertRaisesRegex(
            planner.PlannerError,
            "invalid planner transcript JSON|not canonical",
        ):
            planner.PlannerTranscript.import_bytes(exported[:-1])
    def test_transcript_json_depth_and_recursion_fail_closed(self):
        def nested_array(depth):
            value = 0
            for _ in range(depth):
                value = [value]
            return value
        def nested_object(depth):
            value = 0
            for _ in range(depth):
                value = {"value": value}
            return value
        for factory in (nested_array, nested_object):
            for depth in (
                planner.MAX_JSON_DEPTH - 1,
                planner.MAX_JSON_DEPTH,
            ):
                with self.subTest(
                    shape=factory.__name__,
                    depth=depth,
                ):
                    encoded = planner._canonical(factory(depth))
                    self.assertIsNotNone(json.loads(encoded))
            with self.assertRaisesRegex(
                planner.PlannerError,
                "invalid planner transcript JSON depth",
            ):
                planner._canonical(
                    factory(planner.MAX_JSON_DEPTH + 1)
                )
        at_limit = (
            b"[" * planner.MAX_JSON_DEPTH
            + b"0"
            + b"]" * planner.MAX_JSON_DEPTH
        )
        with self.assertRaisesRegex(
            planner.PlannerError,
            "invalid planner transcript envelope",
        ):
            planner.PlannerTranscript.import_bytes(at_limit)
        above_limit = (
            b"[" * (planner.MAX_JSON_DEPTH + 1)
            + b"0"
            + b"]" * (planner.MAX_JSON_DEPTH + 1)
        )
        with self.assertRaisesRegex(
            planner.PlannerError,
            "invalid planner transcript JSON depth",
        ):
            planner.PlannerTranscript.import_bytes(above_limit)
        object_above_limit = (
            b'{"value":' * (planner.MAX_JSON_DEPTH + 1)
            + b"0"
            + b"}" * (planner.MAX_JSON_DEPTH + 1)
        )
        factory_calls = 0
        def transport_factory():
            nonlocal factory_calls
            factory_calls += 1
            raise AssertionError("invalid transcript started transport")
        with self.assertRaisesRegex(
            planner.PlannerError,
            "invalid planner transcript JSON depth",
        ):
            planner.replay_transcript_on_clean_transport(
                object_above_limit,
                transport_factory,
            )
        self.assertEqual(factory_calls, 0)
        with self.assertRaisesRegex(
            planner.PlannerError,
            "invalid planner transcript JSON",
        ):
            planner.PlannerTranscript.import_bytes(b'{"value":"\xff"}')
        bridge = planner.PlannerBridge(PROVENANCE)
        bridge.begin(PROVENANCE)
        observation = bridge.observe(
            1,
            (),
            (planner.Action("MOVE_WAIT", 1, (1, 0)),),
        )
        planner.collect_observation_pages(bridge, observation)
        valid = bridge.transcript.export()
        with mock.patch.object(
            planner.json,
            "loads",
            side_effect=RecursionError("parser recursion"),
        ):
            with self.assertRaisesRegex(
                planner.PlannerError,
                "invalid planner transcript JSON",
            ):
                planner.PlannerTranscript.import_bytes(valid)
        with mock.patch.object(
            planner.json,
            "dumps",
            side_effect=RecursionError("canonicalizer recursion"),
        ):
            with self.assertRaisesRegex(
                planner.PlannerError,
                "invalid planner transcript JSON recursion",
            ):
                planner.PlannerTranscript.import_bytes(valid)
    def test_completion_timing_is_typed_and_kind_bounded(self):
        bridge = planner.PlannerBridge(PROVENANCE)
        run_id = bridge.begin(PROVENANCE)
        observation = bridge.observe(
            1,
            (),
            tuple(
                planner.Action("MOVE_WAIT", 1, (index + 1, 0))
                for index in range(23)
            ),
        )
        complete = planner.collect_observation_pages(
            bridge,
            observation,
        )
        choice = complete.actions[0]
        bridge.commit(
            planner.Command(
                planner.CommandKind.COMMIT,
                run_id,
                observation.observation_id,
                choice.ordinal,
                choice.token,
            )
        )
        encoded = bridge.transcript.export()
        def mutate_completion(kind, response_frames):
            document = json.loads(encoded)
            completion = next(
                event
                for event in document["events"]
                if event["event"] == "completion"
                    and event["kind"] == kind
            )
            completion["response_frames"] = response_frames
            _rechain_transcript(document)
            return planner._canonical(document)
        for kind, response_frames in (
            (4, planner.COMMAND_RESPONSE_FRAME_LIMIT),
            (2, planner.COMMAND_RESPONSE_FRAME_LIMIT + 1),
            (2, planner.COMMIT_COMPLETION_FRAME_LIMIT),
        ):
            with self.subTest(
                valid_kind=kind,
                response_frames=response_frames,
            ):
                imported = planner.PlannerTranscript.import_bytes(
                    mutate_completion(kind, response_frames)
                )
                self.assertTrue(imported.events)
        for kind, response_frames in (
            (4, -1),
            (4, planner.COMMAND_RESPONSE_FRAME_LIMIT + 1),
            (2, planner.COMMIT_COMPLETION_FRAME_LIMIT + 1),
            (4, True),
            (4, "1"),
            (4, 1.0),
        ):
            with self.subTest(
                invalid_kind=kind,
                response_frames=response_frames,
            ):
                with self.assertRaisesRegex(
                    planner.PlannerError,
                    "completion timing is invalid",
                ):
                    planner.PlannerTranscript.import_bytes(
                        mutate_completion(kind, response_frames)
                    )
        rejected_commit = json.loads(encoded)
        acknowledgement = next(
            event
            for event in rejected_commit["events"]
            if event["event"] == "acknowledgement"
                and event["kind"] == 2
        )
        acknowledgement["result"] = 0
        acknowledgement["rejection"] = 4
        completion = next(
            event
            for event in rejected_commit["events"]
            if event["event"] == "completion"
                and event["kind"] == 2
        )
        completion["response_frames"] = (
            planner.COMMAND_RESPONSE_FRAME_LIMIT + 1
        )
        _rechain_transcript(rejected_commit)
        with self.assertRaisesRegex(
            planner.PlannerError,
            "completion timing is invalid",
        ):
            planner.PlannerTranscript.import_bytes(
                planner._canonical(rejected_commit)
            )
        factory_calls = 0

        def factory():
            nonlocal factory_calls
            factory_calls += 1
            raise AssertionError("invalid timing started transport")
        with self.assertRaisesRegex(
            planner.PlannerError,
            "completion timing is invalid",
        ):
            planner.replay_transcript_on_clean_transport(
                mutate_completion(4, -1),
                factory,
            )
        self.assertEqual(factory_calls, 0)
    def test_transcript_schema_rejects_unknown_keys_pre_factory(self):
        encoded = _recorded_transcript()
        targets = (
            ("envelope", None, ()),
            ("session event", "session", ()),
            ("session provenance", "session", ("provenance",)),
            ("session source", "session", ("provenance", "source")),
            ("session ROM", "session", ("provenance", "source", "rom")),
            ("session scenario", "session", ("provenance", "source", "scenario")),
            ("complete event", "observation_complete", ()),
            ("observation", "observation_complete", ("observation",)),
            ("field", "observation_complete", ("observation", "fields", 0)),
            ("action record", "observation_complete", ("observation", "actions", 0)),
            ("action", "observation_complete", ("observation", "actions", 0, "action")),
            ("action token", "observation_complete", ("observation", "actions", 0, "token")),
            ("command event", "command", ()),
            ("command", "command", ("command",)),
            ("acknowledgement", "acknowledgement", ()),
            ("completion", "completion", ()),
            ("observation page", "observation_page", ()),
            ("settled event", "settled", ()),
            ("settled RNG", "settled", ("rng",)),
            ("settled terminal", "settled", ("terminal",)),
        )
        for name, event_kind, path in targets:
            with self.subTest(schema=name):
                document = json.loads(encoded)
                _transcript_target(document, event_kind, path)["unexpected"] = 1
                _rechain_transcript(document)
                _assert_replay_rejected(
                    self, planner._canonical(document), "schema"
                )
        for name, record in TRANSCRIPT_RECORDS.items():
            with self.subTest(record=name):
                document = json.loads(encoded)
                observation = _transcript_target(
                    document,
                    "observation_complete",
                    ("observation",),
                )
                observation[name] = [{**record, "unexpected": 1}]
                _assert_import_rejected(self, document, "schema")
        document = json.loads(encoded)
        commit = next(
            event["command"]
            for event in document["events"]
            if event["event"] == "command"
                and event["command"]["kind"] == "COMMIT"
        )
        commit["token"]["unexpected"] = 1
        _assert_import_rejected(self, document, "schema")
        for name in ("checkpoint", "telemetry"):
            document = json.loads(encoded)
            _transcript_event(document, "settled")[name] = [
                {"unexpected": 1}
            ]
            _assert_import_rejected(
                self,
                document,
                "invalid settled transcript record",
            )
        document = json.loads(encoded)
        error = {
            "event": "transport_error",
            "code": "COMMAND_ACK_TIMEOUT",
            "command_id": 99,
            "kind": 1,
            "unexpected": 1,
        }
        document["events"].append(error)
        _assert_import_rejected(self, document, "schema")
        missing_required = json.loads(encoded)
        _transcript_event(
            missing_required,
            "command",
        )["command"].pop(
            "observation_id"
        )
        _assert_import_rejected(
            self, missing_required, "command schema"
        )
    def test_transcript_scalars_and_nonfinite_reject_pre_factory(self):
        encoded = _recorded_transcript()
        complete = "observation_complete"
        observation = ("observation",)
        action = observation + ("actions", 0)
        scalar_cases = (
            ("session", ("provenance", "rom_identity"), True),
            ("session", ("provenance", "source", "rom", "size"), -1),
            ("session", ("provenance", "source", "scenario", "schema_version"), 1.0),
            (complete, observation + ("run_id",), "1"),
            (complete, observation + ("chapter",), 0x100),
            (complete, observation + ("page_count",), 0),
            (complete, observation + ("page_index",), 92),
            (complete, observation + ("page_kind",), 1),
            (complete, observation + ("state",), 6),
            (complete, observation + ("rejection",), 11),
            (complete, observation + ("rng_state", 0), "1"),
            (complete, observation + ("actual_seed_identity",), 1.0),
            (complete, observation + ("record_count",), -1),
            (complete, observation + ("fields", 0, "bound"), True),
            (complete, observation + ("fields", 0, "value"), "100"),
            (complete, action + ("ordinal",), True),
            (complete, action + ("action", "kind"), "STAFF"),
            (complete, action + ("action", "actor"), 0x100),
            (complete, action + ("action", "destination", 0), 64),
            (complete, action + ("action", "item_slot"), 5),
            *(
                (complete, action + ("token", f"word{word}"), invalid)
                for word, invalid in enumerate((True, "0", 1.0, -1))
            ),
            (complete, ("page_identity", 2), 93),
            ("settled", ("checkpoint", 0), "0"),
            ("settled", ("command_words", 0), True),
            ("settled", ("telemetry",), [1.0]),
            ("settled", ("rng", "state", 0), -1),
            ("settled", ("terminal", "state"), True),
            ("acknowledgement", ("result",), True),
            ("completion", ("response_frames",), 1.0),
        )
        for event_kind, path, value in scalar_cases:
            with self.subTest(event=event_kind, path=path):
                document = json.loads(encoded)
                _set_transcript_value(document, event_kind, path, value)
                _rechain_transcript(document)
                _assert_replay_rejected(self, planner._canonical(document))
        record_cases = (
            ("map_cells", "x", 64),
            ("units", "state", -1),
            ("inventory", "slot", 5),
            ("resources", "slot", 100),
            ("flags", "state", 2),
        )
        for field, key, value in record_cases:
            with self.subTest(record=field):
                document = json.loads(encoded)
                observation = _transcript_event(
                    document,
                    "observation_complete",
                )["observation"]
                observation[field] = [{
                    **TRANSCRIPT_RECORDS[field],
                    key: value,
                }]
                _rechain_transcript(document)
                _assert_replay_rejected(self, planner._canonical(document))
        session_only = planner.PlannerTranscript()
        session_only.record_session(TRANSCRIPT_SESSION)
        command_cases = (
            {
                "kind": "START", "run_id": 0, "observation_id": 0,
                "expected_identities": [0, 0, "0", 0],
            },
            {
                "kind": "PAGE", "run_id": 1, "observation_id": 1,
                "page_index": -1,
            },
            {
                "kind": "CANCEL", "run_id": True, "observation_id": 1,
            },
            {
                "kind": "COMMIT", "run_id": 1, "observation_id": 1,
                "action_ordinal": 1.0,
                "token": {
                    "word0": 0, "word1": 0, "word2": 0, "word3": 0,
                },
            },
        )
        for command in command_cases:
            with self.subTest(command=command["kind"]):
                document = json.loads(session_only.export())
                document["events"].append(
                    {"event": "command", "command": command}
                )
                _rechain_transcript(document)
                _assert_replay_rejected(self, planner._canonical(document))
        rejected_commit = json.loads(session_only.export())
        rejected_commit["events"].extend((
            {
                "event": "command",
                "command": {
                    "kind": "COMMIT", "run_id": 1,
                    "observation_id": 1, "action_ordinal": 0,
                    "token": {
                        "word0": 0, "word1": 0,
                        "word2": "0", "word3": 0,
                    },
                },
            },
            {
                "event": "acknowledgement", "command_id": 1,
                "kind": 2, "result": 0, "rejection": 4,
            },
        ))
        _rechain_transcript(rejected_commit)
        _assert_replay_rejected(self, planner._canonical(rejected_commit))
        transport_error = json.loads(session_only.export())
        transport_error["events"].append({
            "event": "transport_error",
            "code": "COMMAND_ACK_TIMEOUT",
            "command_id": "1",
            "kind": 1,
        })
        _rechain_transcript(transport_error)
        _assert_replay_rejected(self, planner._canonical(transport_error))
        for constant in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(nonfinite=constant):
                _assert_replay_rejected(
                    self,
                    (
                        '{"events":[],"schema":'
                        + constant
                        + "}"
                    ).encode(),
                    "invalid planner transcript JSON",
                )
                nested = encoded.replace(
                    b'"rom_identity":0',
                    f'"rom_identity":{constant}'.encode(),
                    1,
                )
                _assert_replay_rejected(
                    self,
                    nested,
                    "invalid planner transcript JSON",
                )
                transcript = planner.PlannerTranscript()
                bad_session = dict(TRANSCRIPT_SESSION)
                bad_session["rom_identity"] = float(constant)
                with self.assertRaisesRegex(
                    planner.PlannerError,
                    "invalid planner transcript JSON value",
                ):
                    transcript.record_session(bad_session)
                self.assertEqual(transcript.events, ())
    def test_mailbox_has_no_arbitrary_memory_write_api(self):
        mailbox = planner.Mailbox()
        self.assertFalse(hasattr(mailbox, "write"))
        self.assertFalse(hasattr(mailbox, "address"))
        mailbox.submit(planner.Command(planner.CommandKind.START, 1, 0))
        with self.assertRaisesRegex(planner.PlannerError, "unconsumed"):
            mailbox.submit(planner.Command(planner.CommandKind.START, 1, 0))
    def test_maximum_semantic_transcript_fits_two_mib(self):
        available = planner.Availability.AVAILABLE
        field_values = (64 | (64 << 16), 0, 1, 0, 0, 0, 0, 0)
        fields = tuple(
            planner.Field(name, source, bound, available, field_values[index])
            for index, (name, source, bound) in enumerate(
                planner._SEMANTIC_FIELD_NAMES.values()
            )
        )
        map_cells = tuple(
            planner.MapCell(index % 64, index // 64, 1, 0, available)
            for index in range(planner.MAX_MAP_CELLS)
        )
        units = tuple(
            planner.UnitRecord(slot, 1, 1, (0, 0), (20, 20), 0, 0, available)
            for slot in planner._ROSTER_SLOTS
        )
        inventory = tuple(
            planner.InventoryRecord(unit, slot, 1, 30, 0x1E01, available)
            for unit in planner._ROSTER_SLOTS
            for slot in range(planner.UNIT_ITEM_COUNT)
        )
        resources = (
            planner.ResourceRecord(
                planner.ValueKind.GOLD, None, 999, None, None, available
            ),
            *(
                planner.ResourceRecord(
                    planner.ValueKind.CONVOY_ITEM, index, 0, 0, 0,
                    planner.Availability.EMPTY
                )
                for index in range(planner.CONVOY_ITEM_COUNT)
            ),
            *(
                planner.ResourceRecord(
                    planner.ValueKind.AUTOPLAY_TELEMETRY,
                    index, 0, None, None, available
                )
                for index in range(planner.AUTOPLAY_TELEMETRY_WORDS)
            ),
        )
        flags = tuple(
            planner.FlagRecord(
                planner.ValueKind.PERMANENT_FLAG
                    if index < 2048 else planner.ValueKind.CHAPTER_FLAG,
                index % 2048, index & 1, available
            )
            for index in range(4096)
        )
        actions = tuple(
            planner.ActionRecord(
                index,
                planner.Action("MOVE_WAIT", 1, (index % 64, index // 64)),
                planner.OpaqueToken(index, index + 1, index + 2, index + 3),
            )
            for index in range(planner.MAX_ACTIONS)
        )
        components = (
            (planner.PageKind.MAP, "map_cells", map_cells, 224),
            (planner.PageKind.UNITS, "units", units, 56),
            (planner.PageKind.INVENTORY, "inventory", inventory, 112),
            (planner.PageKind.RESOURCES, "resources", resources, 112),
            (planner.PageKind.FLAGS, "flags", flags, 112),
            (planner.PageKind.ACTIONS, "actions", actions, planner.ACTIONS_PER_PAGE),
        )
        page_count = 1 + sum(
            (len(records) + capacity - 1) // capacity
            for _, _, records, capacity in components
        )
        self.assertEqual(page_count, 92)
        common = {
            "run_id": 1,
            "observation_id": 1,
            "chapter": 1,
            "fields": (),
            "actions": (),
            "page_count": page_count,
            "total_action_count": planner.MAX_ACTIONS,
            "actual_rom_identity": 1,
            "actual_config_identity": 1,
            "actual_scenario_identity": 1,
            "actual_seed_identity": 1,
            "state": 2,
        }
        pages = [planner.Observation(
            **{**common, "fields": fields},
            record_count=len(fields),
            total_record_count=len(fields),
        )]
        for page_kind, field, records, capacity in components:
            for start in range(0, len(records), capacity):
                chunk = records[start : start + capacity]
                pages.append(planner.Observation(
                    **{**common, "page_index": len(pages), "page_kind": page_kind,
                       "record_start": start, "record_count": len(chunk),
                       "total_record_count": len(records), field: chunk}
                ))
        complete = replace(
            pages[0],
            actions=actions,
            map_cells=map_cells,
            units=units,
            inventory=inventory,
            resources=resources,
            flags=flags,
        )
        assembled = planner._assemble_observation_pages(pages)
        self.assertEqual(assembled, complete)
        for implementation in (
            planner.ScriptedPlanner(),
            planner.BoundedSearchPlanner(max_nodes=512),
        ):
            self.assertEqual(implementation.choose(assembled).ordinal, 0)
        transport = _PageReplayTransport(pages)
        replayed = planner.collect_observation_pages(
            transport, transport.start(scenario_identity=1)
        )
        self.assertEqual(replayed, complete)
        self.assertLessEqual(
            transport.largest_exchange,
            planner.MAX_TRANSCRIPT_EXCHANGE_BYTES,
        )
        exported = transport.transcript.export()
        self.assertLessEqual(len(exported), planner.MAX_TRACE_BYTES)
        self.assertEqual(
            planner.PlannerTranscript.import_bytes(exported).export(),
            exported,
        )
        self.assertEqual(
            planner.replay_transcript_on_clean_transport(
                exported, lambda: _PageReplayTransport(pages)
            ),
            exported,
        )
        def raw_page(index, kind, count, total, payload):
            words = [0] * 249
            words[:15] = [
                0x41504C4E, planner.PROTOCOL_VERSION, 996, 1, 2, 2,
                index, 3, kind, 0, count, total, 2, 0, 1,
            ]
            words[25 : 25 + len(payload)] = payload
            return planner.parse_transport_observation(words)
        small_pages = (
            raw_page(0, 1, 8, 8, [
                word for field in range(1, 9)
                for word in (field | (4 << 24), 3 | (2 << 16) if field == 1 else 0)
            ]),
            raw_page(1, 2, 6, 6, [
                x | (y << 6) | (1 << 12)
                for y in range(2) for x in range(3)
            ]),
            raw_page(2, 4, 2, 2, [
                1, 1, 2 | (1 << 16), 0, 0xFFFF, 1, 2, 3, 4, 0,
                5, 1, 0, 2 << 8 | 1 << 16, 0xFFFF, 5, 6, 7, 8, 13,
            ]),
        )
        small_fields = small_pages[0].fields
        small_actions = small_pages[2].actions
        small_transport = _PageReplayTransport(small_pages)
        small_complete = planner.collect_observation_pages(
            small_transport, small_transport.start()
        )
        self.assertEqual((
            small_complete.actions[0].action.destination,
            small_complete.actions[1].action.target_position,
        ), ((2, 1), (2, 1)))
        for implementation in (planner.ScriptedPlanner(), planner.BoundedSearchPlanner()):
            implementation.choose(small_complete)
        small_exported = small_transport.transcript.export()
        def action_mutation(index, field, value):
            records = list(small_actions)
            records[index] = replace(records[index], action=replace(
                records[index].action, **{field: value}
            ))
            return {"actions": tuple(records)}
        coordinate_mutations = (
            ("destination 63", 2, action_mutation(0, "destination", (63, 63))),
            ("destination width", 2, action_mutation(0, "destination", (3, 1))),
            ("destination height", 2, action_mutation(0, "destination", (2, 2))),
            ("target 63", 2, action_mutation(1, "target_position", (63, 63))),
            ("target width", 2, action_mutation(1, "target_position", (3, 1))),
            ("target height", 2, action_mutation(1, "target_position", (2, 2))),
            ("zero dimensions", 0,
             {"fields": (replace(small_fields[0], value=0),)}),
            ("unavailable dimensions", 0, {"fields": (replace(
                small_fields[0], availability=planner.Availability.UNAVAILABLE,
                value=None,
            ),)}),
        )
        for name, index, changes in coordinate_mutations:
            with self.subTest(map_coordinate=name):
                _assert_page_mutation_rejected(
                    self, small_pages, small_exported, index, changes
                )
        page_index = {}
        for index, page in enumerate(pages):
            page_index.setdefault(page.page_kind, index)
        map_page = pages[page_index[planner.PageKind.MAP]]
        unit_page = pages[page_index[planner.PageKind.UNITS]]
        inventory_page = pages[page_index[planner.PageKind.INVENTORY]]
        resource_page = pages[page_index[planner.PageKind.RESOURCES]]
        flag_page = pages[page_index[planner.PageKind.FLAGS]]
        action_page = pages[page_index[planner.PageKind.ACTIONS]]
        swapped = lambda records: (records[1], records[0], *records[2:])
        mutations = (
            ("dimensions", 0,
             {"fields": (replace(pages[0].fields[0],
                                 value=63 | (64 << 16)),
                         *pages[0].fields[1:])}),
            ("field order", 0, {"fields": swapped(pages[0].fields)}),
            ("field identity", 0,
             {"fields": (replace(pages[0].fields[0], source="wrong"),
                         *pages[0].fields[1:])}),
            ("field duplicate", 0,
             {"fields": (pages[0].fields[0],
                                 replace(pages[0].fields[1],
                                         name=pages[0].fields[0].name),
                                 *pages[0].fields[2:])}),
            ("duplicate summary", page_index[planner.PageKind.MAP], {
                "page_kind": planner.PageKind.SUMMARY,
                "map_cells": (), "record_count": 0,
                "total_record_count": len(fields),
            }),
            ("missing summary", 0, {"page_kind": planner.PageKind.MAP}),
            ("duplicate page index", page_index[planner.PageKind.MAP],
             {"page_index": 0}),
            ("map order", page_index[planner.PageKind.MAP],
             {"map_cells": swapped(map_page.map_cells)}),
            ("map duplicate", page_index[planner.PageKind.MAP],
             {"map_cells": (map_page.map_cells[0], map_page.map_cells[0],
                            *map_page.map_cells[2:])}),
            ("map unit absent from roster", page_index[planner.PageKind.MAP],
             {"map_cells": (replace(map_page.map_cells[0], unit=0x40),
                            *map_page.map_cells[1:])}),
            ("cross-page map duplicate", page_index[planner.PageKind.MAP] + 1,
             {"map_cells": (map_page.map_cells[-1],
                            *pages[page_index[planner.PageKind.MAP] + 1]
                                .map_cells[1:])}),
            ("unit order", page_index[planner.PageKind.UNITS],
             {"units": swapped(unit_page.units)}),
            ("unit duplicate", page_index[planner.PageKind.UNITS],
             {"units": (unit_page.units[1], unit_page.units[1],
                        *unit_page.units[2:])}),
            ("absent inventory unit", page_index[planner.PageKind.INVENTORY],
             {"inventory": (replace(inventory_page.inventory[0], unit=0),
                            *inventory_page.inventory[1:])}),
            ("duplicate inventory slot", page_index[planner.PageKind.INVENTORY],
             {"inventory": (inventory_page.inventory[0],
                            inventory_page.inventory[0],
                            *inventory_page.inventory[2:])}),
            ("inventory availability", page_index[planner.PageKind.INVENTORY],
             {"inventory": (replace(inventory_page.inventory[0],
                                    availability=planner.Availability.UNAVAILABLE),
                            *inventory_page.inventory[1:])}),
            ("convoy duplicate", page_index[planner.PageKind.RESOURCES],
             {"resources": (resource_page.resources[0],
                            resource_page.resources[1],
                            resource_page.resources[1],
                            *resource_page.resources[3:])}),
            ("convoy availability", page_index[planner.PageKind.RESOURCES],
             {"resources": (resource_page.resources[0],
                            replace(resource_page.resources[1],
                                    availability=available),
                            *resource_page.resources[2:])}),
            ("flag order", page_index[planner.PageKind.FLAGS],
             {"flags": swapped(flag_page.flags)}),
            ("flag duplicate", page_index[planner.PageKind.FLAGS],
             {"flags": (flag_page.flags[0], flag_page.flags[0],
                        *flag_page.flags[2:])}),
            ("page total", page_index[planner.PageKind.MAP],
             {"total_record_count": len(map_cells) - 1}),
            ("page record count", page_index[planner.PageKind.MAP],
             {"record_count": len(map_page.map_cells) - 1}),
            ("action ordinal", page_index[planner.PageKind.ACTIONS],
             {"actions": (replace(action_page.actions[0], ordinal=1),
                          *action_page.actions[1:])}),
            ("candidate duplicate", page_index[planner.PageKind.ACTIONS],
             {"actions": (action_page.actions[0],
                          replace(action_page.actions[1],
                                  action=action_page.actions[0].action),
                          *action_page.actions[2:])}),
        )
        for name, index, changes in mutations:
            with self.subTest(mutation=name):
                _assert_page_mutation_rejected(
                    self, pages, exported, index, changes
                )
    def test_action_page_decodes_actor_and_target_slots(self):
        words = [0] * 249
        words[:15] = [
            0x41504C4E, planner.PROTOCOL_VERSION, 249 * 4,
            1, 2, 2, 3, 4, 4, 0, 1, 1, 1, 0, 7,
        ]
        words[25:35] = [
            3, 1, 2 | (3 << 16), 2,
            1 | (3 << 8), 0x12345678, 0x9ABCDEF0,
            0x0BADCAFE, 0x10203040, 5,
        ]
        observation = planner.parse_transport_observation(words)
        action = observation.actions[0].action
        self.assertEqual(action.item_slot, 1)
        self.assertEqual(action.target_item_slot, 3)
        self.assertEqual(action.target_position, (0, 0))
        self.assertEqual(
            observation.actions[0].token.words,
            (0x12345678, 0x9ABCDEF0, 0x0BADCAFE, 0x10203040),
        )
        words[25] = 1
        words[28] = 0
        words[29] = 0xFF | (0xFF << 8)
        words[34] = 0
        observation = planner.parse_transport_observation(words)
        self.assertIsNone(observation.actions[0].action.item_slot)
        self.assertIsNone(observation.actions[0].action.target_item_slot)
        words[25] = 3
        words[28] = 2
        words[29] = 0 | (0xFF << 8)
        words[34] = 5
        observation = planner.parse_transport_observation(words)
        self.assertEqual(observation.actions[0].action.item_slot, 0)
        with self.assertRaisesRegex(
            planner.PlannerError,
            "invalid optional item-slot sentinel",
        ):
            words[29] = planner.UNIT_ITEM_COUNT | (0xFF << 8)
            planner.parse_transport_observation(words)
        boundary = words.copy()
        boundary[25:35] = [
            2, 0xB2, 63 | (63 << 16), 63 << 8 | (63 << 16),
            4 | (0xFF << 8), *([0xFFFFFFFF] * 4), 1,
        ]
        valid = planner.parse_transport_observation(boundary)
        self.assertEqual(valid.actions[0].action.destination, (63, 63))
        for implementation in (
            planner.ScriptedPlanner(),
            planner.BoundedSearchPlanner(),
        ):
            self.assertEqual(implementation.choose(valid).ordinal, 0)
        transcript = planner.PlannerTranscript()
        transcript.record_session(TRANSCRIPT_SESSION)
        transcript.record_complete_and_settled(
            valid,
            (0,) * 13,
            (0,) * 16,
        )
        encoded = transcript.export()
        self.assertEqual(
            planner.PlannerTranscript.import_bytes(encoded).export(),
            encoded,
        )
        mutations = (
            ("unknown action", {34: 2}),
            ("kind mismatch", {25: 1}),
            ("destination 64", {27: 64}),
            ("destination 255", {27: 255}),
            ("target x 64", {28: 64 << 8}),
            ("target y 255", {28: 255 << 16}),
            ("actor overflow", {26: 0x100}),
            ("actor sentinel", {26: 0xFF}),
            ("target sentinel", {28: 0xFF}),
            ("item slot", {29: 5 | (0xFF << 8)}),
            ("target slot", {29: 4 | (5 << 8)}),
            ("slot coordinates", {28: 2 | (63 << 8), 29: 4 | (3 << 8)}),
            ("item reserved", {29: 4 | (0xFF << 8) | (1 << 16)}),
            ("target reserved", {28: 1 << 24}),
            ("kind reserved", {25: 0x101}),
            ("combined", {26: 0xFF, 27: 255, 29: 1 << 16, 34: 99}),
            *(
                (f"token {index}", {30 + index: -1})
                for index in range(4)
            ),
        )
        for name, changes in mutations:
            for implementation in (
                planner.ScriptedPlanner(),
                planner.BoundedSearchPlanner(),
            ):
                with self.subTest(mutation=name, planner=type(implementation).__name__):
                    malformed = boundary.copy()
                    for word, value in changes.items():
                        malformed[word] = value
                    transport = mock.Mock()
                    transport.transcript = planner.PlannerTranscript()
                    transcript_before = transport.transcript.export()
                    with mock.patch.object(
                        implementation,
                        "choose",
                        wraps=implementation.choose,
                    ) as choose:
                        with self.assertRaises(planner.PlannerError):
                            decoded = planner.parse_transport_observation(malformed)
                            selected = implementation.choose(decoded)
                            transport.exchange(selected)
                    choose.assert_not_called()
                    transport.exchange.assert_not_called()
                    self.assertEqual(transport.transcript.export(), transcript_before)
        for page_count, page_index in (
            (0, 0), (planner.MAX_PAGE_COUNT + 1, 0),
            (1_000_000_000, 0), (0xFFFFFFFF, 0), (1, 1),
        ):
            malformed = boundary.copy()
            malformed[6:8] = [page_index, page_count]
            with self.assertRaisesRegex(
                planner.PlannerError,
                "page identity is outside v2 bounds",
            ):
                planner.parse_transport_observation(malformed)
        for invalid_word in (-1, 0x100000000):
            malformed = boundary.copy()
            malformed[7] = invalid_word
            with self.assertRaisesRegex(
                planner.PlannerError, "outside fixed u32 range"
            ):
                planner.parse_transport_observation(malformed)
    def test_host_begin_and_commit_limits_are_atomic(self):
        boundary, boundary_observation, boundary_command = (
            _single_action_bridge()
        )
        boundary._committed_count = planner.MAX_TRACE_ACTIONS - 1
        boundary.commit(boundary_command)
        self.assertEqual(boundary._committed_count, planner.MAX_TRACE_ACTIONS)
        bridge, observation, command = _single_action_bridge()
        with self.assertRaisesRegex(
            planner.PlannerError,
            planner.Rejection.PROTOCOL_ERROR.value,
        ):
            bridge.begin(PROVENANCE)
        bridge._committed_count = planner.MAX_TRACE_ACTIONS
        trace_before = bridge.transcript.export()
        with self.assertRaisesRegex(
            planner.PlannerError,
            planner.Rejection.RESOURCE_LIMIT.value,
        ):
            bridge.commit(command)
        self.assertEqual(bridge.transcript.export(), trace_before)
        self.assertIs(bridge._observation, observation)
        with self.assertRaisesRegex(
            planner.PlannerError,
            planner.Rejection.CANCELLED.value,
        ):
            bridge.commit(
                planner.Command(
                    planner.CommandKind.CANCEL,
                    observation.run_id,
                    observation.observation_id,
                )
            )
        bridge.begin(PROVENANCE)
        oversized, observation, command = _single_action_bridge()
        oversized.transcript = planner.PlannerTranscript(
            max_bytes=planner.MAX_TRANSCRIPT_EXCHANGE_BYTES
        )
        trace_before = oversized.transcript.export()
        with self.assertRaisesRegex(
            planner.PlannerError,
            planner.Rejection.RESOURCE_LIMIT.value,
        ):
            oversized.commit(command)
        self.assertEqual(oversized.transcript.export(), trace_before)
        self.assertIs(oversized._observation, observation)
        atomic = planner.PlannerBridge(PROVENANCE)
        atomic.begin(PROVENANCE)
        atomic.transcript = planner.PlannerTranscript(max_bytes=256)
        trace_before = atomic.transcript.export()
        next_id_before = atomic._next_observation_id
        with self.assertRaisesRegex(
            planner.PlannerError,
            planner.Rejection.RESOURCE_LIMIT.value,
        ):
            atomic.observe(
                1,
                (),
                (planner.Action("MOVE_WAIT", 1, (1, 1)),),
            )
        self.assertEqual(atomic.transcript.export(), trace_before)
        self.assertEqual(atomic._next_observation_id, next_id_before)
        self.assertIsNone(atomic._observation)
    def test_security_boundary_has_no_raw_memory_save_or_network_surface(self):
        root = TESTS_DIR.parents[2]
        target = (root / "src" / "expansion_autoplay_planner.c").read_text(encoding="utf-8")
        host = (PLAYTEST_DIR / "autoplay_planner.py").read_text(encoding="utf-8")
        transport = (PLAYTEST_DIR / "planner_transport_backend.c").read_text(
            encoding="utf-8"
        )
        self.assertEqual(
            set(re.findall(r"gActionData\.([A-Za-z0-9_]+)\s*=", target)),
            {"xOther", "yOther", "itemSlotIndex", "trapType"},
        )
        self.assertNotIn("busWrite", target)
        self.assertNotIn("socket", host)
        self.assertNotIn("subprocess", host)
        self.assertNotIn("savestate", host)
        self.assertNotIn("ADDRESS", transport)
        self.assertNotIn("POKE", transport)
        self.assertNotIn('"STEP"', transport)
        self.assertNotIn('"RUN"', transport)
        self.assertNotIn("setKeys", transport)
        self.assertIn("PLANNER_COMMAND_ADDR", transport)
    def test_expansion_config_preserves_positional_api(self):
        from scripts.modernize import expansion_config
        root = TESTS_DIR.parents[2]
        names = (
            "config_mk_path", "config_preset", "abi", "rom_size",
            "text_shift", "build_id_override", "repo_root",
            "version_major", "version_minor", "version_patch",
            "rom_title", "rom_game_code", "rom_maker_code",
            "rom_revision", "save_compat_epoch", "enabled_locales",
            "default_locale", "pseudo_locale", "mechanics_hooks",
            "mechanics_sample", "danger_overlay_menu",
            "blue_phase_delegate", "starter_content", "aoe_reference",
            "custom_spell_effects", "asset_manifest",
            "localized_text_auto_wrap", "casual_mode", "hq_mixer",
            "autoplay_strategies", "bgm_continuation_policy",
            "item_id_cap",
        )
        old_arguments = (
            root / "config.mk", "debug", "aapcs", "16M", 0,
            "abcdef12", root, 1, 2, 3, "POSITIONAL", "TST1", "01",
            1, 2, "en", "en", "0", "0", "0", "0", "0", "0", "0",
            "0", None, "0", "0", "0", "0", "restart", "0xCE",
        )
        keywords = dict(zip(names, old_arguments))
        old_positional = expansion_config.load_identity(*old_arguments)
        old_keyword = expansion_config.load_identity(**keywords)
        self.assertEqual(old_positional.to_dict(), old_keyword.to_dict())
        self.assertEqual(old_positional.bgm_continuation_policy, "restart")
        self.assertEqual(old_positional.item_id_cap, 0xCE)
        self.assertEqual(old_positional.autoplay_planner, 0)
        new_positional = expansion_config.load_identity(
            *old_arguments,
            "1",
        )
        new_keyword = expansion_config.load_identity(
            **keywords,
            autoplay_planner="1",
        )
        self.assertEqual(new_positional.to_dict(), new_keyword.to_dict())
        self.assertEqual(new_positional.autoplay_planner, 1)
        identity_fields = tuple(
            expansion_config.ExpansionIdentity.__dataclass_fields__
        )
        self.assertEqual(
            identity_fields[-4:],
            (
                "bgm_continuation_policy", "item_id_cap",
                "config_fingerprint", "autoplay_planner",
            ),
        )
        legacy_values = [
            getattr(old_keyword, name)
            for name in identity_fields[:-1]
        ]
        legacy_values[-1] = "legacy-fingerprint"
        legacy = expansion_config.ExpansionIdentity(*legacy_values)
        appended = expansion_config.ExpansionIdentity(*legacy_values, 1)
        self.assertEqual(legacy.config_fingerprint, "legacy-fingerprint")
        self.assertEqual((legacy.autoplay_planner, appended.autoplay_planner), (0, 1))
    def test_configured_bare_make_selects_release_and_fails_closed(self):
        root = TESTS_DIR.parents[2]
        build_root = root / "build" / "test-artifacts" / "planner-configure"
        build_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as temporary:
            real_make = shutil.which("make")
            self.assertIsNotNone(real_make, "GNU Make is required")
            forbidden_compiler = Path(temporary) / "arm-none-eabi-gcc"
            forbidden_compiler_record = (
                Path(temporary) / "arm-compiler-was-invoked"
            )
            forbidden_compiler.write_text(
                """#!/bin/sh
: > "$PLANNER_FORBIDDEN_COMPILER_RECORD"
exit 97
""",
                encoding="utf-8",
            )
            forbidden_compiler.chmod(0o755)
            environment = os.environ.copy()
            environment.pop("MAKEFLAGS", None)
            environment["PATH"] = (
                f"{temporary}{os.pathsep}{environment['PATH']}"
            )
            environment["PLANNER_FORBIDDEN_COMPILER_RECORD"] = str(
                forbidden_compiler_record
            )
            configured = subprocess.run(
                [str(root / "configure"), "--enable-autoplay-planner"],
                cwd=temporary,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(
                configured.returncode,
                0,
                configured.stdout + configured.stderr,
            )
            fragment = (Path(temporary) / "config.autotools.mk").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("MODERN_CONFIG :=", fragment)
            self.assertIn("EXPANSION_AUTOPLAY_PLANNER := 1", fragment)
            recorder = Path(temporary) / "recursive-make-recorder"
            record_path = Path(temporary) / "recursive-make.json"
            recorder.write_text(
                """#!/usr/bin/env python3
import json
import os
import subprocess
import sys

arguments = sys.argv[1:]
goal = arguments[-1]
child = subprocess.run(
    [os.environ["PLANNER_REAL_MAKE"], *arguments],
    capture_output=True,
    text=True,
)
with open(os.environ["PLANNER_MAKE_RECORD"], "w", encoding="utf-8") as output:
    json.dump(
        {
            "arguments": arguments,
            "goal": goal,
            "child_returncode": child.returncode,
            "child_stdout": child.stdout,
            "child_stderr": child.stderr,
        },
        output,
        sort_keys=True,
    )
sys.stdout.write(child.stdout)
sys.stderr.write(child.stderr)
raise SystemExit(child.returncode)
""",
                encoding="utf-8",
            )
            recorder.chmod(0o755)
            environment["PLANNER_REAL_MAKE"] = real_make
            environment["PLANNER_MAKE_RECORD"] = str(record_path)
            bare_make = subprocess.run(
                [
                    real_make,
                    "--no-print-directory",
                    f"MAKE={recorder}",
                ],
                cwd=temporary,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertNotEqual(bare_make.returncode, 0)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(record["goal"], "all")
            self.assertNotEqual(record["child_returncode"], 0)
            self.assertIn(
                "modern-debug-only",
                record["child_stdout"] + record["child_stderr"],
            )
            self.assertIn(
                "make expansion-modern-boot-check MODERN_CONFIG=debug",
                record["child_stdout"] + record["child_stderr"],
            )
            release = subprocess.run(
                [
                    real_make,
                    "--no-print-directory",
                    "expansion-modern-rom",
                    "MODERN_CONFIG=release",
                ],
                cwd=temporary,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertNotEqual(release.returncode, 0)
            self.assertIn("modern-debug-only", release.stdout + release.stderr)
            self.assertFalse(
                forbidden_compiler_record.exists(),
                "host-only configure coverage invoked the ARM compiler",
            )
    def test_configured_explicit_debug_goal_builds_in_toolchain_lane(self):
        if host_mode.host_only_enabled():
            self.skipTest("configured ROM build belongs to the toolchain lane")
        if shutil.which("arm-none-eabi-gcc") is None:
            self.skipTest("ARM compiler unavailable")
        root = TESTS_DIR.parents[2]
        build_root = (
            root / "build" / "test-artifacts" / "planner-configure-toolchain"
        )
        build_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as temporary:
            environment = os.environ.copy()
            environment.pop("MAKEFLAGS", None)
            configured = subprocess.run(
                [str(root / "configure"), "--enable-autoplay-planner"],
                cwd=temporary,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(
                configured.returncode,
                0,
                configured.stdout + configured.stderr,
            )
            fragment = (Path(temporary) / "config.autotools.mk").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("MODERN_CONFIG :=", fragment)
            self.assertIn("EXPANSION_AUTOPLAY_PLANNER := 1", fragment)
            built = subprocess.run(
                [
                    "make",
                    "--no-print-directory",
                    "expansion-modern-boot-check",
                    "MODERN_CONFIG=debug",
                ],
                cwd=temporary,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
    def test_host_only_ready_gate_capability_skips(self):
        root = TESTS_DIR.parents[2]
        build_root = root / "build"
        build_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as empty_path:
            environment = os.environ.copy()
            environment["GBA_PLAYTEST_HOST_ONLY"] = "1"
            environment["PATH"] = empty_path
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "unittest",
                    (
                        "tools.gba-playtest.tests.test_autoplay_planner."
                        "PlannerLibmGBAIntegrationTests."
                        "test_backend_requires_exact_ready_before_stdin"
                    ),
                    "-v",
                ],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stdout + completed.stderr,
            )
            self.assertIn(
                "production READY test belongs to the toolchain lane",
                completed.stdout + completed.stderr,
            )
            self.assertIn(
                "skipped=1",
                completed.stdout + completed.stderr,
            )
    def test_public_protocol_layout_is_fixed_width_and_offset_stable(self):
        output = _run_host_c_driver(
            self,
            "planner-layout-driver",
            ("tools/gba-playtest/tests/c/expansion_autoplay_planner_layout_driver.c",),
        )
        layout = dict(
            (key, int(value))
            for key, value in (
                line.split("=", 1)
                for line in output.splitlines()
            )
        )
        self.assertEqual(
            layout,
            {
                "semantic_size": 8,
                "action_size": 40,
                "unit_size": 16,
                "value_size": 8,
                "start_union_size": 4,
                "count_union_size": 4,
                "payload_union_size": 896,
                "observation_size": 996,
                "observation_start_offset": 36,
                "observation_count_offset": 40,
                "observation_payload_offset": 100,
                "command_size": 64,
                "command_payload_offset": 32,
                "command_result_offset": 56,
                "checkpoint_size": 52,
                "checkpoint_mode_offset": 20,
            },
        )
    def test_transport_acknowledgement_enum_is_exact(self):
        root = TESTS_DIR.parents[2]
        build_root = root / "build"
        build_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as temporary:
            executable = Path(temporary) / "planner-transport-ack"
            try:
                build_planner_transport_ack_driver(executable)
            except RuntimeError as error:
                if (
                    "planner transport host compiler unavailable"
                        in str(error)
                    or "mgba" in str(error).lower()
                ):
                    self.skipTest(str(error))
                raise
            completed = subprocess.run(
                [str(executable)],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stdout + completed.stderr,
            )
            self.assertIn(
                "PLANNER_TRANSPORT_SECURITY_TEST: PASS",
                completed.stdout,
            )
    def test_c_mailbox_adapter_accepts_only_typed_token_commit(self):
        output = _run_host_c_driver(
            self, "planner-driver", PLANNER_DRIVER_SOURCES,
            defines=PLANNER_DRIVER_DEFINES,
        )
        self.assertIn("AUTOPLAY_PLANNER_HOST_TEST: PASS", output)
        identities, configs = [], []
        def host_scenario(value, name, sources=PLANNER_DRIVER_SOURCES):
            return _run_host_c_driver(
                self, name, sources, defines=(
                    *PLANNER_DRIVER_DEFINES[:3],
                    "-DFE8_AUTOPLAY_PLANNER_RUNTIME_TEST=1",
                    f"-DFE8_EXPANSION_AUTOPLAY_PLANNER_SCENARIO_ID={value}"))
        for value in ("0", "0xFFFFFFFF"):
            scenario_output = host_scenario(value, f"planner-scenario-{value}")
            identities.append(
                re.search(r"SCENARIO_IDENTITY=([0-9a-f]+)", scenario_output).group(1))
            configs.append(
                re.search(r"CONFIG_IDENTITY=([0-9a-f]+)", scenario_output).group(1))
        self.assertNotEqual(*identities)
        self.assertEqual(configs[0], configs[1])
        for value in INVALID_SCENARIO_IDS:
            with self.subTest(host_scenario_id=value):
                with self.assertRaisesRegex(
                    AssertionError, "ScenarioId|not_a_constant"
                ):
                    host_scenario(value, "planner-scenario-reject", (
                        "tools/gba-playtest/tests/c/"
                        "expansion_autoplay_planner_layout_driver.c",))
    def test_flag_checkpoint_bounds_under_sanitizers(self):
        output = _run_host_c_driver(
            self, "planner-flag-sanitizer", PLANNER_DRIVER_SOURCES,
            defines=PLANNER_DRIVER_DEFINES,
            extra_flags=(
                "-O1", "-g", "-fsanitize=address,undefined",
                "-fno-omit-frame-pointer",
            ),
            compilers=("gcc", "clang"),
            environment={"ASAN_OPTIONS": "detect_leaks=0"},
        )
        self.assertIn("AUTOPLAY_PLANNER_HOST_TEST: PASS", output)
    def test_native_summon_executor_preserves_action_and_coordinates(self):
        output = _run_host_c_driver(
            self,
            "summon-executor-driver",
            (
                "src/cp_decide.c",
                "src/cp_perform.c",
                "src/bmbattle.c",
                "src/mapanim_summon.c",
                "tools/gba-playtest/tests/c/summon_executor_driver.c",
            ),
            defines=(
                "-DFE8_EXPANSION_MODERN_BUILD=1",
                "-DFE8_EXPANSION_DEBUG=1",
                "-DFE8_EXPANSION_AUTOPLAY_PLANNER=1",
                "-DFE8_AUTOPLAY_PLANNER_RUNTIME_TEST=1",
                "-DFE8_PLANNER_STATIONARY_WAIT_TEST=1",
            ),
        )
        self.assertIn("PLANNER_EXECUTOR_HOST_TEST: PASS", output)
    def test_arm_adapter_compiles_at_the_existing_computer_decision_boundary(self):
        compiler = shutil.which("arm-none-eabi-gcc")
        nm = shutil.which("arm-none-eabi-nm")
        size = shutil.which("arm-none-eabi-size")
        if compiler is None or nm is None or size is None:
            self.skipTest("ARM compiler/binutils unavailable")
        root = TESTS_DIR.parents[2]
        build_root = root / "build"
        build_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as temporary:
            temporary_path = Path(temporary)
            scenario_source = (
                root / "tools/gba-playtest/tests/c/"
                "expansion_autoplay_planner_layout_driver.c"
            )
            def compile_scenario(value, name, *extra):
                return _compile_arm_object(
                    self, compiler, scenario_source, temporary_path / name,
                    planner_enabled=True, extra_defines=(
                        *extra,
                        f"-DFE8_EXPANSION_AUTOPLAY_PLANNER_SCENARIO_ID={value}",
                    ))
            for value in ("0", "0xFFFFFFFF"):
                compile_scenario(value, f"scenario-{value}.o")
            compile_scenario(
                "-1", "scenario-archival-inactive.o", "-DFE8_ARCHIVAL_BUILD=1")
            for value in INVALID_SCENARIO_IDS:
                with self.subTest(arm_scenario_id=value):
                    with self.assertRaisesRegex(
                        AssertionError, "ScenarioId|not_a_constant"
                    ):
                        compile_scenario(value, "scenario-reject.o")
            objects = []
            for source in (
                root / "src" / "expansion_autoplay_planner.c",
                root / "src" / "action_semantics.c",
                root / "src" / "cp_decide.c",
            ):
                output = temporary_path / f"{source.stem}.o"
                objects.append(_compile_arm_object(
                    self,
                    compiler,
                    source,
                    output,
                    planner_enabled=True,
                ))
            symbols = subprocess.run(
                [nm, "-S", *map(str, objects)], cwd=root, capture_output=True, text=True
            )
            self.assertEqual(symbols.returncode, 0, symbols.stdout + symbols.stderr)
            self.assertRegex(
                symbols.stdout,
                r"\bU ExpansionAutoplayPlanner_OfferDecision\b",
            )
            observation = re.search(
                r"^[0-9a-fA-F]+\s+([0-9a-fA-F]+)\s+[bBdD]\s+"
                r"gExpansionAutoplayPlannerObservation$",
                symbols.stdout,
                re.MULTILINE,
            )
            self.assertIsNotNone(observation, "planner observation symbol missing")
            self.assertEqual(int(observation.group(1), 16), 996)
            self.assertLessEqual(int(observation.group(1), 16), planner.PAGE_MAX_BYTES)
            command = re.search(
                r"^[0-9a-fA-F]+\s+([0-9a-fA-F]+)\s+[bBdD]\s+"
                r"gExpansionAutoplayPlannerCommand$",
                symbols.stdout,
                re.MULTILINE,
            )
            checkpoint = re.search(
                r"^[0-9a-fA-F]+\s+([0-9a-fA-F]+)\s+[bBdD]\s+"
                r"gExpansionAutoplayPlannerCampaignCheckpoint$",
                symbols.stdout,
                re.MULTILINE,
            )
            self.assertIsNotNone(command, "planner command symbol missing")
            self.assertIsNotNone(checkpoint, "planner checkpoint symbol missing")
            self.assertEqual(int(command.group(1), 16), 64)
            self.assertEqual(int(checkpoint.group(1), 16), 52)
            self.assertNotIn("sPlannerCandidates", symbols.stdout)
            self.assertNotIn("sPlannerSelectedDecision", symbols.stdout)
            section_sizes = _arm_section_sizes(
                self, size, objects[0], objects[1]
            )
            self.assertLessEqual(
                section_sizes.get("ewram_data", 0)
                + section_sizes.get(".bss", 0),
                4096,
            )
            self.assertEqual(section_sizes.get("iwram_data", 0), 0)
            planner_code_size = _arm_code_size(section_sizes)
            self.assertLessEqual(
                planner_code_size,
                12 * 1024,
            )
            hook_code_sizes: dict[bool, int] = {}
            hook_objects: dict[bool, Path] = {}
            for enabled in (False, True):
                output = temporary_path / f"cp-perform-planner-{int(enabled)}.o"
                hook_objects[enabled] = _compile_arm_object(
                    self,
                    compiler,
                    root / "src" / "cp_perform.c",
                    output,
                    planner_enabled=enabled,
                )
                hook_code_sizes[enabled] = _arm_code_size(
                    _arm_section_sizes(self, size, output)
                )
            hook_code_delta = (
                hook_code_sizes[True] - hook_code_sizes[False]
            )
            self.assertGreaterEqual(hook_code_delta, 0)
            self.assertLessEqual(
                planner_code_size + hook_code_delta,
                12 * 1024,
            )
            transition_code_sizes = {False: 0, True: 0}
            for source_name in ("event", "eventscr"):
                for enabled in (False, True):
                    output = temporary_path / (
                        f"{source_name}-planner-{int(enabled)}.o"
                    )
                    _compile_arm_object(
                        self,
                        compiler,
                        root / "src" / f"{source_name}.c",
                        output,
                        planner_enabled=enabled,
                    )
                    transition_code_sizes[enabled] += _arm_code_size(
                        _arm_section_sizes(self, size, output)
                    )
            transition_code_delta = (
                transition_code_sizes[True]
                - transition_code_sizes[False]
            )
            self.assertGreaterEqual(transition_code_delta, 0)
            self.assertLessEqual(
                planner_code_size
                + hook_code_delta
                + transition_code_delta,
                12 * 1024,
            )
            disabled_hook_symbols = subprocess.run(
                [nm, str(hook_objects[False])],
                cwd=root,
                capture_output=True,
                text=True,
            )
            enabled_hook_symbols = subprocess.run(
                [nm, str(hook_objects[True])],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                disabled_hook_symbols.returncode,
                0,
                disabled_hook_symbols.stdout + disabled_hook_symbols.stderr,
            )
            self.assertEqual(
                enabled_hook_symbols.returncode,
                0,
                enabled_hook_symbols.stdout + enabled_hook_symbols.stderr,
            )
            self.assertNotIn("AiSummonAction", disabled_hook_symbols.stdout)
            self.assertIn("AiSummonAction", enabled_hook_symbols.stdout)
            profile_sections: dict[bool, dict[str, int]] = {}
            for enabled in (False, True):
                profile_objects = []
                for source in (
                    root / "src" / "expansion_autoplay.c",
                    root / "src" / "rng.c",
                ):
                    output = temporary_path / (
                        f"{source.stem}-planner-{int(enabled)}.o"
                    )
                    profile_objects.append(_compile_arm_object(
                        self,
                        compiler,
                        source,
                        output,
                        planner_enabled=enabled,
                    ))
                profile_sections[enabled] = _arm_section_sizes(
                    self, size, *profile_objects
                )
            self.assertEqual(
                profile_sections[True].get("iwram_data", 0),
                profile_sections[False].get("iwram_data", 0),
            )
            self.assertLessEqual(
                profile_sections[True].get("ewram_data", 0)
                - profile_sections[False].get("ewram_data", 0),
                5,
            )
            disabled = temporary_path / "planner-release-disabled.o"
            _compile_arm_object(
                self,
                compiler,
                root / "src" / "expansion_autoplay_planner.c",
                disabled,
                planner_enabled=False,
                debug=False,
            )
            disabled_action_semantics = (
                temporary_path / "action-semantics-release-disabled.o"
            )
            _compile_arm_object(
                self,
                compiler,
                root / "src" / "action_semantics.c",
                disabled_action_semantics,
                planner_enabled=False,
                debug=False,
            )
            symbols = subprocess.run(
                [nm, str(disabled), str(disabled_action_semantics)],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(symbols.returncode, 0, symbols.stdout + symbols.stderr)
            self.assertNotIn("gExpansionAutoplayPlanner", symbols.stdout)
            self.assertNotIn("ActionSemantics_", symbols.stdout)
    def test_archival_target_predicates_keep_original_call_graph(self):
        compiler = shutil.which("arm-none-eabi-gcc")
        nm = shutil.which("arm-none-eabi-nm")
        objdump = shutil.which("arm-none-eabi-objdump")
        if compiler is None or nm is None or objdump is None:
            self.skipTest("ARM compiler/binutils unavailable")
        root = TESTS_DIR.parents[2]
        build_root = root / "build"
        build_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as temporary:
            output = Path(temporary) / "bmtarget-archival.o"
            completed = subprocess.run(
                [
                    compiler,
                    "-mcpu=arm7tdmi",
                    "-mthumb",
                    "-mthumb-interwork",
                    "-mabi=aapcs",
                    "-std=gnu89",
                    "-ffreestanding",
                    "-fno-builtin",
                    "-O2",
                    "-ffunction-sections",
                    "-I",
                    str(root / "include"),
                    "-I",
                    str(root / "include" / "generated"),
                    "-DFE8_ARCHIVAL_BUILD=1",
                    "-c",
                    str(root / "src" / "bmtarget.c"),
                    "-o",
                    str(output),
                ],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stdout + completed.stderr,
            )
            symbols = subprocess.run(
                [nm, str(output)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(symbols.returncode, 0, symbols.stderr)
            for predicate in (
                "IsSnagObstacleTarget",
                "IsSnagAttackTargetAt",
                "IsUnitInHealTargetList",
                "HasRangedHealTargetAt",
                "IsUnitInHammerneTargetList",
                "IsUnitInLatonaTargetList",
                "HasLatonaTarget",
                "IsUnitInStaffTargetListAt",
            ):
                self.assertNotIn(predicate, symbols.stdout)
            disassembly = subprocess.run(
                [objdump, "-dr", str(output)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                disassembly.returncode,
                0,
                disassembly.stderr,
            )
            heal = disassembly.stdout.split(
                "<TryAddUnitToHealTargetList>:",
                1,
            )[1].split("\n\n", 1)[0]
            hammerne = disassembly.stdout.split(
                "<TryAddUnitToHammerneTargetList>:",
                1,
            )[1].split("\n\n", 1)[0]
            latona = disassembly.stdout.split(
                "<MakeTargetListForLatona>:",
                1,
            )[1].split("\n\n", 1)[0]
            self.assertIn("AreUnitsAllied", heal)
            self.assertIn("GetUnitCurrentHp", heal)
            self.assertIn("IsSameAllegiance", hammerne)
            self.assertIn("IsItemHammernable", hammerne)
            self.assertIn("GetUnitCurrentHp", latona)
            self.assertNotIn("IsUnitIn", heal + hammerne + latona)

@dataclass(frozen=True)
class TransportAcknowledgement:
    command_id: int
    kind: int
    result: int
    rejection: int

@dataclass(frozen=True)
class TransportCompletion:
    command_id: int
    kind: int
    response_frames: int

class PlannerTransportError(RuntimeError):
    def __init__(self, code: str, command_id: int, kind: int) -> None:
        super().__init__(
            f"{code}: command_id={command_id:#x} kind={kind:#x}"
        )
        self.code = code
        self.command_id = command_id
        self.kind = kind

class PlannerProcessTransport:
    def __init__(self, backend: Path, rom: Path) -> None:
        self.process = subprocess.Popen(
            [str(backend), str(rom)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.checkpoint: tuple[int, ...] = ()
        self.command: tuple[int, ...] = ()
        self.last_acknowledgement: TransportAcknowledgement | None = None
        self.last_completion: TransportCompletion | None = None
        self._next_acknowledgement_id = 1
        self.transcript = planner.PlannerTranscript()
        self._transcript_started = False
        self.observation = self._read_state()
        self._begin_transcript(self.observation)
    def _begin_transcript(
        self,
        observation: planner.Observation,
        *,
        allow_uninitialized: bool = False,
    ) -> None:
        if self._transcript_started:
            return
        if not allow_uninitialized and observation.state != 1:
            return
        provenance = (
            {
                "transport": "restricted-libmgba",
                "rom_identity": observation.actual_rom_identity,
                "config_identity": observation.actual_config_identity,
                "scenario_identity": observation.actual_scenario_identity,
                "seed_identity": observation.actual_seed_identity,
                "ready_run_id": observation.run_id,
                "run_id": observation.run_id + 1,
            }
        )
        self.transcript.record_session_observation(
            provenance,
            observation,
            self.checkpoint,
            self.command,
        )
        self._transcript_started = True
    def _read_protocol_line(self) -> tuple[list[str], str]:
        assert self.process.stdout is not None
        line = self.process.stdout.readline()
        if not line:
            stderr = self.process.stderr.read() if self.process.stderr is not None else ""
            raise AssertionError(f"planner transport terminated: {stderr}")
        fields = line.split()
        if fields and fields[0] == "TRANSPORT_ERROR":
            if len(fields) != 4:
                raise AssertionError(f"malformed transport error: {line}")
            self.transcript.record_transport_error(
                fields[1],
                int(fields[2], 16),
                int(fields[3], 16),
            )
            raise PlannerTransportError(
                fields[1],
                int(fields[2], 16),
                int(fields[3], 16),
            )
        return fields, line
    def _read_acknowledgement(self) -> TransportAcknowledgement:
        fields, line = self._read_protocol_line()
        if not fields or fields[0] != "ACK" or len(fields) != 5:
            raise AssertionError(f"unexpected planner acknowledgement: {line}")
        acknowledgement = TransportAcknowledgement(
            *(int(value, 16) for value in fields[1:])
        )
        if acknowledgement.command_id != self._next_acknowledgement_id:
            raise AssertionError(
                "planner acknowledgement generation mismatch: "
                f"expected {self._next_acknowledgement_id:#x}, "
                f"received {acknowledgement.command_id:#x}"
            )
        self._next_acknowledgement_id += 1
        self.last_acknowledgement = acknowledgement
        self.transcript.record_acknowledgement(
            acknowledgement.command_id,
            acknowledgement.kind,
            acknowledgement.result,
            acknowledgement.rejection,
        )
        return acknowledgement
    def _read_completion(
        self,
        acknowledgement: TransportAcknowledgement,
    ) -> TransportCompletion:
        fields, line = self._read_protocol_line()
        if not fields or fields[0] != "COMPLETE" or len(fields) != 4:
            raise AssertionError(f"unexpected planner completion: {line}")
        completion = TransportCompletion(
            *(int(value, 16) for value in fields[1:])
        )
        if (
            completion.command_id != acknowledgement.command_id
            or completion.kind != acknowledgement.kind
        ):
            raise AssertionError(
                "planner completion does not match acknowledgement"
            )
        self.last_completion = completion
        self.transcript.record_completion(
            completion.command_id,
            completion.kind,
            completion.response_frames,
        )
        return completion
    def _read_state(self) -> planner.Observation:
        fields, line = self._read_protocol_line()
        if (
            not fields
            or fields[0] != "OBS"
            or "CHECKPOINT" not in fields
            or "COMMAND" not in fields
        ):
            raise AssertionError(f"unexpected planner transport response: {line}")
        checkpoint_index = fields.index("CHECKPOINT")
        command_index = fields.index("COMMAND")
        observation = planner.parse_transport_observation(
            int(value, 16) for value in fields[1:checkpoint_index]
        )
        self.checkpoint = tuple(
            int(value, 16)
            for value in fields[checkpoint_index + 1 : command_index]
        )
        self.command = tuple(
            int(value, 16) for value in fields[command_index + 1 :]
        )
        self.observation = observation
        return observation
    def _send(
        self,
        line: str,
        *,
        expect_acknowledgement: bool = True,
        transcript_command: dict[str, object] | None = None,
    ) -> planner.Observation:
        assert self.process.stdin is not None
        self.last_acknowledgement = None
        self.last_completion = None
        if transcript_command is not None:
            self._begin_transcript(
                self.observation,
                allow_uninitialized=True,
            )
            self.transcript.reserve_exchange()
            self.transcript.record_command(transcript_command)
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()
        if expect_acknowledgement:
            acknowledgement = self._read_acknowledgement()
            self._read_completion(acknowledgement)
        observation = self._read_state()
        if self._transcript_started:
            self.transcript.record_observation_page(observation)
            self.transcript.record_settled(
                observation,
                self.checkpoint,
                self.command,
            )
        else:
            self._begin_transcript(observation)
        return observation
    def start(
        self,
        *,
        scenario_identity: int | None = None,
    ) -> planner.Observation:
        ready = self.observation
        expected_identities = (
            ready.actual_rom_identity,
            ready.actual_config_identity,
            (
                ready.actual_scenario_identity
                if scenario_identity is None
                else scenario_identity
            ),
            ready.actual_seed_identity,
        )
        return self._send(
            "START {:08x} {:08x} {:08x} {:08x}".format(
                *expected_identities,
            ),
            transcript_command={
                "kind": planner.CommandKind.START.value,
                "run_id": 0,
                "observation_id": 0,
                "expected_identities": expected_identities,
            },
        )
    def exchange(
        self, command: planner.Command
    ) -> planner.Observation:
        if command.kind is planner.CommandKind.PAGE:
            if command.page_index is None:
                raise AssertionError("PAGE requires page_index")
            return self._send(
                "PAGE {:08x} {:08x} {:08x}".format(
                    command.run_id,
                    command.observation_id,
                    command.page_index,
                ),
                transcript_command={
                    "kind": planner.CommandKind.PAGE.value,
                    "run_id": command.run_id,
                    "observation_id": command.observation_id,
                    "page_index": command.page_index,
                },
            )
        if command.kind is planner.CommandKind.COMMIT:
            if command.action_ordinal is None or command.token is None:
                raise AssertionError("COMMIT requires ordinal and opaque token")
            return self._send(
                "COMMIT {:08x} {:08x} {:08x} "
                "{:08x} {:08x} {:08x} {:08x}".format(
                    command.run_id,
                    command.observation_id,
                    command.action_ordinal,
                    *command.token.words,
                ),
                transcript_command={
                    "kind": planner.CommandKind.COMMIT.value,
                    "run_id": command.run_id,
                    "observation_id": command.observation_id,
                    "action_ordinal": command.action_ordinal,
                    "token": asdict(command.token),
                },
            )
        if command.kind is planner.CommandKind.CANCEL:
            return self._send(
                "CANCEL {:08x} {:08x}".format(
                    command.run_id,
                    command.observation_id,
                ),
                transcript_command={
                    "kind": planner.CommandKind.CANCEL.value,
                    "run_id": command.run_id,
                    "observation_id": command.observation_id,
                },
            )
        raise AssertionError(f"unsupported transport command {command.kind}")
    def record_complete_observation(
        self,
        observation: planner.Observation,
    ) -> None:
        self.transcript.record_complete_and_settled(
            observation,
            self.checkpoint,
            self.command,
        )
    def close(self) -> None:
        if self.process.poll() is None and self.process.stdin is not None:
            try:
                self.process.stdin.write("QUIT\n")
                self.process.stdin.flush()
            except BrokenPipeError:
                pass
        self.process.communicate(timeout=10)

@contextmanager
def _open_transport(backend, rom):
    transport = PlannerProcessTransport(backend, rom)
    try:
        yield transport
    finally:
        transport.close()

class PlannerLibmGBAIntegrationTests(unittest.TestCase):
    def _build_transport(
        self,
        temporary: str,
        *,
        commit_delay_frames: int = 0,
        stall_after_commit: bool = False,
        ignore_commands: bool = False,
        acknowledgement_frame_limit: int = 120,
        commit_completion_frame_limit: int = 18000,
        transition_subcode: int = 2,
        candidate_mode: int = 0,
        flag_domain_mode: int = 0,
        acknowledgement_override: tuple[int, int] | None = None,
        zero_digest: bool = False,
        startup_delay_frames: int = 0,
        startup_state_override: int = 0,
        test_bootstrap: bool = False,
    ) -> tuple[Path, Path]:
        rom = Path(temporary) / "planner-two-chapter.gba"
        elf = Path(temporary) / "planner-two-chapter.elf"
        backend = Path(temporary) / "planner-transport"
        build_production_planner_rom(
            rom,
            elf,
            commit_delay_frames=commit_delay_frames,
            stall_after_commit=stall_after_commit,
            ignore_commands=ignore_commands,
            transition_subcode=transition_subcode,
            candidate_mode=candidate_mode,
            flag_domain_mode=flag_domain_mode,
            acknowledgement_override=acknowledgement_override,
            zero_digest=zero_digest,
            startup_delay_frames=startup_delay_frames,
            startup_state_override=startup_state_override,
        )
        build_planner_transport_backend(
            backend,
            elf,
            acknowledgement_frame_limit=acknowledgement_frame_limit,
            commit_completion_frame_limit=commit_completion_frame_limit,
            test_bootstrap=test_bootstrap,
        )
        return rom, backend
    def _build_or_skip(self, temporary, **kwargs):
        try:
            return self._build_transport(temporary, **kwargs)
        except RuntimeError as error:
            if (
                "planner runtime toolchain unavailable" in str(error)
                or "planner transport host compiler unavailable" in str(error)
            ):
                self.skipTest(str(error))
            raise

    @contextmanager
    def _fixture(self, **kwargs):
        root = (
            TESTS_DIR.parents[2]
            / "build"
            / "test-artifacts"
            / "autoplay-planner"
        )
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            rom, backend = self._build_or_skip(temporary, **kwargs)
            yield rom, backend, Path(temporary)
    def _run_planner(
        self,
        backend: Path,
        rom: Path,
        implementation: planner.ScriptedPlanner | planner.BoundedSearchPlanner,
    ) -> tuple[planner.Observation, tuple[int, ...], bytes]:
        with _open_transport(backend, rom) as transport:
            waiting = transport.start()
            self.assertEqual(waiting.state, 2)
            first = planner.collect_observation_pages(transport, waiting)
            self.assertEqual(len(first.fields), planner.SEMANTIC_FIELD_COUNT)
            self.assertEqual(len(first.map_cells), 8 * 8)
            self.assertEqual(len(first.units), 1)
            self.assertEqual(len(first.inventory), planner.UNIT_ITEM_COUNT)
            self.assertEqual(
                len(first.resources),
                1
                + planner.CONVOY_ITEM_COUNT
                + planner.AUTOPLAY_TELEMETRY_WORDS,
            )
            self.assertEqual(len(first.flags), 128)
            self.assertEqual(len(first.actions), 64)
            self.assertTrue(
                all(
                    record.action.item_slot is None
                    for record in first.actions
                )
            )
            self.assertEqual(first.resources[0].kind, planner.ValueKind.GOLD)
            self.assertEqual(first.resources[0].value, 1000)
            self.assertEqual(
                first.resources[1].kind,
                planner.ValueKind.CONVOY_ITEM,
            )
            self.assertEqual(first.resources[1].item_id, 1)
            self.assertEqual(first.flags[0].state, 0)
            fields = {field.name: field for field in first.fields}
            self.assertEqual(
                fields["map_dimensions"].value,
                8 | (8 << 16),
            )
            self.assertEqual(
                fields["active_unit"].availability,
                planner.Availability.AVAILABLE,
            )
            self.assertEqual(
                fields["objective_id"].availability,
                planner.Availability.UNSUPPORTED_RULE,
            )
            hidden = next(
                cell for cell in first.map_cells
                if (cell.x, cell.y) == (1, 0)
            )
            self.assertEqual(hidden.availability, planner.Availability.NOT_VISIBLE)
            self.assertEqual(hidden.unit, 0)
            choice = implementation.choose(first)
            self.assertEqual(
                implementation.last_semantic_digest,
                planner._digest(planner._observation_semantics(first)),
            )
            waiting = transport.exchange(
                planner.Command(
                    planner.CommandKind.COMMIT,
                    first.run_id,
                    first.observation_id,
                    choice.ordinal,
                    choice.token,
                )
            )
            self.assertEqual(waiting.state, 2)
            self.assertEqual(waiting.chapter, 2)
            self.assertEqual(len(transport.checkpoint), 13)
            transition_checkpoint = transport.checkpoint
            self.assertEqual(
                transition_checkpoint[:12],
                (
                    0x41504C4E,
                    planner.PROTOCOL_VERSION,
                    52,
                    first.run_id,
                    first.chapter,
                    0,
                    first.chapter_turn,
                    *first.rng_state,
                    first.rng_lcg,
                    first.rng_consumption,
                ),
            )
            self.assertNotEqual(transition_checkpoint[12], 0)
            self.assertEqual(waiting.run_id, first.run_id)
            second = planner.collect_observation_pages(transport, waiting)
            choice = implementation.choose(second)
            committed = transport.exchange(
                planner.Command(
                    planner.CommandKind.COMMIT,
                    second.run_id,
                    second.observation_id,
                    choice.ordinal,
                    choice.token,
                )
            )
            self.assertEqual(committed.state, 2)
            self.assertEqual(transport.checkpoint, transition_checkpoint)
            settled = planner.collect_observation_pages(transport, committed)
            cancelled = transport.exchange(
                planner.Command(
                    planner.CommandKind.CANCEL,
                    settled.run_id,
                    settled.observation_id,
                )
            )
            self.assertEqual(cancelled.state, 4)
            self.assertTrue(all(value == 0 for value in transport.checkpoint))
            return (
                settled,
                transition_checkpoint,
                transport.transcript.export(),
            )
    def test_host_driven_production_mailbox_replays_two_chapters(self):
        with self._fixture() as (rom, backend, _):
            symbols = subprocess.run(
                ["arm-none-eabi-nm", str(rom.with_suffix(".elf"))],
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                symbols.returncode,
                0,
                symbols.stdout + symbols.stderr,
            )
            for symbol in (
                "Event2A_MoveToChapter",
                "EventEngine_OnEnd",
                "EndBMapMainForChapterTransition",
                "ExpansionAutoplayPlanner_RecordCampaignCheckpoint",
            ):
                self.assertRegex(symbols.stdout, rf"\b{symbol}(?:\n|$)")
            scripted = self._run_planner(
                backend, rom, planner.ScriptedPlanner()
            )
            searched = self._run_planner(
                backend, rom, planner.BoundedSearchPlanner(max_nodes=512)
            )
            self.assertEqual(scripted, searched)
            for recorded in (scripted[2], searched[2]):
                self.assertEqual(
                    planner.replay_transcript_on_clean_transport(
                        recorded,
                        lambda: PlannerProcessTransport(backend, rom),
                    ),
                    recorded,
                )
            imported = planner.PlannerTranscript.import_bytes(scripted[2])
            self.assertEqual(imported.export(), scripted[2])
            identity_tampered = json.loads(scripted[2])
            start_command = next(
                event["command"]
                for event in identity_tampered["events"]
                if event["event"] == "command"
                    and event["command"]["kind"]
                        == planner.CommandKind.START.value
            )
            start_command["expected_identities"][2] ^= 1
            _rechain_transcript(identity_tampered)
            with self.assertRaisesRegex(
                planner.PlannerError,
                "START command session identity mismatch",
            ):
                planner.PlannerTranscript.import_bytes(
                    planner._canonical(identity_tampered)
                )
            complete_events = [
                event
                for event in imported.events
                if event["event"] == "observation_complete"
            ]
            self.assertGreaterEqual(len(complete_events), 3)
            self.assertTrue(complete_events[-1]["observation"]["inventory"])
            self.assertTrue(complete_events[-1]["observation"]["resources"])
            self.assertTrue(complete_events[-1]["observation"]["flags"])
            settled_events = [
                event
                for event in imported.events
                if event["event"] == "settled"
                and event["telemetry"]
            ]
            self.assertTrue(settled_events)
            self.assertEqual(len(settled_events[-1]["telemetry"]), 16)
    def test_clean_transport_replays_rejection_and_cancel(self):
        with self._fixture() as (rom, backend, _):
            with _open_transport(backend, rom) as transport:
                first = planner.collect_observation_pages(
                    transport, transport.start())
                choice = planner.ScriptedPlanner().choose(first)
                page_indices = {
                    planner.PageKind(observation["page_kind"]): observation["page_index"]
                    for event in reversed(transport.transcript.events)
                    if event["event"] == "observation_page"
                    for observation in (event["observation"],)
                    if observation["run_id"] == first.run_id
                        and observation["observation_id"] == first.observation_id
                }
                for page_kind in planner._PAGE_ORDER[1:]:
                    page_index = page_indices[page_kind]
                    transport.exchange(planner.Command(
                        planner.CommandKind.PAGE, first.run_id,
                        first.observation_id, page_index=page_index,
                    ))
                    stale_page = transport.exchange(planner.Command(
                        planner.CommandKind.PAGE, first.run_id,
                        first.observation_id + 1, page_index=page_index,
                    ))
                    self.assertEqual(stale_page.rejection, 2)
                forged = transport.exchange(
                    planner.Command(
                        planner.CommandKind.COMMIT,
                        first.run_id,
                        first.observation_id,
                        choice.ordinal,
                        planner.OpaqueToken(
                            choice.token.word0 ^ 1,
                            choice.token.word1,
                            choice.token.word2,
                            choice.token.word3,
                        ),
                    )
                )
                self.assertEqual(forged.rejection, 4)
                cancelled = transport.exchange(planner.Command(
                    planner.CommandKind.CANCEL, first.run_id, first.observation_id))
                self.assertEqual(cancelled.state, 4)
                recorded = transport.transcript.export()
            self.assertEqual(planner.replay_transcript_on_clean_transport(
                recorded, lambda: PlannerProcessTransport(backend, rom)), recorded)
            mutations = (
                ("chapter", planner.PageKind.ACTIONS, False, ("chapter",)),
                ("turn", planner.PageKind.ACTIONS, False, ("chapter_turn",)),
                ("rng", planner.PageKind.ACTIONS, False, ("rng_state", 0)),
                ("map", planner.PageKind.MAP, False, ("map_cells", 0, "terrain")),
                ("unit", planner.PageKind.UNITS, False, ("units", 0, "state")),
                ("resource", planner.PageKind.RESOURCES, False, ("resources", 0, "value")),
                ("telemetry", planner.PageKind.RESOURCES, False, ("resources", -1, "value")),
                ("flag", planner.PageKind.FLAGS, False, ("flags", 0, "state")),
                ("action", planner.PageKind.ACTIONS, False,
                 ("actions", 0, "action", "destination", 0)),
                ("token", planner.PageKind.ACTIONS, False,
                 ("actions", 0, "token", "word0")),
                ("page identity", planner.PageKind.ACTIONS, False, ("page_index",)),
                ("terminal state", planner.PageKind.ACTIONS, False, ("state",)),
                ("checkpoint", planner.PageKind.ACTIONS, True, ("checkpoint", 12)),
            )
            for name, page_kind, settled_target, path in mutations:
                with self.subTest(rejected_response=name):
                    document = json.loads(recorded)
                    observation, settled = _rejected_response(document, page_kind)
                    if settled_target:
                        settled["checkpoint"] = [
                            0x41504C4E, planner.PROTOCOL_VERSION, 52,
                            observation["run_id"], observation["chapter"], 0,
                            observation["chapter_turn"], *observation["rng_state"],
                            observation["rng_lcg"], observation["rng_consumption"], 1,
                        ]
                    else:
                        _xor_nested(observation, path)
                        _sync_settled_observation(settled, observation)
                    _rechain_transcript(document)
                    _assert_replay_rejected(
                        self, planner._canonical(document), "rejected response"
                    )
    def test_world_map_transition_records_settled_checkpoint(self):
        with self._fixture(transition_subcode=1) as (rom, backend, _):
            _, checkpoint, transcript = self._run_planner(
                backend,
                rom,
                planner.ScriptedPlanner(),
            )
            self.assertEqual(checkpoint[4], 1)
            self.assertNotEqual(checkpoint[12], 0)
            self.assertEqual(
                planner.PlannerTranscript.import_bytes(transcript).export(),
                transcript,
            )
    def test_no_save_transition_records_and_rearms_checkpoint(self):
        with self._fixture(transition_subcode=3) as (rom, backend, _):
            symbols = subprocess.run(
                ["arm-none-eabi-nm", str(rom.with_suffix(".elf"))],
                capture_output=True,
                text=True,
            )
            self.assertEqual(symbols.returncode, 0, symbols.stderr)
            self.assertRegex(
                symbols.stdout,
                r"\bGotoChapterWithoutSave(?:\n|$)",
            )
            disassembly = subprocess.run(
                [
                    "arm-none-eabi-objdump",
                    "-d",
                    str(rom.with_suffix(".elf")),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                disassembly.returncode,
                0,
                disassembly.stderr,
            )
            no_save = disassembly.stdout.split(
                "<GotoChapterWithoutSave>:",
                1,
            )[1].split("\n\n", 1)[0]
            self.assertIn("<Proc_Goto>", no_save)
            self.assertIn("<Proc_EndEach>", no_save)
            _, checkpoint, transcript = self._run_planner(
                backend,
                rom,
                planner.ScriptedPlanner(),
            )
            self.assertEqual(checkpoint[4], 1)
            self.assertNotEqual(checkpoint[12], 0)
            self.assertEqual(
                planner.PlannerTranscript.import_bytes(transcript).export(),
                transcript,
            )
    def test_exhausted_runs_restore_without_fallback_or_reentry(self):
        for candidate_mode, rejection in ((1, 5), (2, 7)):
            with self.subTest(candidate_mode=candidate_mode):
                with self._fixture(
                    candidate_mode=candidate_mode
                ) as (rom, backend, _):
                    with _open_transport(backend, rom) as transport:
                        exhausted = transport.start()
                        self.assertEqual(exhausted.state, 5)
                        self.assertEqual(exhausted.rejection, rejection)
                        self.assertEqual(exhausted.page_count, 1)
                        self.assertTrue(
                            all(
                                value == 0
                                for value in transport.checkpoint
                            )
                        )
                        stale_start = transport.start()
                        self.assertEqual(stale_start.state, 5)
                        self.assertEqual(stale_start.rejection, 9)
                        self.assertIsNotNone(
                            transport.last_acknowledgement
                        )
                        self.assertEqual(
                            transport.last_acknowledgement.result,
                            0,
                        )
                        self.assertEqual(
                            transport.last_acknowledgement.rejection,
                            9,
                        )
    def test_available_zero_digests_round_trip_live_transport(self):
        with self._fixture(zero_digest=True) as (rom, backend, _):
            for implementation in (
                planner.ScriptedPlanner(),
                planner.BoundedSearchPlanner(max_nodes=512),
            ):
                with _open_transport(backend, rom) as transport:
                    first = planner.collect_observation_pages(
                        transport,
                        transport.start(),
                    )
                    fields = {field.name: field for field in first.fields}
                    self.assertEqual(
                        fields["flags_digest"].availability,
                        planner.Availability.AVAILABLE,
                    )
                    self.assertEqual(fields["flags_digest"].value, 0)
                    self.assertEqual(
                        fields["resource_digest"].availability,
                        planner.Availability.AVAILABLE,
                    )
                    self.assertEqual(fields["resource_digest"].value, 0)
                    choice = implementation.choose(first)
                    cancelled = transport.exchange(
                        planner.Command(
                            planner.CommandKind.CANCEL,
                            first.run_id,
                            first.observation_id,
                        )
                    )
                    self.assertEqual(cancelled.state, 4)
                    exported = transport.transcript.export()
                    self.assertEqual(
                        planner.PlannerTranscript.import_bytes(
                            exported
                        ).export(),
                        exported,
                    )
                    self.assertIsNotNone(choice)
    def test_flag_checkpoint_bounds_on_arm_transport(self):
        root = (
            TESTS_DIR.parents[2]
            / "build"
            / "test-artifacts"
            / "autoplay-planner"
        )
        root.mkdir(parents=True, exist_ok=True)
        cases = (
            (1, planner.Availability.AVAILABLE, 0),
            (2, planner.Availability.AVAILABLE, 8),
            (3, planner.Availability.AVAILABLE, 2048),
            (4, planner.Availability.UNINITIALIZED, 0),
            (5, planner.Availability.UNINITIALIZED, 0),
            (6, planner.Availability.UNINITIALIZED, 2048),
        )
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            for mode, availability, flag_count in cases:
                case_root = Path(temporary) / f"flags-{mode}"
                case_root.mkdir()
                rom, backend = self._build_or_skip(
                    case_root,
                    transition_subcode=3,
                    flag_domain_mode=mode,
                )
                with _open_transport(backend, rom) as transport:
                    first = planner.collect_observation_pages(
                        transport,
                        transport.start(),
                    )
                    fields = {field.name: field for field in first.fields}
                    self.assertEqual(
                        fields["flags_digest"].availability,
                        availability,
                    )
                    self.assertEqual(len(first.flags), flag_count)
                    if flag_count:
                        self.assertTrue(
                            all(
                                record.availability is availability
                                for record in first.flags
                            )
                        )
                    action = first.actions[0]
                    waiting = transport.exchange(
                        planner.Command(
                            planner.CommandKind.COMMIT,
                            first.run_id,
                            first.observation_id,
                            action.ordinal,
                            action.token,
                        )
                    )
                    self.assertEqual(waiting.chapter, 2)
                    self.assertEqual(
                        transport.checkpoint[0],
                        0x41504C4E,
                    )
                    self.assertEqual(
                        transport.checkpoint[4],
                        first.chapter,
                    )
    def test_commit_waits_beyond_legacy_120_frame_window(self):
        with self._fixture(
            commit_delay_frames=180,
            commit_completion_frame_limit=600,
        ) as (rom, backend, _):
            for implementation in (
                planner.ScriptedPlanner(),
                planner.BoundedSearchPlanner(max_nodes=512),
            ):
                with _open_transport(backend, rom) as transport:
                    waiting = transport.start()
                    complete = planner.collect_observation_pages(
                        transport,
                        waiting,
                    )
                    choice = implementation.choose(complete)
                    followup = transport.exchange(
                        planner.Command(
                            planner.CommandKind.COMMIT,
                            complete.run_id,
                            complete.observation_id,
                            choice.ordinal,
                            choice.token,
                        )
                    )
                    self.assertEqual(followup.state, 2)
                    self.assertEqual(followup.chapter, 2)
                    acknowledgement = transport.last_acknowledgement
                    completion = transport.last_completion
                    self.assertIsNotNone(acknowledgement)
                    self.assertIsNotNone(completion)
                    self.assertEqual(acknowledgement.kind, 2)
                    self.assertEqual(acknowledgement.result, 1)
                    self.assertEqual(acknowledgement.rejection, 0)
                    self.assertGreater(completion.response_frames, 120)
                    self.assertEqual(
                        completion.command_id,
                        acknowledgement.command_id,
                    )
    def test_acknowledged_commit_timeout_never_emits_stale_observation(self):
        with self._fixture(
            stall_after_commit=True,
            commit_completion_frame_limit=60,
        ) as (rom, backend, _):
            with _open_transport(backend, rom) as transport:
                waiting = transport.start()
                complete = planner.collect_observation_pages(transport, waiting)
                choice = planner.ScriptedPlanner().choose(complete)
                with self.assertRaises(PlannerTransportError) as raised:
                    transport.exchange(
                        planner.Command(
                            planner.CommandKind.COMMIT,
                            complete.run_id,
                            complete.observation_id,
                            choice.ordinal,
                            choice.token,
                        )
                    )
                self.assertEqual(
                    raised.exception.code,
                    "ACTION_COMPLETION_TIMEOUT",
                )
                self.assertIsNotNone(transport.last_acknowledgement)
                self.assertEqual(transport.last_acknowledgement.kind, 2)
                self.assertEqual(transport.last_acknowledgement.result, 1)
                self.assertEqual(transport.last_acknowledgement.rejection, 0)
                self.assertIsNone(transport.last_completion)
                remaining_stdout, _ = transport.process.communicate(timeout=10)
                self.assertEqual(remaining_stdout, "")
                self.assertEqual(transport.process.returncode, 3)
    def test_unacknowledged_command_returns_typed_timeout(self):
        with self._fixture(
            ignore_commands=True,
            acknowledgement_frame_limit=20,
        ) as (rom, backend, _):
            with _open_transport(backend, rom) as transport:
                with self.assertRaises(PlannerTransportError) as raised:
                    transport.start()
                self.assertEqual(
                    raised.exception.code,
                    "COMMAND_ACK_TIMEOUT",
                )
                self.assertIsNone(transport.last_acknowledgement)
                self.assertIsNone(transport.last_completion)
                remaining_stdout, _ = transport.process.communicate(timeout=10)
                self.assertEqual(remaining_stdout, "")
                self.assertEqual(transport.process.returncode, 3)
    def test_backend_requires_exact_ready_before_stdin(self):
        if host_mode.host_only_enabled():
            self.skipTest(
                "production READY test belongs to the toolchain lane"
            )
        root = (
            TESTS_DIR.parents[2]
            / "build"
            / "test-artifacts"
            / "autoplay-planner"
        )
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            immediate_dir = Path(temporary) / "immediate"
            immediate_dir.mkdir()
            rom, backend = self._build_transport(str(immediate_dir))
            with _open_transport(backend, rom) as transport:
                self.assertEqual(transport.observation.state, 1)
            delayed_dir = Path(temporary) / "delayed"
            delayed_dir.mkdir()
            rom, backend = self._build_transport(
                str(delayed_dir),
                startup_delay_frames=8,
                test_bootstrap=True,
            )
            with _open_transport(backend, rom) as transport:
                self.assertEqual(transport.observation.state, 1)
            failure_cases = (
                ("delayed-no-bootstrap", 8, 0, False, "startup"),
                ("never-ready", 10000, 0, True, "bootstrap"),
                ("wrong-state", 0, 2, True, "bootstrap"),
                ("exhausted-state", 0, 5, True, "bootstrap"),
            )
            for (
                name,
                delay,
                state,
                bootstrap,
                diagnostic,
            ) in failure_cases:
                with self.subTest(startup=name):
                    case_dir = Path(temporary) / name
                    case_dir.mkdir()
                    rom, backend = self._build_transport(
                        str(case_dir),
                        startup_delay_frames=delay,
                        startup_state_override=state,
                        test_bootstrap=bootstrap,
                    )
                    completed = subprocess.run(
                        [str(backend), str(rom)],
                        input="READ\nSTART 0 0 0 0\n",
                        capture_output=True,
                        text=True,
                        timeout=20,
                    )
                    self.assertEqual(completed.returncode, 3)
                    self.assertEqual(completed.stdout, "")
                    self.assertIn(diagnostic, completed.stderr)
    def test_invalid_ack_is_rejected_before_ack_or_observation(self):
        with self._fixture(
            acknowledgement_override=(0, 0xFFFFFFFF)
        ) as (rom, backend, _):
            with _open_transport(backend, rom) as transport:
                with self.assertRaises(PlannerTransportError) as raised:
                    transport.start()
                self.assertEqual(
                    raised.exception.code,
                    "INVALID_COMMAND_ACK",
                )
                self.assertIsNone(transport.last_acknowledgement)
                self.assertIsNone(transport.last_completion)
                remaining_stdout, _ = transport.process.communicate(
                    timeout=10
                )
                self.assertEqual(remaining_stdout, "")
                self.assertEqual(transport.process.returncode, 3)
    def test_restricted_backend_rejects_frame_and_key_controls(self):
        with self._fixture() as (rom, backend, _):
            with _open_transport(backend, rom) as transport:
                observation_before = transport.observation
                checkpoint_before = transport.checkpoint
                command_before = transport.command
                transcript_before = transport.transcript.export()
                assert transport.process.stdin is not None
                assert transport.process.stdout is not None
                def assert_rejected(raw_command, diagnostic):
                    transport.process.stdin.write(raw_command + "\n")
                    transport.process.stdin.flush()
                    self.assertEqual(
                        transport.process.stdout.readline(), diagnostic
                    )
                    transport.process.stdin.write("READ\n")
                    transport.process.stdin.flush()
                    self.assertEqual((
                        transport._read_state(), transport.checkpoint,
                        transport.command, transport.transcript.export(),
                    ), (
                        observation_before, checkpoint_before,
                        command_before, transcript_before,
                    ))
                for raw_command in (
                    "STEP",
                    "RUN 100 1",
                    "KEYS 3ff",
                ):
                    assert_rejected(raw_command, "ERROR unknown typed command\n")
                invalid_words = (
                    "-0", "+0", "-1", "+1", " +0", "\t-0",
                    "100000000", "FFFFFFFFF", "0x1", "1junk",
                )
                commands = (
                    ("START {} 0 0 0", "START"),
                    ("PAGE {} 0 0", "PAGE"),
                    ("COMMIT {} 0 0 0 0 0 0", "COMMIT"),
                    ("CANCEL {} 0", "CANCEL"),
                )
                for template, name in commands:
                    for word in invalid_words:
                        assert_rejected(
                            template.format(word), f"ERROR malformed {name}\n"
                        )
                transport.process.stdin.write("READ" + " " * 507 + "\n")
                transport.process.stdin.flush()
                self.assertEqual(
                    transport._read_state(),
                    observation_before,
                )
                for trailing_command in (
                    " CANCEL 00000000 00000000",
                    " COMMIT 00000000 00000000 00000000 "
                    "00000000 00000000 00000000 00000000",
                ):
                    assert_rejected(
                        "X" * 600 + trailing_command,
                        "ERROR malformed line\n",
                    )
                waiting = transport.start()
                cancelled = transport.exchange(
                    planner.Command(
                        planner.CommandKind.CANCEL,
                        waiting.run_id,
                        waiting.observation_id,
                    )
                )
                self.assertEqual(cancelled.state, 4)
                encoded = json.loads(transport.transcript.export())
            overlong_eof = subprocess.run(
                [str(backend), str(rom)],
                input="X" * 600,
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(overlong_eof.returncode, 0)
            self.assertEqual(
                overlong_eof.stdout.splitlines()[-1:],
                ["ERROR malformed line"],
            )
            self.assertEqual(
                overlong_eof.stdout.count("ERROR malformed line"),
                1,
            )
            for unsupported_kind in ("RUN", 0xFFFFFFFF):
                with self.subTest(unsupported_kind=unsupported_kind):
                    tampered = json.loads(planner._canonical(encoded))
                    command_event = next(
                        event
                        for event in tampered["events"]
                        if event["event"] == "command"
                    )
                    command_event["command"]["kind"] = unsupported_kind
                    _rechain_transcript(tampered)
                    factory_calls = 0
                    def factory():
                        nonlocal factory_calls
                        factory_calls += 1
                        return PlannerProcessTransport(backend, rom)
                    with self.assertRaisesRegex(
                        planner.PlannerError,
                        "unsupported command kind",
                    ):
                        planner.replay_transcript_on_clean_transport(
                            planner._canonical(tampered),
                            factory,
                        )
                    self.assertEqual(factory_calls, 0)
    def test_production_transcript_capacity_rejects_before_mailbox_write(self):
        with self._fixture() as (rom, backend, _):
            with _open_transport(backend, rom) as transport:
                transport.transcript = planner.PlannerTranscript(
                    max_bytes=planner.MAX_TRANSCRIPT_EXCHANGE_BYTES
                )
                transport._transcript_started = False
                transport._begin_transcript(
                    transport.observation,
                    allow_uninitialized=True,
                )
                transcript_before = transport.transcript.export()
                command_before = transport.command
                observation_before = transport.observation
                with self.assertRaisesRegex(
                    planner.PlannerError,
                    planner.Rejection.RESOURCE_LIMIT.value,
                ):
                    transport.start()
                self.assertEqual(transport.transcript.export(), transcript_before)
                self.assertEqual(transport.command, command_before)
                self.assertIs(transport.observation, observation_before)
                self.assertIsNone(transport.last_acknowledgement)
                self.assertIsNone(transport.last_completion)
    def test_host_driven_transport_rejects_and_times_out(self):
        with self._fixture() as (rom, backend, _):
            with _open_transport(backend, rom) as transport:
                wrong_scenario = (
                    transport.observation.actual_scenario_identity ^ 1
                )
                acknowledgements = []
                for _ in range(3):
                    rejected = transport.start(
                        scenario_identity=wrong_scenario
                    )
                    self.assertEqual(rejected.rejection, 9)
                    acknowledgements.append(
                        transport.last_acknowledgement
                    )
                (
                    first_rejection_ack,
                    second_rejection_ack,
                    third_rejection_ack,
                ) = acknowledgements
                self.assertIsNotNone(first_rejection_ack)
                self.assertIsNotNone(second_rejection_ack)
                self.assertIsNotNone(third_rejection_ack)
                self.assertEqual(first_rejection_ack.rejection, 9)
                self.assertEqual(second_rejection_ack.rejection, 9)
                self.assertEqual(third_rejection_ack.rejection, 9)
                self.assertEqual(
                    (
                        first_rejection_ack.command_id + 1,
                        second_rejection_ack.command_id + 1,
                    ),
                    (
                        second_rejection_ack.command_id,
                        third_rejection_ack.command_id,
                    ),
                )
                waiting = transport.start()
                start_acknowledgement = transport.last_acknowledgement
                start_completion = transport.last_completion
                self.assertIsNotNone(start_acknowledgement)
                self.assertIsNotNone(start_completion)
                self.assertEqual(start_acknowledgement.kind, 1)
                self.assertEqual(start_acknowledgement.result, 1)
                self.assertLess(start_completion.response_frames, 120)
                complete = planner.collect_observation_pages(transport, waiting)
                page_acknowledgement = transport.last_acknowledgement
                page_completion = transport.last_completion
                self.assertIsNotNone(page_acknowledgement)
                self.assertIsNotNone(page_completion)
                self.assertEqual(page_acknowledgement.kind, 4)
                self.assertEqual(page_acknowledgement.result, 1)
                self.assertLess(page_completion.response_frames, 120)
                choice = planner.ScriptedPlanner().choose(complete)
                forged = transport.exchange(
                    planner.Command(
                        planner.CommandKind.COMMIT,
                        complete.run_id,
                        complete.observation_id,
                        choice.ordinal,
                        planner.OpaqueToken(
                            choice.token.word0,
                            choice.token.word1,
                            choice.token.word2,
                            choice.token.word3 ^ 1,
                        ),
                    )
                )
                self.assertEqual(forged.rejection, 4)
                cancelled = transport.exchange(
                    planner.Command(
                        planner.CommandKind.CANCEL,
                        complete.run_id,
                        complete.observation_id,
                    )
                )
                self.assertEqual(cancelled.state, 4)
                self.assertEqual(cancelled.rejection, 8)
                cancel_acknowledgement = transport.last_acknowledgement
                cancel_completion = transport.last_completion
                self.assertIsNotNone(cancel_acknowledgement)
                self.assertIsNotNone(cancel_completion)
                self.assertEqual(cancel_acknowledgement.kind, 3)
                self.assertEqual(cancel_acknowledgement.rejection, 8)
                self.assertLess(cancel_completion.response_frames, 120)
                self.assertTrue(all(value == 0 for value in transport.checkpoint))
            with _open_transport(backend, rom) as transport:
                waiting = transport.start()
                complete = planner.collect_observation_pages(transport, waiting)
                choice = planner.ScriptedPlanner().choose(complete)
                continued = transport.exchange(
                    planner.Command(
                        planner.CommandKind.COMMIT,
                        complete.run_id,
                        complete.observation_id,
                        choice.ordinal,
                        choice.token,
                    )
                )
                self.assertEqual(continued.state, 2)
                self.assertEqual(transport.checkpoint[0], 0x41504C4E)
                self.assertEqual(transport.checkpoint[4], 1)
                cancelled = transport.exchange(
                    planner.Command(
                        planner.CommandKind.CANCEL,
                        continued.run_id,
                        continued.observation_id,
                    )
                )
                self.assertEqual(cancelled.state, 4)
                self.assertEqual(cancelled.rejection, 8)
                self.assertTrue(all(value == 0 for value in transport.checkpoint))
            with _open_transport(backend, rom) as transport:
                waiting = transport.start()
                complete = planner.collect_observation_pages(transport, waiting)
                choice = planner.ScriptedPlanner().choose(complete)
                waiting = transport.exchange(
                    planner.Command(
                        planner.CommandKind.COMMIT,
                        complete.run_id,
                        complete.observation_id,
                        choice.ordinal,
                        choice.token,
                    )
                )
                self.assertEqual(waiting.state, 2)
                self.assertEqual(transport.checkpoint[0], 0x41504C4E)
                self.assertEqual(transport.checkpoint[4], 1)
                for _ in range(300):
                    waiting = transport.exchange(
                        planner.Command(
                            planner.CommandKind.PAGE,
                            waiting.run_id,
                            waiting.observation_id,
                            page_index=0,
                        )
                    )
                    if waiting.state == 4:
                        break
                self.assertEqual(waiting.state, 4)
                self.assertEqual(waiting.rejection, 10)
                self.assertTrue(all(value == 0 for value in transport.checkpoint))

    @unittest.skipUnless(
        os.environ.get("PLANNER_PRODUCTION_ROM")
        and os.environ.get("PLANNER_PRODUCTION_ELF"),
        "enabled production ROM/ELF not supplied",
    )
    def test_enabled_production_rom_executes_host_selected_action(self):
        rom = Path(os.environ["PLANNER_PRODUCTION_ROM"])
        elf = Path(os.environ["PLANNER_PRODUCTION_ELF"])
        root = TESTS_DIR.parents[2] / "build" / "test-artifacts" / "autoplay-planner"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            blocked_backend = Path(temporary) / "planner-transport-blocked"
            build_planner_transport_backend(blocked_backend, elf)
            blocked = subprocess.run(
                [str(blocked_backend), str(rom)],
                input="READ\n",
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(blocked.returncode, 3)
            self.assertEqual(blocked.stdout, "")
            self.assertIn("startup is not READY", blocked.stderr)
            backend = Path(temporary) / "planner-transport"
            build_planner_transport_backend(
                backend,
                elf,
                test_bootstrap=True,
            )
            with _open_transport(backend, rom) as transport:
                self.assertEqual(transport.observation.state, 1)
                waiting = transport.start()
                self.assertEqual(waiting.state, 2)
                complete = planner.collect_observation_pages(transport, waiting)
                self.assertGreater(len(complete.map_cells), 0)
                self.assertGreater(len(complete.units), 0)
                self.assertGreater(len(complete.actions), 0)
                choice = planner.ScriptedPlanner().choose(complete)
                actor_before = next(
                    unit for unit in complete.units if unit.slot == choice.action.actor
                )
                self.assertNotEqual(actor_before.position, choice.action.destination)
                forged = transport.exchange(
                    planner.Command(
                        planner.CommandKind.COMMIT,
                        complete.run_id,
                        complete.observation_id,
                        choice.ordinal,
                        planner.OpaqueToken(
                            choice.token.word0 ^ 1,
                            choice.token.word1,
                            choice.token.word2,
                            choice.token.word3,
                        ),
                    )
                )
                self.assertEqual(forged.rejection, 4)
                accepted = transport.exchange(
                    planner.Command(
                        planner.CommandKind.COMMIT,
                        complete.run_id,
                        complete.observation_id,
                        choice.ordinal,
                        choice.token,
                    )
                )
                self.assertEqual(accepted.state, 2)
                self.assertNotEqual(
                    accepted.observation_id,
                    complete.observation_id,
                )
                followup = planner.collect_observation_pages(
                    transport, accepted
                )
                actor_after = next(
                    unit for unit in followup.units if unit.slot == choice.action.actor
                )
                self.assertEqual(actor_after.position, choice.action.destination)
                cancelled = transport.exchange(
                    planner.Command(
                        planner.CommandKind.CANCEL,
                        followup.run_id,
                        followup.observation_id,
                    )
                )
                self.assertEqual(cancelled.state, 4)
                transcript = transport.transcript.export()
                imported = planner.PlannerTranscript.import_bytes(transcript)
                self.assertEqual(imported.export(), transcript)
                self.assertEqual(
                    imported.events[0]["event"],
                    "session",
                )

if __name__ == "__main__":
    unittest.main()
